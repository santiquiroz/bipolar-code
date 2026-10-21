"""Broker de delegación: fallback de modelos para openrouter."""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.models.delegate import AgentStatus, JobRequest
from app.models.provider import ProviderRegistry
from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent, DelegationConfig
from app.services import health_service, providers_service, usage_tracker
from app.services.cli_agents import broker
from app.services.cli_agents import registry as agents_registry


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app.core import config as cfg

    class FakeSettings:
        litellm_config_dir = str(tmp_path / "config")

    (tmp_path / "config").mkdir()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    for mod in (cfg, health_service, broker):
        monkeypatch.setattr(mod, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    broker.reset_for_tests()

    def _openrouter_agent(**overrides):
        base = next(d for d in DEFAULT_CLI_AGENTS if d["id"] == "openrouter")
        return CliAgent(**{**base, "enabled": True, **overrides})

    agent = _openrouter_agent(
        fallback_models=[
            "poolside/laguna-s-2.1:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
        ],
        workspace_allowlist=[str(workspace)],
    )
    registry = ProviderRegistry(
        active_provider_id="copilot", providers=[],
        cli_agents=[agent],
        delegation=DelegationConfig(enabled=True, workspace_allowlist=[str(workspace)], max_attempts=3,
                                    review_default=False,
                                    tier_order={"simple": ["openrouter"], "standard": ["openrouter"],
                                                "complex": ["openrouter"]}),
    )
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)
    monkeypatch.setattr(agents_registry, "probe", AsyncMock(side_effect=lambda a, force=False: AgentStatus(id=a.id, installed=True, auth="ok")))
    monkeypatch.setattr(agents_registry, "resolve_exe", lambda a: f"{a.id}.exe")
    monkeypatch.setattr(usage_tracker, "record", AsyncMock())
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    yield {"registry": registry, "workspace": workspace, "agent": agent, "tmp": tmp_path}
    broker.reset_for_tests()
    health_service.reload_for_tests()


def _fake_subprocess(script):
    calls = []

    async def run(rt, spec):
        calls.append(spec)
        rc, out, err, timed_out = script.pop(0)
        return rc, out, err, timed_out

    return run, calls


def _openrouter_rate_limit():
    return 0, '{"error":"Rate limit exceeded: the upstream provider is temporarily rate-limited"}\n', "", False


def _openrouter_ok():
    return 0, '{"result":"listo","is_error":false,"usage":{"input_tokens":3,"output_tokens":1}}', "", False


def _openrouter_model_unavailable():
    return 0, '{"error":"No endpoints found matching your data policy"}\n', "", False


def _openrouter_quota_exhausted():
    return 0, '{"error":"Rate limit exceeded: free-models-per-day"}\n', "", False


@pytest.mark.asyncio
async def test_openrouter_fallback_on_rate_limit_then_ok(env, monkeypatch):
    agent = env["agent"]
    run, calls = _fake_subprocess([_openrouter_rate_limit(), _openrouter_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="tarea", workspace=str(env["workspace"]), agent_id=agent.id))
    await asyncio.wait_for(broker.wait_job(job.id, 10), timeout=5)
    finished = broker.get_job(job.id)
    assert finished.status == "succeeded"
    assert finished.model == "poolside/laguna-s-2.1:free"
    events = [e for e in broker._jobs[job.id].lines if isinstance(e, dict)]
    fallback_events = [e for e in events if e.get("event") == "model_fallback"]
    assert len(fallback_events) == 1
    assert fallback_events[0]["agent_id"] == agent.id
    assert fallback_events[0]["from"] == agent.default_model
    assert fallback_events[0]["to"] == "poolside/laguna-s-2.1:free"
    assert fallback_events[0]["signal"] == "rate_limit"


@pytest.mark.asyncio
async def test_openrouter_model_unavailable_falls_back(env, monkeypatch):
    agent = env["agent"]
    run, calls = _fake_subprocess([_openrouter_model_unavailable(), _openrouter_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="tarea", workspace=str(env["workspace"]), agent_id=agent.id))
    await asyncio.wait_for(broker.wait_job(job.id, 10), timeout=5)
    finished = broker.get_job(job.id)
    assert finished.status == "succeeded"
    assert finished.model == "poolside/laguna-s-2.1:free"
    events = [e for e in broker._jobs[job.id].lines if isinstance(e, dict)]
    fallback_events = [e for e in events if e.get("event") == "model_fallback"]
    assert fallback_events[0]["signal"] == "model_unavailable"


@pytest.mark.asyncio
async def test_openrouter_quota_exhausted_does_not_fallback(env, monkeypatch):
    agent = env["agent"]
    run, calls = _fake_subprocess([_openrouter_quota_exhausted()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="tarea", workspace=str(env["workspace"]), agent_id=agent.id))
    await asyncio.wait_for(broker.wait_job(job.id, 10), timeout=5)
    finished = broker.get_job(job.id)
    assert finished.status == "quota"
    events = [e for e in broker._jobs[job.id].lines if isinstance(e, dict)]
    fallback_events = [e for e in events if e.get("event") == "model_fallback"]
    assert len(fallback_events) == 0


@pytest.mark.asyncio
async def test_openrouter_skips_non_free_fallback(env, monkeypatch):
    agent = env["agent"]
    agent.fallback_models = [
        "anthropic/claude-sonnet-4.5",  # no es :free
        "poolside/laguna-s-2.1:free",
    ]
    # Re-crear registro con el agente actualizado
    registry = env["registry"]
    registry.cli_agents = [agent]
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)
    run, calls = _fake_subprocess([_openrouter_rate_limit(), _openrouter_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="tarea", workspace=str(env["workspace"]), agent_id=agent.id))
    await asyncio.wait_for(broker.wait_job(job.id, 10), timeout=5)
    finished = broker.get_job(job.id)
    assert finished.status == "succeeded"
    assert finished.model == "poolside/laguna-s-2.1:free"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_openrouter_never_retries_a_model_already_tried(env, monkeypatch):
    agent = env["agent"]
    # el modelo por defecto también figura como respaldo: no debe volver a probarse tras fallar
    agent.fallback_models = [agent.default_model, "poolside/laguna-s-2.1:free"]
    run, calls = _fake_subprocess([_openrouter_rate_limit(), _openrouter_rate_limit()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="tarea", workspace=str(env["workspace"]), agent_id=agent.id))
    await asyncio.wait_for(broker.wait_job(job.id, 10), timeout=5)
    finished = broker.get_job(job.id)
    assert len(calls) == 2
    assert finished.status == "quota"
    events = [e for e in broker._jobs[job.id].lines if isinstance(e, dict)]
    fallback_events = [e for e in events if e.get("event") == "model_fallback"]
    assert [e["to"] for e in fallback_events] == ["poolside/laguna-s-2.1:free"]


def test_fallback_models_round_trip_through_config_json():
    agent = CliAgent(**next(d for d in DEFAULT_CLI_AGENTS if d["id"] == "openrouter"))
    restored = CliAgent.model_validate_json(agent.model_dump_json())
    assert restored.fallback_models == agent.fallback_models
    assert len(restored.fallback_models) == 3


def test_seed_adds_openrouter_fallback_models_when_missing():
    registry = ProviderRegistry(active_provider_id="copilot", providers=[], cli_agents=[])
    providers_service._seed_smart_defaults(registry)
    agent = next(a for a in registry.cli_agents if a.id == "openrouter")
    assert agent.fallback_models == next(d for d in DEFAULT_CLI_AGENTS if d["id"] == "openrouter")["fallback_models"]


def test_seed_keeps_existing_openrouter_fallback_models():
    registry = ProviderRegistry(active_provider_id="copilot", providers=[],
                                cli_agents=[CliAgent(id="openrouter", enabled=True, fallback_models=["x/y:free"])])
    providers_service._seed_smart_defaults(registry)
    agent = next(a for a in registry.cli_agents if a.id == "openrouter")
    assert agent.fallback_models == ["x/y:free"]
