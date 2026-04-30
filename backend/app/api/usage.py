from typing import Optional
from fastapi import APIRouter
from app.services import usage_service, usage_tracker
from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/anthropic")
async def anthropic_usage():
    log.info("request_anthropic_usage")
    return await usage_service.get_anthropic_usage()


@router.get("/logs")
async def log_stats():
    log.info("request_log_stats")
    return await usage_service.get_proxy_log_stats()


@router.get("/history")
async def usage_history(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    limit: int = 100,
):
    return await usage_tracker.get_history(provider_id=provider, model=model, limit=limit)


@router.get("/summary")
async def usage_summary(period: str = "day"):
    return await usage_tracker.get_summary(period=period)
