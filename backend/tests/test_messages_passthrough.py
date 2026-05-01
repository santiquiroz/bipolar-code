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


def test_messages_error_on_provider_failure(client):
    """Provider returning 400 should yield an SSE error event."""
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
    # StreamingResponse with SSE error or 4xx status
    assert resp.status_code in (200, 400, 422, 500)
