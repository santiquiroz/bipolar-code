import json
import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Any, Optional
from app.services import providers_service, token_service
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.utils import sanitize_error as _sanitize_error

log = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


async def _litellm_reachable(proxy_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=2.0, read=3.0)) as client:
            resp = await client.get(f"{proxy_url}/health/readiness")
            return resp.status_code < 400
    except Exception:
        return False


class ChatMessage(BaseModel):
    role: str
    content: Any  # str or list of content parts (text / image_url)


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: Optional[str] = None


@router.post("/completions")
async def chat_completions(body: ChatRequest):
    settings = get_settings()

    _PROXY_ALIASES = set(providers_service.PROXY_ALIASES)
    # Capturar provider al inicio — antes de cualquier await que permita
    # un switch de proveedor concurrente
    provider = providers_service.get_active_provider()
    active_provider_id = provider.id if provider else "unknown"

    if not await _litellm_reachable(settings.proxy_url):
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "El proxy LiteLLM no está disponible."}},
        )

    model = body.model
    if not model or (model not in _PROXY_ALIASES and provider and model == provider.active_model):
        # Modelo nativo del proveedor → usar alias primario de LiteLLM
        model = "claude-sonnet-4-6"

    messages_raw = [m.model_dump() for m in body.messages]
    payload = {
        "model": model,
        "messages": messages_raw,
        "stream": True,
    }
    log.info("chat_request", model=model, n_messages=len(body.messages))

    ctx_window = token_service.get_context_window(model)
    used = token_service.count_tokens(messages_raw)
    ctx_pct = int(used / ctx_window * 100) if ctx_window else 0

    async def generate():
        try:
            timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{settings.proxy_url}/v1/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.proxy_api_key}"},
                ) as resp:
                    if resp.status_code >= 400:
                        raw = await resp.aread()
                        err_msg = _sanitize_error(raw.decode(errors="replace"))
                        err = json.dumps({
                            "error": {
                                "message": err_msg,
                                "status": resp.status_code,
                            }
                        })
                        log.warning("chat_upstream_error", status=resp.status_code, provider=active_provider_id)
                        yield f"data: {err}\n\ndata: [DONE]\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line:
                            yield f"{line}\n\n"
        except Exception as e:
            sanitized = _sanitize_error(str(e))
            log.error("chat_stream_error", error=sanitized, provider=active_provider_id)
            err = json.dumps({"error": {"message": sanitized}})
            yield f"data: {err}\n\ndata: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Context-Usage": f"{used}/{ctx_window} tokens ({ctx_pct}%)",
        },
    )
