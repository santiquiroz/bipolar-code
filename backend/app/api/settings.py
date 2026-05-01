from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services import settings_service
from app.core.logging import get_logger
from app.core.config import get_settings as _get_settings

log = get_logger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


class WriteKeyRequest(BaseModel):
    key: str
    value: str


@router.get("/env")
async def get_env():
    log.info("request_get_env")
    return settings_service.read_env_masked()


@router.post("/env")
async def set_env_key(body: WriteKeyRequest):
    if not body.key or not body.value:
        raise HTTPException(status_code=400, detail="key y value son requeridos")
    log.info("request_set_env_key", key=body.key)
    settings_service.write_env_key(body.key, body.value)
    return {"updated": body.key}


@router.get("/auth-info")
async def get_auth_info():
    s = _get_settings()
    key = s.ui_api_key
    return {
        "api_key_prefix": key[:12] + "..." if len(key) > 12 else key,
        "api_key_length": len(key),
        "rate_limit_rpm": s.rate_limit_rpm,
        "allowed_origins": s.allowed_origins,
        "proxy_base_url": "http://<tu-ip>:8000",
    }


@router.get("/api-key")
async def get_full_api_key():
    """Devuelve el API key completo para configurar Claude Code en PCs remotos."""
    s = _get_settings()
    return {"api_key": s.ui_api_key}
