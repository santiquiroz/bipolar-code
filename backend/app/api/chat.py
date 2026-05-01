import json
import os
import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any, Optional
from app.services import providers_service, token_service
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.utils import sanitize_error as _sanitize_error

log = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: Optional[str] = None


@router.post("/completions")
async def chat_completions(body: ChatRequest):
    settings = get_settings()

    provider = providers_service.get_active_provider()
    active_provider_id = provider.id if provider else "unknown"
    is_anthropic = provider and provider.litellm_prefix == "anthropic"

    messages_raw = [m.model_dump() for m in body.messages]

    if is_anthropic:
        # Anthropic: pasar por litellm que ya maneja el formato
        model = "claude-sonnet-4-6"
        url = f"{settings.proxy_url}/v1/chat/completions"
        forward_headers = {"Authorization": f"Bearer {settings.proxy_api_key}"}
    else:
        # No-Anthropic: llamar al proveedor directamente, igual que /v1/messages
        model = provider.active_model if provider and provider.active_model else "gpt-4o"
        api_base = provider.api_base if provider else settings.proxy_url
        url = f"{api_base.rstrip('/')}/chat/completions"
        api_key = ""
        if provider and provider.auth_env_var:
            api_key = os.environ.get(provider.auth_env_var, "")
        forward_headers = {
            "Authorization": f"Bearer {api_key or 'no-key'}",
            "Content-Type": "application/json",
        }
        if provider and provider.extra_headers:
            forward_headers.update(provider.extra_headers)

    payload = {"model": model, "messages": messages_raw, "stream": True}
    log.info("chat_request", provider=active_provider_id, model=model, n_messages=len(messages_raw))

    ctx_window = token_service.get_context_window(model)
    used = token_service.count_tokens(messages_raw)
    ctx_pct = int(used / ctx_window * 100) if ctx_window else 0

    async def generate():
        try:
            timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=payload, headers=forward_headers) as resp:
                    if resp.status_code >= 400:
                        raw = await resp.aread()
                        try:
                            err_msg = json.loads(raw).get("error", {}).get("message") or raw.decode()
                        except Exception:
                            err_msg = raw.decode(errors="replace")
                        err = json.dumps({"error": {"message": _sanitize_error(err_msg), "status": resp.status_code}})
                        log.warning("chat_upstream_error", status=resp.status_code, provider=active_provider_id)
                        yield f"data: {err}\n\ndata: [DONE]\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line:
                            yield f"{line}\n\n"
        except Exception as e:
            sanitized = _sanitize_error(str(e))
            log.error("chat_stream_error", error=sanitized, provider=active_provider_id)
            yield f"data: {json.dumps({'error': {'message': sanitized}})}\n\ndata: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Context-Usage": f"{used}/{ctx_window} tokens ({ctx_pct}%)",
        },
    )
