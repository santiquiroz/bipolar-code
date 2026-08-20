"""
Compresión semántica de conversaciones: resume la parte vieja del historial
con el provider activo en vez de solo truncar. Opt-in (SEMANTIC_COMPRESSION=true).
Cualquier fallo devuelve None y el caller cae al truncado clásico.
"""
import os

import httpx

from app.core.logging import get_logger
from app.models.provider import Provider

log = get_logger(__name__)

KEEP_RECENT_MESSAGES = 8
_MAX_SOURCE_CHARS = 60000

_SUMMARY_PROMPT = (
    "Resume la siguiente conversación entre un usuario y un asistente de código. "
    "Preserva: decisiones tomadas, archivos tocados y sus cambios, errores encontrados, "
    "y el estado actual de la tarea. Sé compacto (máximo ~800 palabras). "
    "Responde SOLO con el resumen.\n\n"
)


def message_to_text(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else []:
        btype = block.get("type", "")
        if btype == "text":
            parts.append(block.get("text", ""))
        elif btype == "tool_use":
            parts.append(f"[tool_use: {block.get('name', '?')}]")
        elif btype == "tool_result":
            parts.append("[tool_result]")
    return "\n".join(p for p in parts if p)


def _starts_with_tool_result(message: dict) -> bool:
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return False
    return content[0].get("type") == "tool_result"


def split_for_compression(messages: list[dict], keep_recent: int = KEEP_RECENT_MESSAGES) -> tuple[list[dict], list[dict]]:
    """(viejos_a_resumir, recientes_intactos) sin partir un par tool_use/tool_result."""
    if len(messages) <= keep_recent:
        return [], messages
    cut = len(messages) - keep_recent
    while cut > 0 and _starts_with_tool_result(messages[cut]):
        cut -= 1
    return messages[:cut], messages[cut:]


def build_summary_request(old_messages: list[dict], model: str) -> dict:
    transcript = "\n".join(f"{m.get('role', '?')}: {message_to_text(m)}" for m in old_messages)
    return {
        "model": model,
        "messages": [{"role": "user", "content": _SUMMARY_PROMPT + transcript[:_MAX_SOURCE_CHARS]}],
        "max_tokens": 1500,
        "stream": False,
    }


async def compress_messages(messages: list[dict], provider: Provider, model: str) -> list[dict] | None:
    old, recent = split_for_compression(messages)
    if not old:
        return None

    url = f"{provider.api_base.rstrip('/')}/chat/completions"
    api_key = os.environ.get(provider.auth_env_var, "") if provider.auth_env_var else ""
    headers = {"Authorization": f"Bearer {api_key or 'no-key'}", "Content-Type": "application/json"}
    if provider.extra_headers:
        headers.update(provider.extra_headers)
    body = build_summary_request(old, provider.active_model or model)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=90.0, write=5.0, pool=5.0)) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            summary = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log.warning("semantic_compression_failed", provider=provider.id, error=str(e)[:200])
        return None

    if not summary or not str(summary).strip():
        return None

    summary_message = {
        "role": "user",
        "content": [{"type": "text", "text": f"[Resumen de la conversación previa]\n{summary}"}],
    }
    return [summary_message] + recent
