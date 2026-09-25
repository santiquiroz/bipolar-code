"""
Identidad de procesos lanzados por bipolar: un PID file o un proceso con
nombre parecido no basta, porque Windows reutiliza PIDs tras un reboot y el
rig corre otros llama-server/litellm que no son nuestros.
"""
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class ProcessIdentity:
    name_prefixes: tuple[str, ...]
    port: int
    cmdline_marker: str = ""


def cmdline_has_port(cmdline: list[str], port: int) -> bool:
    wanted = str(port)
    if f"--port={wanted}" in cmdline:
        return True
    return any(flag == "--port" and value == wanted for flag, value in zip(cmdline, cmdline[1:]))


def matches_identity(name: str, cmdline: list[str], identity: ProcessIdentity) -> bool:
    if not name.lower().startswith(identity.name_prefixes):
        return False
    if identity.cmdline_marker not in " ".join(cmdline).lower():
        return False
    return cmdline_has_port(cmdline, identity.port)


def owned_process(pid: int, identity: ProcessIdentity) -> psutil.Process | None:
    try:
        proc = psutil.Process(pid)
        owned = matches_identity(proc.name() or "", proc.cmdline() or [], identity)
    except psutil.Error:
        return None
    return proc if owned else None


def find_owned_processes(identity: ProcessIdentity) -> list[psutil.Process]:
    return [
        proc for proc in psutil.process_iter(["pid", "name", "cmdline"])
        if matches_identity(proc.info.get("name") or "", proc.info.get("cmdline") or [], identity)
    ]


def kill_quietly(proc: psutil.Process) -> bool:
    try:
        proc.kill()
        return True
    except psutil.Error:
        return False
