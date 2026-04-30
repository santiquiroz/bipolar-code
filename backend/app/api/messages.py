import asyncio
import json

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import providers_service, token_service, usage_tracker
from app.services.pricing_service import estimate_cost

log = get_logger(__name__)
router = APIRouter(tags=["messages"])

_background_tasks: set[asyncio.Task] = set()


@router.post("/v1/messages")
async def messages_passthrough(request: Request):
    settings = get_settings()
    body = await request.json()

    messages = body.get("messages", [])
    model = body.get("model", "__default__")

    ctx_window = token_service.get_context_window(model)
    used = token_service.count_tokens(messages)
    truncated = False

    if ctx_window > 0 and used >= int(ctx_window * 0.9):
        messages = token_service.truncate_messages(messages, ctx_window)
        body["messages"] = messages
        truncated = True

    ctx_pct = int(used / ctx_window * 100) if ctx_window else 0
    extra_headers = {
        "X-Context-Usage": f"{used}/{ctx_window} tokens ({ctx_pct}%)",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }

    forward_headers = {
        "Authorization": f"Bearer {settings.proxy_api_key}",
        "Content-Type": "application/json",
    }
    for h in ("anthropic-version", "anthropic-beta", "x-api-key"):
        if h in request.headers:
            forward_headers[h] = request.headers[h]

    usage_buf: dict = {"input_tokens": 0, "output_tokens": 0}

    async def generate():
        try:
            timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{settings.proxy_url}/v1/messages",
                    json=body,
                    headers=forward_headers,
                ) as resp:
                    if resp.status_code >= 400:
                        raw = await resp.aread()
                        yield f"data: {raw.decode(errors='replace')}\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                event = json.loads(line[6:])
                                etype = event.get("type", "")
                                if etype == "message_start":
                                    usage_buf["input_tokens"] = event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                                elif etype == "message_delta":
                                    usage_buf["output_tokens"] = event.get("usage", {}).get("output_tokens", 0)
                                elif etype == "message_stop":
                                    active = providers_service.get_active_provider()
                                    pid = active.id if active else "unknown"
                                    cost = estimate_cost(pid, model, usage_buf["input_tokens"], usage_buf["output_tokens"])
                                    task = asyncio.create_task(
                                        usage_tracker.record(
                                            pid, model,
                                            usage_buf["input_tokens"],
                                            usage_buf["output_tokens"],
                                            cost, truncated,
                                        )
                                    )
                                    _background_tasks.add(task)
                                    task.add_done_callback(_background_tasks.discard)
                            except Exception:
                                pass
                        if line:
                            yield f"{line}\n"
        except Exception as e:
            log.error("messages_passthrough_error", error=str(e))
            err = {"type": "error", "error": {"type": "api_error", "message": str(e)}}
            yield f"data: {json.dumps(err)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers=extra_headers)


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
