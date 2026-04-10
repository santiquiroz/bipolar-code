import httpx
import re
from typing import List
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import UsageStats

log = get_logger(__name__)

# Anthropic pricing (per 1M tokens, USD) — update as needed
ANTHROPIC_PRICING = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-6":   {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5":  {"input": 0.25, "output": 1.25},
}


async def get_anthropic_usage() -> List[UsageStats]:
    """Fetch usage from Anthropic API (requires real API key)."""
    settings = get_settings()
    key = settings.anthropic_api_key or settings.anthropic_real_api_key
    if not key:
        log.warning("anthropic_key_missing_for_usage")
        return [UsageStats(provider_id="anthropic", model="all", note="No API key configured", requests_count=0)]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.anthropic.com/v1/usage",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            )
            if resp.status_code == 404:
                # Usage endpoint may not exist — return placeholder
                log.info("anthropic_usage_endpoint_unavailable")
                return [UsageStats(provider_id="anthropic", model="all",
                                   note="Usage API not available on this plan", requests_count=0)]
            resp.raise_for_status()
            data = resp.json()
            log.info("anthropic_usage_fetched", data=data)
            return _parse_anthropic_usage(data)
    except Exception as e:
        log.error("anthropic_usage_error", error=str(e))
        return [UsageStats(provider_id="anthropic", model="all", note=str(e), requests_count=0)]


async def get_proxy_log_stats() -> List[UsageStats]:
    """Parse requests.log for basic usage stats."""
    import os
    log_path = f"{get_settings().litellm_config_dir}/requests.log"
    if not os.path.exists(log_path):
        log.warning("requests_log_not_found", path=log_path)
        return []

    stats: dict[str, dict] = {}
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                model_match = re.search(r'"model"\s*:\s*"([^"]+)"', line)
                if model_match:
                    model = model_match.group(1)
                    stats.setdefault(model, {"requests": 0, "input": 0, "output": 0})
                    stats[model]["requests"] += 1
                    in_match = re.search(r'"input_tokens"\s*:\s*(\d+)', line)
                    out_match = re.search(r'"output_tokens"\s*:\s*(\d+)', line)
                    if in_match:
                        stats[model]["input"] += int(in_match.group(1))
                    if out_match:
                        stats[model]["output"] += int(out_match.group(1))

        result = []
        for model, s in stats.items():
            pricing = ANTHROPIC_PRICING.get(model)
            cost = None
            if pricing:
                cost = round(
                    (s["input"] / 1_000_000) * pricing["input"] +
                    (s["output"] / 1_000_000) * pricing["output"], 6
                )
            result.append(UsageStats(
                provider_id="anthropic",
                model=model,
                input_tokens=s["input"] or None,
                output_tokens=s["output"] or None,
                estimated_cost_usd=cost,
                requests_count=s["requests"],
            ))
        log.info("log_stats_parsed", models=len(result))
        return result
    except Exception as e:
        log.error("log_stats_error", error=str(e))
        return []


def _parse_anthropic_usage(data: dict) -> List[UsageStats]:
    items = data.get("data", [data])
    result = []
    for item in items:
        model = item.get("model", "unknown")
        input_t = item.get("input_tokens", 0)
        output_t = item.get("output_tokens", 0)
        pricing = ANTHROPIC_PRICING.get(model)
        cost = None
        if pricing:
            cost = round((input_t / 1_000_000) * pricing["input"] + (output_t / 1_000_000) * pricing["output"], 6)
        result.append(UsageStats(
            provider_id="anthropic",
            model=model,
            input_tokens=input_t,
            output_tokens=output_t,
            estimated_cost_usd=cost,
            requests_count=item.get("request_count", 0),
        ))
    return result
