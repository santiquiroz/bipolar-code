import asyncio
import os

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger


log = get_logger(__name__)

_GATEWAY_URL = "http://127.0.0.1:8000/v1/chat/completions"
_POLL_TIMEOUT_SECONDS = 25
_RETRY_DELAY_SECONDS = 5

_chat_histories: dict[int, list[dict]] = {}
_logged_disallowed_chat_ids: set[int] = set()
_empty_allowlist_warning_logged = False


def parse_allowed_chat_ids(raw: str) -> set[int]:
    """Convierte una lista CSV en identificadores de chat válidos."""
    chat_ids = set()
    for entry in raw.split(","):
        try:
            chat_ids.add(int(entry.strip()))
        except ValueError:
            continue
    return chat_ids


def chunk_text(text: str, limit: int = 4096) -> list[str]:
    """Divide texto conservando líneas completas siempre que sea posible."""
    if not text:
        return []
    if limit <= 0:
        raise ValueError("limit debe ser mayor que cero")

    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]

        if len(current) + len(line) <= limit:
            current += line
        else:
            if current:
                chunks.append(current)
            current = line

    if current:
        chunks.append(current)
    return chunks


def build_chat_body(
    history: list[dict],
    user_text: str,
    model: str = "claude-sonnet-4-6",
) -> dict:
    """Construye el cuerpo OpenAI para una conversación de Telegram."""
    return {
        "model": model,
        "messages": history + [{"role": "user", "content": user_text}],
        "stream": False,
    }


def trim_history(history: list[dict], max_turns: int = 10) -> list[dict]:
    """Conserva los mensajes correspondientes a los turnos más recientes."""
    max_messages = max(0, max_turns * 2)
    if len(history) <= max_messages:
        return history
    if max_messages == 0:
        return []
    return history[-max_messages:]


def _log_empty_allowlist_once() -> None:
    """Registra una sola vez que el bot no tiene chats autorizados."""
    global _empty_allowlist_warning_logged
    if _empty_allowlist_warning_logged:
        return
    log.warning("telegram_bot_empty_allowlist")
    _empty_allowlist_warning_logged = True


def _log_disallowed_chat_once(chat_id: int) -> None:
    """Registra una sola vez cada chat ignorado por la lista de acceso."""
    if chat_id in _logged_disallowed_chat_ids:
        return
    log.debug("telegram_bot_chat_not_allowed", chat_id=chat_id)
    _logged_disallowed_chat_ids.add(chat_id)


async def _get_updates(
    client: httpx.AsyncClient,
    telegram_url: str,
    offset: int,
) -> list[dict]:
    """Obtiene el siguiente lote de actualizaciones de Telegram."""
    response = await client.get(
        f"{telegram_url}/getUpdates",
        params={"timeout": _POLL_TIMEOUT_SECONDS, "offset": offset},
    )
    response.raise_for_status()
    payload = response.json()
    updates = payload.get("result", [])
    if not payload.get("ok") or not isinstance(updates, list):
        raise ValueError("Respuesta inválida de Telegram")
    return updates


async def _request_completion(
    client: httpx.AsyncClient,
    history: list[dict],
    user_text: str,
) -> str:
    """Solicita una respuesta al gateway local de modelos."""
    response = await client.post(
        _GATEWAY_URL,
        headers={"x-api-key": get_settings().ui_api_key},
        json=build_chat_body(history, user_text),
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("Respuesta inválida del gateway local")
    return content


async def _send_reply(
    client: httpx.AsyncClient,
    telegram_url: str,
    chat_id: int,
    text: str,
) -> None:
    """Envía una respuesta de texto plano a Telegram."""
    for chunk in chunk_text(text):
        response = await client.post(
            f"{telegram_url}/sendMessage",
            json={"chat_id": chat_id, "text": chunk},
        )
        response.raise_for_status()


async def _handle_update(
    client: httpx.AsyncClient,
    telegram_url: str,
    update: dict,
    allowed_chat_ids: set[int],
) -> None:
    """Procesa un mensaje de texto autorizado y conserva su historial."""
    message = update.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("text"), str):
        return

    chat = message.get("chat")
    if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
        return

    chat_id = chat["id"]
    if chat_id not in allowed_chat_ids:
        _log_disallowed_chat_once(chat_id)
        return

    user_text = message["text"]
    history = _chat_histories.get(chat_id, [])
    assistant_text = await _request_completion(client, history, user_text)
    _chat_histories[chat_id] = trim_history(
        history
        + [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]
    )
    await _send_reply(client, telegram_url, chat_id, assistant_text)


async def run_telegram_bot() -> None:
    """Ejecuta el bot opt-in de Telegram mediante long polling."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        log.info("telegram_bot_disabled_no_token")
        return

    allowed_chat_ids = parse_allowed_chat_ids(
        os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
    )
    if not allowed_chat_ids:
        _log_empty_allowlist_once()
        return

    telegram_url = f"https://api.telegram.org/bot{token}"
    timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
    offset = 0
    log.info("telegram_bot_started", allowed_chats=len(allowed_chat_ids))

    while True:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                while True:
                    updates = await _get_updates(client, telegram_url, offset)
                    for update in updates:
                        update_id = update.get("update_id")
                        if isinstance(update_id, int):
                            offset = max(offset, update_id + 1)
                        await _handle_update(
                            client,
                            telegram_url,
                            update,
                            allowed_chat_ids,
                        )
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as exc:
            log.warning(
                "telegram_bot_network_failed",
                error=type(exc).__name__,
            )
            await asyncio.sleep(_RETRY_DELAY_SECONDS)
        except Exception as exc:
            log.warning("telegram_bot_loop_failed", error=str(exc))
            await asyncio.sleep(_RETRY_DELAY_SECONDS)
