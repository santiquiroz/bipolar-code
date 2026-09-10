"""Tests del endpoint OpenAI-compatible /v1/chat/completions (BYOK)."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.openai_compat import resolve_target
from app.core.config import get_settings
from app.models.provider import Provider


@pytest.fixture
def client():
    from app.main import app
    api_key = get_settings().ui_api_key
    return TestClient(app, headers={"x-api-key": api_key})


def _provider(**overrides) -> Provider:
    base = dict(
        id="llamacpp",
        name="llama.cpp",
        api_base="http://127.0.0.1:4002",
        litellm_prefix="openai",
        active_model="qwen3-coder-next",
    )
    return Provider(**{**base, **overrides})


def test_resolve_target_openai_provider_goes_direct():
    url, headers, model = resolve_target(_provider(), get_settings())
    assert url == "http://127.0.0.1:4002/chat/completions"
    assert model == "qwen3-coder-next"
    assert headers["Authorization"] == "Bearer no-key"


def test_resolve_target_anthropic_provider_goes_through_litellm():
    settings = get_settings()
    provider = _provider(id="anthropic", litellm_prefix="anthropic")
    url, headers, model = resolve_target(provider, settings)
    assert url == f"{settings.proxy_url}/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {settings.proxy_api_key}"
    assert model == "claude-sonnet-4-6"


def test_resolve_target_includes_extra_headers():
    provider = _provider(id="copilot", extra_headers={"Copilot-Integration-Id": "vscode-chat"})
    _, headers, _ = resolve_target(provider, get_settings())
    assert headers["Copilot-Integration-Id"] == "vscode-chat"


def test_chat_completions_requires_auth():
    from app.main import app
    unauth = TestClient(app, raise_server_exceptions=False)
    resp = unauth.post("/v1/chat/completions", json={"model": "x", "messages": []})
    assert resp.status_code == 401


def test_chat_completions_rewrites_model_and_forwards(client):
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hola"}]}
    upstream = AsyncMock()
    upstream.status_code = 200
    upstream.json = lambda: {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 5}}
    with patch(
        "app.api.openai_compat.providers_service.pick_provider",
        new=AsyncMock(return_value=(_provider(), None, True)),
    ), patch("app.api.openai_compat.httpx.AsyncClient") as mock_client:
        instance = mock_client.return_value
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.post = AsyncMock(return_value=upstream)
        resp = client.post("/v1/chat/completions", json=body)

    assert resp.status_code == 200
    _, kwargs = instance.post.call_args
    assert kwargs["json"]["model"] == "qwen3-coder-next"
    assert instance.post.call_args[0][0] == "http://127.0.0.1:4002/chat/completions"


def test_chat_completions_upstream_error_relayed(client):
    body = {"model": "x", "messages": []}
    upstream = AsyncMock()
    upstream.status_code = 400
    upstream.json = lambda: {"error": {"message": "bad"}}
    with patch(
        "app.api.openai_compat.providers_service.pick_provider",
        new=AsyncMock(return_value=(_provider(), None, True)),
    ), patch("app.api.openai_compat.httpx.AsyncClient") as mock_client:
        instance = mock_client.return_value
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.post = AsyncMock(return_value=upstream)
        resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 400


def test_resolve_target_adds_v1_for_ollama_default_base():
    from app.api.openai_compat import resolve_target
    from app.core.config import get_settings
    from app.models.provider import Provider
    ollama = Provider(id="ollama", name="Ollama", api_base="http://127.0.0.1:11434", active_model="llama3.2")
    url, _, model = resolve_target(ollama, get_settings())
    assert url == "http://127.0.0.1:11434/v1/chat/completions" and model == "llama3.2"
    lmstudio = Provider(id="lmstudio", name="LM", api_base="http://127.0.0.1:1234/v1", active_model="m")
    assert resolve_target(lmstudio, get_settings())[0] == "http://127.0.0.1:1234/v1/chat/completions"
    copilot = Provider(id="copilot", name="C", api_base="https://api.business.githubcopilot.com", active_model="m")
    assert resolve_target(copilot, get_settings())[0] == "https://api.business.githubcopilot.com/chat/completions"
