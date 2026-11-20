import json
from typing import Any


def parse_sse_events(lines: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_event: str | None = None
    current_data: str | None = None

    for raw_line in lines:
        line = raw_line.rstrip("\n").rstrip("\r")
        if not line:
            # Blank line → end of frame
            if current_data is not None:
                try:
                    events.append(json.loads(current_data))
                except json.JSONDecodeError:
                    pass
            current_event = None
            current_data = None
            continue

        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            current_data = line[6:]

    # Handle last frame without trailing blank line
    if current_data is not None:
        try:
            events.append(json.loads(current_data))
        except json.JSONDecodeError:
            pass

    return events


def collect_message(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    msg_id: str | None = None
    role: str | None = None
    model: str | None = None
    content: list[dict[str, Any]] = []
    stop_reason: str | None = None
    stop_sequence: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    # Map SSE index → position in content list
    idx_to_pos: dict[int, int] = {}
    # Per-block accumulators keyed by SSE index
    text_bufs: dict[int, str] = {}
    tool_blocks: dict[int, dict[str, Any]] = {}  # index → {id, name, input_json_buf}
    thinking_bufs: dict[int, str] = {}
    signature_bufs: dict[int, str] = {}

    for ev in events:
        etype = ev.get("type", "")

        if etype == "error":
            # Signal error to caller
            return None

        if etype == "message_start":
            msg = ev.get("message", {})
            msg_id = msg.get("id")
            role = msg.get("role", "assistant")
            model = msg.get("model")
            usage = msg.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)

        elif etype == "content_block_start":
            idx = ev.get("index", 0)
            block = ev.get("content_block", {})
            btype = block.get("type", "")
            pos = len(content)
            idx_to_pos[idx] = pos

            if btype == "text":
                text_bufs[idx] = block.get("text", "")
                content.append({"type": "text", "text": ""})
            elif btype == "tool_use":
                tool_blocks[idx] = {
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input_json_buf": "",
                }
                content.append({
                    "type": "tool_use",
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": {},
                })
            elif btype == "thinking":
                thinking_bufs[idx] = block.get("thinking", "")
                content.append({"type": "thinking", "thinking": ""})

        elif etype == "content_block_delta":
            idx = ev.get("index", 0)
            delta = ev.get("delta", {})
            dtype = delta.get("type", "")

            if dtype == "text_delta":
                text = delta.get("text", "")
                text_bufs[idx] = text_bufs.get(idx, "") + text
            elif dtype == "input_json_delta":
                partial = delta.get("partial_json", "")
                if idx in tool_blocks:
                    tool_blocks[idx]["input_json_buf"] += partial
            elif dtype == "thinking_delta":
                thinking = delta.get("thinking", "")
                thinking_bufs[idx] = thinking_bufs.get(idx, "") + thinking
            elif dtype == "signature_delta":
                sig = delta.get("signature", "")
                signature_bufs[idx] = signature_bufs.get(idx, "") + sig

        elif etype == "content_block_stop":
            idx = ev.get("index", 0)
            # Finalize tool_use input from accumulated JSON
            if idx in tool_blocks:
                buf = tool_blocks[idx].get("input_json_buf", "")
                try:
                    parsed = json.loads(buf) if buf else {}
                except json.JSONDecodeError:
                    parsed = {}
                # Find the matching content block and set input
                for cb in content:
                    if cb.get("type") == "tool_use" and cb.get("id") == tool_blocks[idx]["id"]:
                        cb["input"] = parsed
                        break

        elif etype == "message_delta":
            delta = ev.get("delta", {})
            if delta.get("stop_reason"):
                stop_reason = delta["stop_reason"]
            if delta.get("stop_sequence") is not None:
                stop_sequence = delta["stop_sequence"]
            usage = ev.get("usage", {})
            if usage.get("input_tokens"):
                input_tokens = usage["input_tokens"]
            if usage.get("output_tokens"):
                output_tokens = usage["output_tokens"]

    # Finalize text blocks using position mapping
    for idx, text in text_bufs.items():
        pos = idx_to_pos.get(idx)
        if pos is not None and pos < len(content):
            content[pos]["text"] = text

    # Finalize thinking blocks using position mapping
    for idx, thinking in thinking_bufs.items():
        pos = idx_to_pos.get(idx)
        if pos is not None and pos < len(content):
            content[pos]["thinking"] = thinking
            if idx in signature_bufs and signature_bufs[idx]:
                content[pos]["signature"] = signature_bufs[idx]

    return {
        "id": msg_id or "",
        "type": "message",
        "role": role or "assistant",
        "content": content,
        "model": model or "",
        "stop_reason": stop_reason,
        "stop_sequence": stop_sequence,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }
