"""Verificador: comandos sin shell, resolución relativa al workspace, timeout que mata el árbol."""
import asyncio
import sys
from pathlib import Path

import psutil
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


def test_parse_check_cd_prefix_sets_cwd(tmp_path):
    sub = tmp_path / "front-end"
    sub.mkdir()
    cwd, argv = verifier.parse_check(f'cd front-end && "{PY}" -c "print(1)"', tmp_path)
    assert cwd == sub.resolve()
    assert Path(argv[0]).resolve() == Path(sys.executable).resolve()
    assert argv[1:] == ["-c", "print(1)"]


def test_parse_check_without_cd_runs_in_workspace(tmp_path):
    cwd, _ = verifier.parse_check(f'"{PY}" -c "print(1)"', tmp_path)
    assert cwd == tmp_path


@pytest.mark.parametrize("bad", ["cd ../x && " + PY + " -c 1", "cd C:/abs && " + PY + " -c 1", "cd front-end/../../x && " + PY + " -c 1"])
def test_parse_check_rejects_subfolder_outside_workspace(bad, tmp_path):
    with pytest.raises(verifier.InvalidCommand, match="subcarpeta fuera del workspace"):
        verifier.parse_check(bad, tmp_path)


def test_parse_check_rejects_missing_subfolder(tmp_path):
    with pytest.raises(verifier.InvalidCommand, match="subcarpeta no existe"):
        verifier.parse_check(f'cd nope && "{PY}" -c 1', tmp_path)


@pytest.mark.asyncio
async def test_run_checks_runs_in_subfolder(tmp_path):
    sub = tmp_path / "front-end"
    sub.mkdir()
    results = await verifier.run_checks([f'cd front-end && "{PY}" -c "import os; print(os.getcwd())"'], tmp_path, timeout_s=30)
    assert results[0].ok and sub.resolve().as_posix().lower() in results[0].output_tail.lower().replace("\\", "/")


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
@pytest.mark.parametrize("output, locked", [
    ("error MSB3027: Could not copy \"obj/App.dll\" to \"bin/App.dll\". The file is being used by another process.", True),
    ("Program.cs(12,5): error CS0103: The name 'foo' does not exist in the current context", False),
])
async def test_failed_check_flags_locked_build_files(tmp_path, output, locked):
    script = tmp_path / "build.py"
    script.write_text(f"import sys\nprint({output!r})\nsys.exit(1)\n", encoding="utf-8")
    results = await verifier.run_checks([f'"{PY}" build.py'], tmp_path, timeout_s=30)
    assert results[0].returncode == 1
    assert bool(results[0].environment_error) is locked


def test_environment_error_ignores_case_and_spanish_message():
    assert verifier.environment_error("El archivo ESTÁ SIENDO UTILIZADO POR OTRO PROCESO") == verifier.LOCKED_FILES
    assert verifier.environment_error("Error: EBUSY: resource busy or locked") == verifier.LOCKED_FILES
    assert verifier.environment_error("FAILED tests/test_x.py::test_y") == ""


@pytest.mark.asyncio
async def test_spawn_os_error_is_a_failed_check(tmp_path, monkeypatch):
    async def boom(*args, **kwargs):
        raise PermissionError("acceso denegado")

    monkeypatch.setattr(verifier.asyncio, "create_subprocess_exec", boom)
    results = await verifier.run_checks([f'"{PY}" -c "print(1)"'], tmp_path, timeout_s=5)
    assert "acceso denegado" in results[0].error and not verifier.checks_passed(results)


@pytest.mark.asyncio
async def test_cancel_during_verify_kills_child(tmp_path, monkeypatch):
    procs = []
    orig = asyncio.create_subprocess_exec

    async def wrapper(*args, **kwargs):
        proc = await orig(*args, **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr(verifier.asyncio, "create_subprocess_exec", wrapper)
    task = asyncio.create_task(
        verifier.run_checks([f'"{PY}" -c "import time; time.sleep(60)"'], tmp_path, timeout_s=60))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert procs, "no se creó el subproceso"
    pid = procs[0].pid
    for _ in range(50):
        if not psutil.pid_exists(pid):
            break
        await asyncio.sleep(0.1)
    try:
        assert not psutil.pid_exists(pid)
    finally:
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass


def test_verify_env_keeps_host_toolchain_vars_and_drops_secrets():
    from app.services.cli_agents.adapters import verify_env
    base = {"ProgramFiles(x86)": r"C:\Program Files (x86)", "VSINSTALLDIR": r"C:\VS", "PATH": r"C:\bin",
            "ANTHROPIC_API_KEY": "secreto", "GITHUB_TOKEN": "secreto", "ANTHROPIC_BASE_URL": "http://x",
            "AWS_SECRET_ACCESS_KEY": "secreto", "DATABASE_URL": "postgres://u:p@h/db"}
    env = verify_env(base)
    assert env["ProgramFiles(x86)"] == r"C:\Program Files (x86)" and env["VSINSTALLDIR"] == r"C:\VS"
    assert env["PATH"] == r"C:\bin"
    for leaked in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "ANTHROPIC_BASE_URL", "AWS_SECRET_ACCESS_KEY", "DATABASE_URL"):
        assert leaked not in env
