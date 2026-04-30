import json
import sys
from functools import lru_cache
from pathlib import Path

import tiktoken

if getattr(sys, "frozen", False):
    _DATA_DIR = Path(sys._MEIPASS) / "app" / "data"
else:
    _DATA_DIR = Path(__file__).parent.parent / "data"


@lru_cache(maxsize=1)
def _encoder():
    return tiktoken.get_encoding("cl100k_base")


@lru_cache(maxsize=1)
def _capabilities() -> dict:
    return json.loads((_DATA_DIR / "model_capabilities.json").read_text(encoding="utf-8"))


def count_tokens(messages: list[dict]) -> int:
    enc = _encoder()
    total = 0
    for message in messages:
        total += 4  # per-message overhead
        content = message.get("content", "")
        if isinstance(content, str):
            total += len(enc.encode(content))
        elif isinstance(content, list):
            for block in content:
                btype = block.get("type", "")
                if btype == "text":
                    total += len(enc.encode(block.get("text", "")))
                elif btype in ("image", "image_url"):
                    source = block.get("source", block)
                    total += 85 if source.get("data") else 765
    return total


def get_context_window(model: str) -> int:
    caps = _capabilities()
    if model in caps:
        return caps[model]["context_window"]
    for key, val in caps.items():
        if key.endswith("/*") and model.startswith(key[:-2]):
            return val["context_window"]
    return caps.get("__default__", {}).get("context_window", 8192)


def supports_vision(model: str) -> bool:
    caps = _capabilities()
    if model in caps:
        return bool(caps[model].get("supports_vision", False))
    for key, val in caps.items():
        if key.endswith("/*") and model.startswith(key[:-2]):
            return bool(val.get("supports_vision", False))
    return bool(caps.get("__default__", {}).get("supports_vision", False))


def truncate_messages(messages: list[dict], context_window: int) -> list[dict]:
    if not messages:
        return messages
    system = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    if not non_system:
        return messages
    last_user = non_system[-1]
    history = non_system[:-1]

    reserved = count_tokens(system + [last_user]) + 1000
    budget = context_window - reserved

    kept: list[dict] = []
    for msg in reversed(history):
        t = count_tokens([msg])
        if budget - t < 0:
            break
        kept.insert(0, msg)
        budget -= t

    if len(kept) < len(history):
        notice = {"role": "system", "content": "[Nota: historial anterior omitido por límite de contexto]"}
        return system + [notice] + kept + [last_user]
    return system + kept + [last_user]
