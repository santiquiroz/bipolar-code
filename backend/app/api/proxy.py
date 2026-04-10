from fastapi import APIRouter, HTTPException
from app.services import proxy_service, providers_service
from pydantic import BaseModel


class RouteRequest(BaseModel):
    mode: str  # 'direct' or 'proxy'
from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])


@router.get("/status")
async def proxy_status():
    log.info("request_proxy_status")
    return await proxy_service.get_proxy_status()


@router.post("/start")
async def start_proxy():
    """Inicia (o reinicia) el proxy con el proveedor activo."""
    log.info("request_proxy_start")
    provider = providers_service.get_active_provider()
    if not provider:
        raise HTTPException(status_code=404, detail="No hay proveedor activo configurado")
    try:
        config_path = providers_service.generate_litellm_config(provider)
        providers_service._kill_litellm()
        providers_service._start_litellm(config_path)
        return {"started": True, "provider": provider.id, "config": str(config_path)}
    except Exception as e:
        log.error("proxy_start_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/route')
async def get_proxy_route():
    """Return current routing mode and litellm health."""
    try:
        mode = proxy_service.get_route_mode()
        status = await proxy_service.get_proxy_status()
        return {
            'mode': mode,
            'litellm_running': status.get('running', False),
            'proxy_status': status,
        }
    except Exception as e:
        log.error('proxy_get_route_error', error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/route')
async def set_proxy_route(body: RouteRequest):
    if body.mode not in ('direct', 'proxy'):
        raise HTTPException(status_code=400, detail='mode must be "direct" or "proxy"')
    log.info('request_set_proxy_route', requested=body.mode)
    try:
        if body.mode == 'proxy':
            res = await proxy_service.enable_proxy_routing()
            return res
        else:
            res = await proxy_service.enable_direct_routing()
            return res
    except Exception as e:
        log.error('proxy_set_route_error', error=str(e), requested=body.mode)
        raise HTTPException(status_code=500, detail=str(e))
