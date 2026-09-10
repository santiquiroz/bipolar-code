"""
Detección y sondeo de agentes CLI: binario, versión y, cuando el CLI lo permite sin
gastar cuota, autenticación y cuota (agy expone /usage y /model en print mode).
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from app.core.logging import get_logger
from app.core.quota_signals import detect_signal
from app.models.delegate import AgentStatus
from app.models.smart import CliAgent
from app.services import health_service
from app.services.cli_agents.adapters import BINARIES, AntigravityAdapter, child_env, exe_argv

log = get_logger(__name__)

PROBE_TTL_SECONDS = 600
PROBE_TIMEOUT_SECONDS = 15
LOW_QUOTA_FRACTION = 0.02
_cache: dict[str, tuple[float, AgentStatus]] = {}
_running: dict[str, int] = {}


def _home() -> Path:
    return Path.home()


def known_paths(agent_id: str) -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA") or (_home() / "AppData" / "Local"))
    return {
        "claude": [_home() / ".local" / "bin" / "claude.exe", _home() / ".local" / "bin" / "claude",
                   local / "Programs" / "claude" / "claude.exe"],
        "antigravity": [_home() / ".gemini" / "bin" / "agy.exe", local / "agy" / "bin" / "agy.exe",
                        _home() / ".local" / "bin" / "agy"],
        "codex": [Path(os.environ.get("APPDATA") or "") / "npm" / "codex.cmd"],
        "copilot": [local / "Microsoft" / "WinGet" / "Links" / "copilot.exe"],
        "ollama": [local / "Programs" / "Ollama" / "ollama.exe"],
    }.get(agent_id, [])


def resolve_exe(agent: CliAgent) -> str:
    if agent.exe_path:
        return agent.exe_path if Path(agent.exe_path).exists() else ""
    found = shutil.which(BINARIES[agent.id])
    if found and not found.lower().endswith(".ps1"):
        return found
    for candidate in known_paths(agent.id):
        if candidate and candidate.exists():
            return str(candidate)
    return found or ""


async def run_capture(argv: list[str], timeout: float = PROBE_TIMEOUT_SECONDS, cwd: Optional[str] = None,
                      env: Optional[dict] = None) -> tuple[Optional[int], str, str]:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, env=env or child_env(os.environ), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=flags,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return None, "", "timeout"
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _version_from(text: str) -> str:
    for token in text.replace("\n", " ").split():
        if token[0].isdigit() and "." in token:
            return token.strip("v,")
    return text.strip()[:40]


async def _probe_version(exe: str) -> tuple[str, str]:
    rc, out, err = await run_capture(exe_argv(exe) + ["--version"])
    if rc == 0:
        return _version_from(out or err), ""
    return "", (err or out or f"exit {rc}").strip()[:200]


async def _probe_codex_auth(exe: str) -> str:
    rc, out, err = await run_capture(exe_argv(exe) + ["login", "status"])
    text = (out + err).lower()
    if rc == 0 and "logged in" in text and "not logged" not in text:
        return "ok"
    if detect_signal(out + err) and detect_signal(out + err).kind == "auth":
        return "auth_error"
    return "unknown"


def _parse_agy_usage(payload: dict) -> dict:
    pools = {}
    for group in ((payload.get("command") or {}).get("data") or {}).get("groups") or []:
        pool = "gemini" if "gemini" in str(group.get("name", "")).lower() else "claude"
        for bucket in group.get("buckets") or []:
            pools[pool] = {
                "remaining_fraction": bucket.get("remaining_fraction"),
                "reset_time": bucket.get("reset_time"),
                "name": group.get("name"),
            }
    return pools


def _apply_agy_pools(pools: dict) -> None:
    for pool, info in pools.items():
        key = f"cli:antigravity#{pool}"
        fraction = info.get("remaining_fraction")
        reset = info.get("reset_time")
        if fraction is None:
            continue
        if fraction <= LOW_QUOTA_FRACTION and reset:
            try:
                until = datetime.fromisoformat(str(reset).replace("Z", "+00:00"))
            except ValueError:
                continue
            health_service.set_exhausted_until(key, until, f"{pool} pool {fraction:.1%}")
        elif health_service.get(key).last_signal == "quota_exhausted":
            health_service.reset(key)


async def _probe_antigravity(exe: str, status: AgentStatus) -> None:
    status.deny_list_present = AntigravityAdapter().deny_list_present()
    rc, out, err = await run_capture(exe_argv(exe) + ["-p", "/usage", "--output-format", "json", "--print-timeout", "30s"], timeout=40)
    signal = detect_signal(out + err)
    if signal and signal.kind == "auth":
        status.auth = "auth_error"
        return
    try:
        payload = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    except ValueError:
        payload = {}
    pools = _parse_agy_usage(payload)
    if pools:
        status.auth = "ok"
        status.quota = pools
        _apply_agy_pools(pools)
    rc, out, _ = await run_capture(exe_argv(exe) + ["-p", "/model", "--output-format", "text", "--print-timeout", "30s"], timeout=40)
    if rc == 0 and out.strip():
        status.default_model = out.strip().splitlines()[0].split("\t")[0]


async def _probe_ollama(status: AgentStatus, api_base: str = "http://127.0.0.1:11434") -> None:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{api_base}/api/tags")
            resp.raise_for_status()
            tags = [m.get("name", "") for m in resp.json().get("models", [])]
        capped = [t for t in tags if t.split(":")[0].endswith(("-32k", "-mechanical"))]
        status.auth = "ok"
        status.quota = {"models": tags, "context_capped": capped}
        if not status.default_model and capped:
            status.default_model = capped[0]
    except Exception as e:
        status.error = f"ollama unreachable: {e.__class__.__name__}"


async def probe(agent: CliAgent, force: bool = False) -> AgentStatus:
    hit = _cache.get(agent.id)
    if hit and not force and time.monotonic() - hit[0] < PROBE_TTL_SECONDS:
        return _with_runtime(hit[1], agent)
    status = AgentStatus(id=agent.id, checked_at=datetime.now(timezone.utc).isoformat(), default_model=agent.default_model)
    exe = resolve_exe(agent)
    status.installed = bool(exe)
    status.exe = exe
    try:
        if agent.id == "ollama":
            await _probe_ollama(status)
            status.installed = status.installed or status.auth == "ok"
        elif exe:
            status.version, status.error = await _probe_version(exe)
            if agent.id == "codex":
                status.auth = await _probe_codex_auth(exe)
            elif agent.id == "antigravity":
                await _probe_antigravity(exe, status)
    except Exception as e:  # el sondeo nunca debe tumbar la API
        status.error = str(e)[:200]
        log.warning("agent_probe_failed", agent=agent.id, error=str(e))
    _cache[agent.id] = (time.monotonic(), status)
    return _with_runtime(status, agent)


def _with_runtime(status: AgentStatus, agent: CliAgent) -> AgentStatus:
    key = AntigravityAdapter.pool_key(agent.default_model) if agent.id == "antigravity" else agent.key
    health = health_service.get(key)
    return status.model_copy(update={
        "state": health.state, "seconds_left": health_service.seconds_left(key),
        "last_signal": health.last_signal, "last_excerpt": health.last_excerpt,
        "running": _running.get(agent.id, 0),
    })


def invalidate(agent_id: Optional[str] = None) -> None:
    if agent_id:
        _cache.pop(agent_id, None)
    else:
        _cache.clear()


def running_count(agent_id: str) -> int:
    return _running.get(agent_id, 0)


def adjust_running(agent_id: str, delta: int) -> None:
    _running[agent_id] = max(0, _running.get(agent_id, 0) + delta)
