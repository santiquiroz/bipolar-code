"""Review guard: fingerprint del árbol, citas verificables y anti-tamper."""
import json
import subprocess
from pathlib import Path

import pytest

from app.models.delegate import Job, JobRequest
from app.services.cli_agents import broker, reviewer
from app.services.cli_agents.adapters import AdapterResult
from app.services.cli_agents.reviewer import Verdict
from tests.test_delegation_broker import _wait, env  # noqa: F401  (fixture)


def _git(ws: Path, *args):
    subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True)


def _init_repo(ws: Path):
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(ws, "add", "a.py")
    _git(ws, "commit", "-qm", "base")


# ── tree_fingerprint ─────────────────────────────────────────────────────────

def test_fingerprint_empty_without_git(tmp_path):
    assert reviewer.tree_fingerprint(tmp_path) == ""


def test_fingerprint_changes_on_tracked_edit(tmp_path):
    _init_repo(tmp_path)
    before = reviewer.tree_fingerprint(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    after = reviewer.tree_fingerprint(tmp_path)
    assert before != "" and before != after


def test_fingerprint_changes_on_new_untracked_file(tmp_path):
    _init_repo(tmp_path)
    before = reviewer.tree_fingerprint(tmp_path)
    (tmp_path / "nuevo.py").write_text("y = 1\n", encoding="utf-8")
    assert reviewer.tree_fingerprint(tmp_path) != before


def test_fingerprint_ignores_bipolar_files(tmp_path):
    _init_repo(tmp_path)
    before = reviewer.tree_fingerprint(tmp_path)
    (tmp_path / ".bipolar" / "jobs").mkdir(parents=True)
    (tmp_path / ".bipolar" / "jobs" / "task.md").write_text("tarea", encoding="utf-8")
    assert reviewer.tree_fingerprint(tmp_path) == before


def test_fingerprint_survives_binary_diff(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_bytes(b"\xff\xfe\x00binario\x80")
    assert reviewer.tree_fingerprint(tmp_path) != ""


# ── resolve_citations ────────────────────────────────────────────────────────

def test_resolve_citations_valid_file_quote(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    v = Verdict("revise", ["falta algo"], citations=[("a.py", "x = 1")], dropped=[])
    result = reviewer.resolve_citations(v, tmp_path, "")
    assert result.issues == ["falta algo"]
    assert result.citations == [("a.py", "x = 1")]
    assert result.dropped == []


def test_resolve_citations_valid_diff_quote(tmp_path):
    v = Verdict("revise", ["error"], citations=[("a.py", "+x = 2")], dropped=[])
    result = reviewer.resolve_citations(v, tmp_path, "+x = 2\n")
    assert result.issues == ["error"]
    assert result.dropped == []


def test_resolve_citations_different_whitespace(tmp_path):
    (tmp_path / "a.py").write_text("hello world\n", encoding="utf-8")
    v = Verdict("revise", ["falla"], citations=[("a.py", "hello   world")], dropped=[])
    result = reviewer.resolve_citations(v, tmp_path, "")
    assert result.issues == ["falla"]
    assert result.dropped == []


def test_resolve_citations_quote_only_in_diff_is_valid(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    v = Verdict("revise", ["falla"], citations=[("a.py", "+z = 9")], dropped=[])
    result = reviewer.resolve_citations(v, tmp_path, "+z = 9\n")
    assert result.issues == ["falla"]
    assert result.dropped == []


def test_resolve_citations_invalid_missing_file(tmp_path):
    v = Verdict("revise", ["falla"], citations=[("noexiste.py", "texto")], dropped=[])
    result = reviewer.resolve_citations(v, tmp_path, "")
    assert result.issues == []
    assert result.dropped == ["falla"]


def test_resolve_citations_invalid_quote_absent(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    v = Verdict("revise", ["falla"], citations=[("a.py", "y = 2")], dropped=[])
    result = reviewer.resolve_citations(v, tmp_path, "")
    assert result.dropped == ["falla"]


def test_resolve_citations_invalid_path_escape(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    v = Verdict("revise", ["falla"], citations=[("../a.py", "x = 1")], dropped=[])
    result = reviewer.resolve_citations(v, tmp_path, "")
    assert result.dropped == ["falla"]
    assert result.issues == []


def test_resolve_citations_string_issue_kept(tmp_path):
    v = Verdict("revise", ["falta test"], citations=[None], dropped=[])
    result = reviewer.resolve_citations(v, tmp_path, "")
    assert result.issues == ["falta test"]
    assert result.dropped == []


def test_resolve_citations_issue_without_citation_entry_kept(tmp_path):
    v = Verdict("revise", ["falta test"])
    result = reviewer.resolve_citations(v, tmp_path, "")
    assert result.issues == ["falta test"]


def test_resolve_citations_does_not_mutate_original(tmp_path):
    v = Verdict("revise", ["falla"], citations=[("noexiste.py", "x")], dropped=[])
    reviewer.resolve_citations(v, tmp_path, "")
    assert v.issues == ["falla"] and v.dropped == []


# ── _as_verdict / parse_verdict ──────────────────────────────────────────────

def test_as_verdict_mixed_issues():
    v = reviewer.parse_verdict('{"verdict":"revise","issues":["a",{"issue":"b","file":"f.py","quote":"q"}]}')
    assert v is not None
    assert v.issues == ["a", "b"]
    assert v.citations == [None, ("f.py", "q")]
    assert v.dropped == []


def test_as_verdict_object_without_quote_is_kept_with_null_citation():
    v = reviewer.parse_verdict('{"verdict":"revise","issues":[{"issue":"b","file":"f.py"}]}')
    assert v is not None
    assert v.issues == ["b"]
    assert v.citations == [None]


def test_as_verdict_object_with_empty_quote_is_kept_with_null_citation():
    v = reviewer.parse_verdict('{"verdict":"revise","issues":[{"issue":"b","file":"f.py","quote":""}]}')
    assert v is not None
    assert v.citations == [None]


def test_resolve_citations_object_without_quote_is_kept(tmp_path):
    v = reviewer.parse_verdict('{"verdict":"revise","issues":[{"issue":"b","file":"noexiste.py"}]}')
    result = reviewer.resolve_citations(v, tmp_path, "")
    assert result.issues == ["b"]
    assert result.dropped == []


def test_as_verdict_quote_without_file_is_checked_against_diff(tmp_path):
    v = reviewer.parse_verdict('{"verdict":"revise","issues":[{"issue":"b","quote":"+z = 9"}]}')
    assert v.citations == [("", "+z = 9")]
    assert reviewer.resolve_citations(v, tmp_path, "+z = 9\n").issues == ["b"]
    assert reviewer.resolve_citations(v, tmp_path, "+x = 1\n").dropped == ["b"]


def test_as_verdict_malformed_object_none():
    assert reviewer.parse_verdict('{"verdict":"revise","issues":[{"bad":"b"}]}') is None
    assert reviewer.parse_verdict('{"verdict":"revise","issues":[{"issue":1,"file":"f"}]}') is None
    assert reviewer.parse_verdict('{"verdict":"revise","issues":[{"issue":"b","quote":3}]}') is None
    assert reviewer.parse_verdict('{"verdict":"revise","issues":[42]}') is None


# ── broker: tampered y unsupported ────────────────────────────────────────────

def _ok_text(text):
    return broker.AttemptOutcome(ok=True, returncode=0, result=AdapterResult(text=text))


def _verdict_text(verdict, issues):
    return json.dumps({"verdict": verdict, "issues": issues})


def _two_reviewers(env, monkeypatch):
    agents = {a.id: a for a in env["registry"].cli_agents}
    env["registry"].delegation.review_default = True
    env["registry"].delegation.thinkers = ["claude", "codex"]
    monkeypatch.setattr(broker, "_review_candidates", lambda *a, **kw: [agents["claude"], agents["codex"]])
    return agents


@pytest.mark.asyncio
async def test_review_tampered_terminates_without_escalation(env, monkeypatch):
    _init_repo(env["workspace"])
    _two_reviewers(env, monkeypatch)
    calls = []

    async def fake(rt, agent, model, timeout_s, task=None, read_only=False):
        calls.append((agent.id, read_only))
        if not read_only:
            return _ok_text("hecho")
        (rt.workspace / "editado_por_revisor.py").write_text("x = 2\n", encoding="utf-8")
        return _ok_text(_verdict_text("approve", []))

    monkeypatch.setattr(broker, "_run_attempt", fake)
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard"))).id)
    assert job.status == "failed"
    assert job.review_status == "tampered"
    assert job.error.startswith("review_tampered")
    assert [c for c in calls if c[1]] == [("claude", True)]
    review = [a for a in job.attempts if a.kind == "review"]
    assert len(review) == 1 and review[0].detail["tampered"] is True


@pytest.mark.asyncio
async def test_review_change_with_concurrent_job_is_not_tampered(env, monkeypatch):
    _init_repo(env["workspace"])
    _two_reviewers(env, monkeypatch)
    ws = env["workspace"]

    async def fake(rt, agent, model, timeout_s, task=None, read_only=False):
        if not read_only:
            return _ok_text("hecho")
        # otro job, en el mismo workspace, sigue corriendo mientras el revisor lee
        other = broker.JobRuntime(
            job=Job(id="otro", created_at="2000-01-01T00:00:00+00:00", started_at="2000-01-01T00:00:00+00:00", status="running"),
            request=JobRequest(task="otra tarea", workspace=str(ws)), workspace=ws)
        broker._jobs["otro"] = other
        (ws / "del_otro_job.py").write_text("x = 2\n", encoding="utf-8")
        return _ok_text(_verdict_text("approve", []))

    monkeypatch.setattr(broker, "_run_attempt", fake)
    try:
        job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(ws), tier_hint="standard"))).id)
    finally:
        broker._jobs.pop("otro", None)
    assert job.status == "succeeded"
    assert job.review_status == "passed"
    assert "review_tree_changed_concurrent" in broker.job_output(job.id)


@pytest.mark.asyncio
async def test_review_unsupported_citations_falls_to_next_reviewer(env, monkeypatch):
    _init_repo(env["workspace"])
    _two_reviewers(env, monkeypatch)
    fake_citation = [{"issue": "falso", "file": "noexiste.py", "quote": "nada"}]

    async def fake(rt, agent, model, timeout_s, task=None, read_only=False):
        if not read_only:
            return _ok_text("hecho")
        if agent.id == "claude":
            return _ok_text(_verdict_text("revise", fake_citation))
        return _ok_text(_verdict_text("approve", []))

    monkeypatch.setattr(broker, "_run_attempt", fake)
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard"))).id)
    assert job.status == "succeeded"
    assert job.review_status == "passed"
    review = [a for a in job.attempts if a.kind == "review"]
    assert [a.agent_id for a in review] == ["claude", "codex"]
    assert review[0].detail["dropped"] == ["falso"] and review[0].detail["issues"] == []


@pytest.mark.asyncio
async def test_review_unsupported_without_next_reviewer_is_skipped(env, monkeypatch):
    _init_repo(env["workspace"])
    agents = _two_reviewers(env, monkeypatch)
    monkeypatch.setattr(broker, "_review_candidates", lambda *a, **kw: [agents["claude"]])
    fake_citation = [{"issue": "falso", "file": "noexiste.py", "quote": "nada"}]

    async def fake(rt, agent, model, timeout_s, task=None, read_only=False):
        if not read_only:
            return _ok_text("hecho")
        return _ok_text(_verdict_text("revise", fake_citation))

    monkeypatch.setattr(broker, "_run_attempt", fake)
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard"))).id)
    assert job.status == "succeeded"
    assert job.review_status == "skipped"
