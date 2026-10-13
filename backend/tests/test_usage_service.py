from app.services.usage_service import _parse_anthropic_usage, ANTHROPIC_PRICING


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
    assert list(ANTHROPIC_PRICING.items()) == [
        ("claude-fable-5-1", {"input": 10.0, "output": 50.0}),
        ("claude-fable-5", {"input": 10.0, "output": 50.0}),
        ("claude-opus-5-5", {"input": 4.0, "output": 20.0}),
        ("claude-opus-5", {"input": 5.0, "output": 25.0}),
        ("claude-opus-4-8", {"input": 5.0, "output": 25.0}),
        ("claude-opus-4-7", {"input": 5.0, "output": 25.0}),
        ("claude-opus-4-6", {"input": 5.0, "output": 25.0}),
        ("claude-sonnet-5-5", {"input": 2.0, "output": 10.0}),
        ("claude-sonnet-5", {"input": 2.0, "output": 10.0}),
        ("claude-sonnet-4-6", {"input": 3.0, "output": 15.0}),
        ("claude-haiku-5-5", {"input": 0.10, "output": 0.50}),
        ("claude-haiku-4-5", {"input": 1.0, "output": 5.0}),
    ]


def test_parse_anthropic_usage_opus_55_costs_24_dollars_per_million_each():
    data = {"data": [{"model": "claude-opus-5-5", "input_tokens": 1_000_000, "output_tokens": 1_000_000}]}

    result = _parse_anthropic_usage(data)

    assert result[0].estimated_cost_usd == 24.0
