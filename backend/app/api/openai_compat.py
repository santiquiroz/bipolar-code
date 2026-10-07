"""
Superficie OpenAI-compatible (/v1/chat/completions) para clientes BYOK:
VS Code Copilot Chat, Cursor, Cline, Continue. Reenvía al provider ganador
del plan de ruta sin transformar el formato (ambos lados hablan OpenAI
chat completions), con failover a la siguiente llave o provider antes del
primer byte.
"""
import asyncio
import json
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.utils import sanitize_error
from app.services import credentials, providers_service, smart_router, token_service, upstream, usage_tracker
from app.services.pricing_service import estimate_cost
from app.services.smart_router import PlanStep

log = get_logger(__name__)
router = APIRouter(tags=["openai-compat"])

_background_tasks: set[asyncio.Task] = set()


@dataclass
class OpenChat:
    resp: Any
    step: PlanStep
    model: str
    attempt_stack: AsyncExitStack | None = None


def resolve_target(step: PlanStep, settings) -> tuple[str, dict, str]:
    """(url, headers, model) del upstream para un paso del plan."""
    provider = step.provider
    if provider.litellm_prefix == "anthropic":
        # litellm traduce OAI→Anthropic; usar alias conocido del config
        url = f"{settings.proxy_url}/v1/chat/completions"
        headers = {"Authorization": f"Bearer {settings.proxy_api_key}"}
        return url, headers, step.model or providers_service.PROXY_ALIASES[0]

    if provider.api_base:
        url = providers_service.oai_chat_completions_url(provider)
    else:
        url = f"{settings.proxy_url}/v1/chat/completions"

    headers = {"Authorization": f"Bearer {credentials.api_key_for(step.slot) or 'no-key'}"}
    if provider.extra_headers:
        headers.update(provider.extra_headers)
    return url, headers, step.model or provider.active_model or ""


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


def _step_payload(body: dict, model: str) -> dict:
    return {**body, "model": model} if model else {**body}


def _error_message(text: str) -> str:
    try:
        parsed = json.loads(text)
        return parsed.get("error", {}).get("message") or text
    except Exception:
        return text


def _fallback_step(routed_model: str | None) -> PlanStep | None:
    fallback = providers_service.get_active_provider()
    if fallback is None:
        return None
    return PlanStep(fallback, routed_model, credentials.credential_slots(fallback)[0], True)


def _exhausted_response(decision, failures: list, route_headers: dict) -> JSONResponse:
    status = upstream.exhausted_status(failures)
    if len(failures) == 1 and failures[0].kind == "fatal":
        message = sanitize_error(failures[0].message)
    else:
        message = sanitize_error("Ningún destino respondió: " + upstream.failures_summary(failures, lambda s: s.label))
    smart_router.report_outcome_sync(decision, "", False, status=status, error=message)
    return JSONResponse(
        status_code=status,
        content=upstream.openai_error_body(status, message),
        headers={**route_headers, "X-Bipolar-Attempts": str(len(failures))},
    )


async def _open_stream(client: httpx.AsyncClient, attempt_stack: AsyncExitStack, url: str, headers: dict, payload: dict):
    try:
        resp = await attempt_stack.enter_async_context(client.stream("POST", url, json=payload, headers=headers))
    except httpx.HTTPError as e:
        await attempt_stack.aclose()
        return upstream.Failed(None, str(e), e)
    if resp.status_code >= 400:
        raw = await resp.aread()
        await attempt_stack.aclose()
        return upstream.Failed(resp.status_code, _error_message(raw.decode(errors="replace")))
    return upstream.Opened(resp)


async def _open_attempt_plain(client: httpx.AsyncClient, step: PlanStep, body: dict, settings):
    url, headers, model = resolve_target(step, settings)
    headers["Content-Type"] = "application/json"
    try:
        resp = await client.post(url, json=_step_payload(body, model), headers=headers)
    except httpx.HTTPError as e:
        return upstream.Failed(None, str(e), e)
    if resp.status_code >= 400:
        return upstream.Failed(resp.status_code, _error_message(resp.text))
    return upstream.Opened(OpenChat(resp, step, model))


async def _open_attempt_stream(client: httpx.AsyncClient, step: PlanStep, body: dict, settings):
    url, headers, model = resolve_target(step, settings)
    headers["Content-Type"] = "application/json"
    attempt_stack = AsyncExitStack()
    outcome = await _open_stream(client, attempt_stack, url, headers, _step_payload(body, model))
    if isinstance(outcome, upstream.Opened):
        return upstream.Opened(OpenChat(outcome.upstream, step, model, attempt_stack))
    return outcome


def _line_usage(line: str) -> dict | None:
    if not (line.startswith("data: ") and '"usage"' in line):
        return None
    try:
        return json.loads(line[6:]).get("usage")
    except ValueError:
        return None


def _safe_json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return {"error": {"message": sanitize_error(resp.text[:500])}}


async def _respond_plain(opened, stack, decision, started, headers):
    await stack.aclose()
    smart_router.report_outcome_sync(decision, opened.step.slot.health_key, True, latency_ms=(time.monotonic() - started) * 1000)
    try:
        _record_usage(opened.step.provider.id, opened.model, opened.resp.json().get("usage") or {})
    except ValueError:
        pass
    return JSONResponse(status_code=opened.resp.status_code, content=_safe_json(opened.resp), headers=headers)


def _stream_chat(opened, stack, decision, started, headers):
    async def generate():
        usage_seen: dict = {}
        try:
            async for line in opened.resp.aiter_lines():
                if chunk_usage := _line_usage(line):
                    usage_seen = chunk_usage
                yield f"{line}\n"
            _record_usage(opened.step.provider.id, opened.model, usage_seen)
            smart_router.report_outcome_sync(decision, opened.step.slot.health_key, True, latency_ms=(time.monotonic() - started) * 1000)
        except Exception as e:
            smart_router.report_outcome_sync(decision, opened.step.slot.health_key, False, error=str(e))
            log.error("openai_compat_stream_error", error=sanitize_error(str(e)))
            yield f"data: {json.dumps({'error': {'message': sanitize_error(str(e))}})}\n\n"
        finally:
            await opened.attempt_stack.aclose()
            await stack.aclose()
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **headers})


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    settings = get_settings()
    body = await request.json()
    prompt_tokens = token_service.count_tokens(body.get("messages") or [])
    decision = await smart_router.decide(body, str(body.get("model", "")), prompt_tokens, request.headers, surface="chat_completions")
    _, routed_model, _ = decision.as_pick()
    route_headers = {"X-Bipolar-Route": decision.to_header(), "X-Bipolar-Decision-Id": decision.decision_id}
    started = time.monotonic()
    stream = bool(body.get("stream"))

    # Un destino anthropic NO activo requeriría traducir el formato OAI→Anthropic
    # (litellm corre con el config del activo): esos pasos se excluyen del plan.
    plan = [s for s in decision.plan if not (s.provider.litellm_prefix == "anthropic" and not s.is_active)]
    if not plan:
        single = _fallback_step(routed_model)
        if single is None:
            return JSONResponse(status_code=502, content=upstream.openai_error_body(502, "Sin provider configurado"), headers=route_headers)
        plan = [single]

    timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
    stack = AsyncExitStack()
    client = await stack.enter_async_context(httpx.AsyncClient(timeout=timeout))

    async def open_attempt(step: PlanStep):
        if stream:
            return await _open_attempt_stream(client, step, body, settings)
        return await _open_attempt_plain(client, step, body, settings)

    try:
        result = await upstream.attempt_plan(plan, open_attempt, lambda s: s.provider.id, smart_router.mark_step_health)
    except Exception:
        await stack.aclose()
        raise

    if not result.ok:
        await stack.aclose()
        return _exhausted_response(decision, result.failures, route_headers)

    opened: OpenChat = result.upstream
    headers = {**route_headers, "X-Bipolar-Target": result.step.label, "X-Bipolar-Attempts": str(len(result.failures) + 1)}
    smart_router.note_success(decision, result.step, body)

    if not stream:
        return await _respond_plain(opened, stack, decision, started, headers)

    return _stream_chat(opened, stack, decision, started, headers)
