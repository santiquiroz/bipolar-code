"""Puerta de calidad: verificación, revisión, revisiones y escalado."""
import pytest

from app.models.delegate import JobRequest
from app.services.cli_agents import broker, verifier
from app.services.cli_agents.adapters import AdapterResult
from app.services.cli_agents.verifier import CheckResult
from tests.test_delegation_broker import _wait, env  # noqa: F401  (fixture)


def _ok(text="hecho"):
    return broker.AttemptOutcome(ok=True, returncode=0, result=AdapterResult(text=text))


def _verdict(v, issues=()):
    import json
    return _ok("revisión\n" + json.dumps({"verdict": v, "issues": list(issues)}))


def _script(monkeypatch, plan):
    """plan: lista de (agent_id, read_only, outcome) en el orden esperado de llamadas."""
    calls = []

    async def fake(rt, agent, model, timeout_s, task=None, read_only=False):
        calls.append((agent.id, read_only, task))
        exp_agent, exp_ro, outcome = plan.pop(0)
        assert (agent.id, read_only) == (exp_agent, exp_ro), f"llamada inesperada {(agent.id, read_only)}"
        return outcome

    monkeypatch.setattr(broker, "_run_attempt", fake)
    return calls


def _checks(monkeypatch, sequence):
    async def fake(commands, workspace, timeout_s):
        return sequence.pop(0) if commands else []
    monkeypatch.setattr(verifier, "run_checks", fake)


def _setup(env):
    env["registry"].delegation.allow_request_verify = True
    env["registry"].delegation.thinkers = ["claude", "codex"]
    env["registry"].delegation.review_default = True


@pytest.mark.asyncio
async def test_pass_checks_and_approve(env, monkeypatch):
    _setup(env)
    _script(monkeypatch, [("codex", False, _ok()), ("claude", True, _verdict("approve"))])
    _checks(monkeypatch, [[CheckResult("pytest", 0, 1.0, "ok")]])
    job = await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", verify=["pytest"]))
    job = await _wait(job.id)
    assert job.status == "succeeded"
    assert job.verification_status == "passed" and job.review_status == "passed"
    assert [a.kind for a in job.attempts] == ["work", "verify", "review"]


@pytest.mark.asyncio
async def test_failed_check_gets_one_revision_then_passes(env, monkeypatch):
    _setup(env)
    calls = _script(monkeypatch, [("codex", False, _ok()), ("codex", False, _ok("arreglado")), ("claude", True, _verdict("approve"))])
    _checks(monkeypatch, [[CheckResult("pytest", 1, 1.0, "1 failed")], [CheckResult("pytest", 0, 1.0, "ok")]])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", verify=["pytest"]))).id)
    assert job.status == "succeeded"
    assert "1 failed" in calls[1][2]


@pytest.mark.asyncio
async def test_reject_escalates_to_next_thinker(env, monkeypatch):
    _setup(env)
    _script(monkeypatch, [
        ("codex", False, _ok()), ("claude", True, _verdict("reject", ["enfoque equivocado"])),
        ("claude", False, _ok("rehecho")), ("codex", True, _verdict("approve")),
    ])
    _checks(monkeypatch, [])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard"))).id)
    assert job.status == "succeeded" and job.escalations == 1
    assert job.agent_id == "claude"


@pytest.mark.asyncio
async def test_unparseable_review_then_no_reviewers_is_skipped_not_passed(env, monkeypatch):
    _setup(env)
    _script(monkeypatch, [("codex", False, _ok()), ("claude", True, _ok("me parece bien"))])
    _checks(monkeypatch, [])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard"))).id)
    assert job.status == "succeeded" and job.review_status == "skipped"


@pytest.mark.asyncio
async def test_no_escalation_target_fails_with_reason(env, monkeypatch):
    _setup(env)
    env["registry"].delegation.max_attempts = 2
    _script(monkeypatch, [
        ("codex", False, _ok()), ("claude", True, _verdict("reject", ["mal"])),
        ("claude", False, _ok()), ("codex", True, _verdict("reject", ["mal otra vez"])),
    ])
    _checks(monkeypatch, [])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", max_revisions=0))).id)
    assert job.status == "failed" and job.error.startswith("review_rejected")
    assert [a.agent_id for a in job.attempts if a.kind == "work" and not a.detail.get("revision")] == ["codex", "claude"]


@pytest.mark.asyncio
async def test_verify_disabled_and_invalid_commands_are_rejected(env):
    with pytest.raises(broker.InvalidRequest, match="verify_disabled"):
        await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), verify=["pytest"]))
    _setup(env)
    with pytest.raises(broker.InvalidRequest, match="invalid_verify"):
        await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), verify=["definitely-not-a-real-binary-xyz"]))


@pytest.mark.asyncio
async def test_review_disabled_skips_review(env, monkeypatch):
    _setup(env)
    _script(monkeypatch, [("codex", False, _ok())])
    _checks(monkeypatch, [])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", review=False))).id)
    assert job.status == "succeeded" and job.review_status == "n/a"
