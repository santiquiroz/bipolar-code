from fastapi import APIRouter
from app.services import models_service
from app.services.token_service import _capabilities, supports_vision, get_context_window
from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/models", tags=["models"])


@router.get("/active")
async def active_models():
    log.info("request_active_models")
    return await models_service.list_active_models()


@router.get("/capabilities")
async def get_capabilities():
    return _capabilities()


@router.get("/capabilities/{model_id:path}")
async def get_model_capabilities(model_id: str):
    caps = _capabilities()
    entry = caps.get(model_id) or caps.get("__default__", {})
    return {
        "model": model_id,
        "context_window": get_context_window(model_id),
        "supports_vision": supports_vision(model_id),
        "supports_tools": entry.get("supports_tools", True),
    }
