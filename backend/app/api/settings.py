from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services import settings_service, proxy_service, copilot_service
from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


class WriteKeyRequest(BaseModel):
    key: str
    value: str


class SetCopilotModelRequest(BaseModel):
    model_id: str  # ej: "claude-sonnet-4.6"


@router.get("/env")
async def get_env():
    """Variables del .env enmascaradas."""
    log.info("request_get_env")
    return settings_service.read_env_masked()


@router.post("/env")
async def set_env_key(body: WriteKeyRequest):
    """Actualiza una variable en el .env."""
    if not body.key or not body.value:
        raise HTTPException(status_code=400, detail="key y value son requeridos")
    log.info("request_set_env_key", key=body.key)
    settings_service.write_env_key(body.key, body.value)
    return {"updated": body.key}


@router.get("/copilot/active-model")
async def get_copilot_active_model():
    model = settings_service.get_active_copilot_model()
    log.info("request_copilot_active_model", model=model)
    return {"model": model}


@router.post("/copilot/model")
async def set_copilot_model(body: SetCopilotModelRequest):
    """
    Cambia el modelo Copilot: reescribe config-copilot.yaml y reinicia el proxy.
    Solo aplica si el backend activo es copilot.
    """
    log.info("request_set_copilot_model", model=body.model_id)
    settings_service.set_copilot_model(body.model_id)
    try:
        await proxy_service.switch_backend(proxy_service.BackendType.copilot)
    except Exception as e:
        log.warning("copilot_model_set_restart_failed", error=str(e))
        return {"model": body.model_id, "restarted": False, "note": str(e)}
    return {"model": body.model_id, "restarted": True}
