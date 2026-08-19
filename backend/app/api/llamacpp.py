"""
Router del servidor llama.cpp local. Thin — delega en llamacpp_service.
"""
from fastapi import APIRouter, HTTPException

from app.core.logging import get_logger
from app.core.utils import sanitize_error
from app.services import llamacpp_service, providers_service

log = get_logger(__name__)
router = APIRouter(prefix="/llamacpp", tags=["llamacpp"])


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
