"""Tests de la rama anthropic_native de /v1/messages (passthrough verbatim)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.provider import Provider


@pytest.fixture
def client():
    from app.main import app
    from app.core.config import get_settings
    api_key = get_settings().ui_api_key
    return TestClient(app, headers={"x-api-key": api_key})


def _native_provider(api_base: str = "http://127.0.0.1:4002") -> Provider:
    return Provider(
        id="llamacpp",
        name="llama.cpp",
        api_base=api_base,
        litellm_prefix="openai",
        active_model="qwen3-coder-next",
        anthropic_native=True,
    )


async def _alines(lines):
    for line in lines:
        yield line


def _mock_stream(lines):
    stream = AsyncMock()
    stream.__aenter__ = AsyncMock(return_value=stream)
    stream.__aexit__ = AsyncMock(return_value=False)
    stream.status_code = 200
    stream.aiter_lines = lambda: _alines(lines)
    return stream


def _post_messages(client, provider):
    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hola"}],
        "max_tokens": 50,
    }
    lines = [
        'data: {"type": "message_start", "message": {"usage": {"input_tokens": 3}}}',
        "",
        'data: {"type": "message_stop"}',
    ]
    with patch(
        "app.api.messages.providers_service.get_active_provider",
        return_value=provider,
    ), patch("app.api.messages.httpx.AsyncClient") as mock_client:
        instance = mock_client.return_value
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.stream = MagicMock(return_value=_mock_stream(lines))
        resp = client.post("/v1/messages", json=body)
        return resp, instance.stream


def test_native_forwards_to_provider_messages_url(client):
    resp, stream_mock = _post_messages(client, _native_provider())
    assert resp.status_code == 200
    _, args, kwargs = stream_mock.mock_calls[0]
    assert args[0] == "POST"
    assert args[1] == "http://127.0.0.1:4002/v1/messages"


def test_native_rewrites_model_to_active_model(client):
    _, stream_mock = _post_messages(client, _native_provider())
    _, _, kwargs = stream_mock.mock_calls[0]
    assert kwargs["json"]["model"] == "qwen3-coder-next"
    assert kwargs["json"]["messages"][0]["content"] == "hola"


def test_native_dedupes_v1_suffix_in_api_base(client):
    _, stream_mock = _post_messages(client, _native_provider("http://127.0.0.1:1234/v1"))
    _, args, _ = stream_mock.mock_calls[0]
    assert args[1] == "http://127.0.0.1:1234/v1/messages"


def test_native_relays_sse_lines(client):
    resp, _ = _post_messages(client, _native_provider())
    assert "message_stop" in resp.text


def test_native_no_auth_header_without_env_var(client):
    _, stream_mock = _post_messages(client, _native_provider())
    _, _, kwargs = stream_mock.mock_calls[0]
    assert "Authorization" not in kwargs["headers"]
    assert "x-api-key" not in kwargs["headers"]


def test_routing_overrides_active_provider(client):
    routed = Provider(
        id="llamacpp-small",
        name="small",
        api_base="http://127.0.0.1:4003",
        litellm_prefix="openai",
        active_model="qwen3-4b",
        anthropic_native=True,
    )
    body = {
        "model": "claude-3-5-haiku-latest",
        "messages": [{"role": "user", "content": "hola"}],
        "max_tokens": 10,
    }
    lines = ['data: {"type": "message_stop"}']
    with patch(
        "app.api.messages.providers_service.get_active_provider",
        return_value=_native_provider(),
    ), patch(
        "app.api.messages.providers_service.resolve_route",
        return_value=(routed, "qwen3-4b"),
    ), patch("app.api.messages.httpx.AsyncClient") as mock_client:
        instance = mock_client.return_value
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.stream = MagicMock(return_value=_mock_stream(lines))
        resp = client.post("/v1/messages", json=body)

    assert resp.status_code == 200
    _, args, kwargs = instance.stream.mock_calls[0]
    assert args[1] == "http://127.0.0.1:4003/v1/messages"
    assert kwargs["json"]["model"] == "qwen3-4b"
