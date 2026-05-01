import asyncio
import os
import re
import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Literal, Optional
from app.models.provider import Provider
from app.services import providers_service
from app.core.logging import get_logger
from app.core.config import get_settings

_PROVIDER_ID_RE = re.compile(r'^[a-z0-9_-]{1,64}$')
_ALLOWED_URL_PREFIXES = ("https://", "http://localhost", "http://127.0.0.1")

# Patrones en el model ID que indican que NO es un LLM de chat compatible con Claude Code
_NON_CHAT_PATTERNS = re.compile(
    r'(rerank|embed|encod|classif|safety|moderat|detect|segment|caption|'
    r'whisper|tts|speech|transcri|ocr|vision-only|image-gen|diffusion|'
    r'speaker|lip.?sync|religh|rembg|ising|synthetic-video)',
    re.IGNORECASE,
)


def _is_chat_model(model_id: str) -> bool:
    return not _NON_CHAT_PATTERNS.search(model_id)


def _validate_provider_id(pid: str) -> None:
    if not _PROVIDER_ID_RE.match(pid):
        raise HTTPException(status_code=422, detail="provider id solo puede contener a-z, 0-9, _ y - (máx 64 chars)")


def _validate_url(url: Optional[str], field: str) -> None:
    if url and not any(url.startswith(p) for p in _ALLOWED_URL_PREFIXES):
        raise HTTPException(status_code=422, detail=f"{field} debe usar https://, http://localhost o http://127.0.0.1")


def _safe_http_error(e: Exception) -> str:
    name = type(e).__name__.lower()
    msg = str(e).lower()
    if "timeout" in name or "timeout" in msg:
        return "Timeout al conectar con el proveedor"
    if "connect" in name:
        return "No se pudo conectar con el proveedor"
    return "Error inesperado al verificar la clave"

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
    _validate_provider_id(body.id)
    _validate_url(body.api_base, "api_base")
    _validate_url(body.models_endpoint, "models_endpoint")
    try:
        return providers_service.add_provider(Provider(**body.model_dump()))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.patch("/{provider_id}")
def update_provider(provider_id: str, body: UpdateProviderRequest):
    log.info("request_update_provider", id=provider_id)
    updates = body.model_dump(exclude_unset=True)
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
async def set_provider_model(provider_id: str, body: SetModelRequest):
    """Cambia el modelo activo. Si el provider es el activo, reinicia LiteLLM."""
    log.info("request_set_provider_model", provider_id=provider_id, model=body.model_id)
    try:
        updated = providers_service.update_provider(provider_id, {"active_model": body.model_id})
        registry = providers_service.load_registry()
        if registry.active_provider_id == provider_id:
            config_path = providers_service.generate_litellm_config(updated)
            await providers_service._kill_litellm()
            providers_service._start_litellm(config_path)
            log.info("litellm_restarted_after_model_change", provider=provider_id, model=body.model_id)
        return updated
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
    token = os.environ.get(env_var, "") if env_var else ""

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
            if not _is_chat_model(model_id):
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


_PROBE_IMAGE_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


async def _probe_chat(url: str, payload: dict, headers: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
        return resp.status_code < 400
    except Exception:
        return False


async def _probe_tool_support(url: str, model: str, headers: dict) -> bool:
    return await _probe_chat(url, {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1, "stream": False,
        "tools": [{"type": "function", "function": {
            "name": "probe", "description": "probe",
            "parameters": {"type": "object", "properties": {}},
        }}],
    }, headers)


async def _probe_vision_support(url: str, model: str, headers: dict) -> bool:
    return await _probe_chat(url, {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "hi"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PROBE_IMAGE_B64}"}},
        ]}],
        "max_tokens": 1, "stream": False,
    }, headers)


async def _probe_system_support(url: str, model: str, headers: dict) -> bool:
    return await _probe_chat(url, {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hi"},
        ],
        "max_tokens": 1, "stream": False,
    }, headers)


async def _fetch_model_limits(provider, model: str, api_key: str) -> tuple[int, int]:
    """Retorna (context_window, max_output_tokens) desde el endpoint de modelos."""
    import urllib.parse
    env_var = provider.models_auth_env_var or provider.auth_env_var
    token = os.environ.get(env_var, "") if env_var else api_key
    headers: dict = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(provider.extra_headers)

    candidates = []
    if provider.models_endpoint:
        candidates.append(f"{provider.models_endpoint.rstrip('/')}/{urllib.parse.quote(model, safe='')}")
    candidates.append(f"{provider.api_base.rstrip('/')}/models/{urllib.parse.quote(model, safe='')}")

    for url in candidates:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                d = resp.json()
                ctx = d.get("max_model_len") or d.get("context_length") or d.get("context_window") or d.get("max_context_length")
                out = d.get("max_output_tokens") or d.get("max_completion_tokens") or d.get("max_generated_tokens")
                if ctx or out:
                    return int(ctx) if ctx else 0, int(out) if out else 0
        except Exception:
            pass
    return 0, 0


@router.get("/{provider_id}/test-model")
async def test_provider_model(provider_id: str, model: str = Query(...)):
    """Verifica acceso y detecta capacidades del modelo (tools, vision, system prompt, context window, max output)."""
    provider = providers_service.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' no encontrado")

    url = f"{provider.api_base.rstrip('/')}/chat/completions"
    api_key = os.environ.get(provider.auth_env_var, "") if provider.auth_env_var else ""
    headers = {
        "Authorization": f"Bearer {api_key or 'no-key'}",
        "Content-Type": "application/json",
    }
    headers.update(provider.extra_headers)

    # 1. Accesibilidad básica
    log.info("test_provider_model", provider=provider_id, model=model)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1, "stream": False}, headers=headers)
        if resp.status_code >= 400:
            if resp.status_code in (401, 403):
                return {"accessible": False, "reason": "Modelo de pago o sin acceso con tu API key"}
            try:
                detail = resp.json().get("error", {}).get("message") or f"HTTP {resp.status_code}"
            except Exception:
                detail = f"HTTP {resp.status_code}"
            return {"accessible": False, "reason": detail[:200]}
    except Exception as e:
        return {"accessible": False, "reason": _safe_http_error(e)}

    # 2. Probes en paralelo
    supports_tools, supports_vision, supports_system, (context_window, max_output_tokens) = await asyncio.gather(
        _probe_tool_support(url, model, headers),
        _probe_vision_support(url, model, headers),
        _probe_system_support(url, model, headers),
        _fetch_model_limits(provider, model, api_key),
    )

    # 3. Guardar en model_info
    model_info = dict(provider.model_info)
    model_info.update({
        "supports_tools": supports_tools,
        "supports_vision": supports_vision,
        "supports_system_prompt": supports_system,
    })
    if context_window:
        model_info["context_window"] = context_window
    if max_output_tokens:
        model_info["max_output_tokens"] = max_output_tokens
    providers_service.update_provider(provider_id, {"model_info": model_info})

    capabilities = {
        "supports_tools": supports_tools,
        "supports_vision": supports_vision,
        "supports_system_prompt": supports_system,
        "context_window": context_window,
        "max_output_tokens": max_output_tokens,
    }
    log.info("model_capabilities_detected", provider=provider_id, model=model, **capabilities)
    return {"accessible": True, "capabilities": capabilities}


@router.post("/{provider_id}/verify-key")
async def verify_provider_key(provider_id: str, body: dict):
    from app.services.settings_service import write_env_key
    api_key = body.get("api_key", "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key required")

    _VERIFY_ENDPOINTS = {
        "nvidia_nim":  ("https://integrate.api.nvidia.com/v1/models", "NVIDIA_NIM_API_KEY"),
        "openrouter":  ("https://openrouter.ai/api/v1/models",        "OPENROUTER_API_KEY"),
        "deepseek":    ("https://api.deepseek.com/models",             "DEEPSEEK_API_KEY"),
    }
    if provider_id in _VERIFY_ENDPOINTS:
        url, env_key = _VERIFY_ENDPOINTS[provider_id]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
            if resp.status_code == 200:
                write_env_key(env_key, api_key)
                get_settings.cache_clear()
                model_count = len(resp.json().get("data", []))
                return {"valid": True, "model_count": model_count}
            return {"valid": False, "error": f"HTTP {resp.status_code}"}
        except Exception as e:
            log.warning("verify_key_error", provider=provider_id, error=str(e))
            return {"valid": False, "error": _safe_http_error(e)}

    raise HTTPException(status_code=400, detail=f"verify-key not supported for {provider_id}")


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
        try:
            return await providers_service.refresh_copilot_token()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            log.error("copilot_token_error", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    return {
        "refreshed": False,
        "note": f"Refresh automático no disponible para '{provider_id}'. Actualiza {provider.auth_env_var} manualmente en Settings."
    }
