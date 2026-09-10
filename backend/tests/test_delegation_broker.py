"""Tests del broker: workspace, elección de agente, failover por cuota y ciclo de vida del job."""
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
        cli_agents=_agents("claude", "codex"),
        delegation=DelegationConfig(enabled=True, workspace_allowlist=[str(workspace)], max_attempts=3,
                                    tier_order={"trivial": ["codex", "claude"], "simple": ["codex", "claude"],
                                                "standard": ["codex", "claude"], "complex": ["codex", "claude"]}),
    )
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)
    monkeypatch.setattr(agents_registry, "probe", AsyncMock(side_effect=lambda a, force=False: AgentStatus(id=a.id, installed=True, auth="unknown")))
    monkeypatch.setattr(agents_registry, "resolve_exe", lambda a: f"{a.id}.exe")
    monkeypatch.setattr(usage_tracker, "record", AsyncMock())
    yield {"registry": registry, "workspace": workspace, "tmp": tmp_path}
    broker.reset_for_tests()
    health_service.reload_for_tests()


def _fake_subprocess(script):
    """script: lista de (returncode, stdout, stderr, timed_out) consumida por intento."""
    calls = []

    async def run(rt, spec):
        calls.append(spec)
        rc, out, err, timed_out = script.pop(0)
        return rc, out, err, timed_out

    return run, calls


def _codex_ok():
    return 0, json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hecho"}}) + "\n" + json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}), "", False


def _codex_quota():
    return 0, json.dumps({"type": "error", "message": "Your workspace is out of credits."}) + "\n" + json.dumps({"type": "turn.failed", "error": {"message": "out of credits"}}), "", False


def _claude_ok():
    return 0, json.dumps({"result": "listo", "is_error": False, "usage": {"input_tokens": 3, "output_tokens": 1}}), "", False


async def _wait(job_id):
    await asyncio.wait_for(broker._jobs[job_id].task, timeout=5)
    return broker.get_job(job_id)


# ── workspace ────────────────────────────────────────────────────────────────

def test_validate_workspace_rules(env, tmp_path):
    ws = env["workspace"]
    assert broker.validate_workspace(str(ws), [str(ws)]) == ws.resolve()
    sub = ws / "src"
    sub.mkdir()
    assert broker.validate_workspace(str(sub), [str(ws)]) == sub.resolve()
    with pytest.raises(broker.WorkspaceNotAllowed, match="workspace_allowlist_empty"):
        broker.validate_workspace(str(ws), [])
    with pytest.raises(broker.WorkspaceNotAllowed, match="workspace_not_absolute"):
        broker.validate_workspace("repo", [str(ws)])
    with pytest.raises(broker.WorkspaceNotAllowed, match="workspace_missing"):
        broker.validate_workspace(str(tmp_path / "nope"), [str(ws)])
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(broker.WorkspaceNotAllowed, match="workspace_not_allowed"):
        broker.validate_workspace(str(other), [str(ws)])
    with pytest.raises(broker.WorkspaceNotAllowed, match="workspace_forbidden"):
        broker.validate_workspace(str(tmp_path / "config"), [str(tmp_path)])
    git_dir = ws / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    with pytest.raises(broker.WorkspaceNotAllowed, match="workspace_forbidden"):
        broker.validate_workspace(str(git_dir), [str(ws)])


# ── elección ─────────────────────────────────────────────────────────────────

def test_choose_agent_respects_order_and_skips_with_reasons(env):
    registry = env["registry"]
    statuses = {"codex": AgentStatus(id="codex", installed=False), "claude": AgentStatus(id="claude", installed=True)}
    agent, model, reasons, skipped = broker.choose_agent("standard", registry, statuses)
    assert agent.id == "claude" and ("codex", "not_installed") in skipped
    agent, _, _, skipped = broker.choose_agent("standard", registry, statuses, exclude=("claude",))
    assert agent is None and ("claude", "already_tried") in skipped


def test_choose_agent_skips_cooling_and_busy(env):
    registry = env["registry"]
    statuses = {a.id: AgentStatus(id=a.id, installed=True) for a in registry.cli_agents}
    from app.core.quota_signals import QuotaSignal
    health_service.mark_signal("cli:codex", QuotaSignal("rate_limit", 300, "429"))
    agents_registry.adjust_running("claude", +1)
    try:
        agent, _, _, skipped = broker.choose_agent("standard", registry, statuses)
    finally:
        agents_registry.adjust_running("claude", -1)
    assert agent is None
    assert ("codex", "cooling:rate_limit") in skipped and ("claude", "busy") in skipped


# ── submit ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dry_run_returns_choice_without_running(env, monkeypatch):
    run, calls = _fake_subprocess([_codex_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="genera tests para src/a.py", workspace=str(env["workspace"]), dry_run=True))
    assert job.agent_id == "codex" and job.tier in ("simple", "standard") and job.status == "queued"
    assert calls == [] and broker.get_job(job.id) is None


@pytest.mark.asyncio
async def test_submit_rejects_when_disabled_or_depth_or_bad_workspace(env, tmp_path):
    env["registry"].delegation.enabled = False
    with pytest.raises(broker.DelegationDisabled, match="delegation_disabled"):
        await broker.submit(JobRequest(task="implementa el endpoint de facturas", workspace=str(env["workspace"])))
    env["registry"].delegation.enabled = True
    with pytest.raises(broker.DelegationDisabled, match="recursion_guard"):
        await broker.submit(JobRequest(task="implementa el endpoint de facturas", workspace=str(env["workspace"])), depth_header="1")
    with pytest.raises(broker.WorkspaceNotAllowed):
        await broker.submit(JobRequest(task="implementa el endpoint de facturas", workspace=str(tmp_path)))


@pytest.mark.asyncio
async def test_job_succeeds_and_records_usage(env, monkeypatch):
    run, calls = _fake_subprocess([_codex_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="implementa el endpoint", workspace=str(env["workspace"])))
    job = await _wait(job.id)
    assert job.status == "succeeded" and job.output_tail == "hecho" and job.tokens_in == 5
    assert calls[0].argv[-1] == "-" and calls[0].stdin_payload
    usage_tracker.record.assert_awaited_once()
    assert usage_tracker.record.await_args.args[0] == "cli:codex"
    assert health_service.effective_state("cli:codex") == "available"


@pytest.mark.asyncio
async def test_quota_signal_fails_over_to_next_agent_and_marks_exhausted(env, monkeypatch):
    run, calls = _fake_subprocess([_codex_quota(), _claude_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="implementa el endpoint", workspace=str(env["workspace"])))
    job = await _wait(job.id)
    assert job.status == "succeeded" and job.agent_id == "claude"
    assert [a.agent_id for a in job.attempts] == ["codex", "claude"]
    assert job.attempts[0].signal == "quota_exhausted"
    assert health_service.effective_state("cli:codex") == "exhausted"
    events = [e["event"] for e in broker._jobs[job.id].lines]
    assert "attempt" in events and events[-1] == "done"


@pytest.mark.asyncio
async def test_pinned_agent_does_not_fail_over(env, monkeypatch):
    run, _ = _fake_subprocess([_codex_quota()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="implementa el endpoint de facturas", workspace=str(env["workspace"]), agent_id="codex"))
    job = await _wait(job.id)
    assert job.status == "quota" and len(job.attempts) == 1


@pytest.mark.asyncio
async def test_generic_failure_does_not_blind_retry(env, monkeypatch):
    run, _ = _fake_subprocess([(1, "", "boom", False)])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="implementa el endpoint de facturas", workspace=str(env["workspace"])))
    job = await _wait(job.id)
    assert job.status == "failed" and len(job.attempts) == 1


@pytest.mark.asyncio
async def test_timeout_marks_status_timeout(env, monkeypatch):
    run, _ = _fake_subprocess([(None, "", "", True)])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="implementa el endpoint de facturas", workspace=str(env["workspace"])))
    job = await _wait(job.id)
    assert job.status == "timeout"


@pytest.mark.asyncio
async def test_subscribe_replays_lines_and_ends_with_done(env, monkeypatch):
    run, _ = _fake_subprocess([_codex_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="implementa el endpoint de facturas", workspace=str(env["workspace"])))
    await _wait(job.id)
    events = [ev async for ev in broker.subscribe(job.id)]
    assert events[-1]["event"] == "done" and events[-1]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_cancel_marks_cancelled(env, monkeypatch):
    started = asyncio.Event()

    async def slow(rt, spec):
        started.set()
        await asyncio.sleep(10)
        return 0, "", "", False

    monkeypatch.setattr(broker, "_run_subprocess", slow)
    job = await broker.submit(JobRequest(task="implementa el endpoint de facturas", workspace=str(env["workspace"])))
    await asyncio.wait_for(started.wait(), timeout=2)
    cancelled = await broker.cancel(job.id)
    assert cancelled.status == "cancelled"
    assert broker.list_jobs()[0].id == job.id
