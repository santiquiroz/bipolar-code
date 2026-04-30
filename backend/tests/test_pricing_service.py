import pytest
from app.services.pricing_service import (
    get_model_price, estimate_cost, cache_openrouter_prices,
    is_free, get_all_prices,
)


def test_get_price_anthropic_sonnet():
    price = get_model_price("anthropic", "claude-sonnet-4-6")
    assert price is not None
    assert price["input"] == 3.00
    assert price["output"] == 15.00


def test_get_price_nvidia_nim():
    price = get_model_price("nvidia_nim", "meta/llama-3.1-70b-instruct")
    assert price is not None
    assert price["input"] == 0.35


def test_get_price_deepseek():
    price = get_model_price("deepseek", "deepseek-chat")
    assert price is not None
    assert price["input"] == 0.27


def test_get_price_copilot_returns_none():
    assert get_model_price("copilot", "claude-sonnet-4.6") is None


def test_get_price_ollama_returns_none():
    assert get_model_price("ollama", "llama3.2") is None


def test_get_price_unknown_returns_none():
    assert get_model_price("anthropic", "nonexistent-model") is None


def test_estimate_cost_basic():
    cost = estimate_cost("anthropic", "claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost is not None
    assert abs(cost - 18.00) < 0.001


def test_estimate_cost_free_returns_none():
    assert estimate_cost("ollama", "llama3.2", 1000, 500) is None


def test_estimate_cost_small_request():
    cost = estimate_cost("deepseek", "deepseek-chat", input_tokens=1000, output_tokens=500)
    assert cost is not None
    assert cost < 0.01


def test_cache_openrouter_prices():
    cache_openrouter_prices([
        {"id": "openrouter/test-model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}
    ])
    price = get_model_price("openrouter", "openrouter/test-model")
    assert price is not None
    assert abs(price["input"] - 1.0) < 0.001
    assert abs(price["output"] - 2.0) < 0.001


def test_is_free_ollama():
    assert is_free("ollama") is True


def test_is_free_copilot():
    assert is_free("copilot") is True


def test_is_free_anthropic():
    assert is_free("anthropic") is False


def test_get_all_prices_returns_dict():
    result = get_all_prices()
    assert "providers" in result
    assert "anthropic" in result["providers"]
