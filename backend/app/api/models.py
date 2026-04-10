from fastapi import APIRouter
from app.services import models_service
from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/models", tags=["models"])


@router.get("/active")
async def active_models():
    log.info("request_active_models")
    return await models_service.list_active_models()
