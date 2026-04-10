from app.services.usage_service import _parse_anthropic_usage, ANTHROPIC_PRICING
from app.models.schemas import BackendType


def test_parse_anthropic_usage_basic():
    data = {"data": [{"model": "claude-sonnet-4-6", "input_tokens": 1_000_000, "output_tokens": 500_000, "request_count": 10}]}
    result = _parse_anthropic_usage(data)
    assert len(result) == 1
    assert result[0].model == "claude-sonnet-4-6"
    assert result[0].input_tokens == 1_000_000
    assert result[0].estimated_cost_usd == round(3.0 + 7.5, 6)


def test_parse_anthropic_usage_no_pricing():
    data = {"data": [{"model": "unknown-model", "input_tokens": 100, "output_tokens": 50}]}
    result = _parse_anthropic_usage(data)
    assert result[0].estimated_cost_usd is None


def test_anthropic_pricing_keys():
    for model in ANTHROPIC_PRICING:
        assert "input" in ANTHROPIC_PRICING[model]
        assert "output" in ANTHROPIC_PRICING[model]
