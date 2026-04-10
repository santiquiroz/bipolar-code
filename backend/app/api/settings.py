from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services import settings_service
from app.core.logging import get_logger

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
