"""Tests de /api/smart y /api/delegate con TestClient."""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.models.delegate import AgentStatus
from app.models.provider import Provider, ProviderRegistry
from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent, DelegationConfig, RouteTarget, SmartRoutingConfig, TierPolicy
from app.services import decisions_log, health_service, providers_service
from app.services.cli_agents import broker
from app.services.cli_agents import registry as agents_registry


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app.core import config as cfg
    from app.main import app

    real_settings = cfg.get_settings()

    (tmp_path / "config").mkdir()

    class FakeSettings:
        litellm_config_dir = str(tmp_path / "config")
        ui_api_key = real_settings.ui_api_key

    for mod in (cfg, health_service, broker):
        monkeypatch.setattr(mod, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    broker.reset_for_tests()
    registry = ProviderRegistry(
        active_provider_id="copilot",
        providers=[Provider(id="copilot", name="c", api_base="https://c.example.com/v1", active_model="gpt"),
                   Provider(id="ollama", name="o", api_base="http://127.0.0.1:11434", active_model="llama")],
        smart=SmartRoutingConfig(enabled=True, mode="shadow", tiers=[TierPolicy(tier="trivial", targets=[RouteTarget(provider_id="ollama")])]),
        cli_agents=[CliAgent(**d) for d in DEFAULT_CLI_AGENTS],
        delegation=DelegationConfig(enabled=False),
    )
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)

    def fake_update(smart=None, cli_agents=None, delegation=None):
        if smart is not None:
            registry.smart = smart
        if cli_agents is not None:
            registry.cli_agents = cli_agents
        if delegation is not None:
            registry.delegation = delegation
        return registry

    monkeypatch.setattr(providers_service, "update_smart_config", fake_update)
    monkeypatch.setattr(providers_service, "_is_reachable", AsyncMock(return_value=True))
    monkeypatch.setattr(agents_registry, "probe", AsyncMock(side_effect=lambda a, force=False: AgentStatus(id=a.id, installed=a.id != "codex", auth="ok" if a.id == "ollama" else "unknown")))
    client = TestClient(app, headers={"x-api-key": real_settings.ui_api_key})
    yield client, registry
    broker.reset_for_tests()
    health_service.reload_for_tests()


def test_smart_routes_require_auth(env):
    client, _ = env
    assert TestClient(client.app).get("/api/smart/config").status_code == 401
    assert TestClient(client.app).get("/api/delegate/jobs").status_code == 401


def test_get_config_returns_agents_and_recommendations(env):
    client, _ = env
    resp = client.get("/api/smart/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["smart"]["mode"] == "shadow" and data["tiers"] == ["trivial", "simple", "standard", "complex"]
    assert {a["id"] for a in data["agents"]} == {"claude", "codex", "copilot", "antigravity", "ollama"}
    codes = {r["code"] for r in data["recommendations"]}
    assert {"ollama_anthropic_native", "shadow_mode"} <= codes


def test_put_config_validates_and_persists(env):
    client, registry = env
    bad = client.put("/api/smart/config", json={"smart": {"enabled": True, "thresholds": {"simple": 50, "standard": 20, "complex": 75}}})
    assert bad.status_code == 400
    unknown = client.put("/api/smart/config", json={"smart": {"tiers": [{"tier": "trivial", "targets": [{"provider_id": "nope"}]}]}})
    assert unknown.status_code == 400 and "nope" in unknown.json()["detail"]
    dangerous = client.put("/api/smart/config", json={"cli_agents": [{"id": "claude", "extra_args": ["--dangerously-skip-permissions"]}]})
    assert dangerous.status_code == 400
    ok = client.put("/api/smart/config", json={"smart": {"enabled": True, "mode": "active", "tiers": [{"tier": "trivial", "targets": [{"provider_id": "ollama"}]}]},
                                                "delegation": {"enabled": True, "workspace_allowlist": ["C:/definitely/missing" if True else "/tmp"]}})
    assert ok.status_code == 200
    assert registry.smart.mode == "active" and registry.delegation.enabled is True
    assert ok.json()["warnings"]


def test_classify_and_explain(env):
    client, _ = env
    cls = client.post("/api/smart/classify", json={"task": "Diagnostica la causa raíz del deadlock y refactoriza"}).json()
    assert cls["intent"] == "debug" and cls["tier"] in ("standard", "complex")
    body = {"model": "claude-3-5-haiku", "messages": [{"role": "user", "content": "hola"}]}
    explain = client.post("/api/smart/explain", json={"body": body, "prompt_tokens": 10}).json()
    assert explain["tier"] == "trivial" and explain["would_key"] == "provider:ollama" and explain["mode"] == "shadow"
    assert client.post("/api/smart/classify", json={}).status_code == 400


def test_health_reset_and_decisions_endpoints(env):
    client, _ = env
    from app.core.quota_signals import QuotaSignal
    health_service.mark_signal("provider:copilot", QuotaSignal("rate_limit", 60, "429"))
    assert client.get("/api/smart/health").json()["targets"]["provider:copilot"]["state"] == "cooling"
    assert client.post("/api/smart/health/reset", params={"target": "provider:copilot"}).json()["reset"] == ["provider:copilot"]
    assert client.get("/api/smart/decisions").status_code == 200
    summary = client.get("/api/smart/decisions/summary", params={"period": "week"}).json()
    assert summary["period"] == "week" and "count" in summary


def test_preset_and_probe(env):
    client, registry = env
    preset = client.post("/api/smart/presets/default").json()
    assert {t["tier"] for t in preset["tiers"]} == {"trivial", "simple", "standard", "complex"}
    assert all(t["provider_id"] in ("copilot", "ollama") for p in preset["tiers"] for t in p["targets"])
    assert client.post("/api/smart/agents/claude/probe").json()["installed"] is True
    assert client.post("/api/smart/agents/nope/probe").status_code == 404


def test_delegate_disabled_returns_409_and_dry_run_works(env, tmp_path, monkeypatch):
    client, registry = env
    ws = tmp_path / "repo"
    ws.mkdir()
    resp = client.post("/api/delegate/jobs", json={"task": "haz algo", "workspace": str(ws)})
    assert resp.status_code == 409 and resp.json()["detail"] == "delegation_disabled"
    registry.delegation = DelegationConfig(enabled=True, workspace_allowlist=[str(ws)])
    for agent in registry.cli_agents:
        agent.enabled = agent.id == "claude"
    dry = client.post("/api/delegate/jobs", json={"task": "genera specs para src/a.py", "workspace": str(ws), "dry_run": True})
    assert dry.status_code == 200 and dry.json()["agent_id"] == "claude"
    bad_ws = client.post("/api/delegate/jobs", json={"task": "implementa el endpoint", "workspace": str(tmp_path)})
    assert bad_ws.status_code == 400 and bad_ws.json()["detail"] == "workspace_not_allowed"
    assert client.get("/api/delegate/jobs/nope").status_code == 404
    assert client.get("/api/delegate/jobs").json() == {"jobs": []}
