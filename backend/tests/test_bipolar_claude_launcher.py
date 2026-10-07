"""Lanzador bipolar-claude: núcleo puro (espejo, settings, slug, transcript)."""
import importlib.util
import io
import json
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bipolar_claude.py"


def _load():
    spec = importlib.util.spec_from_file_location("bipolar_claude", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_project_slug_matches_claude_code():
    bc = _load()
    assert bc.project_slug(Path(r"C:\Users\santi\.claude-mem\observer sessions")) == "C--Users-santi--claude-mem-observer-sessions"
    assert bc.project_slug(Path(r"c:\personal\bipolar-code\bipolar-code")) == "c--personal-bipolar-code-bipolar-code"


def test_mirrored_settings_strips_proxy_and_wraps_statusline():
    bc = _load()
    user = {"env": {"ANTHROPIC_BASE_URL": "http://x", "ANTHROPIC_API_KEY": "k", "FOO": "1"},
            "statusLine": {"type": "command", "command": "node mine.js"}, "theme": "dark"}
    cmd = bc.statusline_command("python", Path("/r/bipolar-statusline.py"), "claude-2", user["statusLine"]["command"])
    out = bc.mirrored_settings(user, cmd)
    assert out["env"] == {"FOO": "1"}
    assert out["statusLine"] == {"type": "command", "command": cmd}
    assert '--agent-id "claude-2"' in cmd and '--then "node mine.js"' in cmd
    assert out["theme"] == "dark"
    assert user["env"]["ANTHROPIC_BASE_URL"] == "http://x"


def test_mirrored_settings_drops_empty_env():
    bc = _load()
    out = bc.mirrored_settings({"env": {"ANTHROPIC_BASE_URL": "http://x"}}, "cmd")
    assert "env" not in out


def test_merge_mcp_servers_account_wins():
    bc = _load()
    merged = bc.merge_mcp_servers({"mcpServers": {"a": {"url": "acct"}}, "other": 1},
                                  {"mcpServers": {"a": {"url": "user"}, "b": {"url": "u2"}}, "secret": "x"})
    assert merged["mcpServers"] == {"a": {"url": "acct"}, "b": {"url": "u2"}}
    assert merged["other"] == 1 and "secret" not in merged


def test_mirror_plan_links_missing_and_skips_real_dirs(tmp_path):
    bc = _load()
    user, acct = tmp_path / "user", tmp_path / "acct"
    for d in ("skills", "plugins", "agents"):
        (user / d).mkdir(parents=True)
    (user / "CLAUDE.md").write_text("reglas", encoding="utf-8")
    (acct / "agents").mkdir(parents=True)
    plan = bc.mirror_plan(user, acct)
    kinds = {(k, dst.name) for k, _, dst in plan}
    assert ("link", "skills") in kinds and ("link", "plugins") in kinds
    assert ("skip", "agents") in kinds
    assert ("copy", "CLAUDE.md") in kinds


def test_latest_transcript_case_insensitive(tmp_path):
    bc = _load()
    cwd = Path(r"C:\work\repo")
    folder = tmp_path / "projects" / bc.project_slug(cwd).lower()
    folder.mkdir(parents=True)
    old, new = folder / "a.jsonl", folder / "b.jsonl"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    os.utime(old, (1, 1))
    assert bc.latest_transcript(tmp_path, cwd) == new
    assert bc.latest_transcript(tmp_path, Path(r"C:\otro")) is None


def test_read_api_key_env_then_dotenv(tmp_path):
    bc = _load()
    (tmp_path / ".env").write_text('UI_API_KEY="bc-file"\n', encoding="utf-8")
    assert bc.read_api_key(tmp_path, {"BIPOLAR_API_KEY": "bc-env"}) == "bc-env"
    assert bc.read_api_key(tmp_path, {}) == "bc-file"
    assert bc.read_api_key(tmp_path / "nope", {}) == ""


def _pick_opener(payload=None, status=200, exc=None):
    class Resp:
        def __init__(self):
            self.status = status

        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=None):
        if exc:
            raise exc
        return Resp()

    return opener


def test_fetch_pick_failures_are_none():
    bc = _load()
    assert bc.fetch_pick("http://x", "k", opener=_pick_opener(exc=OSError("down"))) is None
    assert bc.fetch_pick("http://x", "k", opener=_pick_opener({"mode": "proxy"}, status=401)) is None
    assert bc.fetch_pick("http://x", "k", opener=_pick_opener({"mode": "proxy"})) == {"mode": "proxy"}


def test_launch_plan_modes():
    bc = _load()
    assert bc.launch_plan(None, "k", "http://b") == ({}, "plain")
    assert bc.launch_plan({"mode": "account", "account_dir": "C:/a", "agent_id": "claude-2"}, "k", "http://b") == ({"CLAUDE_CONFIG_DIR": "C:/a"}, "account")
    env, mode = bc.launch_plan({"mode": "proxy", "base_url": "http://evil"}, "k", "http://b")
    assert mode == "proxy" and env == {"ANTHROPIC_BASE_URL": "http://b", "ANTHROPIC_API_KEY": "k"}


def test_apply_mirror_never_overwrites_real_dir(tmp_path, monkeypatch):
    bc = _load()
    user, acct = tmp_path / "user", tmp_path / "acct"
    (user / "skills").mkdir(parents=True)
    (user / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://x"}}), encoding="utf-8")
    (acct / "skills").mkdir(parents=True)
    (acct / "skills" / "mine.md").write_text("propio", encoding="utf-8")
    links = []
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    warnings = bc.apply_mirror(user, acct, "cmd", link=lambda s, d: links.append((s, d)))
    assert (acct / "skills" / "mine.md").read_text(encoding="utf-8") == "propio"
    assert any("skills" in w for w in warnings) and links == []
    settings = json.loads((acct / "settings.json").read_text(encoding="utf-8"))
    assert "env" not in settings and settings["statusLine"]["command"] == "cmd"


def test_main_dry_run_account_mode(tmp_path, monkeypatch):
    bc = _load()
    acct = tmp_path / "accounts" / "claude-2"
    acct.mkdir(parents=True)
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    out = io.StringIO()
    opener = _pick_opener({"mode": "account", "agent_id": "claude-2", "account_dir": str(acct)})
    rc = bc.main(["--bc-dry-run", "--bc-no-mirror", "-p", "hola"], {"LITELLM_CONFIG_DIR": str(tmp_path), "BIPOLAR_API_KEY": "k"},
                 run=lambda *a, **k: 99, opener=opener, out=out)
    assert rc == 0
    report = json.loads(out.getvalue().strip().splitlines()[-1])
    assert report["mode"] == "account" and report["args"] == ["-p", "hola"] and "CLAUDE_CONFIG_DIR" in report["env"]


def test_main_plain_when_bipolar_down_and_passes_exit_code(tmp_path, monkeypatch):
    bc = _load()
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    seen = {}

    def run(argv, env=None):
        seen["argv"], seen["env"] = argv, env
        return 7

    rc = bc.main(["--version"], {"LITELLM_CONFIG_DIR": str(tmp_path), "ANTHROPIC_BASE_URL": "http://keep"}, run=run,
                 opener=_pick_opener(exc=OSError("down")), out=io.StringIO())
    assert rc == 7 and Path(seen["argv"][0]).stem.lower() == "claude" and seen["argv"][1:] == ["--version"]
    assert seen["env"]["ANTHROPIC_BASE_URL"] == "http://keep"


def test_main_account_mode_drops_inherited_proxy_vars(tmp_path, monkeypatch):
    bc = _load()
    acct = tmp_path / "accounts" / "claude-2"
    acct.mkdir(parents=True)
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    seen = {}
    bc.main(["--bc-no-mirror"], {"LITELLM_CONFIG_DIR": str(tmp_path), "ANTHROPIC_BASE_URL": "http://proxy", "BIPOLAR_API_KEY": "k"},
            run=lambda argv, env=None: seen.setdefault("env", env) and 0,
            opener=_pick_opener({"mode": "account", "agent_id": "claude-2", "account_dir": str(acct)}), out=io.StringIO())
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == str(acct) and "ANTHROPIC_BASE_URL" not in seen["env"]


def test_continue_without_previous_session_warns(tmp_path, monkeypatch):
    bc = _load()
    acct = tmp_path / "accounts" / "claude-3"
    acct.mkdir(parents=True)
    (tmp_path / "accounts" / ".last").write_text(json.dumps({"agent_id": "claude-2", "account_dir": str(tmp_path / "accounts" / "claude-2")}), encoding="utf-8")
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    out, seen = io.StringIO(), {}
    bc.main(["--bc-continue", "--bc-no-mirror"], {"LITELLM_CONFIG_DIR": str(tmp_path), "BIPOLAR_API_KEY": "k"},
            run=lambda argv, env=None: seen.setdefault("argv", argv) and 0,
            opener=_pick_opener({"mode": "account", "agent_id": "claude-3", "account_dir": str(acct)}), out=out)
    assert "--resume" not in seen["argv"] and "nueva" in out.getvalue()


def test_continue_copies_transcript_and_resumes(tmp_path, monkeypatch):
    bc = _load()
    prev, new = tmp_path / "accounts" / "claude-2", tmp_path / "accounts" / "claude-3"
    new.mkdir(parents=True)
    folder = prev / "projects" / bc.project_slug(Path.cwd())
    folder.mkdir(parents=True)
    (folder / "sess-123.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (tmp_path / "accounts" / ".last").write_text(json.dumps({"agent_id": "claude-2", "account_dir": str(prev)}), encoding="utf-8")
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    seen = {}
    bc.main(["--bc-continue", "--bc-no-mirror"], {"LITELLM_CONFIG_DIR": str(tmp_path), "BIPOLAR_API_KEY": "k"},
            run=lambda argv, env=None: seen.setdefault("argv", argv) and 0,
            opener=_pick_opener({"mode": "account", "agent_id": "claude-3", "account_dir": str(new)}), out=io.StringIO())
    assert seen["argv"][-2:] == ["--resume", "sess-123"]
    assert (new / "projects" / bc.project_slug(Path.cwd()) / "sess-123.jsonl").exists()


def test_dry_run_account_has_no_side_effects(tmp_path, monkeypatch):
    bc = _load()
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    acct = tmp_path / "accounts" / "claude-2"
    acct.mkdir(parents=True)
    monkeypatch.setattr(bc.Path, "home", lambda: home)
    out = io.StringIO()

    def run(*a, **k):
        raise AssertionError("dry-run no debe lanzar claude")

    rc = bc.main(["--bc-dry-run", "-p", "hola"], {"LITELLM_CONFIG_DIR": str(tmp_path), "BIPOLAR_API_KEY": "k"},
                 run=run, opener=_pick_opener({"mode": "account", "agent_id": "claude-2", "account_dir": str(acct)}), out=out)
    assert rc == 0
    assert list(acct.iterdir()) == []
    assert not (tmp_path / "accounts" / ".last").exists()
    report = json.loads(out.getvalue().strip().splitlines()[-1])
    assert report["mode"] == "account" and report["mirror"] is True and report["args"] == ["-p", "hola"]


def test_valid_account_dir_only_under_accounts(tmp_path):
    bc = _load()
    acct = tmp_path / "accounts" / "claude-2"
    acct.mkdir(parents=True)
    assert bc._valid_account_dir(str(acct), tmp_path) is not None
    assert bc._valid_account_dir(str(tmp_path / "home" / ".claude"), tmp_path) is None
    assert bc._valid_account_dir(str(tmp_path / "accounts"), tmp_path) is None
    assert bc._valid_account_dir("", tmp_path) is None
    assert bc.launch_plan({"mode": "account", "agent_id": "claude-2",
                           "account_dir": str(tmp_path / "home" / ".claude")}, "k", "http://b", tmp_path) == ({}, "plain")


def test_main_account_dir_outside_accounts_is_plain_without_writes(tmp_path, monkeypatch):
    bc = _load()
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    monkeypatch.setattr(bc.Path, "home", lambda: home)
    out = io.StringIO()

    def run(*a, **k):
        raise AssertionError("dry-run no debe lanzar claude")

    rc = bc.main(["--bc-dry-run", "-p", "hola"], {"LITELLM_CONFIG_DIR": str(tmp_path), "BIPOLAR_API_KEY": "k"},
                 run=run, opener=_pick_opener({"mode": "account", "agent_id": "claude-2",
                                               "account_dir": str(home / ".claude")}), out=out)
    assert rc == 0
    report = json.loads(out.getvalue().strip().splitlines()[-1])
    assert report["mode"] == "plain" and report["mirror"] is False
    assert "cuenta con carpeta inválida; se abre claude normal" in out.getvalue()
    assert not (tmp_path / "accounts" / ".last").exists()
    assert not (home / ".claude" / "settings.json").exists()


def test_continue_ignores_last_outside_accounts(tmp_path, monkeypatch):
    bc = _load()
    evil = tmp_path / "evil"
    folder = evil / "projects" / bc.project_slug(Path.cwd())
    folder.mkdir(parents=True)
    (folder / "sess-evil.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    new = tmp_path / "accounts" / "claude-3"
    new.mkdir(parents=True)
    (tmp_path / "accounts" / ".last").write_text(
        json.dumps({"agent_id": "claude-2", "account_dir": str(evil)}), encoding="utf-8")
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    out, seen = io.StringIO(), {}
    bc.main(["--bc-continue", "--bc-no-mirror"], {"LITELLM_CONFIG_DIR": str(tmp_path), "BIPOLAR_API_KEY": "k"},
            run=lambda argv, env=None: seen.setdefault("argv", argv) and 0,
            opener=_pick_opener({"mode": "account", "agent_id": "claude-3", "account_dir": str(new)}), out=out)
    assert "--resume" not in seen["argv"] and "nueva" in out.getvalue()
    assert not (new / "projects" / bc.project_slug(Path.cwd()) / "sess-evil.jsonl").exists()


def test_agent_id_regex():
    bc = _load()
    assert bc.AGENT_ID_RE.match("claude-2")
    assert not bc.AGENT_ID_RE.match("EVIL!!")
    assert not bc.AGENT_ID_RE.match("")


def test_main_invalid_agent_id_is_plain(tmp_path, monkeypatch):
    bc = _load()
    acct = tmp_path / "accounts" / "claude-2"
    acct.mkdir(parents=True)
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    assert bc.launch_plan({"mode": "account", "agent_id": "EVIL!!",
                           "account_dir": str(acct)}, "k", "http://b") == ({}, "plain")
    out = io.StringIO()
    rc = bc.main(["--bc-dry-run", "--bc-no-mirror"], {"LITELLM_CONFIG_DIR": str(tmp_path), "BIPOLAR_API_KEY": "k"},
                 run=lambda *a, **k: 99,
                 opener=_pick_opener({"mode": "account", "agent_id": "EVIL!!", "account_dir": str(acct)}), out=out)
    assert rc == 0
    report = json.loads(out.getvalue().strip().splitlines()[-1])
    assert report["mode"] == "plain"
    assert "inválid" in out.getvalue()


def test_statusline_command_quotes_agent_id():
    bc = _load()
    cmd = bc.statusline_command("python", Path("/r/bipolar-statusline.py"), "claude-2", None)
    assert '--agent-id "claude-2"' in cmd


def test_make_link_rejects_shell_metachars(tmp_path, monkeypatch):
    bc = _load()
    monkeypatch.setattr(bc.os, "name", "nt")
    calls = []
    monkeypatch.setattr(bc.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(OSError, match="ruta con caracteres no admitidos para mklink"):
        bc.make_link(tmp_path / "a&b", tmp_path / "dst")
    with pytest.raises(OSError, match="ruta con caracteres no admitidos para mklink"):
        bc.make_link(tmp_path / "src", tmp_path / "d|st")
    assert calls == []


def test_apply_mirror_link_error_warns_and_continues(tmp_path, monkeypatch):
    bc = _load()
    user, acct = tmp_path / "user", tmp_path / "acct"
    (user / "skills").mkdir(parents=True)
    (user / "agents").mkdir(parents=True)
    (user / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    attempted = []

    def link(src, dst):
        attempted.append(dst.name)
        if dst.name == "skills":
            raise OSError("ruta con caracteres no admitidos para mklink")

    warnings = bc.apply_mirror(user, acct, "cmd", link=link)
    assert any("skills" in w for w in warnings)
    assert "agents" in attempted
    settings = json.loads((acct / "settings.json").read_text(encoding="utf-8"))
    assert settings["statusLine"]["command"] == "cmd"


def test_copy_unlinks_symlink_destination(tmp_path, monkeypatch):
    bc = _load()
    user, acct = tmp_path / "user", tmp_path / "acct"
    user.mkdir(parents=True)
    acct.mkdir(parents=True)
    (user / "CLAUDE.md").write_text("reglas nuevas", encoding="utf-8")
    real = tmp_path / "real.md"
    real.write_text("original", encoding="utf-8")
    dst = acct / "CLAUDE.md"
    try:
        os.symlink(real, dst)
    except OSError:
        pytest.skip("sin permiso para crear symlinks")
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path / "home")
    bc.apply_mirror(user, acct, "cmd", link=lambda s, d: None)
    assert not dst.is_symlink()
    assert dst.read_text(encoding="utf-8") == "reglas nuevas"
    assert real.read_text(encoding="utf-8") == "original"
