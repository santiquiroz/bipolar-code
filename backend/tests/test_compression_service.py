from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.models.provider import Provider
from app.services import compression_service


def _provider() -> Provider:
    return Provider(
        id="test-provider",
        name="Test Provider",
        api_base="http://localhost:4000/v1",
        active_model="provider-model",
    )


def test_message_to_text_passes_through_string_content():
    assert compression_service.message_to_text({"content": "plain text"}) == "plain text"


def test_message_to_text_joins_text_blocks():
    message = {
        "content": [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
    }

    assert compression_service.message_to_text(message) == "first\nsecond"


def test_message_to_text_formats_tool_use_block():
    message = {"content": [{"type": "tool_use", "name": "read_file"}]}

    assert compression_service.message_to_text(message) == "[tool_use: read_file]"


def test_message_to_text_formats_tool_result_block():
    message = {"content": [{"type": "tool_result", "content": "ignored"}]}

    assert compression_service.message_to_text(message) == "[tool_result]"


def test_message_to_text_returns_empty_string_for_empty_list():
    assert compression_service.message_to_text({"content": []}) == ""


def test_split_for_compression_keeps_all_messages_when_at_or_below_limit():
    messages = [{"role": "user", "content": str(index)} for index in range(3)]

    old, recent = compression_service.split_for_compression(messages, keep_recent=3)

    assert old == []
    assert recent is messages


def test_split_for_compression_moves_cut_back_before_tool_pair():
    messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "older"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "read_file"}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "content": "result"}],
        },
        {"role": "assistant", "content": "latest"},
    ]

    old, recent = compression_service.split_for_compression(messages, keep_recent=2)

    assert old == messages[:2]
    assert recent == messages[2:]


def test_split_for_compression_uses_normal_split_sizes():
    messages = [{"role": "user", "content": str(index)} for index in range(6)]

    old, recent = compression_service.split_for_compression(messages, keep_recent=2)

    assert old == messages[:4]
    assert recent == messages[4:]


def test_build_summary_request_sets_options_and_includes_roles():
    request = compression_service.build_summary_request(
        [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ],
        "summary-model",
    )

    assert request["model"] == "summary-model"
    assert request["stream"] is False
    assert request["max_tokens"] == 1500
    assert "user: question\nassistant: answer" in request["messages"][0]["content"]


def test_build_summary_request_caps_source_at_60000_characters():
    request = compression_service.build_summary_request(
        [{"role": "user", "content": "x" * 70_000}],
        "summary-model",
    )

    content = request["messages"][0]["content"]
    assert len(content) <= len(compression_service._SUMMARY_PROMPT) + 60_000


@pytest.mark.asyncio
async def test_compress_messages_returns_summary_followed_by_recent_messages(monkeypatch):
    messages = [
        {"role": "user", "content": f"message {index}"}
        for index in range(10)
    ]
    response = Mock()
    response.json.return_value = {
        "choices": [{"message": {"content": "resumen"}}]
    }
    client = AsyncMock()
    client.post.return_value = response
    async_client = Mock()
    async_client.return_value.__aenter__ = AsyncMock(return_value=client)
    async_client.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(compression_service.httpx, "AsyncClient", async_client)

    result = await compression_service.compress_messages(
        messages,
        _provider(),
        "requested-model",
    )

    assert result is not None
    assert result[1:] == messages[-compression_service.KEEP_RECENT_MESSAGES :]
    assert result[0]["role"] == "user"
    assert result[0]["content"][0]["text"].startswith(
        "[Resumen de la conversación previa]"
    )
    assert result[0]["content"][0]["text"].endswith("resumen")


@pytest.mark.asyncio
async def test_compress_messages_returns_none_without_calling_http_when_short(monkeypatch):
    async_client = Mock()
    monkeypatch.setattr(compression_service.httpx, "AsyncClient", async_client)
    messages = [
        {"role": "user", "content": f"message {index}"}
        for index in range(compression_service.KEEP_RECENT_MESSAGES)
    ]

    result = await compression_service.compress_messages(
        messages,
        _provider(),
        "requested-model",
    )

    assert result is None
    async_client.assert_not_called()


@pytest.mark.asyncio
async def test_compress_messages_returns_none_on_http_error(monkeypatch):
    messages = [
        {"role": "user", "content": f"message {index}"}
        for index in range(compression_service.KEEP_RECENT_MESSAGES + 1)
    ]
    response = Mock()
    response.raise_for_status.side_effect = httpx.HTTPError("request failed")
    client = AsyncMock()
    client.post.return_value = response
    async_client = Mock()
    async_client.return_value.__aenter__ = AsyncMock(return_value=client)
    async_client.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(compression_service.httpx, "AsyncClient", async_client)

    result = await compression_service.compress_messages(
        messages,
        _provider(),
        "requested-model",
    )

    assert result is None
