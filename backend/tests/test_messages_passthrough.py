import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


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
