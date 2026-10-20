"""Carril openrouter: Claude Code headless contra la API Anthropic de OpenRouter, solo modelos :free."""
import json
from unittest.mock import AsyncMock

import pytest

from app.core.quota_signals import detect_signal
from app.models.delegate import AgentStatus
from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent
from app.services import providers_service
from app.services.cli_agents import adapters as ad
from app.services.cli_agents import broker
from app.services.cli_agents import registry as agents_registry

FREE_MODEL = "minimax/minimax-m3.1-flash:free"
KEY = "sk-or-test-key"


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    from app.core import config as cfg

    config = tmp_path / "config"
    config.mkdir()

    class FakeSettings:
        litellm_config_dir = str(config)

    for mod in (cfg, ad, providers_service, broker):
        monkeypatch.setattr(mod, "get_settings", lambda: FakeSettings())
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return config


def _agent(**overrides) -> CliAgent:
    base = next(d for d in DEFAULT_CLI_AGENTS if d["id"] == "openrouter")
    return CliAgent(**{**base, "enabled": True, **overrides})


def _build(workspace, model=FREE_MODEL, read_only=False):
    return ad.OpenRouterAdapter().build(_agent(), "claude.exe", "job1", "tarea", model, workspace, "simple", 600,
                                        read_only=read_only)


def test_build_points_claude_code_at_openrouter_with_free_model(config_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    spec = _build(tmp_path)
    assert spec.env["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    assert spec.env["ANTHROPIC_AUTH_TOKEN"] == KEY
    assert spec.env["ANTHROPIC_API_KEY"] == ""
    assert all(spec.env[name] == FREE_MODEL for name in ad.OPENROUTER_MODEL_VARS)
    assert spec.env["CLAUDE_CONFIG_DIR"].endswith("openrouter-claude")
    assert spec.argv[spec.argv.index("--model") + 1] == FREE_MODEL
    assert KEY not in " ".join(spec.argv) and KEY not in " ".join(spec.redacted)


def test_build_uses_default_model_when_none_requested(config_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    spec = _build(tmp_path, model="")
    assert spec.env["ANTHROPIC_MODEL"] == FREE_MODEL


@pytest.mark.parametrize("model", ["anthropic/claude-sonnet-4.5", "openrouter/auto", "x/y:free;rm", "free"])
def test_build_refuses_paid_or_malformed_models(config_dir, monkeypatch, tmp_path, model):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    with pytest.raises(ad.AdapterUnsafe, match="openrouter_model_not_free"):
        _build(tmp_path, model=model)


def test_build_refuses_without_key(config_dir, tmp_path):
    with pytest.raises(ad.AdapterUnsafe, match="openrouter_key_missing"):
        _build(tmp_path)


def test_read_only_build_keeps_plan_mode(config_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    argv = _build(tmp_path, read_only=True).argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"


def test_free_models_daily_cap_is_quota_exhausted():
    body = '{"error":{"message":"Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model requests per day"}}'
    assert detect_signal(body, 429).kind == "quota_exhausted"


def test_copilot_credit_floor(tmp_path):
    agent = CliAgent(**{**next(d for d in DEFAULT_CLI_AGENTS if d["id"] == "copilot"), "max_credits": 7})
    argv = ad.CopilotAdapter().build(agent, "copilot.exe", "job2", "tarea", "", tmp_path, "simple", 600).argv
    assert argv[argv.index("--max-ai-credits") + 1] == str(ad.COPILOT_MIN_AI_CREDITS)


@pytest.mark.parametrize("allowlist, use_workspace, expected", [
    ([], True, "agent_workspace_not_allowed"),
    (["<ws>"], False, "agent_workspace_not_allowed"),
    (["<other>"], True, "agent_workspace_not_allowed"),
    (["<ws>"], True, None),
])
def test_reject_requires_agent_workspace_allowlist(config_dir, tmp_path, allowlist, use_workspace, expected):
    workspace, other = tmp_path / "repo", tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    entries = [str(workspace) if e == "<ws>" else str(other) for e in allowlist]
    agent = _agent(workspace_allowlist=entries)
    status = AgentStatus(id="openrouter", installed=True, auth="ok")
    reason = broker._reject_agent(agent, "simple", "task", status, FREE_MODEL, False,
                                  workspace if use_workspace else None)
    assert reason == expected


def _registry_json(tier_order: dict, agents: list[dict]) -> dict:
    return {
        "active_provider_id": "copilot",
        "providers": [{"id": "copilot", "name": "Copilot", "api_base": "https://api.business.githubcopilot.com",
                       "active_model": "gpt-4o"}],
        "cli_agents": agents,
        "delegation": {"enabled": True, "tier_order": tier_order},
    }


def test_seeding_places_openrouter_before_deepseek(config_dir):
    agents = [dict(d) for d in DEFAULT_CLI_AGENTS if d["id"] != "openrouter"]
    tier_order = {"simple": ["muse", "copilot", "deepseek"], "complex": ["codex"]}
    (config_dir / "providers.json").write_text(json.dumps(_registry_json(tier_order, agents)), encoding="utf-8")
    registry = providers_service.load_registry()
    assert registry.delegation.tier_order == {
        "simple": ["muse", "copilot", "openrouter", "deepseek"], "complex": ["codex", "openrouter"],
    }
    seeded = next(a for a in registry.cli_agents if a.id == "openrouter")
    assert seeded.enabled is False and seeded.workspace_allowlist == []


def test_seeding_keeps_user_position_of_openrouter(config_dir):
    agents = [dict(d) for d in DEFAULT_CLI_AGENTS]
    tier_order = {"simple": ["openrouter", "deepseek", "copilot"]}
    (config_dir / "providers.json").write_text(json.dumps(_registry_json(tier_order, agents)), encoding="utf-8")
    assert providers_service.load_registry().delegation.tier_order == tier_order


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _fake_client(response: _FakeResponse, calls: list):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            calls.append((url, headers))
            return response

    return FakeClient


async def _probe(monkeypatch, response=None):
    calls: list = []
    monkeypatch.setattr(agents_registry, "_probe_version", AsyncMock(return_value=("2.1.0", "")))
    if response is not None:
        monkeypatch.setattr(agents_registry.httpx, "AsyncClient", _fake_client(response, calls))
    status = AgentStatus(id="openrouter")
    await agents_registry._probe_openrouter("claude.exe", status)
    return status, calls


async def test_probe_reads_key_limits_without_spending_requests(config_dir, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    payload = {"data": {"limit": 0, "limit_remaining": 0, "usage": 0, "is_free_tier": True, "label": "x"}}
    status, calls = await _probe(monkeypatch, _FakeResponse(200, payload))
    assert status.auth == "ok" and status.version == "2.1.0"
    assert status.quota == {"limit": 0, "limit_remaining": 0, "usage": 0, "is_free_tier": True}
    assert status.error == ""
    assert calls == [("https://openrouter.ai/api/v1/key", {"Authorization": f"Bearer {KEY}"})]


async def test_probe_marks_auth_error_on_401(config_dir, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    status, _ = await _probe(monkeypatch, _FakeResponse(401, {}))
    assert status.auth == "auth_error"


async def test_probe_without_key_skips_request(config_dir, monkeypatch):
    status, calls = await _probe(monkeypatch, _FakeResponse(200, {}))
    assert status.auth == "auth_error" and status.error == "OPENROUTER_API_KEY sin valor"
    assert calls == []


@pytest.mark.parametrize("limit", [None, 5])
async def test_probe_warns_when_key_can_spend_credits(config_dir, monkeypatch, limit):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    status, _ = await _probe(monkeypatch, _FakeResponse(200, {"data": {"limit": limit}}))
    assert status.auth == "ok" and "límite de crédito 0" in status.error
