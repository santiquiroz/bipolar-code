"""Verificador: corre comandos sin shell, con timeout que mata el árbol."""
import asyncio
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psutil

from app.services.cli_agents.adapters import exe_argv, verify_env

OUTPUT_TAIL_CHARS = 8000


class InvalidCommand(ValueError):
    pass


CD_PREFIX_RE = re.compile(r"^cd\s+(\S+)\s+&&\s+(.+)$")
OUTSIDE_WORKSPACE = "subcarpeta fuera del workspace"


def parse_check(command: str, workspace: Path) -> tuple[Path, list[str]]:
    """Devuelve (cwd, argv). Prefijo opcional `cd <subcarpeta> && <comando>`; `&&`, `;` y `|` sin ese prefijo
    no son operadores (sin shell): viajan como argumentos literales, como siempre."""
    cwd = workspace
    match = CD_PREFIX_RE.match(command.strip())
    if match:
        cwd, command = _subfolder(match.group(1), workspace), match.group(2)
    text = command.replace("\\", "/") if sys.platform == "win32" else command
    tokens = shlex.split(text, posix=True)
    if not tokens:
        raise InvalidCommand("comando vacío")
    token = tokens[0]
    if "/" in token:
        found = shutil.which(str(cwd / token))
    else:
        found = shutil.which(token)
    if not found:
        raise InvalidCommand(f"no se encontró el ejecutable: {token}")
    return cwd, exe_argv(found) + tokens[1:]


def parse_command(command: str, workspace: Path) -> list[str]:
    return parse_check(command, workspace)[1]


def _subfolder(raw: str, workspace: Path) -> Path:
    rel = raw.replace("\\", "/")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or ".." in rel.split("/"):
        raise InvalidCommand(OUTSIDE_WORKSPACE)
    root = workspace.resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise InvalidCommand(OUTSIDE_WORKSPACE)
    if not target.is_dir():
        raise InvalidCommand(f"subcarpeta no existe: {rel}")
    return target


@dataclass
class CheckResult:
    command: str
    returncode: Optional[int]
    duration_s: float
    output_tail: str
    timed_out: bool = False
    error: str = ""
    environment_error: str = ""

    @property
    def ok(self) -> bool:
        return not self.timed_out and not self.error and self.returncode == 0


LOCKED_FILE_MARKERS = ("msb3027", "msb3021", "being used by another process",
                       "está siendo utilizado por otro proceso", "ebusy", "resource busy or locked")
LOCKED_FILES = "archivos del build bloqueados por otro proceso"


def environment_error(output: str) -> str:
    """Fallos que no se corrigen en el código: otro proceso tiene bloqueados los archivos del build."""
    text = output.lower()
    return LOCKED_FILES if any(marker in text for marker in LOCKED_FILE_MARKERS) else ""


def _kill_tree_pid(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except psutil.Error:
        pass


async def _run_one(command: str, workspace: Path, timeout_s: int) -> CheckResult:
    start = time.monotonic()
    try:
        cwd, argv = parse_check(command, workspace)
    except InvalidCommand as e:
        return CheckResult(command, None, 0.0, "", error=str(e))
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), env=verify_env(os.environ),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kwargs)
    except OSError as e:
        return CheckResult(command, None, time.monotonic() - start, "", error=f"no se pudo ejecutar: {e}")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
    except asyncio.TimeoutError:
        _kill_tree_pid(proc.pid)
        await proc.wait()
        return CheckResult(command, proc.returncode, time.monotonic() - start, "", timed_out=True)
    except asyncio.CancelledError:
        _kill_tree_pid(proc.pid)
        try:
            await proc.wait()
        except Exception:
            pass
        raise
    tail = (out or b"").decode("utf-8", "replace")[-OUTPUT_TAIL_CHARS:]
    env_error = environment_error(tail) if proc.returncode != 0 else ""
    return CheckResult(command, proc.returncode, time.monotonic() - start, tail, environment_error=env_error)


async def run_checks(commands: list[str], workspace: Path, timeout_s: int) -> list[CheckResult]:
    results: list[CheckResult] = []
    for command in commands:
        result = await _run_one(command, workspace, timeout_s)
        results.append(result)
        if not result.ok:
            break
    return results


def checks_passed(results: list[CheckResult]) -> bool:
    return all(r.ok for r in results)
