import pytest
from app.services.token_service import count_tokens, get_context_window, truncate_messages, supports_vision


def test_count_tokens_simple_message():
    msgs = [{"role": "user", "content": "Hello world"}]
    result = count_tokens(msgs)
    assert 4 < result < 25


def test_count_tokens_image_block_with_data():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "What is this?"},
        {"type": "image", "source": {"type": "base64", "data": "abc123"}},
    ]}]
    result = count_tokens(msgs)
    assert result > 85


def test_count_tokens_image_block_url_only():
    msgs = [{"role": "user", "content": [
        {"type": "image", "source": {"type": "url", "url": "https://example.com/img.png"}},
    ]}]
    result = count_tokens(msgs)
    assert result >= 765


def test_get_context_window_known_model():
    assert get_context_window("claude-sonnet-4-6") == 200000


def test_get_context_window_unknown_model_returns_default():
    assert get_context_window("totally-unknown-model-xyz") == 8192


def test_get_context_window_wildcard_openrouter():
    assert get_context_window("openrouter/some/model") == 32000


def test_get_context_window_deepseek():
    assert get_context_window("deepseek-chat") == 64000


def test_supports_vision_true():
    assert supports_vision("claude-sonnet-4-6") is True


def test_supports_vision_false():
    assert supports_vision("deepseek-chat") is False


def test_supports_vision_unknown_returns_false():
    assert supports_vision("unknown-model") is False


def test_truncate_preserves_system_prompt_and_last_message():
    system = {"role": "system", "content": "You are helpful."}
    history = [{"role": "user", "content": f"question {i}"} for i in range(30)]
    last = {"role": "user", "content": "final question here"}
    msgs = [system] + history + [last]
    result = truncate_messages(msgs, context_window=300)
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "You are helpful."
    assert result[-1] == last


def test_truncate_inserts_notice_when_truncated():
    system = {"role": "system", "content": "sys"}
    history = [{"role": "user", "content": "x" * 300} for _ in range(5)]
    last = {"role": "user", "content": "final"}
    msgs = [system] + history + [last]
    result = truncate_messages(msgs, context_window=400)
    notice_msgs = [m for m in result if "omitido" in m.get("content", "")]
    assert len(notice_msgs) == 1


def test_truncate_noop_when_fits():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ]
    result = truncate_messages(msgs, context_window=200000)
    assert len(result) == len(msgs)
