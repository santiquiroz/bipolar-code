import httpx
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.models.provider import Provider
from app.services import providers_service
from app.core.logging import get_logger
from app.core.config import get_settings

log = get_logger(__name__)
router = APIRouter(prefix="/providers", tags=["providers"])


class AddProviderRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    api_base: str
    litellm_prefix: str = "openai"
    auth_env_var: str = ""
    extra_headers: dict = {}
    models_endpoint: Optional[str] = None
    models_auth_env_var: str = ""
    active_model: str = ""
    model_info: dict = {}
    drop_params: bool = True
    use_chat_completions_for_anthropic: bool = False


class UpdateProviderRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    api_base: Optional[str] = None
    litellm_prefix: Optional[str] = None
    auth_env_var: Optional[str] = None
    extra_headers: Optional[dict] = None
    models_endpoint: Optional[str] = None
    models_auth_env_var: Optional[str] = None
    active_model: Optional[str] = None
    model_info: Optional[dict] = None
    drop_params: Optional[bool] = None
    use_chat_completions_for_anthropic: Optional[bool] = None


class SwitchProviderRequest(BaseModel):
    provider_id: str


class SetModelRequest(BaseModel):
    model_id: str


@router.get("")
def list_providers():
    log.info("request_list_providers")
    registry = providers_service.load_registry()
    return {
        "active_provider_id": registry.active_provider_id,
        "providers": registry.providers,
    }


@router.get("/{provider_id}")
def get_provider(provider_id: str):
    provider = providers_service.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' no encontrado")
    return provider


@router.post("")
def add_provider(body: AddProviderRequest):
    log.info("request_add_provider", id=body.id)
    try:
        return providers_service.add_provider(Provider(**body.model_dump()))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.patch("/{provider_id}")
def update_provider(provider_id: str, body: UpdateProviderRequest):
    log.info("request_update_provider", id=provider_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return providers_service.update_provider(provider_id, updates)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{provider_id}")
def delete_provider(provider_id: str):
    log.info("request_delete_provider", id=provider_id)
    try:
        providers_service.delete_provider(provider_id)
        return {"deleted": provider_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/switch")
async def switch_provider(body: SwitchProviderRequest):
    log.info("request_switch_provider", provider_id=body.provider_id)
    try:
        return await providers_service.switch_to_provider(body.provider_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{provider_id}/model")
def set_provider_model(provider_id: str, body: SetModelRequest):
    """Cambia el modelo activo de un proveedor (sin reiniciar)."""
    log.info("request_set_provider_model", provider_id=provider_id, model=body.model_id)
    try:
        return providers_service.update_provider(provider_id, {"active_model": body.model_id})
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{provider_id}/models")
async def list_provider_models(provider_id: str):
    """Lista modelos disponibles llamando al models_endpoint del proveedor."""
    provider = providers_service.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' no encontrado")
    if not provider.models_endpoint:
        return {"models": [], "note": "Este proveedor no tiene endpoint de modelos configurado"}

    settings = get_settings()
    env_var = provider.models_auth_env_var or provider.auth_env_var
    token = os.environ.get(env_var, "") or getattr(settings, env_var.lower(), "")

    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(provider.extra_headers)

    log.info("fetching_provider_models", provider=provider_id, endpoint=provider.models_endpoint)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(provider.models_endpoint, headers=headers)
            resp.raise_for_status()
            raw = resp.json()

        # Normalizar respuesta: OpenAI devuelve {"data": [{"id":...}]}, otros pueden devolver lista directa
        items = raw if isinstance(raw, list) else raw.get("data", raw.get("models", []))
        models = [
            {
                "id": m.get("id", m.get("name", "")),
                "name": m.get("name", m.get("id", "")),
                "vendor": (m.get("vendor", {}) or {}).get("name") if isinstance(m.get("vendor"), dict) else m.get("vendor"),
            }
            for m in items if isinstance(m, dict)
        ]
        log.info("provider_models_fetched", provider=provider_id, count=len(models))
        return {"models": models}
    except httpx.HTTPStatusError as e:
        log.error("provider_models_http_error", provider=provider_id, status=e.response.status_code)
        raise HTTPException(status_code=e.response.status_code, detail=f"Error del proveedor: {e.response.text[:200]}")
    except Exception as e:
        log.error("provider_models_error", provider=provider_id, error=str(e))
        raise HTTPException(status_code=502, detail=str(e))
