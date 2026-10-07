"""Modo solo lectura por adaptador: flags exactos y rechazo donde no hay garantía."""
import json
from pathlib import Path

import pytest

from app.models.smart import CliAgent
from app.services.cli_agents import adapters
from app.services.cli_agents.adapters import AdapterUnsafe, ADAPTERS


def _build(base, tmp_path, read_only, monkeypatch=None):
    return ADAPTERS[base].build(CliAgent(id=base), f"{base}.exe", "job1", "revisa esto", "", tmp_path, "standard", 600, read_only=read_only)


def test_claude_read_only_uses_plan_mode_and_blocks_edit_tools(tmp_path):
    argv = _build("claude", tmp_path, True).argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    blocked = argv[argv.index("--disallowedTools") + 1].split(",")
    assert {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"} <= set(blocked)
    argv_rw = _build("claude", tmp_path, False).argv
    assert argv_rw[argv_rw.index("--permission-mode") + 1] == "acceptEdits"


def test_codex_read_only_sandbox(tmp_path):
    spec = _build("codex", tmp_path, True)
    assert spec.argv[spec.argv.index("--sandbox") + 1] == "read-only"
    assert b"solo lectura" in spec.stdin_payload


def test_muse_read_only_flags(tmp_path):
    argv = _build("muse", tmp_path, True).argv
    assert "--disable-write" in argv and "--disable-shell" in argv


@pytest.mark.parametrize("base", ["copilot", "antigravity"])
def test_unsupported_adapters_refuse_read_only(base, tmp_path):
    with pytest.raises(AdapterUnsafe, match="read_only_unsupported"):
        _build(base, tmp_path, True)


def test_supports_read_only_table():
    assert adapters.supports_read_only("claude") and adapters.supports_read_only("codex")
    assert adapters.supports_read_only("muse") and adapters.supports_read_only("deepseek") and adapters.supports_read_only("cursor")
    assert not adapters.supports_read_only("copilot") and not adapters.supports_read_only("antigravity")


def test_deepseek_read_only_permission_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    spec = _build("deepseek", tmp_path, True)
    assert spec.env["DSH_PERMISSION_MODE"] == "read-only"
    assert b"solo lectura" in spec.stdin_payload
    spec_rw = _build("deepseek", tmp_path, False)
    assert spec_rw.env["DSH_PERMISSION_MODE"] == "workspace-write"


def test_cursor_read_only_ask_mode_without_force(tmp_path, monkeypatch):
    config_dir = tmp_path / "cr"
    monkeypatch.setenv("CURSOR_RESCUE_HOME", str(config_dir))
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "cli-config.json").write_text(json.dumps({
        "permissions": {"deny": list(adapters.CURSOR_CRITICAL_DENY)},
    }), encoding="utf-8")
    argv = _build("cursor", tmp_path, True).argv
    assert argv[argv.index("--mode") + 1] == "ask"
    assert "--force" not in argv
    argv_rw = _build("cursor", tmp_path, False).argv
    assert "--force" in argv_rw and "--mode" not in argv_rw
