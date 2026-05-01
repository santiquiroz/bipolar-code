import asyncio
import json
import os
import re
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.utils import sanitize_error as _sanitize_error
from app.services import providers_service, token_service, usage_tracker
from app.services.pricing_service import estimate_cost

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


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/v1/messages")
async def messages_passthrough(request: Request):
    settings = get_settings()
    body = await request.json()

    messages = body.get("messages", [])
    model = body.get("model", "__default__")

    active = providers_service.get_active_provider()
    active_provider_id = active.id if active else "unknown"
    is_anthropic = active and active.litellm_prefix == "anthropic"

    ctx_window = token_service.get_context_window(model)
    used = token_service.count_tokens(messages)
    truncated = False

    if ctx_window > 0 and used >= int(ctx_window * 0.9):
        messages = token_service.truncate_messages(messages, ctx_window)
        body["messages"] = messages
        truncated = True

    ctx_pct = int(used / ctx_window * 100) if ctx_window else 0
    response_headers = {
        "X-Context-Usage": f"{used}/{ctx_window} tokens ({ctx_pct}%)",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }

    usage_buf: dict = {"input_tokens": 0, "output_tokens": 0}
    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    async def generate():
        try:
            timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:

                if is_anthropic:
                    # Anthropic provider: passthrough to litellm /v1/messages
                    forward_headers = {
                        "Authorization": f"Bearer {settings.proxy_api_key}",
                        "Content-Type": "application/json",
                    }
                    for h in ("anthropic-version", "anthropic-beta"):
                        if h in request.headers:
                            forward_headers[h] = request.headers[h]

                    async with client.stream(
                        "POST", f"{settings.proxy_url}/v1/messages",
                        json=body, headers=forward_headers,
                    ) as resp:
                        if resp.status_code >= 400:
                            raw = await resp.aread()
                            try:
                                err_msg = json.loads(raw).get("error", {}).get("message") or raw.decode()
                            except Exception:
                                err_msg = raw.decode(errors="replace")
                            yield _sse_error(_sanitize_error(err_msg))
                            return
                        async for line in resp.aiter_lines():
                            if line.startswith("data: "):
                                try:
                                    ev = json.loads(line[6:])
                                    etype = ev.get("type", "")
                                    if etype == "message_start":
                                        usage_buf["input_tokens"] = ev.get("message", {}).get("usage", {}).get("input_tokens", 0)
                                    elif etype == "message_delta":
                                        usage_buf["output_tokens"] = ev.get("usage", {}).get("output_tokens", 0)
                                    elif etype == "message_stop":
                                        _record_usage(active_provider_id, model, usage_buf, truncated)
                                except Exception as e:
                                    log.warning("event_parse_failed", error=str(e))
                            # Yield ALL lines including empty ones — empty lines are SSE event separators
                            yield f"{line}\n"

                else:
                    # Non-Anthropic: call provider directly with OAI format
                    provider_model = (active.active_model or model) if active else model
                    max_tools = active.max_tools if active else 0
                    model_info = active.model_info if active else {}
                    is_claude = _is_claude_model(provider_model)
                    # Capacidades dinámicas detectadas al seleccionar el modelo
                    include_tools = model_info.get("supports_tools", is_claude)
                    strip_images = not model_info.get("supports_vision", True)
                    system_as_user = not model_info.get("supports_system_prompt", True)
                    blocked_tools = set() if is_claude else {"Agent"}
                    system_prefix = "" if is_claude else _NON_CLAUDE_SYSTEM_PREFIX
                    oai_body = _anthropic_to_oai_request(
                        body, provider_model, max_tools, blocked_tools, system_prefix,
                        include_tools=include_tools,
                        strip_images=strip_images,
                        system_as_user=system_as_user,
                    )

                    # Cap max_tokens con los límites conocidos del modelo
                    ctx_limit = model_info.get("context_window", 0)
                    out_limit = model_info.get("max_output_tokens", 0)
                    if (ctx_limit > 0 or out_limit > 0) and oai_body.get("max_tokens"):
                        ctx_cap = max(512, ctx_limit - token_service.count_tokens(body.get("messages", [])) - 256) if ctx_limit > 0 else oai_body["max_tokens"]
                        out_cap = out_limit if out_limit > 0 else oai_body["max_tokens"]
                        oai_body["max_tokens"] = min(oai_body["max_tokens"], ctx_cap, out_cap)

                    # Build provider URL directly (bypass litellm)
                    if active and active.api_base:
                        provider_url = _chat_completions_url(active.api_base)
                    else:
                        provider_url = f"{settings.proxy_url}/v1/chat/completions"

                    # Auth
                    api_key = ""
                    if active and active.auth_env_var:
                        api_key = os.environ.get(active.auth_env_var, "")
                    if not api_key:
                        api_key = "no-key"

                    forward_headers = {
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    }
                    if active and active.extra_headers:
                        forward_headers.update(active.extra_headers)

                    log.info(
                        "oai_direct_request",
                        provider=active_provider_id,
                        url=provider_url,
                        model=provider_model,
                        msgs=len(oai_body.get("messages", [])),
                        has_tools=bool(oai_body.get("tools")),
                        num_tools=len(oai_body.get("tools", [])),
                        ctx_limit=ctx_limit,
                        max_tokens=oai_body.get("max_tokens"),
                    )

                    # Primera llamada — con retry automático si falla por context window
                    retry_body: dict | None = None
                    detected_ctx = 0

                    async with client.stream(
                        "POST", provider_url, json=oai_body, headers=forward_headers,
                    ) as resp:
                        if resp.status_code >= 400:
                            raw = await resp.aread()
                            try:
                                err_msg = json.loads(raw).get("error", {}).get("message") or raw.decode()
                            except Exception:
                                err_msg = raw.decode(errors="replace")

                            ctx_match = re.search(r'maximum context length is (\d+)', err_msg, re.IGNORECASE)
                            out_match = re.search(r'maximum.*?(?:output|completion|generated).*?(?:tokens?|length).*?(\d+)', err_msg, re.IGNORECASE)
                            if (ctx_match or out_match) and oai_body.get("max_tokens"):
                                if ctx_match:
                                    detected_ctx = int(ctx_match.group(1))
                                    msg_tok_match = re.search(r'\((\d+) in the messages?', err_msg, re.IGNORECASE)
                                    msg_tokens = int(msg_tok_match.group(1)) if msg_tok_match else token_service.count_tokens(body.get("messages", []))
                                    new_max = max(512, detected_ctx - msg_tokens - 256)
                                    if active:
                                        _save_model_info(active, "context_window", detected_ctx)
                                else:
                                    new_max = int(out_match.group(1))
                                    if active:
                                        _save_model_info(active, "max_output_tokens", new_max)
                                retry_body = {**oai_body, "max_tokens": new_max}
                                log.info("limit_detected_retrying", new_max_tokens=new_max, error_snippet=err_msg[:120])
                            else:
                                log.error("provider_error", status=resp.status_code, provider=active_provider_id, error=_sanitize_error(err_msg))
                                yield _sse_error(_sanitize_error(err_msg))
                                return
                        else:
                            async for chunk in _oai_stream_to_anthropic(resp.aiter_lines(), message_id, model, usage_buf):
                                yield chunk

                    # Retry con max_tokens ajustado
                    if retry_body:
                        async with client.stream(
                            "POST", provider_url, json=retry_body, headers=forward_headers,
                        ) as resp2:
                            if resp2.status_code >= 400:
                                raw2 = await resp2.aread()
                                try:
                                    err2 = json.loads(raw2).get("error", {}).get("message") or raw2.decode()
                                except Exception:
                                    err2 = raw2.decode(errors="replace")
                                log.error("provider_error_after_retry", status=resp2.status_code, provider=active_provider_id)
                                yield _sse_error(_sanitize_error(err2))
                                return
                            async for chunk in _oai_stream_to_anthropic(resp2.aiter_lines(), message_id, model, usage_buf):
                                yield chunk

                    _record_usage(active_provider_id, model, usage_buf, truncated)

        except Exception as e:
            log.error("messages_passthrough_error", error=_sanitize_error(str(e)))
            yield _sse_error(_sanitize_error(str(e)))

    return StreamingResponse(generate(), media_type="text/event-stream", headers=response_headers)


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
