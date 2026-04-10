from fastapi import APIRouter, HTTPException
from app.services import proxy_service
from app.models.schemas import ProxyStatus, SwitchBackendRequest
from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])


@router.get("/status", response_model=ProxyStatus)
async def proxy_status():
    log.info("request_proxy_status")
    return await proxy_service.get_proxy_status()


@router.post("/switch")
async def switch_backend(body: SwitchBackendRequest):
    log.info("request_switch_backend", backend=body.backend)
    try:
        return await proxy_service.switch_backend(body.backend)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
