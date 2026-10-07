"""Tests de los adaptadores CLI: argv seguro, prompt fuera de argv, env limpio y parseo."""
import json
import os
import sys
from pathlib import Path

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


def _write_cursor_deny_list(config_dir):
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "cli-config.json").write_text(json.dumps({
        "permissions": {"deny": list(ad.CURSOR_CRITICAL_DENY)},
    }), encoding="utf-8")


def test_cursor_build_refuses_without_deny_list(tmp_path, monkeypatch, clean_env):
    monkeypatch.setenv("CURSOR_RESCUE_HOME", str(tmp_path / "cr"))
    with pytest.raises(ad.AdapterUnsafe, match="cursor_deny_list_missing"):
        ad.CursorAdapter().build(_agent("cursor"), "cursor-agent", "j", "t", "", tmp_path, "simple", 600)


def test_cursor_build_uses_pointer_and_minimal_environment(tmp_path, monkeypatch, clean_env):
    config_dir = tmp_path / "cr"
    monkeypatch.setenv("CURSOR_RESCUE_HOME", str(config_dir))
    monkeypatch.setenv("CURSOR_API_KEY", "sk-x")
    _write_cursor_deny_list(config_dir)
    spec = ad.CursorAdapter().build(_agent("cursor"), "cursor-agent", "j", "tarea", "", tmp_path, "simple", 600)

    assert spec.argv[:1] == ad.exe_argv("cursor-agent")
    assert spec.argv[spec.argv.index("-p") + 1] == ad.POINTER_PROMPT.format(rel=".bipolar/jobs/j/task.md")
    assert spec.argv[spec.argv.index("--output-format") + 1] == "json"
    assert "--trust" in spec.argv and "--force" in spec.argv
    assert spec.argv[spec.argv.index("--workspace") + 1] == str(tmp_path)
    assert spec.argv[spec.argv.index("--model") + 1] == "auto"
    assert "tarea" not in spec.argv
    assert ad.TASK_CONSTRAINTS in spec.pointer_file.read_text(encoding="utf-8")
    assert spec.env["CURSOR_CONFIG_DIR"] == str(config_dir)
    assert spec.env["CURSOR_API_KEY"] == "sk-x"


@pytest.mark.parametrize("isolate", ["on", "off"])
def test_cursor_windows_bundle_uses_newest_version_and_preload(tmp_path, monkeypatch, clean_env, isolate):
    config_dir = tmp_path / "cr"
    monkeypatch.setenv("CURSOR_RESCUE_HOME", str(config_dir))
    _write_cursor_deny_list(config_dir)
    (config_dir / "isolate").write_text(isolate, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    shim = bin_dir / "cursor-agent.cmd"
    shim.parent.mkdir()
    shim.touch()
    versions = bin_dir / "versions"
    for version in ("2026.09.26-aaa", "2026.09.28-bbb"):
        version_dir = versions / version
        version_dir.mkdir(parents=True)
        (version_dir / "node.exe").touch()
        (version_dir / "index.js").touch()

    spec = ad.CursorAdapter().build(_agent("cursor"), str(shim), "j", "t", "auto", tmp_path, "simple", 600)
    newest = versions / "2026.09.28-bbb"
    assert Path(spec.argv[0]) == newest / "node.exe"
    assert spec.argv[1] == "--require"
    assert Path(spec.argv[2]).exists()
    assert "os.homedir" in Path(spec.argv[2]).read_text(encoding="utf-8")
    assert Path(spec.argv[3]) == newest / "index.js"
    if isolate == "on":
        assert spec.env["CURSOR_RESCUE_FAKE_HOME"] == str(config_dir / "home")
        assert (config_dir / "home").is_dir()
    else:
        assert "CURSOR_RESCUE_FAKE_HOME" not in spec.env


def test_cursor_parse_result_and_errors():
    adapter = ad.CursorAdapter()
    success = json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "result": "terminado",
        "session_id": "sess-1", "usage": {"inputTokens": 12, "outputTokens": 4},
    })
    result = adapter.parse(success, "", 0, None)
    assert result.text == "terminado" and result.structured_error is False
    assert result.usage == {"input_tokens": 12, "output_tokens": 4} and result.session_id == "sess-1"
    error = adapter.parse(json.dumps({"is_error": True, "result": "falló"}), "", 0, None)
    assert error.structured_error is True
    startup_error = adapter.parse("", "ActionRequiredError: Named models unavailable", 1, None)
    assert startup_error.structured_error is True and "Named models unavailable" in startup_error.text


def test_workspace_path_with_metachars_rejected():
    bad = r"C:\repo&calc" if sys.platform == "win32" else "/repo\x01"
    with pytest.raises(ad.AdapterUnsafe):
        ad.validate_path_argv(bad)


@pytest.mark.parametrize("tier,effort", [("trivial", "low"), ("simple", "low"), ("standard", "medium"), ("complex", "high")])
def test_effort_for_tier(tier, effort):
    assert ad.effort_for_tier(tier) == effort


# ── DeepSeek Harness (dsh) ───────────────────────────────────────────────────

@pytest.fixture
def dsh_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return tmp_path / "dsh-home"


def _dsh_layout(tmp_path):
    """Instalación de Electron: el shim dsh.cmd apunta al cli.js dentro de app.asar."""
    root = tmp_path / "DeepSeek Harness"
    (root / "DeepSeek Harness.exe").parent.mkdir(parents=True, exist_ok=True)
    (root / "DeepSeek Harness.exe").touch()
    resources = root / "resources"
    resources.mkdir(parents=True, exist_ok=True)
    (resources / "app.asar").touch()
    exe = resources / "runtime" / "cli" / "bin" / "dsh.cmd"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.touch()
    return root, exe


def _dsh_build(tmp_path, exe, model="deepseek-v4-pro", job_id="jobdsh"):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    return ad.DeepseekAdapter().build(_agent("deepseek"), str(exe), job_id, "haz X", model, workspace, "complex", 900), workspace


def test_deepseek_build_uses_electron_launcher_and_model_patch(tmp_path, dsh_env):
    root, exe = _dsh_layout(tmp_path)
    spec, workspace = _dsh_build(tmp_path, exe)
    argv = spec.argv

    assert argv[0] == str(root / "DeepSeek Harness.exe")
    assert argv[1] == "--expose-internals"
    cli_js = Path(argv[2])
    assert cli_js.as_posix().endswith("dsh-desktop-host/lib/cli.js")
    assert "resources/app.asar" in cli_js.as_posix()
    assert argv[argv.index("--profile") + 1] == "headless"
    assert "--json" in argv and argv[-1] == "-"
    assert spec.env["ELECTRON_RUN_AS_NODE"] == "1"
    assert spec.env["DSH_PERMISSION_MODE"] == "workspace-write"
    assert spec.stdin_payload.decode("utf-8") == "haz X" + ad.TASK_CONSTRAINTS
    assert spec.pointer_file is None
    assert spec.cwd == str(workspace)

    patch = Path(argv[argv.index("--patch") + 1])
    assert patch == workspace / ".bipolar" / "jobs" / "jobdsh" / "dsh-model.patch.yml"
    assert patch.exists()
    content = patch.read_text(encoding="utf-8")
    assert "provider: deepseek-account" in content
    assert "model: deepseek-v4-pro" in content
    assert "DEEPSEEK_API_KEY" not in content and "sk-" not in content


def test_deepseek_foreign_exe_skips_electron_launcher(tmp_path, dsh_env):
    exe = tmp_path / "dsh.exe"
    exe.touch()
    spec, _ = _dsh_build(tmp_path, exe, model="deepseek-flash")
    assert spec.argv[0] == str(exe)
    assert "ELECTRON_RUN_AS_NODE" not in spec.env


def test_deepseek_empty_model_falls_back_to_default(tmp_path, dsh_env):
    root, exe = _dsh_layout(tmp_path)
    spec, _ = _dsh_build(tmp_path, exe, model="")
    patch = Path(spec.argv[spec.argv.index("--patch") + 1])
    assert "model: deepseek-flash" in patch.read_text(encoding="utf-8")


@pytest.mark.parametrize("model", ["!!js process.exit()", "deepseek-flash\nprovider: x", "a b"])
def test_deepseek_rejects_unsafe_model(tmp_path, dsh_env, model):
    root, exe = _dsh_layout(tmp_path)
    with pytest.raises(ad.AdapterUnsafe):
        _dsh_build(tmp_path, exe, model=model)


@pytest.mark.parametrize("arg", ["--patch=x.yml", "--profile"])
def test_deepseek_dangerous_extra_args_rejected(arg):
    with pytest.raises(ad.AdapterUnsafe):
        ad.validate_extra_args([arg])


def _write_dsh_credentials(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / ".credentials.yaml").write_text("  deepseek-account-platform/default:\n    token: secret\n", encoding="utf-8")


def test_deepseek_account_credentials_win_over_api_key(tmp_path, monkeypatch):
    home = tmp_path / "dsh-home"
    _write_dsh_credentials(home)
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abc")
    adapter = ad.DeepseekAdapter()
    assert adapter.provider() == "deepseek-account"
    assert adapter.has_credentials() is True


def test_deepseek_api_key_only_is_official_provider(tmp_path, monkeypatch):
    home = tmp_path / "dsh-home"
    home.mkdir()
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abc")
    adapter = ad.DeepseekAdapter()
    assert adapter.provider() == "deepseek-official"
    assert adapter.has_credentials() is True


def test_deepseek_without_credentials(tmp_path, monkeypatch):
    home = tmp_path / "dsh-home"
    home.mkdir()
    monkeypatch.setenv("DSH_HOME", str(home))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    adapter = ad.DeepseekAdapter()
    assert adapter.provider() == "deepseek-account"
    assert adapter.has_credentials() is False


def test_deepseek_env_passes_own_secret_but_not_other_secrets(tmp_path, monkeypatch, dsh_env):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    root, exe = _dsh_layout(tmp_path)
    spec, _ = _dsh_build(tmp_path, exe, model="deepseek-flash")
    assert spec.env["DEEPSEEK_API_KEY"] == "sk-ds"
    assert "OPENAI_API_KEY" not in spec.env


def test_deepseek_env_drops_api_key_when_account_signed_in(tmp_path, monkeypatch, dsh_env):
    dsh_env.mkdir(parents=True, exist_ok=True)
    (dsh_env / ".credentials.yaml").write_text("  deepseek-account-platform/default:\n    token: t\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    _, exe = _dsh_layout(tmp_path)
    spec, _ = _dsh_build(tmp_path, exe, model="deepseek-flash")
    assert "DEEPSEEK_API_KEY" not in spec.env


def _dsh_run(*, turn_error=None, final_text="final answer"):
    lines = [
        {"type": "session", "sessionId": "session-abc", "cwd": "C:\\ws"},
        {"type": "status", "phase": "turn_start", "turn": 1},
        {"type": "tool_call", "callId": "c1", "tool": "write", "input": {"file_path": "hello.txt", "content": "ok"}},
        {"type": "tool_result", "callId": "c1", "status": "completed", "result": "Created file"},
        {"type": "status", "phase": "step_end", "turn": 1, "step": 1, "usage": {"inputTokens": 12538, "outputTokens": 164}},
        {"type": "status", "phase": "step_end", "turn": 1, "step": 2, "usage": {"inputTokens": 183, "outputTokens": 325}},
        {"type": "status", "phase": "turn_end", "turn": 1,
         "reason": turn_error or {"kind": "completed"}},
        {"type": "final", "text": final_text},
    ]
    return "\n".join(json.dumps(line) for line in lines)


def test_deepseek_parse_completed_run():
    result = ad.DeepseekAdapter().parse(_dsh_run(), "", 0, None)
    assert result.text == "final answer"
    assert result.structured_error is False
    assert result.usage == {"input_tokens": 12721, "output_tokens": 489}
    assert result.session_id == "session-abc"


def test_deepseek_parse_failed_turn_reports_structured_error():
    out = _dsh_run(turn_error={"kind": "error", "error": {"message": "llm-deepseek: no API key", "code": "MISSING_CREDENTIAL"}},
                   final_text="")
    result = ad.DeepseekAdapter().parse(out, "", 1, None)
    assert result.structured_error is True
    assert result.text == "MISSING_CREDENTIAL: llm-deepseek: no API key"


def test_deepseek_parse_error_event():
    result = ad.DeepseekAdapter().parse(json.dumps({"type": "error", "message": "boom"}), "", 0, None)
    assert result.structured_error is True and result.text == "boom"


def test_deepseek_parse_falls_back_to_stderr():
    result = ad.DeepseekAdapter().parse("", "dsh: crashed", 1, None)
    assert result.structured_error is True and result.text == "dsh: crashed"


def test_deepseek_parse_ignores_step_end_without_usage():
    out = "\n".join([
        json.dumps({"type": "status", "phase": "step_end", "turn": 1, "step": 1, "usage": {"inputTokens": 10, "outputTokens": 2}}),
        json.dumps({"type": "status", "phase": "step_end", "turn": 1, "step": 2}),
    ])
    assert ad.DeepseekAdapter().parse(out, "", 0, None).usage == {"input_tokens": 10, "output_tokens": 2}


@pytest.fixture
def muse_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "muse-config"
    monkeypatch.setenv("MUSE_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("META_API_KEY", raising=False)
    return config_dir


def _muse_layout(tmp_path):
    root = tmp_path / "muse"
    root.mkdir()
    shim = root / "muse.cmd"
    shim.touch()
    binary = root / "muse-bin-1.4.3-R5018.1.exe"
    binary.touch()
    (root / ".muse-version").write_text("1.4.3-R5018.1\n", encoding="utf-8")
    return shim, binary


def _muse_build(tmp_path, exe, model="", tier="standard", **overrides):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    spec = ad.MuseAdapter().build(
        _agent("muse", **overrides), str(exe), "jobmuse", "haz X", model, workspace, tier, 900,
    )
    return spec, workspace


@pytest.mark.parametrize("tier,effort", [("trivial", "low"), ("simple", "low"), ("standard", "medium"), ("complex", "high")])
def test_muse_build_uses_binary_pointer_and_fixed_safety_flags(tmp_path, muse_env, tier, effort):
    shim, binary = _muse_layout(tmp_path)
    spec, workspace = _muse_build(tmp_path, shim, tier=tier)
    pointer = workspace / ".bipolar" / "jobs" / "jobmuse" / "task.md"
    assert spec.argv == [
        str(binary), "exec", "--json", "--prompt-file", str(pointer),
        "--workspace", str(workspace), "--approval-mode", "never", "--approval-judge", "off",
        "--no-foreign-personal-context", "--user-input-auto-resolve", "--max-model-steps", "60",
        "--reasoning-effort", effort,
    ]
    assert spec.pointer_file == pointer
    assert pointer.read_text(encoding="utf-8") == "haz X" + ad.TASK_CONSTRAINTS
    assert (workspace / ".bipolar" / ".gitignore").read_text() == "*\n"
    assert spec.cwd == str(workspace)
    assert spec.stdin_payload is None
    assert "haz X" not in " ".join(spec.argv)


def test_muse_build_adds_valid_model_and_safe_extra_args(tmp_path, muse_env):
    shim, _ = _muse_layout(tmp_path)
    spec, _ = _muse_build(tmp_path, shim, model="meta-model-v1", extra_args=["--verbose"])
    assert spec.argv[-3:] == ["--model", "meta-model-v1", "--verbose"]


@pytest.mark.parametrize("version", [None, "missing-version"])
def test_muse_binary_falls_back_to_lexicographically_greatest(tmp_path, version):
    shim, binary = _muse_layout(tmp_path)
    newest = shim.parent / "muse-bin-9.0.exe"
    newest.touch()
    version_file = shim.parent / ".muse-version"
    if version is None:
        version_file.unlink()
    else:
        version_file.write_text(version, encoding="utf-8")
    assert ad.MuseAdapter().bin_for(str(shim)) == [str(newest)]


def test_muse_named_version_wins_over_greatest_binary(tmp_path):
    shim, binary = _muse_layout(tmp_path)
    (shim.parent / "muse-bin-9.0.exe").touch()
    assert ad.MuseAdapter().bin_for(str(shim)) == [str(binary)]


def test_muse_shim_without_binary_uses_exe_argv(tmp_path):
    shim = tmp_path / "muse.cmd"
    shim.touch()
    assert ad.MuseAdapter().bin_for(str(shim)) == ad.exe_argv(str(shim))


def test_muse_non_shim_executable_uses_exe_argv(tmp_path):
    shim, _ = _muse_layout(tmp_path)
    exe = shim.parent / "other.exe"
    exe.touch()
    assert ad.MuseAdapter().bin_for(str(exe)) == ad.exe_argv(str(exe))


def test_muse_shim_name_is_case_insensitive(tmp_path):
    shim, binary = _muse_layout(tmp_path)
    upper_shim = shim.parent / "MUSE.CMD"
    assert ad.MuseAdapter().bin_for(str(upper_shim)) == [str(binary)]


def test_muse_binary_resolves_shim_symlink_first(tmp_path):
    shim, binary = _muse_layout(tmp_path)
    link = tmp_path / "muse-link.cmd"
    try:
        link.symlink_to(shim)
    except OSError:
        pytest.skip("El sistema no permite crear symlinks")
    assert ad.MuseAdapter().bin_for(str(link)) == [str(binary)]


@pytest.mark.parametrize("model", ["--yolo", "a b", "!!x"])
def test_muse_build_rejects_unsafe_model(tmp_path, muse_env, model):
    shim, _ = _muse_layout(tmp_path)
    with pytest.raises(ad.AdapterUnsafe, match="model_not_allowed"):
        _muse_build(tmp_path, shim, model=model)


@pytest.mark.parametrize("arg", ["--disable-sandbox", "--trust-workspace", "--approval-mode=never", "--workspace=x", "--prompt-file=x", "--permission-profile=x"])
def test_muse_build_rejects_dangerous_extra_args(tmp_path, muse_env, arg):
    shim, _ = _muse_layout(tmp_path)
    with pytest.raises(ad.AdapterUnsafe):
        _muse_build(tmp_path, shim, extra_args=[arg])


@pytest.mark.parametrize("api_key", [None, "meta-secret"])
def test_muse_env_passes_own_key_only_when_set(tmp_path, monkeypatch, muse_env, clean_env, api_key):
    if api_key:
        monkeypatch.setenv("META_API_KEY", api_key)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    shim, _ = _muse_layout(tmp_path)
    spec, _ = _muse_build(tmp_path, shim)
    assert spec.env.get("META_API_KEY") == api_key
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "SOME_PASSWORD"):
        assert name not in spec.env


def test_muse_env_marks_workspace_as_git_safe_directory(tmp_path, muse_env):
    shim, _ = _muse_layout(tmp_path)
    spec, workspace = _muse_build(tmp_path, shim)
    assert spec.env["GIT_CONFIG_COUNT"] == "1"
    assert spec.env["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert spec.env["GIT_CONFIG_VALUE_0"] == str(workspace).replace("\\", "/")


@pytest.mark.parametrize("field", ["access_token", "api_key"])
def test_muse_signed_in_with_auth_file(muse_env, field):
    muse_env.mkdir()
    (muse_env / "auth.json").write_text(json.dumps({"providers": {"meta": {field: "secret"}}}), encoding="utf-8")
    assert ad.MuseAdapter().config_dir() == muse_env
    assert ad.MuseAdapter().signed_in() is True


def test_muse_default_config_dir_is_under_home(tmp_path, monkeypatch):
    monkeypatch.delenv("MUSE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert ad.MuseAdapter().config_dir() == tmp_path / ".config" / "muse"


@pytest.mark.parametrize("content", ["invalid-json", "[]", "{}", '{"providers":null}', '{"providers":{"meta":null}}', '{"providers":{"meta":{"access_token":"","api_key":""}}}'])
def test_muse_signed_in_rejects_missing_or_malformed_credentials(muse_env, content):
    muse_env.mkdir()
    (muse_env / "auth.json").write_text(content, encoding="utf-8")
    assert ad.MuseAdapter().signed_in() is False


def test_muse_api_key_takes_priority_over_malformed_auth_file(muse_env, monkeypatch):
    muse_env.mkdir()
    (muse_env / "auth.json").write_text("invalid-json", encoding="utf-8")
    monkeypatch.setenv("META_API_KEY", "meta-secret")
    assert ad.MuseAdapter().signed_in() is True


def _muse_run(terminal="completed", **payload):
    records = [
        {"stream": {"kind": "session", "id": "session-muse"}, "payload_type": "run.model.configured", "payload": {"model_id": "meta/model-v1"}},
        {"stream": {"kind": "session", "id": "session-muse"}, "payload_type": "run.terminal.finished", "payload": {"terminal": terminal, **payload}},
    ]
    return "\n".join(json.dumps(record) for record in records)


@pytest.mark.parametrize("returncode", [0, None])
def test_muse_parse_completed_terminal(returncode):
    result = ad.MuseAdapter().parse(_muse_run(text="listo"), "noise", returncode, None)
    assert result.text == "listo"
    assert result.structured_error is False
    assert result.usage == {"input_tokens": 0, "output_tokens": 0}
    assert result.session_id == "session-muse"


def test_muse_parse_failed_terminal_reason():
    result = ad.MuseAdapter().parse(_muse_run("failed", reason="rate limit exceeded"), "", 0, None)
    assert result.text == "rate limit exceeded"
    assert result.structured_error is True


def test_muse_parse_completed_terminal_with_failed_process():
    result = ad.MuseAdapter().parse(_muse_run(text="listo"), "", 1, None)
    assert result.text == "listo"
    assert result.structured_error is True


def test_muse_parse_missing_terminal_falls_back_to_stderr():
    result = ad.MuseAdapter().parse("", "muse: crashed", 1, None)
    assert result.text == "muse: crashed"
    assert result.structured_error is True
    assert result.session_id == ""


def test_muse_parse_missing_terminal_is_error_even_with_zero_returncode():
    result = ad.MuseAdapter().parse(json.dumps({"payload_type": "run.model.configured", "payload": {"model_id": "meta/default"}}), "", 0, None)
    assert result.structured_error is True
