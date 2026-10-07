import asyncio
import json
import re
import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.utils import sanitize_error as _sanitize_error
from app.services import compression_service, credentials, providers_service, smart_router, token_service, upstream, usage_tracker
from app.services.pricing_service import estimate_cost
from app.services.smart_router import PlanStep

log = get_logger(__name__)
router = APIRouter(tags=["messages"])

_background_tasks: set[asyncio.Task] = set()

_STOP_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
}


def _chat_completions_url(api_base: str) -> str:
    return f"{api_base.rstrip('/')}/chat/completions"


def _is_claude_model(model_name: str) -> bool:
    return "claude" in model_name.lower()


# ── Anthropic → OpenAI conversion ────────────────────────────────────────────

def _content_block_to_oai(block: dict) -> dict | None:
    btype = block.get("type")
    if btype == "text":
        return {"type": "text", "text": block.get("text", "")}
    if btype == "image":
        src = block.get("source", {})
        if src.get("type") == "base64":
            url = f"data:{src['media_type']};base64,{src['data']}"
        else:
            url = src.get("url", "")
        return {"type": "image_url", "image_url": {"url": url}}
    return None


_NON_CLAUDE_SYSTEM_PREFIX = (
    "You are a direct, helpful assistant. "
    "Respond with text for conversational messages. "
    "Only use tools when the task explicitly requires reading files, running commands, or modifying code. "
    "Never use tools to ask clarifying questions or greet the user.\n\n"
)


def _anthropic_to_oai_messages(body: dict, system_prefix: str = "", strip_images: bool = False, system_as_user: bool = False) -> list[dict]:
    oai_messages: list[dict] = []

    system = body.get("system")
    system_text = ""
    if system:
        if isinstance(system, str):
            system_text = system_prefix + system
        elif isinstance(system, list):
            text = "\n".join(b.get("text", "") for b in system if b.get("type") == "text")
            if text:
                system_text = system_prefix + text
    elif system_prefix:
        system_text = system_prefix.strip()

    if system_text:
        if system_as_user:
            oai_messages.append({"role": "user", "content": f"<context>\n{system_text}\n</context>"})
            oai_messages.append({"role": "assistant", "content": "Understood."})
        else:
            oai_messages.append({"role": "system", "content": system_text})

    for msg in body.get("messages", []):
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            oai_messages.append({"role": role, "content": content})
            continue

        tool_calls = []
        oai_parts = []
        tool_results = []

        for block in content:
            btype = block.get("type")
            if btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
            elif btype == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, list):
                    result_content = "\n".join(
                        b.get("text", "") for b in result_content if b.get("type") == "text"
                    )
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": result_content,
                })
            else:
                if strip_images and btype == "image":
                    continue
                part = _content_block_to_oai(block)
                if part:
                    oai_parts.append(part)

        if tool_results:
            oai_messages.extend(tool_results)
        elif tool_calls:
            oai_messages.append({"role": "assistant", "tool_calls": tool_calls, "content": ""})
        else:
            oai_messages.append({
                "role": role,
                "content": oai_parts if len(oai_parts) != 1 or oai_parts[0]["type"] != "text"
                           else oai_parts[0]["text"],
            })

    return oai_messages


def _anthropic_to_oai_request(body: dict, model: str, max_tools: int = 0, blocked_tools: set[str] | None = None, system_prefix: str = "", include_tools: bool = True, strip_images: bool = False, system_as_user: bool = False) -> dict:
    req: dict = {
        "model": model,
        "messages": _anthropic_to_oai_messages(body, system_prefix, strip_images, system_as_user),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if "max_tokens" in body:
        req["max_tokens"] = body["max_tokens"]
    if "temperature" in body:
        req["temperature"] = body["temperature"]
    if "stop_sequences" in body:
        req["stop"] = body["stop_sequences"]
    if include_tools and (tools := body.get("tools")):
        blocked = blocked_tools or set()
        oai_tools = [
            {"type": "function", "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            }}
            for t in tools
            if t.get("name") not in blocked
        ]
        if max_tools > 0:
            oai_tools = oai_tools[:max_tools]
        req["tools"] = oai_tools
        if tc := body.get("tool_choice"):
            if isinstance(tc, dict) and tc.get("type") == "auto":
                req["tool_choice"] = "auto"
            elif isinstance(tc, dict) and tc.get("type") == "any":
                req["tool_choice"] = "required"
            elif isinstance(tc, dict) and tc.get("type") == "tool":
                req["tool_choice"] = {"type": "function", "function": {"name": tc.get("name", "")}}
    return req


# ── OpenAI SSE → Anthropic SSE conversion ────────────────────────────────────

def _sse(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _sse_error(message: str) -> str:
    return _sse("error", {"type": "error", "error": {"type": "api_error", "message": message}})


async def _oai_stream_to_anthropic(resp_iter, message_id: str, model: str, usage_buf: dict):
    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": message_id, "type": "message", "role": "assistant",
            "content": [], "model": model,
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 1},
        },
    })

    text_started = False
    # tool_blocks: openai_index → {"id", "name", "args_buf", "block_idx"}
    tool_blocks: dict[int, dict] = {}
    next_block_idx = 0  # next Anthropic block index to assign
    stop_reason = "end_turn"
    input_tokens = 0
    output_tokens = 0

    async for line in resp_iter:
        if not line.startswith("data: "):
            if line:
                log.debug("oai_stream_non_data_line", line=line[:200])
            continue
        raw = line[6:].strip()
        if raw == "[DONE]":
            break
        try:
            chunk = json.loads(raw)
        except Exception:
            log.warning("oai_stream_json_parse_error", raw=raw[:200])
            continue

        # usage from stream_options
        if usage := chunk.get("usage"):
            input_tokens = usage.get("prompt_tokens", input_tokens)
            output_tokens = usage.get("completion_tokens", output_tokens)

        choices = chunk.get("choices", [])
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        # Text content
        if text := delta.get("content"):
            if not text_started:
                block_idx = next_block_idx
                next_block_idx += 1
                yield _sse("content_block_start", {
                    "type": "content_block_start", "index": block_idx,
                    "content_block": {"type": "text", "text": ""},
                })
                text_started = True
                text_block_idx = block_idx
            yield _sse("content_block_delta", {
                "type": "content_block_delta", "index": text_block_idx,
                "delta": {"type": "text_delta", "text": text},
            })

        # Tool calls
        for tc in delta.get("tool_calls", []):
            oai_idx = tc.get("index", 0)
            fn = tc.get("function", {})

            if tc.get("id") and oai_idx not in tool_blocks:
                # New tool call: open a block
                block_idx = next_block_idx
                next_block_idx += 1
                tool_blocks[oai_idx] = {
                    "id": tc["id"],
                    "name": fn.get("name", ""),
                    "args_buf": "",
                    "block_idx": block_idx,
                }
                yield _sse("content_block_start", {
                    "type": "content_block_start", "index": block_idx,
                    "content_block": {
                        "type": "tool_use", "id": tc["id"],
                        "name": fn.get("name", ""), "input": {},
                    },
                })
            elif oai_idx in tool_blocks and tc.get("id"):
                # Update tool id/name if different chunk has it
                tool_blocks[oai_idx]["id"] = tc["id"]

            if args := fn.get("arguments", ""):
                if oai_idx in tool_blocks:
                    blk = tool_blocks[oai_idx]
                    blk["args_buf"] += args
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta", "index": blk["block_idx"],
                        "delta": {"type": "input_json_delta", "partial_json": args},
                    })

        if finish_reason:
            stop_reason = _STOP_REASON_MAP.get(finish_reason, "end_turn")

    # Emit minimal text block if the model produced nothing (guards against empty stream)
    if not text_started and not tool_blocks:
        block_idx = next_block_idx
        next_block_idx += 1
        yield _sse("content_block_start", {
            "type": "content_block_start", "index": block_idx,
            "content_block": {"type": "text", "text": ""},
        })
        yield _sse("content_block_delta", {
            "type": "content_block_delta", "index": block_idx,
            "delta": {"type": "text_delta", "text": " "},
        })
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": block_idx})
    else:
        # Close open blocks
        if text_started:
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_block_idx})
        for blk in tool_blocks.values():
            yield _sse("content_block_stop", {"type": "content_block_stop", "index": blk["block_idx"]})

    usage_buf["input_tokens"] = input_tokens
    usage_buf["output_tokens"] = output_tokens

    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    })
    yield _sse("message_stop", {"type": "message_stop"})


# ── Failover antes del primer byte ─────────────────────────────────────────────

@dataclass
class OpenUpstream:
    kind: str  # "native" (directo o vía litellm) u "oai"
    resp: Any
    attempt_stack: AsyncExitStack
    step: PlanStep
    model: str


def _error_message(raw: bytes) -> str:
    try:
        return json.loads(raw).get("error", {}).get("message") or raw.decode()
    except Exception:
        return raw.decode(errors="replace")


def _native_request(step: PlanStep, body: dict, request: Request, settings, model: str) -> tuple[str, dict, dict]:
    provider = step.provider
    payload = {**body, "model": step.model or provider.active_model or model}
    headers = {"Content-Type": "application/json"}
    for name in ("anthropic-version", "anthropic-beta"):
        if name in request.headers:
            headers[name] = request.headers[name]
    if provider.litellm_prefix == "anthropic" and not provider.anthropic_native and step.is_active:
        headers["Authorization"] = f"Bearer {settings.proxy_api_key}"
        return f"{settings.proxy_url}/v1/messages", headers, payload
    key = credentials.api_key_for(step.slot)
    if key:
        headers["x-api-key"] = key
    return f"{provider.api_base.rstrip('/').removesuffix('/v1')}/v1/messages", headers, payload


def _oai_request(step: PlanStep, body: dict, model: str) -> tuple[str, dict, dict]:
    provider = step.provider
    provider_model = step.model or provider.active_model or model
    info = provider.model_info or {}
    is_claude = _is_claude_model(provider_model)
    oai_body = _anthropic_to_oai_request(
        body, provider_model, provider.max_tools, set() if is_claude else {"Agent"},
        "" if is_claude else _NON_CLAUDE_SYSTEM_PREFIX,
        include_tools=info.get("supports_tools", is_claude),
        strip_images=not info.get("supports_vision", True),
        system_as_user=not info.get("supports_system_prompt", True),
    )
    ctx_limit = info.get("context_window", 0)
    out_limit = info.get("max_output_tokens", 0)
    if (ctx_limit > 0 or out_limit > 0) and oai_body.get("max_tokens"):
        ctx_cap = max(512, ctx_limit - token_service.count_tokens(body.get("messages", [])) - 256) if ctx_limit > 0 else oai_body["max_tokens"]
        out_cap = out_limit if out_limit > 0 else oai_body["max_tokens"]
        oai_body["max_tokens"] = min(oai_body["max_tokens"], ctx_cap, out_cap)
    if provider.api_base:
        url = providers_service.oai_chat_completions_url(provider)
    else:
        url = f"{get_settings().proxy_url}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {credentials.api_key_for(step.slot) or 'no-key'}", "Content-Type": "application/json"}
    if provider.extra_headers:
        headers.update(provider.extra_headers)
    log.info(
        "oai_direct_request",
        provider=provider.id,
        url=url,
        model=provider_model,
        msgs=len(oai_body.get("messages", [])),
        has_tools=bool(oai_body.get("tools")),
        num_tools=len(oai_body.get("tools", [])),
        ctx_limit=ctx_limit,
        max_tokens=oai_body.get("max_tokens"),
    )
    return url, headers, oai_body


def _context_retry_body(err_msg: str, oai_body: dict, body: dict, provider) -> dict | None:
    if not oai_body.get("max_tokens"):
        return None
    ctx_match = re.search(r'maximum context length is (\d+)', err_msg, re.IGNORECASE)
    out_match = re.search(r'maximum.*?(?:output|completion|generated).*?(?:tokens?|length).*?(\d+)', err_msg, re.IGNORECASE)
    if not (ctx_match or out_match):
        return None
    if ctx_match:
        detected_ctx = int(ctx_match.group(1))
        msg_match = re.search(r'\((\d+) in the messages?', err_msg, re.IGNORECASE)
        msg_tokens = int(msg_match.group(1)) if msg_match else token_service.count_tokens(body.get("messages", []))
        new_max = max(512, detected_ctx - msg_tokens - 256)
        _save_model_info(provider, "context_window", detected_ctx)
    else:
        new_max = int(out_match.group(1))
        _save_model_info(provider, "max_output_tokens", new_max)
    log.info("limit_detected_retrying", new_max_tokens=new_max, error_snippet=err_msg[:120])
    return {**oai_body, "max_tokens": new_max}


async def _open_stream(client: httpx.AsyncClient, attempt_stack: AsyncExitStack, url: str, headers: dict, payload: dict) -> upstream.Opened | upstream.Failed:
    try:
        resp = await attempt_stack.enter_async_context(client.stream("POST", url, json=payload, headers=headers))
    except httpx.HTTPError as e:
        await attempt_stack.aclose()
        return upstream.Failed(None, str(e), e)
    if resp.status_code >= 400:
        raw = await resp.aread()
        await attempt_stack.aclose()
        return upstream.Failed(resp.status_code, _error_message(raw))
    return upstream.Opened(resp)


def _is_native_step(step: PlanStep) -> bool:
    return step.provider.anthropic_native or (step.provider.litellm_prefix == "anthropic" and not step.is_active)


def _local_unreachable(step: PlanStep, failed: upstream.Failed) -> upstream.Failed:
    if failed.exc is not None and step.provider.anthropic_native and providers_service._is_local_base(step.provider.api_base):
        return upstream.Failed(None, f"Servidor local no responde en {step.provider.api_base}. Inícialo desde Providers → llama.cpp (Start).", failed.exc)
    return failed


async def _open_native(client, step: PlanStep, body: dict, request: Request, settings, model: str) -> upstream.Opened | upstream.Failed:
    url, headers, payload = _native_request(step, body, request, settings, model)
    attempt_stack = AsyncExitStack()
    outcome = await _open_stream(client, attempt_stack, url, headers, payload)
    if isinstance(outcome, upstream.Opened):
        return upstream.Opened(OpenUpstream("native", outcome.upstream, attempt_stack, step, model))
    return _local_unreachable(step, outcome)


async def _open_oai(client, step: PlanStep, body: dict, model: str) -> upstream.Opened | upstream.Failed:
    url, headers, oai_body = _oai_request(step, body, model)
    attempt_stack = AsyncExitStack()
    first = await _open_stream(client, attempt_stack, url, headers, oai_body)
    if isinstance(first, upstream.Opened):
        return upstream.Opened(OpenUpstream("oai", first.upstream, attempt_stack, step, model))
    retry_body = _context_retry_body(first.message, oai_body, body, step.provider) if first.status == 400 and first.exc is None else None
    if retry_body is None:
        return first
    retry_stack = AsyncExitStack()
    second = await _open_stream(client, retry_stack, url, headers, retry_body)
    if isinstance(second, upstream.Opened):
        return upstream.Opened(OpenUpstream("oai", second.upstream, retry_stack, step, model))
    return second


async def _open_attempt(client, step: PlanStep, body: dict, request: Request, settings, model: str) -> upstream.Opened | upstream.Failed:
    if _is_native_step(step) or step.provider.litellm_prefix == "anthropic":
        return await _open_native(client, step, body, request, settings, model)
    return await _open_oai(client, step, body, model)


async def _relay_native(resp, usage_buf: dict):
    async for line in resp.aiter_lines():
        if line.startswith("data: "):
            try:
                ev = json.loads(line[6:])
                etype = ev.get("type", "")
                if etype == "message_start":
                    usage_buf["input_tokens"] = ev.get("message", {}).get("usage", {}).get("input_tokens", 0)
                elif etype == "message_delta":
                    usage_buf["output_tokens"] = ev.get("usage", {}).get("output_tokens", 0)
            except Exception as e:
                log.warning("event_parse_failed", error=str(e))
        yield f"{line}\n"


async def _relay_oai(resp, message_id: str, model: str, usage_buf: dict):
    async for chunk in _oai_stream_to_anthropic(resp.aiter_lines(), message_id, model, usage_buf):
        yield chunk


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/v1/messages")
async def messages_passthrough(request: Request):
    settings = get_settings()
    body = await request.json()

    messages = body.get("messages", [])
    model = body.get("model", "__default__")

    ctx_window = token_service.get_context_window(model)
    used = token_service.count_tokens(messages)

    decision = await smart_router.decide(body, model, used, request.headers, surface="messages")
    active, routed_model, is_active_provider = decision.as_pick()
    truncated = False

    if ctx_window > 0 and used >= int(ctx_window * 0.9):
        compressed = None
        if settings.semantic_compression and active:
            compressed = await compression_service.compress_messages(messages, active, model)
        if compressed:
            messages = compressed
            log.info(
                "semantic_compression_applied",
                before_tokens=used,
                after_tokens=token_service.count_tokens(messages),
            )
        else:
            messages = token_service.truncate_messages(messages, ctx_window)
        body["messages"] = messages
        truncated = True

    ctx_pct = int(used / ctx_window * 100) if ctx_window else 0
    response_headers = {
        "X-Context-Usage": f"{used}/{ctx_window} tokens ({ctx_pct}%)",
        "X-Bipolar-Route": decision.to_header(),
        "X-Bipolar-Decision-Id": decision.decision_id,
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    started = time.monotonic()

    plan = decision.plan
    if not plan and active is not None:
        plan = [PlanStep(active, routed_model, credentials.credential_slots(active)[0], is_active_provider)]
    if not plan:
        return JSONResponse(status_code=502, content=upstream.anthropic_error_body(502, "Sin provider configurado"), headers=response_headers)

    timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
    stack = AsyncExitStack()
    client = await stack.enter_async_context(httpx.AsyncClient(timeout=timeout))

    async def open_attempt(step: PlanStep) -> upstream.Opened | upstream.Failed:
        return await _open_attempt(client, step, body, request, settings, model)

    try:
        result = await upstream.attempt_plan(plan, open_attempt, lambda s: s.provider.id, smart_router.mark_step_health)
    except Exception:
        await stack.aclose()
        raise

    if not result.ok:
        await stack.aclose()
        status = upstream.exhausted_status(result.failures)
        if len(result.failures) == 1 and result.failures[0].kind == "fatal":
            message = _sanitize_error(result.failures[0].message)
        else:
            message = _sanitize_error("Ningún destino respondió: " + upstream.failures_summary(result.failures, lambda s: s.label))
        smart_router.report_outcome_sync(decision, "", False, status=status, error=message)
        return JSONResponse(status_code=status, content=upstream.anthropic_error_body(status, message), headers={**response_headers, "X-Bipolar-Attempts": str(len(result.failures))})

    opened: OpenUpstream = result.upstream
    headers = {**response_headers, "X-Bipolar-Target": result.step.label, "X-Bipolar-Attempts": str(len(result.failures) + 1)}
    smart_router.note_success(decision, result.step, body)
    usage_buf: dict = {"input_tokens": 0, "output_tokens": 0}
    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    async def generate():
        try:
            if opened.kind == "native":
                async for chunk in _relay_native(opened.resp, usage_buf):
                    yield chunk
            else:
                async for chunk in _relay_oai(opened.resp, message_id, opened.model, usage_buf):
                    yield chunk
            _record_usage(opened.step.provider.id, opened.model, usage_buf, truncated)
            smart_router.report_outcome_sync(decision, opened.step.slot.health_key, True, latency_ms=(time.monotonic() - started) * 1000)
        except Exception as e:
            smart_router.report_outcome_sync(decision, opened.step.slot.health_key, False, error=str(e))
            yield _sse_error(_sanitize_error(str(e)))
        finally:
            await opened.attempt_stack.aclose()
            await stack.aclose()

    return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)


def _save_model_info(provider, key: str, value) -> None:
    info = dict(provider.model_info)
    info[key] = value
    try:
        providers_service.update_provider(provider.id, {"model_info": info})
    except Exception:
        pass


def _record_usage(provider_id: str, model: str, usage_buf: dict, truncated: bool) -> None:
    cost = estimate_cost(provider_id, model, usage_buf["input_tokens"], usage_buf["output_tokens"])
    task = asyncio.create_task(
        usage_tracker.record(
            provider_id, model,
            usage_buf["input_tokens"], usage_buf["output_tokens"],
            cost, truncated,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.get("/v1/models")
async def list_models_anthropic():
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": 0, "owned_by": "anthropic"}
            for m in [
                "claude-opus-4-20250514",
                "claude-sonnet-4-20250514",
                "claude-haiku-4-20250514",
                "claude-3-opus-20240229",
                "claude-3-5-sonnet-20241022",
                "claude-3-haiku-20240307",
            ]
        ],
    }
