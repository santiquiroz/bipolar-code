"""Verificador: corre comandos sin shell, con timeout que mata el árbol."""
import asyncio
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psutil

from app.services.cli_agents.adapters import child_env, exe_argv

OUTPUT_TAIL_CHARS = 8000


class InvalidCommand(ValueError):
    pass


def parse_command(command: str, workspace: Path) -> list[str]:
    text = command.replace("\\", "/") if sys.platform == "win32" else command
    tokens = shlex.split(text, posix=True)
    if not tokens:
        raise InvalidCommand("comando vacío")
    token = tokens[0]
    if "/" in token:
        found = shutil.which(str(workspace / token))
    else:
        found = shutil.which(token)
    if not found:
        raise InvalidCommand(f"no se encontró el ejecutable: {token}")
    return exe_argv(found) + tokens[1:]


@dataclass
class CheckResult:
    command: str
    returncode: Optional[int]
    duration_s: float
    output_tail: str
    timed_out: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.timed_out and not self.error and self.returncode == 0


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
        argv = parse_command(command, workspace)
    except InvalidCommand as e:
        return CheckResult(command, None, 0.0, "", error=str(e))
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(workspace), env=child_env(os.environ),
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
    tail = (out or b"").decode("utf-8", "replace")[-OUTPUT_TAIL_CHARS:]
    return CheckResult(command, proc.returncode, time.monotonic() - start, tail)


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
