import json
import sys
from functools import lru_cache
from pathlib import Path

if getattr(sys, "frozen", False):
    _DATA_DIR = Path(sys._MEIPASS) / "app" / "data"
else:
    _DATA_DIR = Path(__file__).parent.parent / "data"
_dynamic_cache: dict[str, dict] = {}


@lru_cache(maxsize=1)
def _static_pricing() -> dict:
    return json.loads((_DATA_DIR / "pricing.json").read_text(encoding="utf-8"))


def get_model_price(provider_id: str, model: str) -> dict | None:
    if provider_id in _dynamic_cache and model in _dynamic_cache[provider_id]:
        return _dynamic_cache[provider_id][model]
    provider_data = _static_pricing().get("providers", {}).get(provider_id, {})
    if provider_data.get("__free__") or provider_data.get("__flat_rate__") or provider_data.get("__dynamic__"):
        return None
    return provider_data.get(model)


def estimate_cost(
    provider_id: str, model: str, input_tokens: int, output_tokens: int
) -> float | None:
    price = get_model_price(provider_id, model)
    if price is None:
        return None
    cost = (input_tokens / 1_000_000) * price["input"]
    cost += (output_tokens / 1_000_000) * price["output"]
    return round(cost, 8)


def cache_openrouter_prices(models: list[dict]) -> None:
    for m in models:
        model_id = m.get("id", "")
        pricing = m.get("pricing", {})
        if pricing and model_id:
            _dynamic_cache.setdefault("openrouter", {})[model_id] = {
                "input": float(pricing.get("prompt", 0)) * 1_000_000,
                "output": float(pricing.get("completion", 0)) * 1_000_000,
            }


def is_free(provider_id: str) -> bool:
    p = _static_pricing().get("providers", {}).get(provider_id, {})
    return bool(p.get("__free__") or p.get("__flat_rate__"))


def get_all_prices() -> dict:
    result = dict(_static_pricing())
    for provider_id, models in _dynamic_cache.items():
        result.setdefault("providers", {}).setdefault(provider_id, {}).update(models)
    return result
