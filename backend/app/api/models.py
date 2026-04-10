from fastapi import APIRouter
from app.services import models_service, copilot_service
from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/models", tags=["models"])


@router.get("/active")
async def active_models():
    log.info("request_active_models")
    return await models_service.list_active_models()


@router.get("/copilot")
async def copilot_models():
    log.info("request_copilot_models")
    return await copilot_service.list_copilot_models()


@router.get("/github")
async def github_models():
    log.info("request_github_models")
    return await copilot_service.list_github_models()
