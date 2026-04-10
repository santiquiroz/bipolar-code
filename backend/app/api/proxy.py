from fastapi import APIRouter
from app.services import proxy_service
from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/proxy", tags=["proxy"])


@router.get("/status")
async def proxy_status():
    log.info("request_proxy_status")
    return await proxy_service.get_proxy_status()
