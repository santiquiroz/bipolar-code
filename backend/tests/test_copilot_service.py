from app.services.copilot_service import _parse_copilot_models


def test_parse_copilot_models_list():
    raw = [
        {"id": "claude-sonnet-4-6", "name": "Claude Sonnet", "vendor": {"name": "Anthropic"}},
        {"id": "gpt-4o", "name": "GPT-4o"},
    ]
    models = _parse_copilot_models(raw)
    assert len(models) == 2
    assert models[0].id == "claude-sonnet-4-6"
    assert models[0].vendor == "Anthropic"


def test_parse_copilot_models_dict_wrapper():
    raw = {"data": [{"id": "claude-sonnet-4-6", "name": "Claude Sonnet"}]}
    models = _parse_copilot_models(raw)
    assert len(models) == 1


def test_parse_copilot_models_empty():
    assert _parse_copilot_models([]) == []
    assert _parse_copilot_models({}) == []
