"""Tests de strict_agent y estado queued del broker de delegación."""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.models.delegate import AgentStatus, JobRequest
from app.models.provider import ProviderRegistry
from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent, DelegationConfig
from app.services import health_service, providers_service, usage_tracker
from app.services.cli_agents import broker
from app.services.cli_agents import registry as agents_registry


def _agents(*ids, **overrides) -> list[CliAgent]:
    return [CliAgent(**{**d, "enabled": d["id"] in ids, **overrides.get(d["id"], {})}) for d in DEFAULT_CLI_AGENTS]


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
    registry = ProviderRegistry(
        active_provider_id="copilot", providers=[],
        cli_agents=_agents("claude", "codex", claude={"max_concurrency": 1}),
        delegation=DelegationConfig(enabled=True, workspace_allowlist=[str(workspace)], max_attempts=3,
                                    review_default=False,
                                    tier_order={"trivial": ["codex", "claude"], "simple": ["codex", "claude"],
                                                "standard": ["codex", "claude"], "complex": ["codex", "claude"]}),
    )
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)
    monkeypatch.setattr(agents_registry, "probe", AsyncMock(
        side_effect=lambda a, force=False: AgentStatus(id=a.id, installed=True, auth="unknown")))
    monkeypatch.setattr(agents_registry, "resolve_exe", lambda a: f"{a.id}.exe")
    monkeypatch.setattr(usage_tracker, "record", AsyncMock())
    yield {"registry": registry, "workspace": workspace, "tmp": tmp_path}
    broker.reset_for_tests()
    health_service.reload_for_tests()


def _codex_ok():
    return 0, json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hecho"}}) + "\n" + json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}), "", False


def _codex_fail():
    return 1, "", "boom", False


def _claude_ok():
    return 0, json.dumps({"result": "listo", "is_error": False, "usage": {"input_tokens": 3, "output_tokens": 1}}), "", False


async def _wait(job_id):
    await asyncio.wait_for(broker._jobs[job_id].task, timeout=5)
    return broker.get_job(job_id)


def _fake_subprocess(script):
    """script: lista de (returncode, stdout, stderr, timed_out) consumida por intento."""
    calls = []

    async def run(rt, spec):
        calls.append(spec)
        rc, out, err, timed_out = script.pop(0)
        return rc, out, err, timed_out

    return run, calls


# ── strict_agent ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_strict_agent_rechaza_si_el_pedido_esta_agotado(env):
    """Agente pedido agotado + strict_agent=True → NoAgentAvailable, ningún job en _jobs."""
    until = datetime(2099, 1, 1, 5, 7, 47, tzinfo=timezone.utc)
    health_service.set_exhausted_until("cli:codex", until, "Subscription quota exhausted")

    with pytest.raises(broker.NoAgentAvailable) as exc_info:
        await broker.submit(JobRequest(
            task="arregla el bug",
            workspace=str(env["workspace"]),
            agent_id="codex",
            strict_agent=True,
        ))

    assert exc_info.value.reasons
    # ningún job creado
    assert len(broker._jobs) == 0


@pytest.mark.asyncio
async def test_sin_strict_mantiene_la_redireccion(env):
    """Mismo escenario con strict_agent=False → el job se crea con otro agente y redirect no vacío."""
    until = datetime(2099, 1, 1, 5, 7, 47, tzinfo=timezone.utc)
    health_service.set_exhausted_until("cli:codex", until, "Subscription quota exhausted")

    job = await broker.submit(JobRequest(
        task="arregla el bug",
        workspace=str(env["workspace"]),
        agent_id="codex",
        strict_agent=False,
        dry_run=True,
    ))

    assert job.agent_id == "claude"
    assert job.redirect != ""
    assert "codex" in job.redirect


@pytest.mark.asyncio
async def test_strict_agent_no_cambia_de_agente_tras_fallar(env, monkeypatch):
    """El agente pedido falla con un error no transitorio → el job termina failed y attempts tiene un solo agent_id."""
    run, _ = _fake_subprocess([_codex_fail()])
    monkeypatch.setattr(broker, "_run_subprocess", run)

    job = await broker.submit(JobRequest(
        task="implementa el endpoint",
        workspace=str(env["workspace"]),
        agent_id="codex",
        strict_agent=True,
    ))
    job = await _wait(job.id)

    assert job.status == "failed"
    assert len(job.attempts) == 1
    assert job.attempts[0].agent_id == "codex"
    # No se intentó con otro agente
    agent_ids = [a.agent_id for a in job.attempts]
    assert agent_ids == ["codex"]


# ── queued state ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_job_queda_queued_mientras_espera_cupo_del_agente(env, monkeypatch):
    """max_concurrency=1, un primer job bloqueado (fake que espera un asyncio.Event) y un segundo job del mismo agente
    → el segundo está en queued; al soltar el evento pasa a running y termina."""
    release_first = asyncio.Event()
    second_status_snapshot: list[str] = []

    async def slow_first(rt, spec):
        # Simula un trabajo largo que mantiene el semáforo del agente
        await release_first.wait()
        return _codex_ok()[0], _codex_ok()[1], _codex_ok()[2], _codex_ok()[3]

    async def fast_second(rt, spec):
        return _codex_ok()[0], _codex_ok()[1], _codex_ok()[2], _codex_ok()[3]

    call_count = {"n": 0}

    async def dispatch(rt, spec):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return await slow_first(rt, spec)
        else:
            return await fast_second(rt, spec)

    monkeypatch.setattr(broker, "_run_subprocess", dispatch)

    # Primer job: bloquea el semáforo de codex
    job1 = await broker.submit(JobRequest(
        task="trabajo largo",
        workspace=str(env["workspace"]),
        agent_id="codex",
    ))

    # Esperar a que job1 tome el semáforo global y empiece
    await asyncio.sleep(0.05)

    # Segundo job: mismo agente, debe quedar en queued
    job2 = await broker.submit(JobRequest(
        task="trabajo corto",
        workspace=str(env["workspace"]),
        agent_id="codex",
    ))

    # Dar tiempo a que job2 intente tomar el semáforo del agente
    await asyncio.sleep(0.05)

    # Capturar el estado de job2 mientras espera
    second_status_snapshot.append(broker.get_job(job2.id).status)

    # Soltar el primer job
    release_first.set()

    # Esperar a que ambos terminen
    job1_final = await _wait(job1.id)
    job2_final = await _wait(job2.id)

    # job2 estuvo en queued en algún momento
    assert "queued" in second_status_snapshot, f"job2 nunca estuvo queued: {second_status_snapshot}"

    # Ambos terminan correctamente
    assert job1_final.status == "succeeded"
    assert job2_final.status == "succeeded"

    # Verificar que se emitió el evento waiting_agent_slot para job2
    job2_events = [e["event"] for e in broker._jobs[job2.id].lines]
    assert "waiting_agent_slot" in job2_events
