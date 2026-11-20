"""Tests for backend/app/services/sse_message_collector.py — pure functions, no network."""
import json
import pytest
from app.services.sse_message_collector import parse_sse_events, collect_message


# ── parse_sse_events ──────────────────────────────────────────────────────────

def test_parse_sse_events_basic():
    lines = [
        "event: message_start\n",
        'data: {"type": "message_start", "message": {"id": "msg_123"}}\n',
        "\n",
        "event: message_stop\n",
        'data: {"type": "message_stop"}\n',
        "\n",
    ]
    events = parse_sse_events(lines)
    assert len(events) == 2
    assert events[0]["type"] == "message_start"
    assert events[0]["message"]["id"] == "msg_123"
    assert events[1]["type"] == "message_stop"


def test_parse_sse_events_no_trailing_blank():
    lines = [
        "event: message_stop\n",
        'data: {"type": "message_stop"}\n',
    ]
    events = parse_sse_events(lines)
    assert len(events) == 1
    assert events[0]["type"] == "message_stop"


def test_parse_sse_events_empty_lines():
    lines = ["\n", "\n"]
    events = parse_sse_events(lines)
    assert events == []


def test_parse_sse_events_malformed_json_skipped():
    lines = [
        "event: bad\n",
        "data: {not json}\n",
        "\n",
        "event: good\n",
        'data: {"type": "ok"}\n',
        "\n",
    ]
    events = parse_sse_events(lines)
    assert len(events) == 1
    assert events[0]["type"] == "ok"


# ── collect_message: text deltas ──────────────────────────────────────────────

def test_collect_text_two_deltas():
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_abc",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "World"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    ]
    msg = collect_message(events)
    assert msg is not None
    assert msg["id"] == "msg_abc"
    assert msg["type"] == "message"
    assert msg["role"] == "assistant"
    assert msg["model"] == "claude-sonnet-4-6"
    assert msg["stop_reason"] == "end_turn"
    assert len(msg["content"]) == 1
    assert msg["content"][0]["type"] == "text"
    assert msg["content"][0]["text"] == "Hello World"
    assert msg["usage"]["input_tokens"] == 10
    assert msg["usage"]["output_tokens"] == 5


# ── collect_message: tool_use with input_json_delta ───────────────────────────

def test_collect_tool_use_input_json_three_chunks():
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_tool",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 20, "output_tokens": 0},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "tool_1", "name": "read_file", "input": {}},
        },
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"pa'}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": 'th": "/'}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": 'tmp"}'}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"input_tokens": 20, "output_tokens": 8},
        },
    ]
    msg = collect_message(events)
    assert msg is not None
    assert len(msg["content"]) == 1
    assert msg["content"][0]["type"] == "tool_use"
    assert msg["content"][0]["id"] == "tool_1"
    assert msg["content"][0]["name"] == "read_file"
    assert msg["content"][0]["input"] == {"path": "/tmp"}
    assert msg["stop_reason"] == "tool_use"


# ── collect_message: thinking + text ──────────────────────────────────────────

def test_collect_thinking_and_text():
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_think",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 5, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Let me "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "think."}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Answer."}},
        {"type": "content_block_stop", "index": 1},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"input_tokens": 5, "output_tokens": 10},
        },
    ]
    msg = collect_message(events)
    assert msg is not None
    assert len(msg["content"]) == 2
    assert msg["content"][0]["type"] == "thinking"
    assert msg["content"][0]["thinking"] == "Let me think."
    assert msg["content"][1]["type"] == "text"
    assert msg["content"][1]["text"] == "Answer."
    assert msg["usage"]["input_tokens"] == 5
    assert msg["usage"]["output_tokens"] == 10


# ── collect_message: usage fusion ─────────────────────────────────────────────

def test_collect_usage_fusion():
    """input_tokens from message_start, output_tokens from message_delta."""
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_u",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 42, "output_tokens": 0},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "x"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"input_tokens": 42, "output_tokens": 7},
        },
    ]
    msg = collect_message(events)
    assert msg is not None
    assert msg["usage"]["input_tokens"] == 42
    assert msg["usage"]["output_tokens"] == 7


# ── collect_message: error event ──────────────────────────────────────────────

def test_collect_error_returns_none():
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_err",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        },
        {
            "type": "error",
            "error": {"type": "overloaded_error", "message": "server busy"},
        },
    ]
    result = collect_message(events)
    assert result is None


def test_collect_error_event_is_detected():
    """The function that detects error returns None — caller can inspect events."""
    events = [
        {
            "type": "error",
            "error": {"type": "api_error", "message": "upstream disconnected"},
        },
    ]
    result = collect_message(events)
    assert result is None
    # The error event is still in the list for the caller to inspect
    assert events[0]["type"] == "error"
    assert events[0]["error"]["message"] == "upstream disconnected"


# ── collect_message: full SSE round-trip (parse + collect) ────────────────────

def test_full_roundtrip_text():
    lines = [
        "event: message_start\n",
        'data: {"type":"message_start","message":{"id":"msg_r","type":"message","role":"assistant","model":"claude-sonnet-4-6","usage":{"input_tokens":3,"output_tokens":0}}}\n',
        "\n",
        "event: content_block_start\n",
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n',
        "\n",
        "event: content_block_delta\n",
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hi"}}\n',
        "\n",
        "event: content_block_delta\n",
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" there"}}\n',
        "\n",
        "event: content_block_stop\n",
        'data: {"type":"content_block_stop","index":0}\n',
        "\n",
        "event: message_delta\n",
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"input_tokens":3,"output_tokens":4}}\n',
        "\n",
        "event: message_stop\n",
        'data: {"type":"message_stop"}\n',
        "\n",
    ]
    events = parse_sse_events(lines)
    msg = collect_message(events)
    assert msg is not None
    assert msg["content"][0]["text"] == "Hi there"
    assert msg["stop_reason"] == "end_turn"
    assert msg["usage"]["input_tokens"] == 3
    assert msg["usage"]["output_tokens"] == 4
