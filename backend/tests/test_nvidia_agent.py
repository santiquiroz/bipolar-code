"""Carril nvidia: Claude Code headless contra el endpoint /v1/messages propio, fijado a nvidia_nim."""
import json
from unittest.mock import AsyncMock

import pytest

from app.models.delegate import AgentStatus, JobRequest
from app.models.provider import ProviderRegistry
from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent, DelegationConfig
from app.services import health_service, providers_service, usage_tracker
from app.services.cli_agents import adapters as ad
from app.services.cli_agents import broker
from app.services.cli_agents import registry as agents_registry

MODEL = "moonshotai/kimi-k3.1-pro"
FALLBACK = "nvidia/nemotron-3-ultra-550b-a55b"
DELEGATE_KEY = "del-testkey"


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    from app.core import config as cfg

    config = tmp_path / "config"
    config.mkdir()

    class FakeSettings:
        litellm_config_dir = str(config)
        self_base_url = "http://127.0.0.1:8000"
        delegate_proxy_key = DELEGATE_KEY

    for mod in (cfg, ad, providers_service, broker):
        monkeypatch.setattr(mod, "get_settings", lambda: FakeSettings())
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    return config


def _agent(**overrides) -> CliAgent:
    base = next(d for d in DEFAULT_CLI_AGENTS if d["id"] == "nvidia")
    return CliAgent(**{**base, "enabled": True, **overrides})


def _build(workspace, model=MODEL, read_only=False):
    return ad.NvidiaAdapter().build(_agent(), "claude.exe", "job1", "tarea", model, workspace, "simple", 600,
                                   read_only=read_only)


def test_build_points_claude_code_at_own_backend_with_pinned_provider(config_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nv-key")
    spec = _build(tmp_path)
    assert spec.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8000"
    assert spec.env["ANTHROPIC_AUTH_TOKEN"] == DELEGATE_KEY
    assert spec.env["ANTHROPIC_API_KEY"] == ""
    assert spec.env["ANTHROPIC_CUSTOM_HEADERS"] == "x-bipolar-provider: nvidia_nim"
    assert all(spec.env[name] == MODEL for name in ad.CLAUDE_MODEL_VARS)
    assert spec.env["CLAUDE_CONFIG_DIR"].endswith("nvidia-claude")
    assert spec.env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert spec.argv[spec.argv.index("--model") + 1] == MODEL
    assert "nv-key" not in " ".join(spec.argv) and "nv-key" not in " ".join(spec.redacted)
    ui_key = getattr(config_dir, "ui_key", "")
    assert DELEGATE_KEY not in " ".join(spec.argv)


def test_build_uses_default_model_when_none_requested(config_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nv-key")
    spec = _build(tmp_path, model="")
    assert spec.env["ANTHROPIC_MODEL"] == MODEL


@pytest.mark.parametrize("model", ["minimax/minimax-m3.1-flash:free", "free", "x/y:free;rm", "a/b c"])
def test_build_refuses_invalid_models(config_dir, monkeypatch, tmp_path, model):
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nv-key")
    with pytest.raises(ad.AdapterUnsafe, match="nvidia_model_invalid"):
        _build(tmp_path, model=model)


def test_build_refuses_without_key(config_dir, tmp_path):
    with pytest.raises(ad.AdapterUnsafe, match="nvidia_key_missing"):
        _build(tmp_path)


def test_parse_zeroes_cost(config_dir, tmp_path):
    out = json.dumps({"result": "listo", "is_error": False, "usage": {"input_tokens": 10, "output_tokens": 5},
                      "total_cost_usd": 0.42})
    assert ad.NvidiaAdapter().parse(out, "", 0, None).usage["cost_usd"] == 0.0


def test_read_only_build_keeps_plan_mode(config_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nv-key")
    argv = _build(tmp_path, read_only=True).argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def json(self) -> dict:
        return {}


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
    status = AgentStatus(id="nvidia")
    await agents_registry._probe_nvidia("claude.exe", status)
    return status, calls


async def test_probe_ok(config_dir, monkeypatch):
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nv-key")
    status, calls = await _probe(monkeypatch, _FakeResponse(200))
    assert status.auth == "ok" and status.version == "2.1.0"
    assert status.quota == {"meter": "40 req/min, sin medidor"}
    assert calls == [("https://integrate.api.nvidia.com/v1/models", {"Authorization": "Bearer nv-key"})]


@pytest.mark.parametrize("code", [401, 403])
async def test_probe_auth_error(config_dir, monkeypatch, code):
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nv-key")
    status, _ = await _probe(monkeypatch, _FakeResponse(code))
    assert status.auth == "auth_error"


async def test_probe_without_key_skips_request(config_dir, monkeypatch):
    status, calls = await _probe(monkeypatch, _FakeResponse(200))
    assert status.auth == "auth_error" and status.error == "NVIDIA_NIM_API_KEY sin valor"
    assert calls == []


async def test_probe_unreachable_reports_class(config_dir, monkeypatch):
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nv-key")
    monkeypatch.setattr(agents_registry, "_probe_version", AsyncMock(return_value=("2.1.0", "")))

    class BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            raise ConnectionError("down")

    monkeypatch.setattr(agents_registry.httpx, "AsyncClient", BoomClient)
    status = AgentStatus(id="nvidia")
    await agents_registry._probe_nvidia("claude.exe", status)
    assert status.error == "nvidia unreachable: ConnectionError"


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
    status = AgentStatus(id="nvidia", installed=True, auth="ok")
    reason = broker._reject_agent(agent, "simple", "task", status, MODEL, False,
                                  workspace if use_workspace else None)
    assert reason == expected


@pytest.fixture
def broker_env(tmp_path, monkeypatch):
    from app.core import config as cfg

    class FakeSettings:
        litellm_config_dir = str(tmp_path / "config")
        self_base_url = "http://127.0.0.1:8000"
        delegate_proxy_key = DELEGATE_KEY

    (tmp_path / "config").mkdir()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    for mod in (cfg, health_service, broker):
        monkeypatch.setattr(mod, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    broker.reset_for_tests()

    agent = _agent(workspace_allowlist=[str(workspace)])
    registry = ProviderRegistry(
        active_provider_id="copilot", providers=[],
        cli_agents=[agent],
        delegation=DelegationConfig(enabled=True, workspace_allowlist=[str(workspace)], max_attempts=3,
                                    review_default=False,
                                    tier_order={"simple": ["nvidia"], "standard": ["nvidia"],
                                                "complex": ["nvidia"]}),
    )
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)
    monkeypatch.setattr(agents_registry, "probe", AsyncMock(side_effect=lambda a, force=False: AgentStatus(id=a.id, installed=True, auth="ok")))
    monkeypatch.setattr(agents_registry, "resolve_exe", lambda a: f"{a.id}.exe")
    monkeypatch.setattr(usage_tracker, "record", AsyncMock())
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nv-key")
    yield {"registry": registry, "workspace": workspace, "agent": agent}
    broker.reset_for_tests()
    health_service.reload_for_tests()


def _fake_subprocess(script):
    calls = []

    async def run(rt, spec):
        calls.append(spec)
        rc, out, err, timed_out = script.pop(0)
        return rc, out, err, timed_out

    return run, calls


def _nvidia_error(text: str):
    return 0, json.dumps({"error": text}) + "\n", "", False


def _nvidia_ok():
    return 0, json.dumps({"result": "listo", "is_error": False, "usage": {"input_tokens": 3, "output_tokens": 1}}), "", False


@pytest.mark.asyncio
async def test_nvidia_fallback_on_rate_limit_then_ok(broker_env, monkeypatch):
    run, _ = _fake_subprocess([_nvidia_error("Rate limit exceeded: slow down"), _nvidia_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    import asyncio
    job = await broker.submit(JobRequest(task="tarea", workspace=str(broker_env["workspace"]), agent_id="nvidia"))
    finished = await asyncio.wait_for(broker.wait_job(job.id, 10), timeout=5)
    assert finished.status == "succeeded"
    assert finished.model == FALLBACK
    events = [e for e in broker._jobs[job.id].lines if isinstance(e, dict)]
    fallbacks = [e for e in events if e.get("event") == "model_fallback"]
    assert len(fallbacks) == 1 and fallbacks[0]["to"] == FALLBACK


@pytest.mark.asyncio
async def test_nvidia_skips_non_matching_fallback(broker_env, monkeypatch):
    broker_env["agent"].fallback_models = ["bad model!!", FALLBACK]
    run, calls = _fake_subprocess([_nvidia_error("Rate limit exceeded: slow down"), _nvidia_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    import asyncio
    job = await broker.submit(JobRequest(task="tarea", workspace=str(broker_env["workspace"]), agent_id="nvidia"))
    finished = await asyncio.wait_for(broker.wait_job(job.id, 10), timeout=5)
    assert finished.status == "succeeded" and finished.model == FALLBACK
    assert len(calls) == 2
