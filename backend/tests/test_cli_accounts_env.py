"""Entorno de cuenta: variable de carpeta por adaptador, login por archivo y flags de aislamiento."""
from pathlib import Path

from app.models.smart import CliAgent
from app.services.cli_agents import adapters
from app.services.cli_agents.adapters import ClaudeAdapter, account_env, account_has_login, child_env


def test_account_env_per_adapter(tmp_path):
    claude = CliAgent(id="claude-2", account_dir=str(tmp_path))
    codex = CliAgent(id="codex-2", account_dir=str(tmp_path))
    assert account_env(claude) == {"CLAUDE_CONFIG_DIR": str(tmp_path)}
    assert account_env(codex) == {"CODEX_HOME": str(tmp_path)}
    assert account_env(CliAgent(id="claude")) == {}
    assert account_env(CliAgent(id="muse-2", account_dir=str(tmp_path))) == {}


def test_parent_account_vars_are_not_inherited():
    env = child_env({"CLAUDE_CONFIG_DIR": "C:/user/.claude-work", "PATH": "x"})
    assert "CLAUDE_CONFIG_DIR" not in env


def test_extra_account_var_wins_over_blocklist():
    env = child_env({"CLAUDE_CONFIG_DIR": "C:/user/.claude-work", "PATH": "x"}, {"CLAUDE_CONFIG_DIR": "C:/acc"})
    assert env["CLAUDE_CONFIG_DIR"] == "C:/acc"


def test_account_has_login_checks_file_only(tmp_path):
    agent = CliAgent(id="claude-2", account_dir=str(tmp_path))
    assert account_has_login(agent) is False
    (tmp_path / ".credentials.json").write_text("{}", encoding="utf-8")
    assert account_has_login(agent) is True
    assert account_has_login(CliAgent(id="cursor-2", account_dir=str(tmp_path))) is None
    assert account_has_login(CliAgent(id="claude")) is None


def test_claude_jobs_ignore_user_settings_and_mcp(tmp_path):
    spec = ClaudeAdapter().build(CliAgent(id="claude"), "claude.exe", "job1", "tarea", "", tmp_path, "standard", 600)
    argv = spec.argv
    assert argv[argv.index("--setting-sources") + 1] == "project,local"
    assert "--strict-mcp-config" in argv


def test_supports_accounts():
    assert adapters.supports_accounts("claude") and adapters.supports_accounts("codex")
    assert not adapters.supports_accounts("muse") and not adapters.supports_accounts("copilot")


def test_cursor_accounts_are_not_supported_to_keep_its_deny_list(tmp_path):
    assert not adapters.supports_accounts("cursor")
    assert account_env(CliAgent(id="cursor-2", account_dir=str(tmp_path))) == {}
