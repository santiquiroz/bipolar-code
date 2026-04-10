import json
import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any, Optional
from app.services import providers_service
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: Any  # str or list of content parts (text / image_url)


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: Optional[str] = None


@router.post("/completions")
async def chat_completions(body: ChatRequest):
    settings = get_settings()

    model = body.model
    if not model:
        provider = providers_service.get_active_provider()
        model = (provider.active_model if provider else None) or "claude-sonnet-4-6"

    payload = {
        "model": model,
        "messages": [m.model_dump() for m in body.messages],
        "stream": True,
    }
    log.info("chat_request", model=model, n_messages=len(body.messages))

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
                        err = json.dumps({
                            "error": {
                                "message": raw.decode(errors="replace"),
                                "status": resp.status_code,
                            }
                        })
                        yield f"data: {err}\n\ndata: [DONE]\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line:
                            yield f"{line}\n\n"
        except Exception as e:
            log.error("chat_stream_error", error=str(e))
            err = json.dumps({"error": {"message": str(e)}})
            yield f"data: {err}\n\ndata: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
