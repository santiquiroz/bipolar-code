"""Verificador: comandos sin shell, resolución relativa al workspace, timeout que mata el árbol."""
import sys
from pathlib import Path

import pytest

from app.services.cli_agents import verifier

PY = sys.executable.replace("\\", "/")


def test_parse_command_resolves_path_and_splits(tmp_path):
    argv = verifier.parse_command(f'"{PY}" -c "print(1)"', tmp_path)
    assert Path(argv[0]).resolve() == Path(sys.executable).resolve()
    assert argv[1:] == ["-c", "print(1)"]


def test_parse_command_relative_to_workspace(tmp_path):
    tool = tmp_path / "tools" / ("run.cmd" if sys.platform == "win32" else "run.sh")
    tool.parent.mkdir()
    tool.write_text("@echo ok\n" if sys.platform == "win32" else "#!/bin/sh\necho ok\n", encoding="utf-8")
    if sys.platform != "win32":
        tool.chmod(0o755)
    argv = verifier.parse_command(f"tools/{tool.name} --flag", tmp_path)
    assert str(tool.resolve()).lower() in " ".join(argv).lower().replace("/", "\\") or str(tool) in " ".join(argv)
    assert argv[-1] == "--flag"


@pytest.mark.parametrize("bad", ["", "   ", "definitely-not-a-real-binary-xyz --x"])
def test_parse_command_rejects(bad, tmp_path):
    with pytest.raises(verifier.InvalidCommand):
        verifier.parse_command(bad, tmp_path)


@pytest.mark.asyncio
async def test_run_checks_stops_at_first_failure(tmp_path):
    results = await verifier.run_checks(
        [f'"{PY}" -c "print(\'uno\')"', f'"{PY}" -c "import sys; print(\'dos\'); sys.exit(3)"', f'"{PY}" -c "print(\'tres\')"'],
        tmp_path, timeout_s=30)
    assert [r.returncode for r in results] == [0, 3]
    assert "dos" in results[1].output_tail
    assert not verifier.checks_passed(results)


@pytest.mark.asyncio
async def test_run_checks_timeout_kills_process(tmp_path):
    results = await verifier.run_checks([f'"{PY}" -c "import time; time.sleep(60)"'], tmp_path, timeout_s=1)
    assert results[0].timed_out and not results[0].ok


@pytest.mark.asyncio
async def test_invalid_command_is_a_failed_check(tmp_path):
    results = await verifier.run_checks(["definitely-not-a-real-binary-xyz"], tmp_path, timeout_s=5)
    assert results[0].error and not verifier.checks_passed(results)


def test_checks_passed_empty_is_true():
    assert verifier.checks_passed([])


@pytest.mark.asyncio
async def test_spawn_os_error_is_a_failed_check(tmp_path, monkeypatch):
    async def boom(*args, **kwargs):
        raise PermissionError("acceso denegado")

    monkeypatch.setattr(verifier.asyncio, "create_subprocess_exec", boom)
    results = await verifier.run_checks([f'"{PY}" -c "print(1)"'], tmp_path, timeout_s=5)
    assert "acceso denegado" in results[0].error and not verifier.checks_passed(results)
