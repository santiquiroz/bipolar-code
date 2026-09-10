"""Tests del clasificador determinista de complejidad."""
import pytest

from app.services import route_classifier as rc


def _body(text: str, model: str = "claude-sonnet-4-6", tools: int = 0, **extra) -> dict:
    body = {"model": model, "messages": [{"role": "user", "content": text}], **extra}
    if tools:
        body["tools"] = [{"name": f"t{i}", "input_schema": {"type": "object"}} for i in range(tools)]
    return body


def test_greeting_with_haiku_is_trivial():
    cls = rc.classify_request(_body("hola", model="claude-3-5-haiku"), prompt_tokens=20)
    assert cls.tier == "trivial"
    assert cls.score == 0
    assert "greeting:-15" in cls.reasons


def test_reasons_are_ordered_contributions_and_end_with_tier():
    cls = rc.classify_request(_body("explain what this does"), prompt_tokens=100)
    assert cls.reasons[0] == "base:+10"
    task = rc.classify_task("Diagnostica la causa raíz del deadlock y refactoriza el servicio")
    assert task.reasons[0] == "base:+25" and task.tier == "standard" and "multi_intent:+5" in task.reasons
    assert cls.reasons[-1].startswith("tier:")


def test_agentic_turn_with_tool_results_lands_standard():
    body = {
        "model": "claude-sonnet-4-6",
        "tools": [{"name": f"t{i}", "input_schema": {}} for i in range(15)],
        "messages": [
            {"role": "user", "content": "implementa el endpoint"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "t1", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
        ],
    }
    cls = rc.classify_request(body, prompt_tokens=12_000)
    assert cls.signals.has_tool_results is True
    assert cls.intent == "agentic_loop"
    assert cls.tier == "standard"


def test_debug_request_with_opus_and_large_prompt_is_complex():
    text = "Diagnostica la causa raíz del deadlock y refactoriza el servicio de pagos"
    cls = rc.classify_request(_body(text, model="claude-opus-4-6"), prompt_tokens=40_000)
    assert cls.tier == "complex"
    assert cls.intent == "debug"
    assert "model:opus:+25" in cls.reasons


def test_thinking_floors_to_complex():
    cls = rc.classify_request(_body("hola", thinking={"type": "enabled", "budget_tokens": 1024}), prompt_tokens=10)
    assert cls.tier == "complex"
    assert "floor:complex" in cls.reasons


def test_opus_floors_to_standard():
    cls = rc.classify_request(_body("resume esto", model="claude-opus-4-6"), prompt_tokens=10)
    assert rc.tier_index(cls.tier) >= rc.tier_index("standard")


def test_openai_shape_counts_tool_role_and_image_url():
    body = {
        "model": "gpt-4o",
        "tools": [{"type": "function", "function": {"name": "f"}}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "mira"}, {"type": "image_url", "image_url": {"url": "data:x"}}]},
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}], "content": ""},
            {"role": "tool", "tool_call_id": "c1", "content": "done"},
        ],
    }
    s = rc.extract_signals(body, prompt_tokens=500, surface="chat_completions")
    assert s.has_tool_results is True
    assert s.has_images is True
    assert s.n_tools == 1


@pytest.mark.parametrize("tokens,points", [(1_000, 0), (5_000, 5), (20_000, 10), (80_000, 20), (150_000, 30)])
def test_token_buckets(tokens, points):
    assert rc._token_points(tokens) == points


@pytest.mark.parametrize("score,tier", [(0, "trivial"), (24, "trivial"), (25, "simple"), (49, "simple"), (50, "standard"), (74, "standard"), (75, "complex"), (100, "complex")])
def test_tier_thresholds(score, tier):
    assert rc.tier_from_score(score) == tier


def test_custom_thresholds_are_respected():
    assert rc.tier_from_score(30, {"simple": 10, "standard": 20, "complex": 40}) == "standard"


def test_detect_intent_priority_and_default():
    assert rc.detect_intent("Traceback (most recent call last)") == "debug"
    assert rc.detect_intent("traduce este texto") == "translate"
    assert rc.detect_intent("buenas tardes") == "chat"
    assert rc.detect_intent("ok", n_tools=3, has_tool_results=True) == "agentic_loop"


def test_classify_task_from_text_counts_paths():
    task = "Renombra Foo en src/a/b.py, src/c/d.py y tests/e/f.py"
    cls = rc.classify_task(task)
    assert cls.signals.file_path_refs >= 3
    assert cls.intent == "code_edit"


def test_header_override_marks_source_and_ignores_invalid():
    cls = rc.classify_request(_body("hola"), prompt_tokens=5)
    forced = rc.with_header_override(cls, "complex")
    assert forced.tier == "complex" and forced.source == "header"
    assert rc.with_header_override(cls, "gigantic") is cls


def test_conversation_key_stable_when_messages_appended():
    base = {"system": "sys", "messages": [{"role": "user", "content": "primer mensaje"}]}
    longer = {"system": "sys", "messages": base["messages"] + [{"role": "assistant", "content": "ok"}, {"role": "user", "content": "otro"}]}
    assert rc.conversation_key(base) == rc.conversation_key(longer)


def test_classification_is_deterministic():
    body = _body("refactoriza el módulo de auth", tools=3)
    assert rc.classify_request(body, 3_000) == rc.classify_request(body, 3_000)
