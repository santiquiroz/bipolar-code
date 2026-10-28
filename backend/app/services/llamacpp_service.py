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
from app.services.process_identity import (
    ProcessIdentity,
    find_owned_processes,
    kill_quietly,
    owned_process,
)

log = get_logger(__name__)

DEFAULT_PORT = 4002
_LLAMA_SERVER_NAMES = ("llama-server",)
# Heurística KV cache: ~40 KiB/token cubre modelos GQA 30-80B en q8 KV.
_KV_MIB_PER_TOKEN = 0.04
_MODEL_OVERHEAD_FACTOR = 1.15
# Las iGPU reportan memoria compartida como VRAM y acapararían el tensor-split.
_INTEGRATED_GPU_PATTERNS = (
    r"radeon\(tm\) graphics$",
    r"radeon graphics$",
    r"\b(uhd|iris|hd) graphics\b",
    r"radeon \d{3}m\b",
    r"vega \d+ graphics",
    r"adreno",
    r"mali",
)
_INTEGRATED_GPU_RE = re.compile(
    "|".join(f"(?:{p})" for p in _INTEGRATED_GPU_PATTERNS), re.IGNORECASE
)
# Si --list-devices declara el tipo de dispositivo, manda sobre la heurística.
_DEVICE_TYPE_RE = re.compile(r"\b(integrated|discrete)\b", re.IGNORECASE)

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
        backend_name, index = m.group(1), int(m.group(2))
        name = m.group(3).strip()
        devices.append({
            "id": f"{backend_name}{index}",
            "index": index,
            "backend": backend_name,
            "name": name,
            "integrated": _is_integrated(line, name),
            "vram_total_mib": int(m.group(4)),
            "vram_free_mib": int(m.group(5)),
        })
    return devices


def _is_integrated(line: str, name: str) -> bool:
    type_match = _DEVICE_TYPE_RE.search(line)
    if type_match:
        return type_match.group(1).lower() == "integrated"
    return bool(_INTEGRATED_GPU_RE.search(name))


def _device_id(device: dict):
    if device.get("id"):
        return device["id"]
    if device.get("backend") is not None and device.get("index") is not None:
        return f"{device['backend']}{device['index']}"
    return None


def select_devices(devices: list[dict], setting="auto") -> list[dict]:
    """Filtra y ordena los dispositivos según local_launch["devices"].

    "auto" (o ausente): descarta las integradas salvo que todas lo sean.
    lista: respeta su orden e ignora los ids desconocidos; si no casa
    ninguno, equivale a "auto".
    """
    if not isinstance(setting, list):
        non_integrated = [d for d in devices if not d.get("integrated", False)]
        return non_integrated or list(devices)
    by_id = {}
    for device in devices:
        device_id = _device_id(device)
        if device_id is not None:
            by_id[device_id] = device
    wanted = [str(item) for item in setting]
    unknown = [item for item in wanted if item not in by_id]
    if unknown:
        log.warning("llamacpp_unknown_devices", unknown=unknown)
    matched = [by_id[item] for item in wanted if item in by_id]
    if not matched:
        return select_devices(devices, "auto")
    return matched


def list_devices(exe: str) -> list[dict]:
    if not exe:
        return []
    try:
        result = subprocess.run(
            [exe, "--list-devices"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=30,
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

    cmd = [exe]
    if launch.get("router_mode"):
        # Router mode: sin --model, sirve todos los GGUF del dir con
        # load/unload dinámico; los requests eligen modelo por nombre
        from app.services.hf_models_service import models_dir
        cmd += ["--models-dir", str(launch.get("models_dir") or models_dir())]
    else:
        cmd += ["--model", str(launch.get("model_path", ""))]
    cmd += [
        "--ctx-size", str(ctx_size),
        "--n-gpu-layers", str(ngl),
        "--host", str(launch.get("host", "127.0.0.1")),
        "--port", str(port),
        "--jinja",
        "--slots",
    ]

    selected = select_devices(devices, launch.get("devices", "auto"))
    selected_ids = [_device_id(d) for d in selected]
    listed_ids = [_device_id(d) for d in devices]
    if selected and all(selected_ids) and all(listed_ids) and selected_ids != listed_ids:
        cmd += ["--device", ",".join(selected_ids)]

    if len(selected) > 1:
        cmd += ["--split-mode", split_mode]
        split = launch.get("tensor_split", "auto")
        ratios = compute_tensor_split(selected) if split == "auto" else [float(x) for x in split]
        if ratios:
            cmd += ["--tensor-split", ",".join(str(r) for r in ratios)]

    rpc_servers = launch.get("rpc_servers") or []
    if isinstance(rpc_servers, list) and rpc_servers:
        # llama.cpp RPC: workers remotos (ggml-rpc-server) se suman como
        # devices — la granja multi-PC del backlog v3
        cmd += ["--rpc", ",".join(str(s) for s in rpc_servers)]

    extra = launch.get("extra_args", [])
    if isinstance(extra, list):
        cmd += [str(a) for a in extra]
    return cmd


async def get_status(provider: Provider) -> dict:
    import httpx

    port = port_from_api_base(provider.api_base)
    proc = _owned_llamacpp(port)
    running = proc is not None
    status = {
        "running": running,
        "pid": proc.pid if running else None,
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
    router_mode = bool(provider.local_launch.get("router_mode"))
    model_path = str(provider.local_launch.get("model_path", "")).strip()
    if not router_mode and (not model_path or not Path(model_path).exists()):
        raise ValueError(f"Modelo GGUF no encontrado: '{model_path}'")

    current = await get_status(provider)
    if current["running"]:
        return {**current, "already_running": True}

    devices = list_devices(exe)
    selected = select_devices(devices, provider.local_launch.get("devices", "auto"))
    fit = None
    if not router_mode:
        fit = estimate_fit(model_path, int(provider.local_launch.get("ctx_size", 32768)), selected)
        if devices and not fit["fits"]:
            log.warning("model_may_not_fit", **fit)

    cmd = build_cmdline(provider, devices)
    out_log = _config_dir() / "llamacpp-out.log"
    err_log = _config_dir() / "llamacpp-err.log"
    _spawn_detached(cmd, out_log, err_log)
    log.info("llamacpp_started", cmd=" ".join(cmd), devices=len(devices), selected=[_device_id(d) for d in selected], fit=fit)
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

    port = port_from_api_base(provider.api_base)
    killed = _kill_pid_file_server(port) + _kill_stray_servers(port)
    log.info("llamacpp_stopped", killed=killed, forced=force)
    return {"stopped": True, "killed": killed}


def _llamacpp_identity(port: int) -> ProcessIdentity:
    return ProcessIdentity(name_prefixes=_LLAMA_SERVER_NAMES, port=port)


def _owned_llamacpp(port: int) -> psutil.Process | None:
    pid = _read_pid()
    if pid is None:
        return None
    proc = owned_process(pid, _llamacpp_identity(port))
    if proc is None:
        log.warning("llamacpp_stale_pid_file", pid=pid)
        _pid_file().unlink(missing_ok=True)
    return proc


def _kill_pid_file_server(port: int) -> int:
    proc = _owned_llamacpp(port)
    if proc is None:
        return 0
    _pid_file().unlink(missing_ok=True)
    if kill_quietly(proc):
        return 1
    log.warning("llamacpp_pid_kill_failed", pid=proc.pid)
    return 0


def _kill_stray_servers(port: int) -> int:
    return sum(kill_quietly(proc) for proc in find_owned_processes(_llamacpp_identity(port)))


def _read_pid() -> int | None:
    try:
        return int(_pid_file().read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def tail_logs(lines: int = 80) -> list[str]:
    from app.core.utils import tail_file
    merged = []
    for name, tag in (("llamacpp-out.log", "out"), ("llamacpp-err.log", "err")):
        merged += [f"[{tag}] {line}" for line in tail_file(_config_dir() / name, lines)]
    return merged[-lines:]


def _has_launch_target(provider: Provider) -> bool:
    if provider.local_launch.get("router_mode"):
        return True
    return bool(str(provider.local_launch.get("model_path", "")).strip())


async def autostart_if_configured() -> None:
    """Arranque al boot del backend: solo si el provider llamacpp lo pide explícitamente."""
    from app.services import providers_service
    provider = providers_service.get_provider("llamacpp")
    if not provider or not provider.local_launch.get("autostart"):
        return
    if not _has_launch_target(provider):
        return
    try:
        await start_llamacpp(provider)
        log.info("llamacpp_autostarted")
    except Exception as e:
        log.warning("llamacpp_autostart_failed", error=str(e))
