import io
import json

import claude_agent_sdk as sdk
import pytest

from app.services.cli_agents import anthropic_runner as runner


def _args(read_only=False):
    args = ["--model", "claude-sonnet-5-5", "--cwd", "/repo", "--max-turns", "60", "--max-budget-usd", "3"]
    return args + (["--read-only"] if read_only else [])


def _result(**changes):
    values = {"subtype": "success", "duration_ms": 20, "duration_api_ms": 10, "is_error": False, "num_turns": 1, "session_id": "sess", "result": "listo", "total_cost_usd": 0.15, "usage": {"input_tokens": 9, "output_tokens": 2}}
    return sdk.ResultMessage(**{**values, **changes})


@pytest.mark.parametrize("read_only", [False, True])
def test_runner_options_enforce_tools_isolation_and_limits(read_only):
    options = runner.build_options(runner.parse_args(_args(read_only)))
    assert options.model == "claude-sonnet-5-5" and options.cwd == "/repo"
    assert options.max_turns == 60 and options.max_budget_usd == 3
    assert options.setting_sources == [] and options.strict_mcp_config is True
    assert options.system_prompt == {"type": "preset", "preset": "claude_code"}
    expected = ["Read", "Glob", "Grep"] if read_only else ["Read", "Glob", "Grep", "Edit", "Write", "Bash"]
    assert options.allowed_tools == expected and options.tools == expected
    assert options.permission_mode == ("plan" if read_only else "acceptEdits")
    expected_denied = {"Task", "Agent", "WebFetch", "WebSearch"}
    commands = ["git push", "git commit", "git reset", "git checkout", "git clean", "git stash", "rm", "rmdir", "del", "Remove-Item", "powershell", "pwsh", "cmd", "curl", "wget"]
    expected_denied.update(f"Bash({command}:*)" for command in commands)
    assert expected_denied <= set(options.disallowed_tools)
    if read_only:
        assert {"Edit", "Write", "Bash"} <= set(options.disallowed_tools)


def _run(monkeypatch, capsys, messages=(), error=None):
    captured = {}

    async def query(**kwargs):
        captured.update(kwargs)
        print("sdk output")
        for message in messages:
            yield message
        if error:
            raise error

    monkeypatch.setattr(sdk, "query", query)
    monkeypatch.setattr(runner.sys, "stdin", io.StringIO("responde ok"))
    code = runner.main(_args())
    output = capsys.readouterr()
    assert len(output.out.splitlines()) == 1
    assert "sdk output" in output.err
    return code, json.loads(output.out), captured


def test_runner_success_outputs_one_json_line(monkeypatch, capsys):
    assistant = sdk.AssistantMessage(content=[sdk.TextBlock("parcial")], model="claude-sonnet-5-5")
    code, result, captured = _run(monkeypatch, capsys, [assistant, _result()])
    assert code == 0 and result["text"] == "listo" and result["is_error"] is False
    assert result["total_cost_usd"] == 0.15 and result["usage"] == {"input_tokens": 9, "output_tokens": 2}
    assert result["session_id"] == "sess" and not result["error"]
    assert captured["prompt"] == "responde ok"


@pytest.mark.parametrize("error", [sdk.CLINotFoundError("CLI ausente"), sdk.ProcessError("falló proceso", exit_code=1), sdk.ResultError("falló resultado")])
def test_runner_sdk_exception_outputs_error_json(monkeypatch, capsys, error):
    code, result, _ = _run(monkeypatch, capsys, error=error)
    assert code == 1 and result["is_error"] is True and str(error) in result["error"]


@pytest.mark.parametrize("subtype", ["error_max_budget_usd", "error_max_turns"])
def test_runner_limits_preserve_paid_usage(monkeypatch, capsys, subtype):
    code, result, _ = _run(monkeypatch, capsys, [_result(subtype=subtype, is_error=True, result=None)])
    assert code == 1 and subtype in result["error"]
    assert result["total_cost_usd"] == 0.15 and result["usage"]["input_tokens"] == 9


def test_runner_result_exception_preserves_cost_and_api_status(monkeypatch, capsys):
    error = sdk.ResultError("credit balance too low", data={"api_error_status": 400, "total_cost_usd": 0.2, "usage": {"input_tokens": 10, "output_tokens": 3}, "session_id": "paid"})
    code, result, _ = _run(monkeypatch, capsys, error=error)
    assert code == 1 and result["api_error_status"] == 400
    assert result["total_cost_usd"] == 0.2 and result["session_id"] == "paid"


def test_runner_without_final_result_fails(monkeypatch, capsys):
    code, result, _ = _run(monkeypatch, capsys)
    assert code == 1 and result["is_error"] and result["error"]
