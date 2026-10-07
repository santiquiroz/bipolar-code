"""Lanzador bipolar-claude: núcleo puro (espejo, settings, slug, transcript)."""
import importlib.util
import json
import os
from pathlib import Path

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
    assert "--agent-id claude-2" in cmd and '--then "node mine.js"' in cmd
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
