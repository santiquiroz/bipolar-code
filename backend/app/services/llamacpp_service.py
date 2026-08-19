"""
Gestión del servidor llama.cpp local (llama-server): detección de GPUs,
tensor-split proporcional a VRAM libre, start/stop/status con PID file.
Espejo del patrón usado para litellm en providers_service.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import psutil

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.provider import Provider

log = get_logger(__name__)

DEFAULT_PORT = 4002
# Heurística KV cache: ~40 KiB/token cubre modelos GQA 30-80B en q8 KV.
_KV_MIB_PER_TOKEN = 0.04
_MODEL_OVERHEAD_FACTOR = 1.15

_DEVICE_LINE = re.compile(
    r"^\s*([A-Za-z]+)(\d+):\s+(.+?)\s+\((\d+)\s+MiB,\s+(\d+)\s+MiB free\)"
)


def _config_dir() -> Path:
    return Path(get_settings().litellm_config_dir)


def _pid_file() -> Path:
    return _config_dir() / "llamacpp.pid"


def resolve_exe(provider: Provider) -> str:
    configured = str(provider.local_launch.get("exe_path", "")).strip()
    if configured:
        return configured
    found = shutil.which("llama-server")
    return found or ""


def port_from_api_base(api_base: str) -> int:
    match = re.search(r":(\d+)", api_base.split("//")[-1])
    return int(match.group(1)) if match else DEFAULT_PORT


def parse_devices(output: str) -> list[dict]:
    devices = []
    for line in output.splitlines():
        m = _DEVICE_LINE.match(line)
        if not m:
            continue
        devices.append({
            "index": int(m.group(2)),
            "backend": m.group(1),
            "name": m.group(3).strip(),
            "vram_total_mib": int(m.group(4)),
            "vram_free_mib": int(m.group(5)),
        })
    return devices


def list_devices(exe: str) -> list[dict]:
    if not exe:
        return []
    try:
        result = subprocess.run(
            [exe, "--list-devices"],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return parse_devices(result.stdout + "\n" + result.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("list_devices_failed", exe=exe, error=str(e))
        return []


def compute_tensor_split(devices: list[dict]) -> list[float]:
    frees = [max(0, d["vram_free_mib"]) for d in devices]
    total = sum(frees)
    if total <= 0:
        return []
    return [round(f / total, 3) for f in frees]


def estimate_fit(model_path: str, ctx_size: int, devices: list[dict]) -> dict:
    path = Path(model_path)
    model_mib = path.stat().st_size / (1024 * 1024) if path.exists() else 0
    needed = int(model_mib * _MODEL_OVERHEAD_FACTOR + ctx_size * _KV_MIB_PER_TOKEN)
    available = sum(d["vram_free_mib"] for d in devices)
    return {
        "fits": needed <= available if model_mib > 0 else False,
        "needed_mib": needed,
        "available_mib": available,
    }


def build_cmdline(provider: Provider, devices: list[dict]) -> list[str]:
    launch = provider.local_launch
    exe = resolve_exe(provider)
    port = port_from_api_base(provider.api_base)
    ctx_size = int(launch.get("ctx_size", 32768))
    ngl = int(launch.get("ngl", 999))
    split_mode = str(launch.get("split_mode", "layer"))

    cmd = [
        exe,
        "--model", str(launch.get("model_path", "")),
        "--ctx-size", str(ctx_size),
        "--n-gpu-layers", str(ngl),
        "--host", str(launch.get("host", "127.0.0.1")),
        "--port", str(port),
        "--jinja",
        "--slots",
    ]

    if len(devices) > 1:
        cmd += ["--split-mode", split_mode]
        split = launch.get("tensor_split", "auto")
        ratios = compute_tensor_split(devices) if split == "auto" else [float(x) for x in split]
        if ratios:
            cmd += ["--tensor-split", ",".join(str(r) for r in ratios)]

    extra = launch.get("extra_args", [])
    if isinstance(extra, list):
        cmd += [str(a) for a in extra]
    return cmd


async def get_status(provider: Provider) -> dict:
    import httpx

    pid = _read_pid()
    running = pid is not None and psutil.pid_exists(pid)
    port = port_from_api_base(provider.api_base)
    status = {
        "running": running,
        "pid": pid if running else None,
        "port": port,
        "model_path": provider.local_launch.get("model_path") or None,
        "healthy": False,
        "busy_slots": -1,
    }
    if not running:
        return status

    base = provider.api_base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
        try:
            resp = await client.get(f"{base}/health")
            status["healthy"] = resp.status_code == 200
        except httpx.HTTPError:
            return status
        try:
            resp = await client.get(f"{base}/slots")
            if resp.status_code == 200:
                slots = resp.json()
                status["busy_slots"] = sum(1 for s in slots if s.get("is_processing"))
        except (httpx.HTTPError, ValueError):
            pass
    return status


async def start_llamacpp(provider: Provider) -> dict:
    exe = resolve_exe(provider)
    if not exe:
        raise ValueError(
            "llama-server no encontrado. Instala un release Vulkan de llama.cpp "
            "y configura exe_path en el provider."
        )
    model_path = str(provider.local_launch.get("model_path", "")).strip()
    if not model_path or not Path(model_path).exists():
        raise ValueError(f"Modelo GGUF no encontrado: '{model_path}'")

    current = await get_status(provider)
    if current["running"]:
        return {**current, "already_running": True}

    devices = list_devices(exe)
    fit = estimate_fit(model_path, int(provider.local_launch.get("ctx_size", 32768)), devices)
    if devices and not fit["fits"]:
        log.warning("model_may_not_fit", **fit)

    cmd = build_cmdline(provider, devices)
    out_log = _config_dir() / "llamacpp-out.log"
    err_log = _config_dir() / "llamacpp-err.log"
    _spawn_detached(cmd, out_log, err_log)
    log.info("llamacpp_started", cmd=" ".join(cmd), devices=len(devices), fit=fit)
    return {"started": True, "cmdline": cmd, "devices": devices, "fit": fit}


def _spawn_detached(cmd: list[str], out_log: Path, err_log: Path) -> None:
    with open(out_log, "ab") as fout, open(err_log, "ab") as ferr:
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(
                cmd, stdout=fout, stderr=ferr, stdin=subprocess.DEVNULL,
                creationflags=flags,
            )
        else:
            proc = subprocess.Popen(
                cmd, stdout=fout, stderr=ferr, stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
    _pid_file().write_text(str(proc.pid), encoding="utf-8")


async def stop_llamacpp(provider: Provider, force: bool = False) -> dict:
    status = await get_status(provider)
    if status["running"] and status["busy_slots"] > 0 and not force:
        raise RuntimeError(
            f"llama-server tiene {status['busy_slots']} slot(s) procesando. "
            "Usa force=true para detenerlo de todos modos."
        )

    killed = 0
    pid = _read_pid()
    if pid is not None:
        try:
            psutil.Process(pid).kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            log.warning("llamacpp_pid_kill_failed", pid=pid, error=str(e))
        _pid_file().unlink(missing_ok=True)

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name.startswith("llama-server"):
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    log.info("llamacpp_stopped", killed=killed, forced=force)
    return {"stopped": True, "killed": killed}


def _read_pid() -> int | None:
    try:
        return int(_pid_file().read_text().strip())
    except (FileNotFoundError, ValueError):
        return None
