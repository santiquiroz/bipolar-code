"""Puerta de calidad: verificación, revisión, revisiones y escalado."""
import shutil
import subprocess

import pytest

from app.models.delegate import JobRequest
from app.services.cli_agents import broker, reviewer, verifier
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
@pytest.mark.skipif(shutil.which("git") is None, reason="git no disponible")
async def test_worker_aplica_manifiesto_y_emite_evento_files_deleted(env, monkeypatch):
    workspace = env["workspace"]
    target = workspace / "obsolete.txt"
    target.write_text("old", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=workspace, capture_output=True, check=True)
    subprocess.run(["git", "add", "obsolete.txt"], cwd=workspace, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )

    async def fake_worker(rt, agent, model, timeout_s, task=None, read_only=False):
        manifest = workspace / ".bipolar" / "jobs" / rt.job.id / "borrar.txt"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("obsolete.txt\n", encoding="utf-8")
        return _ok("borrado solicitado")

    monkeypatch.setattr(broker, "_run_attempt", fake_worker)
    job = await _wait((await broker.submit(JobRequest(
        task="elimina archivo obsolete", workspace=str(workspace), tier_hint="standard", review=False,
    ))).id)

    assert job.status == "succeeded"
    assert not target.exists()
    assert job.attempts[0].detail["deleted"] == ["obsolete.txt"]
    assert any(event.get("event") == "files_deleted" and event.get("deleted") == ["obsolete.txt"]
               for event in broker._jobs[job.id].lines)


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("git") is None, reason="git no disponible")
async def test_worker_borra_archivo_nuevo_en_directorio_nuevo(env, monkeypatch):
    workspace = env["workspace"]
    subprocess.run(["git", "init"], cwd=workspace, capture_output=True, check=True)
    seed = workspace / "seed.txt"
    seed.write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=workspace, capture_output=True, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )

    async def fake_worker(rt, agent, model, timeout_s, task=None, read_only=False):
        nested = workspace / "fresh" / "nested.txt"
        nested.parent.mkdir()
        nested.write_text("new", encoding="utf-8")
        manifest = workspace / ".bipolar" / "jobs" / rt.job.id / "borrar.txt"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text("fresh/nested.txt\n", encoding="utf-8")
        return _ok("borrado solicitado")

    monkeypatch.setattr(broker, "_run_attempt", fake_worker)
    job = await _wait((await broker.submit(JobRequest(
        task="elimina el archivo nuevo", workspace=str(workspace), tier_hint="standard", review=False,
    ))).id)

    assert job.status == "succeeded"
    assert not (workspace / "fresh" / "nested.txt").exists()
    assert job.attempts[0].detail["deleted"] == ["fresh/nested.txt"]


def test_job_texto_no_aplica_manifiesto_de_borrado(env):
    workspace = env["workspace"]
    target = workspace / "keep.txt"
    target.write_text("keep", encoding="utf-8")
    manifest = workspace / ".bipolar" / "jobs" / "readonly" / "borrar.txt"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("keep.txt\n", encoding="utf-8")
    runtime = broker.JobRuntime(
        job=broker.Job(id="readonly", created_at="now", tier="standard"),
        request=JobRequest(task="solo lectura", workspace=str(workspace), mode="text"),
        workspace=workspace,
    )

    broker._apply_job_deletions(runtime)

    assert target.exists() and manifest.exists()


@pytest.mark.asyncio
async def test_failed_check_gets_one_revision_then_passes(env, monkeypatch):
    _setup(env)
    calls = _script(monkeypatch, [("codex", False, _ok()), ("codex", False, _ok("arreglado")), ("claude", True, _verdict("approve"))])
    _checks(monkeypatch, [[CheckResult("pytest", 1, 1.0, "1 failed")], [CheckResult("pytest", 0, 1.0, "ok")]])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", verify=["pytest"]))).id)
    assert job.status == "succeeded"
    assert "1 failed" in calls[1][2]


@pytest.mark.asyncio
async def test_locked_build_files_fail_without_revision(env, monkeypatch):
    _setup(env)

    async def no_revise(*args, **kwargs):
        raise AssertionError("no debe pedir revisión por un bloqueo del entorno")

    monkeypatch.setattr(broker, "_revise", no_revise)
    _script(monkeypatch, [("codex", False, _ok())])
    locked = CheckResult("dotnet build", 1, 1.0, "error MSB3027", environment_error=verifier.LOCKED_FILES)
    _checks(monkeypatch, [[locked]])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", verify=["pytest"]))).id)
    assert job.status == "failed" and job.verification_status == "failed"
    assert job.error.startswith("verify_environment") and "dotnet build" in job.error
    assert job.escalations == 0
    verify = next(a for a in job.attempts if a.kind == "verify")
    assert verify.detail["environment_error"] == verifier.LOCKED_FILES


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


@pytest.mark.asyncio
async def test_quality_gate_falla_si_la_revision_previa_pidio_cambios_y_no_hay_revisor(env, monkeypatch):
    _setup(env)
    original_candidates = broker._review_candidates
    count_cand = [0]

    def fake_candidates(registry, statuses, worker, tier, workspace=None):
        count_cand[0] += 1
        if count_cand[0] == 1:
            return original_candidates(registry, statuses, worker, tier, workspace)
        return []

    monkeypatch.setattr(broker, "_review_candidates", fake_candidates)
    _script(monkeypatch, [("codex", False, _ok()), ("claude", True, _verdict("revise", ["cambio pedido"])), ("codex", False, _ok("corregido"))])
    _checks(monkeypatch, [])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard"))).id)
    assert job.status == "failed"
    assert job.review_status == "unreviewed"
    assert job.error.startswith("review_unavailable")
    # debe haber un intento review con revise
    review_attempts = [a for a in job.attempts if a.kind == "review"]
    assert len(review_attempts) >= 1
    assert review_attempts[0].detail.get("verdict") == "revise"


@pytest.mark.asyncio
async def test_quality_gate_sigue_aprobando_sin_revisor_si_no_hubo_revision_previa(env, monkeypatch):
    _setup(env)
    monkeypatch.setattr(broker, "_review_candidates", lambda *a, **kw: [])
    _script(monkeypatch, [("codex", False, _ok())])
    _checks(monkeypatch, [])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard"))).id)
    assert job.status == "succeeded" and job.review_status == "skipped"


def test_textos_revision_y_escalada_contienen_conserva_pruebas():
    rev_text = reviewer.revision_task("t", ["i"])
    esc_text = reviewer.escalation_task("t", ["h"])
    assert "Conserva el código y las pruebas que ya existían" in rev_text
    assert "Conserva el código y las pruebas que ya existían" in esc_text
