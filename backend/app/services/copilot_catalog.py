import asyncio
import time

from app.core.logging import get_logger
from app.models.provider import Provider
from app.services import credentials, providers_service

log = get_logger(__name__)

AUTO_MODEL_ID = "bipolar-auto"
AUTO_MODEL_NAME = "Bipolar Auto"
DEFAULT_MAX_INPUT = 128000
DEFAULT_MAX_OUTPUT = 16000
CACHE_TTL_S = 300

_WINDOW_KEYS = ("context_length", "context_window", "max_model_len", "max_context_length")
_OUTPUT_KEYS = ("max_output_tokens", "max_completion_tokens")


def _dict_at(item: dict, *keys: str) -> dict:
    current = item
    for key in keys:
        current = current.get(key) if isinstance(current, dict) else None
    return current if isinstance(current, dict) else {}


def _first_int(source: dict, keys: tuple[str, ...]) -> int:
    for key in keys:
        value = source.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def _split_window(window: int, output: int) -> tuple[int, int]:
    output = output or min(DEFAULT_MAX_OUTPUT, window // 4)
    return window - output, output


def _token_limits(item: dict) -> tuple[int, int]:
    limits = _dict_at(item, "capabilities", "limits")
    max_input = _first_int(limits, ("max_prompt_tokens",))
    max_output = (
        _first_int(limits, ("max_output_tokens",))
        or _first_int(_dict_at(item, "top_provider"), ("max_completion_tokens",))
        or _first_int(item, _OUTPUT_KEYS)
    )
    window = _first_int(limits, ("max_context_window_tokens",)) or _first_int(item, _WINDOW_KEYS)
    if max_input or not window:
        return max_input, max_output
    return _split_window(window, max_output)


def _supports_tools(item: dict) -> bool | None:
    supports = _dict_at(item, "capabilities", "supports")
    if "tool_calls" in supports:
        return bool(supports["tool_calls"])
    params = item.get("supported_parameters")
    return "tools" in params if isinstance(params, list) else None


def _supports_vision(item: dict) -> bool | None:
    supports = _dict_at(item, "capabilities", "supports")
    if "vision" in supports:
        return bool(supports["vision"])
    modalities = _dict_at(item, "architecture").get("input_modalities")
    return "image" in modalities if isinstance(modalities, list) else None


def model_limits(item: dict) -> tuple[int, int, bool | None, bool | None]:
    max_input, max_output = _token_limits(item)
    return max_input, max_output, _supports_tools(item), _supports_vision(item)


def _optional_bool(value) -> bool | None:
    return bool(value) if value is not None else None


def _model_info_limits(info: dict) -> tuple[int, int, bool | None, bool | None]:
    max_output = _first_int(info, ("max_output_tokens",))
    window = _first_int(info, ("context_window",))
    max_input, max_output = _split_window(window, max_output) if window else (0, max_output)
    tools = _optional_bool(info.get("supports_tools"))
    vision = _optional_bool(info.get("supports_vision"))
    return max_input, max_output, tools, vision


def _is_known(value) -> bool:
    # False es un valor conocido (soporte negado), aunque False == 0
    return isinstance(value, bool) or bool(value)


def _first_known(*values):
    return next((v for v in values if _is_known(v)), values[-1])


def catalog_entry(provider: Provider, model: str, item: dict | None) -> dict:
    from_item = model_limits(item or {})
    is_active = model == provider.active_model
    from_info = _model_info_limits(provider.model_info) if is_active else (0, 0, None, None)
    defaults = (DEFAULT_MAX_INPUT, DEFAULT_MAX_OUTPUT, True, False)
    max_input, max_output, tools, vision = (
        _first_known(a, b, c) for a, b, c in zip(from_item, from_info, defaults)
    )
    return {
        "id": f"{provider.id}/{model}",
        "provider_id": provider.id,
        "model": model,
        "name": f"{provider.id} · {model}",
        "max_input_tokens": max_input,
        "max_output_tokens": max_output,
        "tools": tools,
        "vision": vision,
    }


def keep_model(provider: Provider, model_id: str) -> bool:
    if provider.id != "openrouter":
        return True
    return model_id.endswith(":free") or model_id == provider.active_model


def auto_entry() -> dict:
    return {
        "id": AUTO_MODEL_ID,
        "provider_id": None,
        "model": AUTO_MODEL_ID,
        "name": AUTO_MODEL_NAME,
        "max_input_tokens": DEFAULT_MAX_INPUT,
        "max_output_tokens": DEFAULT_MAX_OUTPUT,
        "tools": True,
        "vision": True,
    }


_cache: dict[str, tuple[float, list[dict]]] = {}


def clear_cache() -> None:
    _cache.clear()


def _has_key(provider: Provider) -> bool:
    return any(credentials.api_key_for(slot) for slot in credentials.credential_slots(provider))


async def is_usable(provider: Provider) -> bool:
    if providers_service._is_local_base(provider.api_base):
        return await providers_service._is_reachable(provider.api_base)
    return _has_key(provider)


async def _safe_items(provider: Provider) -> list[dict]:
    try:
        return await providers_service.fetch_model_items(provider)
    except Exception as exc:
        log.warning("copilot_catalog_models_failed", provider=provider.id, error=str(exc))
        return []


def _model_ids(provider: Provider, by_id: dict[str, dict]) -> list[str]:
    head = [provider.active_model] if provider.active_model else []
    rest = sorted(m for m in by_id if m != provider.active_model and keep_model(provider, m))
    return head + rest


async def _fresh_entries(provider: Provider) -> list[dict]:
    by_id = {item["id"]: item for item in await _safe_items(provider)}
    return [catalog_entry(provider, m, by_id.get(m)) for m in _model_ids(provider, by_id)]


async def provider_entries(provider: Provider) -> list[dict]:
    cached = _cache.get(provider.id)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_S:
        return cached[1]
    entries = await _fresh_entries(provider)
    _cache[provider.id] = (time.monotonic(), entries)
    return entries


async def _usable_entries(provider: Provider) -> list[dict]:
    return await provider_entries(provider) if await is_usable(provider) else []


async def build_catalog() -> list[dict]:
    registry = providers_service.load_registry()
    # En paralelo: un models_endpoint lento (hasta 10 s) no debe sumar su espera a la de los demás
    groups = await asyncio.gather(*(_usable_entries(p) for p in registry.providers))
    return [auto_entry()] + [entry for group in groups for entry in group]
