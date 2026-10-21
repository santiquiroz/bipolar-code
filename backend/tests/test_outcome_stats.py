from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.models.delegate import JobRequest
from app.models.provider import ProviderRegistry
from app.models.smart import CliAgent, DelegationConfig
from app.services.cli_agents import broker, outcome_stats
from tests.test_broker_quality_gate import _checks, _ok, _script, _setup, _verdict, _wait, env


@pytest.fixture(autouse=True)
def reset_outcomes():
    outcome_stats.reset_for_tests()
    yield
    outcome_stats.reset_for_tests()


@pytest.mark.asyncio
async def test_record_summary_filters_results_and_days(tmp_path, monkeypatch):
    db_path = tmp_path / "usage.db"
    monkeypatch.setattr(outcome_stats.usage_tracker, "_db_path", lambda: db_path)

    await outcome_stats.record("claude", "simple", "first_pass")
    await outcome_stats.record("claude", "simple", "revised")
    await outcome_stats.record("claude", "simple", "rejected")
    await outcome_stats.record("claude", "simple", "invalid")
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "INSERT INTO delegate_outcomes (timestamp, agent_id, tier, result) VALUES (?, ?, ?, ?)",
            ((datetime.now(timezone.utc) - timedelta(days=40)).isoformat(), "claude", "simple", "first_pass"),
        )
        await db.commit()

    assert await outcome_stats.summary(days=30) == [{
        "agent_id": "claude", "tier": "simple", "n": 3,
        "first_pass": 1, "revised": 1, "rejected": 1, "accept_pct": 67,
    }]


@pytest.mark.asyncio
async def test_accept_pct_requires_refresh_and_minimum_samples(tmp_path, monkeypatch):
    monkeypatch.setattr(outcome_stats.usage_tracker, "_db_path", lambda: tmp_path / "usage.db")
    await outcome_stats.record("codex", "standard", "first_pass")
    assert outcome_stats.accept_pct("codex", "standard", 1) is None

    await outcome_stats.refresh()
    assert outcome_stats.accept_pct("codex", "standard", 2) is None
    assert outcome_stats.accept_pct("codex", "standard", 1) == 100


@pytest.mark.asyncio
async def test_candidate_order_demotes_low_acceptance_and_preserves_preferred(tmp_path, monkeypatch):
    monkeypatch.setattr(outcome_stats.usage_tracker, "_db_path", lambda: tmp_path / "usage.db")
    for result in ("first_pass", "rejected", "rejected", "rejected", "rejected", "rejected"):
        await outcome_stats.record("claude", "simple", result)
    await outcome_stats.record("codex", "simple", "rejected")
    await outcome_stats.refresh()
    registry = ProviderRegistry(
        cli_agents=[CliAgent(id="claude"), CliAgent(id="codex"), CliAgent(id="muse")],
        delegation=DelegationConfig(tier_order={"simple": ["claude", "codex", "muse"]}),
    )

    assert broker._candidate_order("simple", registry, "") == ["claude", "codex", "muse"]
    registry.delegation.demote_low_acceptance = True
    assert broker._candidate_order("simple", registry, "") == ["codex", "muse", "claude"]
    assert broker._candidate_order("simple", registry, "claude") == ["claude", "codex", "muse"]


@pytest.mark.asyncio
async def test_broker_records_first_pass_after_accepted_gate(env, monkeypatch):
    outcome_stats.reset_for_tests()
    _setup(env)
    env["registry"].delegation.review_default = False
    _script(monkeypatch, [("codex", False, _ok())])
    _checks(monkeypatch, [])
    job = await _wait((await broker.submit(
        JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard")
    )).id)

    assert job.status == "succeeded"
    rows = await outcome_stats.summary()
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "codex"
    assert rows[0]["tier"] == "standard"
    assert rows[0]["n"] == 1
    assert rows[0]["first_pass"] == 1
    assert rows[0]["revised"] == 0
    assert rows[0]["rejected"] == 0
    assert rows[0]["accept_pct"] == 100


@pytest.mark.asyncio
async def test_broker_records_revised_and_rejected_quality_results(env, monkeypatch):
    outcome_stats.reset_for_tests()
    _setup(env)
    _script(monkeypatch, [
        ("codex", False, _ok()), ("claude", True, _verdict("revise", ["cambio"])),
        ("codex", False, _ok("corregido")), ("claude", True, _verdict("approve")),
    ])
    _checks(monkeypatch, [])
    revised = await _wait((await broker.submit(
        JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard")
    )).id)
    assert revised.status == "succeeded"
    rows = await outcome_stats.summary()
    row_codex = next((r for r in rows if r["agent_id"] == "codex"), None)
    assert row_codex is not None
    assert row_codex["revised"] == 1

    outcome_stats.reset_for_tests()
    broker.reset_for_tests()
    _setup(env)
    env["registry"].delegation.max_attempts = 2
    _script(monkeypatch, [
        ("codex", False, _ok()), ("claude", True, _verdict("reject", ["mal"])),
        ("claude", False, _ok()), ("codex", True, _verdict("reject", ["mal otra vez"])),
    ])
    _checks(monkeypatch, [])
    rejected = await _wait((await broker.submit(
        JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", max_revisions=0)
    )).id)
    assert rejected.status == "failed"
    rows = await outcome_stats.summary()
    row_rejected = next((r for r in rows if r["agent_id"] == "codex" and r.get("rejected", 0) == 1), None)
    assert row_rejected is not None


def test_delegate_stats_endpoint_requires_auth_and_returns_summary(monkeypatch):
    from app.main import app

    async def fake_summary(days=30):
        assert days == 7
        return [{"agent_id": "claude", "tier": "simple", "n": 5, "accept_pct": 80}]

    monkeypatch.setattr(outcome_stats, "summary", fake_summary)
    settings = get_settings()
    client = TestClient(
        app,
        client=("127.0.0.1", 50000),
        headers={"x-api-key": settings.ui_api_key},
    )
    response = client.get("/api/delegate/stats?days=7")
    assert response.status_code == 200
    assert response.json() == [{"agent_id": "claude", "tier": "simple", "n": 5, "accept_pct": 80}]
    assert TestClient(app, client=("127.0.0.1", 50000)).get("/api/delegate/stats").status_code == 401
    assert client.get("/api/delegate/stats?days=366").status_code == 422
