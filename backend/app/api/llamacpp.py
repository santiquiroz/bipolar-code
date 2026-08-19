"""
Router del servidor llama.cpp local y descarga de modelos GGUF desde HF.
Thin — delega en llamacpp_service y hf_models_service.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.logging import get_logger
from app.core.utils import sanitize_error
from app.services import hf_models_service, llamacpp_service, providers_service

log = get_logger(__name__)
router = APIRouter(prefix="/llamacpp", tags=["llamacpp"])


class DownloadRequest(BaseModel):
    repo_id: str
    filename: str


class UseModelRequest(BaseModel):
    path: str


def _llamacpp_provider():
    provider = providers_service.get_provider("llamacpp")
    if not provider:
        raise HTTPException(status_code=404, detail="Provider 'llamacpp' no registrado")
    return provider


@router.get("/devices")
async def devices():
    provider = _llamacpp_provider()
    exe = llamacpp_service.resolve_exe(provider)
    return {
        "exe_found": bool(exe),
        "exe_path": exe,
        "devices": llamacpp_service.list_devices(exe),
    }


@router.get("/status")
async def status():
    return await llamacpp_service.get_status(_llamacpp_provider())


@router.post("/start")
async def start():
    try:
        return await llamacpp_service.start_llamacpp(_llamacpp_provider())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=sanitize_error(str(e)))
    except Exception as e:
        log.error("llamacpp_start_failed", error=str(e))
        raise HTTPException(status_code=500, detail=sanitize_error(str(e)))


@router.post("/stop")
async def stop(force: bool = False):
    try:
        return await llamacpp_service.stop_llamacpp(_llamacpp_provider(), force=force)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=sanitize_error(str(e)))


@router.get("/logs")
async def logs(lines: int = 80):
    return {"logs": llamacpp_service.tail_logs(lines)}


# ── Modelos GGUF (Hugging Face) ──────────────────────────────────────────────

@router.get("/hf/search")
async def hf_search(q: str):
    try:
        return {"results": await hf_models_service.search_gguf(q)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=sanitize_error(str(e)))


@router.get("/hf/files")
async def hf_files(repo_id: str):
    try:
        return {"files": await hf_models_service.list_repo_gguf_files(repo_id)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=sanitize_error(str(e)))


@router.post("/hf/download")
async def hf_download(body: DownloadRequest):
    try:
        return await hf_models_service.start_download(body.repo_id, body.filename)
    except Exception as e:
        raise HTTPException(status_code=502, detail=sanitize_error(str(e)))


@router.get("/hf/downloads")
async def hf_downloads():
    return {"downloads": hf_models_service.get_downloads()}


@router.delete("/hf/download/{download_id:path}")
async def hf_cancel(download_id: str):
    return {"cancelled": hf_models_service.cancel_download(download_id)}


@router.get("/models")
async def local_models():
    return {"models": hf_models_service.list_local_models()}


@router.post("/use-model")
async def use_model(body: UseModelRequest):
    provider = _llamacpp_provider()
    launch = {**provider.local_launch, "model_path": body.path}
    updated = providers_service.update_provider(provider.id, {"local_launch": launch})
    return {"model_path": updated.local_launch.get("model_path", "")}
