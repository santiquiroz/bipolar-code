from app.models.provider import Provider
from app.services import providers_service
from app.services.copilot_catalog import auto_entry, catalog_entry, keep_model, model_limits


def _provider(**kwargs) -> Provider:
    base = {"id": "llamacpp", "name": "l", "api_base": "http://127.0.0.1:4002"}
    return Provider(**{**base, **kwargs})


def test_model_limits_copilot_shape():
    item = {
        "id": "gpt-5.4",
        "capabilities": {
            "limits": {"max_prompt_tokens": 128000, "max_output_tokens": 64000},
            "supports": {"tool_calls": True, "vision": True},
        },
    }
    assert model_limits(item) == (128000, 64000, True, True)


def test_model_limits_openrouter_shape():
    item = {
        "id": "x:free",
        "context_length": 262144,
        "top_provider": {"max_completion_tokens": 32768},
        "architecture": {"input_modalities": ["text", "image"]},
        "supported_parameters": ["tools"],
    }
    assert model_limits(item) == (229376, 32768, True, True)


def test_model_limits_window_only():
    assert model_limits({"id": "m", "context_length": 32768}) == (24576, 8192, None, None)


def test_model_limits_unknown():
    assert model_limits({}) == (0, 0, None, None)


def test_entry_falls_back_to_model_info_for_active_model():
    p = _provider(
        active_model="qwen",
        model_info={"context_window": 65536, "max_output_tokens": 8192, "supports_tools": False},
    )
    entry = catalog_entry(p, "qwen", None)
    assert entry["max_input_tokens"] == 57344
    assert entry["max_output_tokens"] == 8192
    assert entry["tools"] is False
    assert entry["vision"] is False
    assert entry["id"] == "llamacpp/qwen"
    assert entry["name"] == "llamacpp · qwen"


def test_entry_defaults_for_unknown_non_active_model():
    p = _provider(active_model="qwen", model_info={"context_window": 65536})
    entry = catalog_entry(p, "otro", None)
    assert entry["max_input_tokens"] == 128000
    assert entry["max_output_tokens"] == 16000
    assert entry["tools"] is True
    assert entry["vision"] is False


def test_keep_model_openrouter_only_free_and_active():
    p = _provider(id="openrouter", active_model="c")
    assert keep_model(p, "a:free") is True
    assert keep_model(p, "b") is False
    assert keep_model(p, "c") is True
    assert keep_model(_provider(), "b") is True


def test_auto_entry_shape():
    assert auto_entry() == {
        "id": "bipolar-auto",
        "provider_id": None,
        "model": "bipolar-auto",
        "name": "Bipolar Auto",
        "max_input_tokens": 128000,
        "max_output_tokens": 16000,
        "tools": True,
        "vision": True,
    }


class _FakeResponse:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"data": [{"id": "a"}, {"id": "a"}, {"name": "b"}, {"id": "whisper-1"}, "junk"]}


def _fake_client(calls: list):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            calls.append((url, headers))
            return _FakeResponse()

    return FakeClient


async def test_fetch_model_items_normalizes_and_filters(monkeypatch):
    calls: list = []
    monkeypatch.setenv("TEST_MODELS_KEY", "sk-test")
    monkeypatch.setattr(providers_service.httpx, "AsyncClient", _fake_client(calls))
    p = _provider(models_endpoint="http://127.0.0.1:4002/v1/models", auth_env_var="TEST_MODELS_KEY")
    items = await providers_service.fetch_model_items(p)
    assert [m["id"] for m in items] == ["a", "b"]
    assert calls[0][1]["Authorization"] == "Bearer sk-test"
