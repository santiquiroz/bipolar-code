"""
Superficie OpenAI-compatible (/v1/chat/completions) para clientes BYOK:
VS Code Copilot Chat, Cursor, Cline, Continue. Reenvía al provider activo
sin transformar el formato (ambos lados hablan OpenAI chat completions).
"""
import asyncio
import json
import os
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.utils import sanitize_error
from app.services import providers_service, smart_router, token_service, usage_tracker
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
    prompt_tokens = token_service.count_tokens(body.get("messages") or [])
    decision = await smart_router.decide(body, str(body.get("model", "")), prompt_tokens, request.headers, surface="chat_completions")
    active, routed_model, is_active_provider = decision.as_pick()
    route_headers = {"X-Bipolar-Route": decision.to_header(), "X-Bipolar-Decision-Id": decision.decision_id}
    started = time.monotonic()

    def _outcome(ok: bool, status: int | None = None, error: str = "") -> None:
        key = f"provider:{active.id}" if active else ""
        smart_router.report_outcome_sync(decision, key, ok, latency_ms=(time.monotonic() - started) * 1000, status=status, error=error)
    # Un destino anthropic NO activo requeriría traducir el formato OAI→Anthropic
    # (litellm corre con el config del activo): en ese caso se ignora la ruta
    if active and active.litellm_prefix == "anthropic" and not is_active_provider:
        active = providers_service.get_active_provider()
        routed_model = None
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
            _outcome(False, None, "connect error")
            return JSONResponse(
                status_code=502,
                content={"error": {"message": f"Provider '{provider_id}' no responde en {url}"}},
                headers=route_headers,
            )
        if resp.status_code < 400:
            _outcome(True)
            try:
                _record_usage(provider_id, body.get("model", ""), resp.json().get("usage") or {})
            except ValueError:
                pass
        else:
            _outcome(False, resp.status_code, resp.text[:300])
        return JSONResponse(status_code=resp.status_code, content=_safe_json(resp), headers=route_headers)

    async def generate():
        usage_seen: dict = {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        raw = await resp.aread()
                        _outcome(False, resp.status_code, raw.decode(errors="replace"))
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
            _outcome(False, None, "connect error")
            yield f"data: {json.dumps({'error': {'message': f'Provider {provider_id} no responde'}})}\n\n"
        except Exception as e:
            _outcome(False, None, str(e))
            log.error("openai_compat_stream_error", error=sanitize_error(str(e)))
            yield f"data: {json.dumps({'error': {'message': sanitize_error(str(e))}})}\n\n"
        finally:
            if usage_seen:
                _outcome(True)
                _record_usage(provider_id, body.get("model", ""), usage_seen)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **route_headers},
    )


def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return {"error": {"message": sanitize_error(resp.text[:500])}}
