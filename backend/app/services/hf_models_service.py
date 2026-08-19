"""
Búsqueda y descarga de modelos GGUF desde Hugging Face al dir local de modelos.
Descargas en background con progreso en memoria y resume por Range sobre .part.
"""
import asyncio
import os
import re
import time
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

HF_API = "https://huggingface.co/api/models"
_MULTIPART = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$")
_CHUNK_SIZE = 1024 * 1024

_downloads: dict[str, dict] = {}
_tasks: dict[str, asyncio.Task] = {}


def models_dir() -> Path:
    path = Path(get_settings().litellm_config_dir) / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _hf_headers() -> dict:
    token = os.environ.get("HF_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


async def search_gguf(query: str, limit: int = 20) -> list[dict]:
    params = {
        "search": query,
        "filter": "gguf",
        "sort": "downloads",
        "direction": "-1",
        "limit": str(limit),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(HF_API, params=params, headers=_hf_headers())
        resp.raise_for_status()
    return [
        {
            "id": m.get("id", ""),
            "downloads": m.get("downloads", 0),
            "likes": m.get("likes", 0),
            "updated": m.get("lastModified", ""),
        }
        for m in resp.json()
    ]


async def list_repo_gguf_files(repo_id: str) -> list[dict]:
    url = f"{HF_API}/{repo_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params={"blobs": "true"}, headers=_hf_headers())
        resp.raise_for_status()
    siblings = resp.json().get("siblings", [])
    files = [
        {"filename": s["rfilename"], "size": s.get("size") or 0}
        for s in siblings
        if s.get("rfilename", "").endswith(".gguf")
    ]
    return group_multipart(files)


def group_multipart(files: list[dict]) -> list[dict]:
    """Agrupa GGUFs multi-parte (-00001-of-0000N) en una sola entrada descargable."""
    groups: dict[str, dict] = {}
    singles: list[dict] = []
    for f in files:
        m = _MULTIPART.search(f["filename"])
        if not m:
            singles.append({**f, "parts": [f["filename"]]})
            continue
        base = _MULTIPART.sub("", f["filename"])
        group = groups.setdefault(base, {"filename": base, "size": 0, "parts": []})
        group["size"] += f["size"]
        group["parts"].append(f["filename"])
    for group in groups.values():
        group["parts"].sort()
    return sorted(singles + list(groups.values()), key=lambda x: x["filename"])


def expand_parts(repo_id: str, filename: str, files: list[dict]) -> list[str]:
    for f in files:
        if f["filename"] == filename:
            return f["parts"]
    return [filename]


def list_local_models() -> list[dict]:
    result = []
    for path in sorted(models_dir().glob("*.gguf")):
        m = _MULTIPART.search(path.name)
        if m and m.group(1) != "00001":
            continue  # solo la primera parte es cargable por llama.cpp
        result.append({"filename": path.name, "path": str(path), "size": path.stat().st_size})
    return result


def download_id(repo_id: str, filename: str) -> str:
    return f"{repo_id}::{filename}"


def get_downloads() -> list[dict]:
    return list(_downloads.values())


async def start_download(repo_id: str, filename: str) -> dict:
    did = download_id(repo_id, filename)
    existing = _downloads.get(did)
    if existing and existing["status"] in ("downloading", "queued"):
        return existing

    files = await list_repo_gguf_files(repo_id)
    parts = expand_parts(repo_id, filename, files)
    total = next((f["size"] for f in files if f["filename"] == filename), 0)

    state = {
        "id": did,
        "repo_id": repo_id,
        "filename": filename,
        "parts": parts,
        "status": "queued",
        "total_bytes": total,
        "downloaded_bytes": 0,
        "speed_bps": 0,
        "error": "",
    }
    _downloads[did] = state
    task = asyncio.create_task(_run_download(state))
    _tasks[did] = task
    task.add_done_callback(lambda _: _tasks.pop(did, None))
    return state


def cancel_download(did: str) -> bool:
    task = _tasks.get(did)
    state = _downloads.get(did)
    if task and not task.done():
        task.cancel()
        if state:
            state["status"] = "cancelled"
        return True
    return False


async def _run_download(state: dict) -> None:
    state["status"] = "downloading"
    try:
        for part in state["parts"]:
            await _download_file(state, part)
        state["status"] = "done"
        log.info("hf_download_done", id=state["id"], bytes=state["downloaded_bytes"])
    except asyncio.CancelledError:
        state["status"] = "cancelled"
        raise
    except Exception as e:
        state["status"] = "error"
        state["error"] = str(e)[:300]
        log.error("hf_download_failed", id=state["id"], error=str(e))


async def _download_file(state: dict, part: str) -> None:
    url = f"https://huggingface.co/{state['repo_id']}/resolve/main/{part}?download=true"
    dest = models_dir() / Path(part).name
    tmp = dest.with_suffix(dest.suffix + ".part")
    if dest.exists():
        state["downloaded_bytes"] += dest.stat().st_size
        return

    resume_from = tmp.stat().st_size if tmp.exists() else 0
    headers = _hf_headers()
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
        state["downloaded_bytes"] += resume_from

    last_tick = time.monotonic()
    tick_bytes = 0
    timeout = httpx.Timeout(connect=15.0, read=60.0, write=15.0, pool=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 416:
                # .part ya completo: renombrar y seguir
                tmp.rename(dest)
                return
            resp.raise_for_status()
            mode = "ab" if resume_from else "wb"
            with open(tmp, mode) as f:
                async for chunk in resp.aiter_bytes(_CHUNK_SIZE):
                    f.write(chunk)
                    state["downloaded_bytes"] += len(chunk)
                    tick_bytes += len(chunk)
                    now = time.monotonic()
                    if now - last_tick >= 1.0:
                        state["speed_bps"] = int(tick_bytes / (now - last_tick))
                        last_tick = now
                        tick_bytes = 0
    tmp.rename(dest)
