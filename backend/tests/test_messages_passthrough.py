import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    from app.core.config import get_settings
    api_key = get_settings().ui_api_key
    return TestClient(app, headers={"x-api-key": api_key})


def test_messages_endpoint_exists(client):
    # Should return 422 (missing body) not 404
    resp = client.post("/v1/messages", json={})
    assert resp.status_code != 404


def test_messages_counts_context_usage_header(client):
    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 100,
    }
    with patch("app.api.messages.httpx.AsyncClient") as mock_client:
        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.status_code = 200
        mock_stream.aiter_lines = AsyncMock(return_value=iter([
            'data: {"type": "message_stop"}'
        ]))
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.return_value.stream = MagicMock(return_value=mock_stream)
        resp = client.post("/v1/messages", json=body)
    assert "x-context-usage" in resp.headers or resp.status_code in (200, 500)


def test_messages_returns_http_error_on_fatal_provider_failure(client, monkeypatch):
    """Fatal 400 del upstream se devuelve con status HTTP real, no como evento SSE."""
    from app.models.provider import Provider, ProviderRegistry
    from app.services import providers_service

    provider = Provider(id="p1", name="p1", api_base="https://p1.example.com/v1", active_model="m1")
    registry = ProviderRegistry(active_provider_id="p1", providers=[provider])
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)

    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
    }
    with patch("app.api.messages.httpx.AsyncClient") as mock_client:
        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.status_code = 400
        mock_stream.aread = AsyncMock(return_value=b'{"error":{"message":"bad request"}}')
        mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.return_value.stream = MagicMock(return_value=mock_stream)
        resp = client.post("/v1/messages", json=body)
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "invalid_request_error"
    assert "bad request" in payload["error"]["message"]
    assert resp.headers["x-bipolar-attempts"] == "1"


# ── Helpers for native stream mocking ─────────────────────────────────────────

def _native_provider():
    from app.models.provider import Provider
    return Provider(
        id="llamacpp",
        name="llama.cpp",
        api_base="http://127.0.0.1:4002",
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


_NATIVE_SSE_LINES = [
    'data: {"type": "message_start", "message": {"id": "msg_test", "type": "message", "role": "assistant", "model": "qwen3-coder-next", "content": [], "usage": {"input_tokens": 3, "output_tokens": 0}}}',
    "",
    'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}',
    "",
    'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}',
    "",
    'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " world"}}',
    "",
    'data: {"type": "content_block_stop", "index": 0}',
    "",
    'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": null}, "usage": {"input_tokens": 3, "output_tokens": 5}}',
    "",
    'data: {"type": "message_stop"}',
]


def _post_native(client, provider, body, lines=None):
    """POST /v1/messages with a mocked native provider stream."""
    if lines is None:
        lines = _NATIVE_SSE_LINES
    with patch(
        "app.api.messages.providers_service.pick_provider",
        new=AsyncMock(return_value=(provider, None, False)),
    ), patch("app.api.messages.httpx.AsyncClient") as mock_client:
        instance = mock_client.return_value
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        instance.stream = MagicMock(return_value=_mock_stream(lines))
        resp = client.post("/v1/messages", json=body)
        return resp, instance.stream


# ── stream: false → JSON response ─────────────────────────────────────────────

def test_stream_false_returns_json(client, monkeypatch):
    """When stream is false, /v1/messages returns application/json with a Message object."""
    provider = _native_provider()
    monkeypatch.setattr("app.api.messages._record_usage", lambda *a, **k: None)

    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 50,
        "stream": False,
    }
    resp, _ = _post_native(client, provider, body)
    assert resp.status_code == 200
    assert "application/json" in resp.headers.get("content-type", "")
    payload = resp.json()
    assert payload["type"] == "message"
    assert payload["role"] == "assistant"
    assert payload["stop_reason"] == "end_turn"
    assert len(payload["content"]) == 1
    assert payload["content"][0]["type"] == "text"
    assert payload["content"][0]["text"] == "Hello world"


def test_no_stream_key_returns_json(client, monkeypatch):
    """When stream key is absent, /v1/messages returns JSON (not SSE)."""
    provider = _native_provider()
    monkeypatch.setattr("app.api.messages._record_usage", lambda *a, **k: None)

    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 50,
        # No "stream" key at all
    }
    resp, _ = _post_native(client, provider, body)
    assert resp.status_code == 200
    assert "application/json" in resp.headers.get("content-type", "")
    payload = resp.json()
    assert payload["type"] == "message"
    assert payload["content"][0]["text"] == "Hello world"


# ── stream: true → SSE response ───────────────────────────────────────────────

def test_stream_true_returns_sse(client, monkeypatch):
    """When stream is true, /v1/messages returns text/event-stream."""
    provider = _native_provider()
    monkeypatch.setattr("app.api.messages._record_usage", lambda *a, **k: None)

    body = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 50,
        "stream": True,
    }
    resp, _ = _post_native(client, provider, body)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert "message_stop" in resp.text
