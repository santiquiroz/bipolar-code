from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.models.provider import Provider, ProviderRegistry
from app.services import copilot_catalog, providers_service
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


@pytest.fixture
def catalog_env(monkeypatch):
    state = {"providers": [], "items": {}, "calls": {}}

    async def fake_fetch(provider):
        state["calls"][provider.id] = state["calls"].get(provider.id, 0) + 1
        result = state["items"].get(provider.id, [])
        if isinstance(result, Exception):
            raise result
        return result

    reachable = AsyncMock(return_value=True)
    monkeypatch.setattr(
        providers_service, "load_registry", lambda: ProviderRegistry(providers=state["providers"])
    )
    monkeypatch.setattr(providers_service, "_is_reachable", reachable)
    monkeypatch.setattr(providers_service, "fetch_model_items", fake_fetch)
    state["reachable"] = reachable
    copilot_catalog.clear_cache()
    yield state
    copilot_catalog.clear_cache()


def _cloud(provider_id: str, env_var: str, **kwargs) -> Provider:
    base = {"id": provider_id, "name": provider_id, "api_base": "https://api.example.com/v1"}
    return Provider(**{**base, "auth_env_var": env_var, **kwargs})


def _models_of(catalog: list[dict], provider_id: str) -> list[str]:
    return [e["model"] for e in catalog if e["provider_id"] == provider_id]


async def test_catalog_starts_with_auto(catalog_env):
    catalog = await copilot_catalog.build_catalog()
    assert catalog[0]["id"] == "bipolar-auto"


async def test_cloud_provider_without_key_is_excluded(catalog_env, monkeypatch):
    monkeypatch.delenv("BIPOLAR_TEST_MISSING_KEY", raising=False)
    catalog_env["providers"] = [_cloud("nube", "BIPOLAR_TEST_MISSING_KEY", active_model="m")]
    catalog = await copilot_catalog.build_catalog()
    assert _models_of(catalog, "nube") == []


async def test_local_provider_without_key_is_included_when_reachable(catalog_env):
    catalog_env["providers"] = [_provider(active_model="qwen")]
    catalog = await copilot_catalog.build_catalog()
    assert _models_of(catalog, "llamacpp") == ["qwen"]


async def test_local_provider_unreachable_is_excluded(catalog_env):
    catalog_env["reachable"].return_value = False
    catalog_env["providers"] = [_provider(active_model="qwen")]
    catalog = await copilot_catalog.build_catalog()
    assert _models_of(catalog, "llamacpp") == []


async def test_openrouter_keeps_only_free_and_active(catalog_env, monkeypatch):
    monkeypatch.setenv("BIPOLAR_TEST_OR_KEY", "sk-or")
    catalog_env["providers"] = [_cloud("openrouter", "BIPOLAR_TEST_OR_KEY", active_model="c")]
    catalog_env["items"]["openrouter"] = [{"id": "a:free"}, {"id": "b"}]
    catalog = await copilot_catalog.build_catalog()
    assert _models_of(catalog, "openrouter") == ["c", "a:free"]


async def test_failing_models_endpoint_keeps_active_model(catalog_env, monkeypatch):
    monkeypatch.setenv("BIPOLAR_TEST_KEY", "sk")
    catalog_env["providers"] = [
        _cloud("p1", "BIPOLAR_TEST_KEY", active_model="m1"),
        _cloud("p2", "BIPOLAR_TEST_KEY", active_model="m2"),
    ]
    catalog_env["items"]["p1"] = httpx.ConnectTimeout("timeout")
    catalog_env["items"]["p2"] = [{"id": "z"}, {"id": "m2"}, {"id": "a"}]
    catalog = await copilot_catalog.build_catalog()
    assert _models_of(catalog, "p1") == ["m1"]
    assert _models_of(catalog, "p2") == ["m2", "a", "z"]


async def test_failing_provider_without_active_model_has_no_entries(catalog_env, monkeypatch):
    monkeypatch.setenv("BIPOLAR_TEST_KEY", "sk")
    catalog_env["providers"] = [_cloud("p1", "BIPOLAR_TEST_KEY")]
    catalog_env["items"]["p1"] = httpx.ConnectTimeout("timeout")
    catalog = await copilot_catalog.build_catalog()
    assert _models_of(catalog, "p1") == []


async def test_cache_avoids_second_fetch(catalog_env, monkeypatch):
    monkeypatch.setenv("BIPOLAR_TEST_KEY", "sk")
    catalog_env["providers"] = [
        _cloud("p1", "BIPOLAR_TEST_KEY", active_model="m1"),
        _provider(active_model="qwen"),
    ]
    await copilot_catalog.build_catalog()
    await copilot_catalog.build_catalog()
    assert catalog_env["calls"] == {"p1": 1, "llamacpp": 1}


def test_endpoint_requires_key(catalog_env):
    from app.main import app
    response = TestClient(app).get("/v1/bipolar/models")
    assert response.status_code == 401


def test_endpoint_returns_data_list(catalog_env):
    from app.main import app
    from app.core.config import get_settings
    client = TestClient(app, headers={"x-api-key": get_settings().ui_api_key})
    response = client.get("/v1/bipolar/models")
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "bipolar-auto"
