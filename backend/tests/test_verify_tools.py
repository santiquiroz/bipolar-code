import aiosqlite
import pytest

from app.models.delegate import JobRequest
from app.services import usage_tracker
from app.services.cli_agents import adapters, broker, job_ledger
from app.services.cli_agents.adapters import redact, verify_tools, with_allowed_tools

from tests.test_delegation_broker import _claude_ok, _fake_subprocess, _wait, env  # noqa: F401


def test_verify_tools_two_commands_and_empty():
    assert verify_tools(["npm test", "", "npm test", "pytest -q"]) == [
        "Bash(npm test)", "PowerShell(npm test)", "Bash(pytest -q)", "PowerShell(pytest -q)"]
    assert verify_tools(None) == [] and verify_tools([]) == []


def test_with_allowed_tools_appends_to_right_value_without_mutating():
    argv = ["claude", "-p", "--allowedTools", "Bash(npm test:*)", "--disallowedTools", "Task"]
    out = with_allowed_tools(argv, ["Bash(x)", "PowerShell(x)"])
    assert out == ["claude", "-p", "--allowedTools", "Bash(npm test:*),Bash(x),PowerShell(x)", "--disallowedTools", "Task"]
    assert argv[3] == "Bash(npm test:*)" and out is not argv


def test_with_allowed_tools_noop_cases():
    argv = ["claude", "-p"]
    assert with_allowed_tools(argv, ["Bash(x)"]) == argv
    assert with_allowed_tools(argv, ["Bash(x)"]) is not argv
    with_flag = ["--allowedTools", "A"]
    assert with_allowed_tools(with_flag, []) == with_flag


def test_redact_still_works_with_extended_argv():
    argv = with_allowed_tools(["--allowedTools", "A"], ["B"])
    assert redact(argv, 5) == ["--allowedTools", "A,B", "<task:5 chars>"]


@pytest.mark.asyncio
async def test_claude_job_gets_verify_tools_and_is_recorded(env, monkeypatch):  # noqa: F811
    db_path = env["tmp"] / "usage.db"
    monkeypatch.setattr(usage_tracker, "_db_path", lambda: db_path)
    job_ledger.reset_for_tests()
    env["registry"].delegation.allow_request_verify = True
    run, calls = _fake_subprocess([_claude_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    request = JobRequest(task="implementa", workspace=str(env["workspace"]), agent_id="claude", verify=["npm test"])
    job = await _wait((await broker.submit(request)).id)
    argv = calls[0].argv
    assert "Bash(npm test)" in argv[argv.index("--allowedTools") + 1].split(",")
    async with aiosqlite.connect(str(db_path)) as db:
        rows = await (await db.execute("SELECT id, status FROM delegate_jobs")).fetchall()
    assert rows == [(job.id, job.status)]
    job_ledger.reset_for_tests()
