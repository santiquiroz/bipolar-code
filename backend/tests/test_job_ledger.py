from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.delegate import Attempt, Job
from app.models.provider import ProviderRegistry
from app.models.smart import CliAgent
from app.services import usage_tracker
from app.services.cli_agents import job_ledger, outcome_stats

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def ledger_db(tmp_path, monkeypatch):
    db_path = tmp_path / "usage.db"
    monkeypatch.setattr(usage_tracker, "_db_path", lambda: db_path)
    job_ledger.reset_for_tests()
    outcome_stats.reset_for_tests()
    yield db_path
    job_ledger.reset_for_tests()
    outcome_stats.reset_for_tests()


def _job(job_id="j1", **kw) -> Job:
    base = dict(id=job_id, created_at=(NOW - timedelta(seconds=30)).isoformat(),
                finished_at=NOW.isoformat(), status="succeeded", agent_id="codex", tier="simple")
    base.update(kw)
    return Job(**base)


async def _fetch(db_path, sql):
    async with aiosqlite.connect(str(db_path)) as db:
        return await (await db.execute(sql)).fetchall()


async def _seed_job(db_path, job_id, agent, status, days_ago=1):
    await job_ledger.init_tables()
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "INSERT INTO delegate_jobs (id, finished_at, agent_id, tier, status, attempts) VALUES (?,?,?,?,?,1)",
            (job_id, (NOW - timedelta(days=days_ago)).isoformat(), agent, "simple", status),
        )
        await db.commit()


async def _seed_request(db_path, provider_id, model, cost, tin=10, tout=5):
    await usage_tracker.init_db()
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "INSERT INTO requests (timestamp, provider_id, model, input_tokens, output_tokens, cost_usd) "
            "VALUES (?,?,?,?,?,?)",
            ((NOW - timedelta(hours=1)).isoformat(), provider_id, model, tin, tout, cost),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_record_job_inserts_and_replaces(ledger_db):
    await job_ledger.record_job(_job(attempts=[Attempt(agent_id="codex", started_at=NOW.isoformat())], tokens_in=7, cost_usd=0.5))
    await job_ledger.record_job(_job(status="failed", escalations=2))
    rows = await _fetch(ledger_db, "SELECT id, agent_id, tier, status, attempts, escalations FROM delegate_jobs")
    assert rows == [("j1", "codex", "simple", "failed", 0, 2)]


@pytest.mark.asyncio
async def test_record_job_duration_and_defaults(ledger_db):
    await job_ledger.record_job(_job(agent_id=None))
    await job_ledger.record_job(_job("j2", created_at="no-es-fecha"))
    rows = dict((r[0], r[1:]) for r in await _fetch(ledger_db, "SELECT id, agent_id, duration_s FROM delegate_jobs"))
    assert rows["j1"] == ("none", pytest.approx(30, abs=0.01))
    assert rows["j2"][1] is None


@pytest.mark.asyncio
async def test_record_job_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("sin disco")
    monkeypatch.setattr(usage_tracker, "_db_path", boom)
    await job_ledger.record_job(_job())


def test_pool_of_by_base():
    pools = {a: job_ledger.pool_of(CliAgent(id=a)) for a in
             ("claude", "anthropic", "openrouter", "ollama", "deepseek", "muse", "codex", "cursor")}
    assert pools == {"claude": "anthropic_account", "anthropic": "anthropic_api", "openrouter": "free",
                     "ollama": "free", "deepseek": "prepaid", "muse": "subscription",
                     "codex": "subscription", "cursor": "subscription"}
    assert job_ledger.pool_of(CliAgent(id="claude-2")) == "anthropic_account"


@pytest.mark.asyncio
async def test_efficiency_shape_and_values(ledger_db):
    agents = [CliAgent(id="claude", name="Claude", model_by_tier={"standard": "sonnet"}),
              CliAgent(id="deepseek"), CliAgent(id="codex", name="Codex"), CliAgent(id="muse")]
    for i in range(3):
        await _seed_job(ledger_db, f"c{i}", "claude", "succeeded")
    await _seed_job(ledger_db, "c3", "claude", "failed")
    await _seed_job(ledger_db, "x1", "codex", "succeeded")
    await _seed_job(ledger_db, "old", "codex", "succeeded", days_ago=60)
    for i in range(2):
        await _seed_job(ledger_db, f"d{i}", "deepseek", "quota")
    await _seed_request(ledger_db, "cli:claude", "sonnet", 0.2)
    await _seed_request(ledger_db, "cli:claude", "sonnet", None)
    await _seed_request(ledger_db, "cli:deepseek", "ds", None)
    await outcome_stats.record("claude", "simple", "first_pass")
    await outcome_stats.record("claude", "simple", "rejected")

    result = await job_ledger.efficiency(30, agents)

    assert list(result) == ["days", "since", "totals", "pools", "agents", "warnings"]
    assert result["days"] == 30
    assert result["totals"] == {"jobs": 7, "succeeded": 4, "success_pct": 57.1, "cost_usd": 0.2,
                                "tokens_in": 30, "tokens_out": 15, "anthropic_share_pct": 75.0}
    assert [p["pool"] for p in result["pools"]] == list(job_ledger.POOL_LABELS)
    assert [p["label"] for p in result["pools"]] == list(job_ledger.POOL_LABELS.values())
    assert result["pools"][0] == {"pool": "anthropic_account", "label": "Suscripción Claude",
                                  "jobs": 4, "succeeded": 3, "cost_usd": 0.2}
    assert [a["agent_id"] for a in result["agents"]] == ["claude", "deepseek", "codex"]
    claude, deepseek, codex = result["agents"]
    assert claude == {"agent_id": "claude", "name": "Claude", "pool": "anthropic_account", "jobs": 4,
                      "succeeded": 3, "success_pct": 75.0, "statuses": {"succeeded": 3, "failed": 1},
                      "accept_pct": 50.0, "cost_usd": 0.2, "tokens_in": 20, "tokens_out": 10,
                      "cost_per_success": 0.0667, "models": ["sonnet"]}
    assert deepseek["cost_usd"] is None and deepseek["cost_per_success"] is None
    assert deepseek["statuses"] == {"quota": 2} and deepseek["accept_pct"] is None
    assert codex["jobs"] == 1 and codex["cost_usd"] is None


@pytest.mark.asyncio
async def test_efficiency_includes_agent_with_only_usage_rows(ledger_db):
    await _seed_request(ledger_db, "cli:muse", "m", 0.1)
    result = await job_ledger.efficiency(7, [CliAgent(id="muse")])
    assert [(a["agent_id"], a["jobs"], a["cost_usd"]) for a in result["agents"]] == [("muse", 0, 0.1)]
    assert result["totals"]["success_pct"] is None and result["totals"]["anthropic_share_pct"] is None


@pytest.mark.asyncio
async def test_efficiency_month_spend_only_for_capped_agents(ledger_db):
    await _seed_request(ledger_db, "cli:deepseek", "ds", 9.0)
    capped = CliAgent(id="deepseek", name="DeepSeek", max_monthly_usd=10)
    result = await job_ledger.efficiency(30, [capped])
    assert [w["code"] for w in result["warnings"]] == ["monthly_budget"]
    assert result["warnings"][0]["level"] == "medium"
    assert all("_accept_n" not in a for a in result["agents"])


def _row(agent_id="codex", **kw):
    row = {"agent_id": agent_id, "name": agent_id, "pool": "subscription", "jobs": 0, "succeeded": 0,
           "statuses": {}, "accept_pct": None, "_accept_n": 0}
    row.update(kw)
    return row


def _codes(rows=(), agents=(), totals=None, spend=None):
    return [w["code"] for w in job_ledger.efficiency_warnings(list(rows), list(agents), totals or {}, spend or {})]


def test_warning_same_account_opus():
    for model in ("", "claude-opus-4-6"):
        w = job_ledger.efficiency_warnings([], [CliAgent(id="claude", enabled=True, default_model=model)], {}, {})
        assert [(x["code"], x["level"], x["agent_id"]) for x in w] == [("same_account_opus", "high", "claude")]
    assert "Opus en tu misma cuenta" in w[0]["message"]
    assert _codes(agents=[CliAgent(id="claude", model_by_tier={"standard": "sonnet"})]) == []
    assert _codes(agents=[CliAgent(id="claude-2", account_dir="C:/a", default_model="opus")]) == []
    assert _codes(agents=[CliAgent(id="claude", enabled=False, default_model="opus")]) == []
    assert _codes(agents=[CliAgent(id="codex")]) == []


def test_warning_anthropic_share_high():
    w = job_ledger.efficiency_warnings([], [], {"anthropic_share_pct": 81.0, "succeeded": 10}, {})
    assert w[0]["code"] == "anthropic_share_high" and w[0]["level"] == "medium"
    assert w[0]["message"].startswith("El 81 % de los jobs exitosos")
    assert _codes(totals={"anthropic_share_pct": 49.9, "succeeded": 20}) == []
    assert _codes(totals={"anthropic_share_pct": 90.0, "succeeded": 9}) == []
    assert _codes(totals={"anthropic_share_pct": None, "succeeded": 0}) == []


def test_warning_lane_never_succeeds():
    row = _row("deepseek", name="DeepSeek", jobs=9, statuses={"auth_error": 1, "quota": 8})
    w = job_ledger.efficiency_warnings([row], [], {}, {})
    assert w[0]["code"] == "lane_never_succeeds" and w[0]["agent_id"] == "deepseek"
    assert w[0]["message"] == ("DeepSeek: 0 de 9 jobs terminaron bien (quota 8, auth_error 1). "
                               "Revisa login o saldo, o sácalo del orden.")
    assert _codes(rows=[_row(jobs=4, statuses={"quota": 4})]) == []
    assert _codes(rows=[_row(jobs=9, succeeded=1, statuses={"succeeded": 1, "quota": 8})]) == []


def test_warning_monthly_budget_levels():
    agent = CliAgent(id="deepseek", name="DeepSeek", max_monthly_usd=10)
    high = job_ledger.efficiency_warnings([], [agent], {}, {"deepseek": 8.0})
    assert (high[0]["code"], high[0]["level"]) == ("monthly_budget", "medium")
    assert high[0]["message"] == "DeepSeek lleva US$8.00 de US$10 este mes."
    mid = job_ledger.efficiency_warnings([], [agent], {}, {"deepseek": 5.0})
    assert mid[0]["level"] == "info"
    assert _codes(agents=[agent], spend={"deepseek": 4.9}) == []
    assert _codes(agents=[CliAgent(id="deepseek")], spend={"deepseek": 99}) == []


def test_warning_low_acceptance():
    w = job_ledger.efficiency_warnings([_row(accept_pct=40.0, _accept_n=5)], [], {}, {})
    assert (w[0]["code"], w[0]["level"]) == ("low_acceptance", "info")
    assert w[0]["message"] == "codex: el revisor aprueba solo el 40 % de sus entregas."
    assert _codes(rows=[_row(accept_pct=40.0, _accept_n=4)]) == []
    assert _codes(rows=[_row(accept_pct=50.0, _accept_n=9)]) == []
    assert _codes(rows=[_row(accept_pct=None)]) == []


def test_warning_order():
    rows = [_row("deepseek", name="D", jobs=5, statuses={"quota": 5}, accept_pct=10.0, _accept_n=5)]
    agents = [CliAgent(id="claude", enabled=True), CliAgent(id="deepseek", max_monthly_usd=10)]
    totals = {"anthropic_share_pct": 80.0, "succeeded": 10}
    assert _codes(rows, agents, totals, {"deepseek": 9.0}) == [
        "same_account_opus", "anthropic_share_high", "lane_never_succeeds", "monthly_budget", "low_acceptance"]


def _client():
    from app.main import app
    settings = get_settings()
    return TestClient(app, client=("127.0.0.1", 50000), headers={"x-api-key": settings.ui_api_key})


def test_efficiency_endpoint_shape_and_validation(monkeypatch):
    from app.api import delegate as delegate_api
    registry = ProviderRegistry(cli_agents=[CliAgent(id="codex", name="Codex")])
    monkeypatch.setattr(delegate_api.providers_service, "load_registry", lambda: registry)
    client = _client()

    response = client.get("/api/delegate/efficiency?days=7")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["days", "since", "totals", "pools", "agents", "warnings"]
    assert body["days"] == 7 and len(body["pools"]) == 5
    assert client.get("/api/delegate/efficiency").json()["days"] == 30
    assert client.get("/api/delegate/efficiency?days=0").status_code == 422
    assert client.get("/api/delegate/efficiency?days=366").status_code == 422
    assert TestClient(client.app, client=("127.0.0.1", 50000)).get("/api/delegate/efficiency").status_code == 401
