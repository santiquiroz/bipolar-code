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
        seen_ids: set = set()
        models = []
        for m in items:
            if not isinstance(m, dict):
                continue
            model_id = m.get("id", m.get("name", ""))
            if not model_id or model_id in seen_ids:
                continue
            seen_ids.add(model_id)
            models.append({
                "id": model_id,
                "name": m.get("name", model_id),
                "vendor": (m.get("vendor", {}) or {}).get("name") if isinstance(m.get("vendor"), dict) else m.get("vendor"),
            })
        log.info("provider_models_fetched", provider=provider_id, count=len(models))
        return {"models": models}
    except httpx.HTTPStatusError as e:
        log.error("provider_models_http_error", provider=provider_id, status=e.response.status_code)
        # Devolvemos el status real para que el frontend pueda mostrar mensajes específicos
        raise HTTPException(
            status_code=e.response.status_code,
            detail={"message": f"Error del proveedor ({e.response.status_code})", "http_status": e.response.status_code}
        )
    except httpx.ConnectError:
        log.error("provider_models_connect_error", provider=provider_id)
        raise HTTPException(status_code=503, detail={"message": "No se pudo conectar al endpoint de modelos", "http_status": 503})
    except Exception as e:
        log.error("provider_models_error", provider=provider_id, error=str(e))
        raise HTTPException(status_code=502, detail={"message": str(e), "http_status": 502})


@router.post("/{provider_id}/refresh-token")
async def refresh_provider_token(provider_id: str):
    """
    Refresca el token de autenticación del proveedor.
    Para Copilot: llama directamente a la GitHub Copilot token API usando GITHUB_OAUTH_TOKEN.
    Para otros: retorna instrucciones manuales.
    """
    provider = providers_service.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' no encontrado")

    log.info("request_refresh_token", provider=provider_id)

    if provider_id == "copilot":
        settings = get_settings()
        oauth_token = os.environ.get("GITHUB_OAUTH_TOKEN", "") or settings.github_oauth_token
        if not oauth_token:
            raise HTTPException(status_code=400, detail="GITHUB_OAUTH_TOKEN no configurado")

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.github.com/copilot_internal/v2/token",
                    headers={
                        "Authorization": f"Bearer {oauth_token}",
                        "Editor-Version": "vscode/1.85.0",
                        "Editor-Plugin-Version": "copilot-chat/0.22.0",
                        "User-Agent": "GithubCopilot/1.138.0",
                    }
                )
            resp.raise_for_status()
            data = resp.json()
            new_token = data.get("token", "")
            if not new_token:
                raise HTTPException(status_code=502, detail=f"GitHub no devolvió token: {data}")

            # Actualizar .env en disco
            env_path = providers_service._config_dir() / ".env"
            lines = [l for l in env_path.read_text(encoding="utf-8").splitlines()
                     if not l.startswith("COPILOT_SESSION_TOKEN")]
            lines.append(f"COPILOT_SESSION_TOKEN={new_token}")
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            # Actualizar env del proceso actual para que el próximo start_litellm lo use
            os.environ["COPILOT_SESSION_TOKEN"] = new_token

            # Limpiar caché de settings
            from app.core.config import get_settings as _gs
            _gs.cache_clear()

            log.info("copilot_token_refreshed", token_length=len(new_token))
            return {"refreshed": True, "token_length": len(new_token)}

        except httpx.HTTPStatusError as e:
            log.error("copilot_token_http_error", status=e.response.status_code)
            raise HTTPException(status_code=502, detail=f"Error de GitHub ({e.response.status_code})")
        except Exception as e:
            log.error("copilot_token_error", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    return {
        "refreshed": False,
        "note": f"Refresh automático no disponible para '{provider_id}'. Actualiza {provider.auth_env_var} manualmente en Settings."
    }
