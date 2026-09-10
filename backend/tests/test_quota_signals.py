"""Tests de detección de señales de cuota/auth/sobrecarga."""
import pytest

from app.core import quota_signals as qs


@pytest.mark.parametrize("text,kind", [
    ("hit a rate limit, try again later", "rate_limit"),
    ("HTTP 429 Too Many Requests", "rate_limit"),
    ("RESOURCE_EXHAUSTED (code 429): Individual quota reached", "quota_exhausted"),
    ("You've hit your usage limit", "quota_exhausted"),
    ("insufficient_quota", "quota_exhausted"),
    ("Your workspace is out of credits. Ask your workspace owner to refill", "quota_exhausted"),
    ("weekly limit reached", "quota_exhausted"),
    ("authentication required", "auth"),
    ("Not logged in. Run codex login", "auth"),
    ("401 Unauthorized", "auth"),
    ("The service is overloaded", "overloaded"),
    ("529 overloaded_error", "overloaded"),
])
def test_detect_signal_kinds(text, kind):
    signal = qs.detect_signal(text)
    assert signal is not None
    assert signal.kind == kind


@pytest.mark.parametrize("text", ["compiled successfully", "rate: 3 files/s", "all tests passed"])
def test_detect_signal_negatives(text):
    assert qs.detect_signal(text) is None


def test_status_code_drives_kind_without_text():
    assert qs.detect_signal("", status=429).kind == "rate_limit"
    assert qs.detect_signal("", status=401).kind == "auth"
    assert qs.detect_signal("", status=503).kind == "overloaded"
    assert qs.detect_signal("", status=500) is None


@pytest.mark.parametrize("text,seconds", [
    ("retry after 30", 30),
    ("Retry-After: 45s", 45),
    ("try again in 2 minutes", 120),
    ("Resets in 3h", 3 * 3600),
    ("no hint here", None),
])
def test_parse_retry_after(text, seconds):
    assert qs.parse_retry_after(text) == seconds


def test_cooldown_from_header_clamps():
    assert qs.cooldown_from_header("5", 900) == 30
    assert qs.cooldown_from_header("99999", 900) == 3600
    assert qs.cooldown_from_header(None, 900) == 900
    assert qs.cooldown_from_header("abc", 900) == 900


def test_signal_from_attempt_ignores_quota_word_in_long_successful_output():
    stdout = ("Implemented the rate limit middleware as requested.\n" * 40)
    assert qs.signal_from_attempt(0, stdout, "", structured_error=False) is None


def test_signal_from_attempt_counts_structured_error_even_with_exit_zero():
    stdout = '{"type":"error","message":"Your workspace is out of credits."}\n' * 30
    signal = qs.signal_from_attempt(0, stdout, "", structured_error=True)
    assert signal is not None and signal.kind == "quota_exhausted"


def test_signal_from_attempt_short_output_counts():
    signal = qs.signal_from_attempt(0, "", "hit a rate limit")
    assert signal is not None and signal.kind == "rate_limit"


def test_signal_from_attempt_agy_interrupted_is_quota_exhausted():
    stdout = '{"status":"ERROR","error":"The stream was interrupted. Please continue the task you were working on."}'
    signal = qs.signal_from_attempt(0, stdout, "[agy] print timeout after 9m0s with turn in progress")
    assert signal is not None and signal.kind == "quota_exhausted"


def test_excerpt_is_compact_and_bounded():
    text = "a  b\n\n c" + "x" * 500
    excerpt = qs.make_excerpt(text)
    assert "\n" not in excerpt and len(excerpt) <= qs.EXCERPT_CHARS
