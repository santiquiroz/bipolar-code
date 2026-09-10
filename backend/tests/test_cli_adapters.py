"""Tests de los adaptadores CLI: argv seguro, prompt fuera de argv, env limpio y parseo."""
import json
import os
import sys

import pytest

from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent
from app.services.cli_agents import adapters as ad


def _agent(agent_id: str, **overrides) -> CliAgent:
    base = next(d for d in DEFAULT_CLI_AGENTS if d["id"] == agent_id)
    return CliAgent(**{**base, "enabled": True, **overrides})


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("COPILOT_SESSION_TOKEN", "tok")
    monkeypatch.setenv("SOME_PASSWORD", "x")


def test_child_env_strips_secrets_and_sets_guards(clean_env):
    env = ad.child_env(os.environ)
    assert "ANTHROPIC_BASE_URL" not in env and "ANTHROPIC_API_KEY" not in env
    assert "COPILOT_SESSION_TOKEN" not in env and "SOME_PASSWORD" not in env
    assert env["BIPOLAR_DELEGATION_DEPTH"] == "1" and env["GIT_TERMINAL_PROMPT"] == "0"
    assert "PATH" in env


@pytest.mark.parametrize("arg", ["--dangerously-skip-permissions", "--permission-mode=bypassPermissions", "--yolo",
                                 "--allow-all", "--sandbox=danger-full-access", "--add-dir", "-C", "--autopilot", "rm -rf"])
def test_dangerous_extra_args_rejected(arg):
    with pytest.raises(ad.AdapterUnsafe):
        ad.validate_extra_args([arg])


def test_safe_extra_args_accepted():
    assert ad.validate_extra_args(["--verbose", "--max-turns=10"]) == ["--verbose", "--max-turns=10"]


def test_claude_build_has_safety_flags_and_pointer(tmp_path, clean_env):
    spec = ad.ClaudeAdapter().build(_agent("claude"), "claude.exe", "job1", "haz X", "claude-sonnet-4-6", tmp_path, "standard", 600)
    argv = spec.argv
    assert argv[0] == "claude.exe" and argv[1] == "-p"
    assert "--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--disallowedTools") + 1] == "Task,Agent,WebFetch,WebSearch"
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path)
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-6"
    assert "haz X" not in " ".join(argv)
    assert spec.pointer_file.exists() and ad.TASK_CONSTRAINTS in spec.pointer_file.read_text(encoding="utf-8")
    assert (tmp_path / ".bipolar" / ".gitignore").read_text() == "*\n"
    assert "ANTHROPIC_BASE_URL" not in spec.env


def test_claude_parse_json_result():
    out = json.dumps({"result": "listo", "is_error": False, "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01, "session_id": "s1"})
    r = ad.ClaudeAdapter().parse(out, "", 0, None)
    assert r.text == "listo" and r.structured_error is False and r.usage["cost_usd"] == 0.01 and r.session_id == "s1"


def test_codex_build_uses_stdin_dash_and_workspace_write(tmp_path, clean_env):
    spec = ad.CodexAdapter().build(_agent("codex"), "codex.exe", "job2", "tarea", "", tmp_path, "standard", 900)
    argv = spec.argv
    assert argv[1:5] == ["exec", "--sandbox", "workspace-write", "--skip-git-repo-check"]
    assert argv[-1] == "-" and "-C" in argv and argv[argv.index("-C") + 1] == str(tmp_path)
    assert spec.stdin_payload.decode("utf-8").startswith("tarea")
    assert "danger-full-access" not in " ".join(argv)


@pytest.mark.skipif(sys.platform != "win32", reason="shim .cmd solo en Windows")
def test_cmd_shim_runs_through_comspec(monkeypatch):
    monkeypatch.setenv("ComSpec", r"C:\Windows\System32\cmd.exe")
    assert ad.exe_argv(r"C:\Users\x\AppData\Roaming\npm\codex.cmd")[:4] == [r"C:\Windows\System32\cmd.exe", "/d", "/s", "/c"]
    assert ad.exe_argv(r"C:\tools\agy.exe") == [r"C:\tools\agy.exe"]


def test_codex_parse_prefers_out_file_and_detects_structured_error(tmp_path):
    out_file = tmp_path / "last.md"
    out_file.write_text("respuesta final", encoding="utf-8")
    events = "\n".join([
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "parcial"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 3}}),
    ])
    r = ad.CodexAdapter().parse(events, "", 0, out_file)
    assert r.text == "respuesta final" and r.usage == {"input_tokens": 7, "output_tokens": 3} and r.structured_error is False
    failed = "\n".join([
        json.dumps({"type": "error", "message": "Your workspace is out of credits."}),
        json.dumps({"type": "turn.failed", "error": {"message": "Your workspace is out of credits."}}),
    ])
    r2 = ad.CodexAdapter().parse(failed, "", 0, tmp_path / "missing.md")
    assert r2.structured_error is True and "out of credits" in r2.text


def test_copilot_build_deny_list_wins_and_effort_low_for_simple(tmp_path, clean_env):
    spec = ad.CopilotAdapter().build(_agent("copilot", max_credits=7), "copilot.exe", "job3", "tarea", "gpt-5.4", tmp_path, "simple", 600)
    argv = spec.argv
    joined = " ".join(argv)
    for rule in ad.CopilotAdapter.DENY:
        assert rule in argv
    assert "--allow-all" not in joined and "--autopilot" not in joined
    assert argv[argv.index("--max-ai-credits") + 1] == "7"
    assert argv[argv.index("--effort") + 1] == "low"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "tarea" not in joined


def test_copilot_parse_collects_assistant_text_and_errors():
    out = "\n".join([json.dumps({"role": "assistant", "content": "hola"}), json.dumps({"error": {"message": "hit a rate limit"}})])
    r = ad.CopilotAdapter().parse(out, "", 1, None)
    assert "hola" in r.text and r.structured_error is True


def test_antigravity_refuses_without_deny_list(tmp_path, monkeypatch, clean_env):
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    with pytest.raises(ad.AdapterUnsafe, match="agy_deny_list_missing"):
        ad.AntigravityAdapter().build(_agent("antigravity"), "agy.exe", "j", "t", "gemini-3.8-flash-low", tmp_path, "simple", 600)


def test_antigravity_build_with_deny_list_and_effort_only_for_gemini(tmp_path, monkeypatch, clean_env):
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path))
    settings = tmp_path / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"permissions": {"deny": ["command(regex:.*\\bgit\\s+push\\b.*)", "command(regex:.*\\brm\\b.*)"]}}), encoding="utf-8")
    adapter = ad.AntigravityAdapter()
    gem = adapter.build(_agent("antigravity"), "agy.exe", "j1", "t", "gemini-3.1-pro-high", tmp_path, "complex", 600)
    assert "--dangerously-skip-permissions" in gem.argv and "--disable-slash-commands" in gem.argv
    assert gem.argv[gem.argv.index("--print-timeout") + 1] == "570s"
    assert gem.argv[gem.argv.index("--effort") + 1] == "high" and gem.pool_key == "cli:antigravity#gemini"
    cla = adapter.build(_agent("antigravity"), "agy.exe", "j2", "t", "claude-sonnet-4-6", tmp_path, "complex", 600)
    assert "--effort" not in cla.argv and cla.pool_key == "cli:antigravity#claude"


def test_antigravity_parse_reports_denied_actions_and_errors():
    out = json.dumps({"status": "SUCCESS", "response": "ok", "denied_actions": [{"action": "command", "display_name": "RunCommand"}], "usage": {"input_tokens": 1, "output_tokens": 1}})
    r = ad.AntigravityAdapter().parse(out, "jetski: no output produced\nnoise", 0, None)
    assert "denied_actions: command" in r.text and "jetski:" in r.text and r.structured_error is False
    err = json.dumps({"status": "ERROR", "response": "", "error": "The stream was interrupted."})
    assert ad.AntigravityAdapter().parse(err, "", 0, None).structured_error is True


def test_workspace_path_with_metachars_rejected():
    bad = r"C:\repo&calc" if sys.platform == "win32" else "/repo\x01"
    with pytest.raises(ad.AdapterUnsafe):
        ad.validate_path_argv(bad)


@pytest.mark.parametrize("tier,effort", [("trivial", "low"), ("simple", "low"), ("standard", "medium"), ("complex", "high")])
def test_effort_for_tier(tier, effort):
    assert ad.effort_for_tier(tier) == effort
