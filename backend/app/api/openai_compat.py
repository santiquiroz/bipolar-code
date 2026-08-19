"""
Superficie OpenAI-compatible (/v1/chat/completions) para clientes BYOK:
VS Code Copilot Chat, Cursor, Cline, Continue. Reenvía al provider activo
sin transformar el formato (ambos lados hablan OpenAI chat completions).
"""
import asyncio
import json
import os

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.utils import sanitize_error
from app.services import providers_service, usage_tracker
from app.services.pricing_service import estimate_cost

log = get_logger(__name__)
router = APIRouter(tags=["openai-compat"])

_background_tasks: set[asyncio.Task] = set()


def resolve_target(active, settings) -> tuple[str, dict, str]:
    """(url, headers, model) del upstream para el provider activo."""
    if active and active.litellm_prefix == "anthropic":
        # litellm traduce OAI→Anthropic; usar alias conocido del config
        url = f"{settings.proxy_url}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {settings.proxy_api_key}"}
        return url, headers, providers_service.PROXY_ALIASES[0]

    api_base = active.api_base.rstrip("/") if active and active.api_base else ""
    url = f"{api_base}/chat/completions" if api_base else f"{settings.proxy_url}/v1/chat/completions"

    api_key = ""
    if active and active.auth_env_var:
        api_key = os.environ.get(active.auth_env_var, "")
    headers = {"Authorization": f"Bearer {api_key or 'no-key'}"}
    if active and active.extra_headers:
        headers.update(active.extra_headers)

    model = (active.active_model if active else "") or ""
    return url, headers, model


def _record_usage(provider_id: str, model: str, usage: dict) -> None:
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    if not input_tokens and not output_tokens:
        return
    cost = estimate_cost(provider_id, model, input_tokens, output_tokens)
    task = asyncio.create_task(
        usage_tracker.record(provider_id, model, input_tokens, output_tokens, cost, False)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    settings = get_settings()
    body = await request.json()
    active = providers_service.get_active_provider()
    routed_model = None
    route = providers_service.resolve_route(str(body.get("model", "")))
    # Routing en la superficie OAI: solo destinos OpenAI-compat (un destino
    # anthropic requeriría traducir el formato, cosa que esta ruta no hace)
    if route and route[0].litellm_prefix != "anthropic":
        active, routed_model = route
    provider_id = active.id if active else "unknown"

    url, headers, model = resolve_target(active, settings)
    if routed_model:
        model = routed_model
    if model:
        body["model"] = model
    headers["Content-Type"] = "application/json"
    stream = bool(body.get("stream"))

    timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)

    if not stream:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=body, headers=headers)
        except httpx.ConnectError:
            return JSONResponse(
                status_code=502,
                content={"error": {"message": f"Provider '{provider_id}' no responde en {url}"}},
            )
        if resp.status_code < 400:
            try:
                _record_usage(provider_id, body.get("model", ""), resp.json().get("usage") or {})
            except ValueError:
                pass
        return JSONResponse(status_code=resp.status_code, content=_safe_json(resp))

    async def generate():
        usage_seen: dict = {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        raw = await resp.aread()
                        yield f"data: {json.dumps({'error': {'message': sanitize_error(raw.decode(errors='replace'))}})}\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line.startswith("data: ") and '"usage"' in line:
                            try:
                                chunk_usage = json.loads(line[6:]).get("usage")
                                if chunk_usage:
                                    usage_seen = chunk_usage
                            except ValueError:
                                pass
                        yield f"{line}\n"
        except httpx.ConnectError:
            yield f"data: {json.dumps({'error': {'message': f'Provider {provider_id} no responde'}})}\n\n"
        except Exception as e:
            log.error("openai_compat_stream_error", error=sanitize_error(str(e)))
            yield f"data: {json.dumps({'error': {'message': sanitize_error(str(e))}})}\n\n"
        finally:
            if usage_seen:
                _record_usage(provider_id, body.get("model", ""), usage_seen)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return {"error": {"message": sanitize_error(resp.text[:500])}}
