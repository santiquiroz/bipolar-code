"""Failover antes del primer byte: clasificación de fallas y recorrido del plan."""
import httpx
import pytest

from app.services import upstream
from app.services.upstream import AttemptFailure, Failed, Opened


@pytest.mark.parametrize("status,text,expected", [
    (401, "", "retry_credential"),
    (402, "", "retry_credential"),
    (403, "", "retry_credential"),
    (429, "", "retry_credential"),
    (400, "insufficient balance", "retry_credential"),
    (400, "You exceeded your current quota", "retry_credential"),
    (500, "", "retry_provider"),
    (503, "", "retry_provider"),
    (529, "Overloaded", "retry_provider"),
    (404, "model not found", "retry_provider"),
    (408, "", "retry_provider"),
    (400, "messages: field required", "fatal"),
    (413, "request too large", "fatal"),
    (422, "", "fatal"),
])
def test_classify_failure_by_status_and_text(status, text, expected):
    assert upstream.classify_failure(status, text) == expected


def test_classify_failure_exception_is_provider_level():
    assert upstream.classify_failure(None, "", httpx.ConnectError("boom")) == "retry_provider"


def _runner(outcomes: dict):
    calls = []

    async def open_attempt(step):
        calls.append(step)
        return outcomes[step]

    return open_attempt, calls


@pytest.mark.asyncio
async def test_credential_failure_moves_to_next_slot_same_provider():
    plan = ["a#0", "a#1", "b#0"]
    open_attempt, calls = _runner({"a#0": Failed(429, "rate limit"), "a#1": Opened("stream-a1"), "b#0": Opened("stream-b")})
    seen = []
    result = await upstream.attempt_plan(plan, open_attempt, lambda s: s.split("#")[0], lambda s, f: seen.append((s, f.kind)))
    assert result.ok and result.step == "a#1" and result.upstream == "stream-a1"
    assert calls == ["a#0", "a#1"]
    assert seen == [("a#0", "retry_credential")]


@pytest.mark.asyncio
async def test_provider_failure_skips_remaining_slots_of_that_provider():
    plan = ["a#0", "a#1", "b#0"]
    open_attempt, calls = _runner({"a#0": Failed(None, "connect", httpx.ConnectError("x")), "a#1": Opened("never"), "b#0": Opened("stream-b")})
    result = await upstream.attempt_plan(plan, open_attempt, lambda s: s.split("#")[0], lambda s, f: None)
    assert result.step == "b#0"
    assert calls == ["a#0", "b#0"]


@pytest.mark.asyncio
async def test_fatal_failure_stops_without_trying_others():
    plan = ["a#0", "b#0"]
    open_attempt, calls = _runner({"a#0": Failed(400, "messages: field required"), "b#0": Opened("never")})
    result = await upstream.attempt_plan(plan, open_attempt, lambda s: s.split("#")[0], lambda s, f: None)
    assert not result.ok
    assert calls == ["a#0"]
    assert [f.kind for f in result.failures] == ["fatal"]
    assert upstream.exhausted_status(result.failures) == 400


@pytest.mark.asyncio
async def test_all_failed_mixed_rate_limit_and_connect_maps_to_429():
    plan = ["a#0", "b#0"]
    open_attempt, _ = _runner({"a#0": Failed(429, "rate limit"), "b#0": Failed(None, "connect", httpx.ConnectError("x"))})
    result = await upstream.attempt_plan(plan, open_attempt, lambda s: s.split("#")[0], lambda s, f: None)
    assert not result.ok
    assert upstream.exhausted_status(result.failures) == 429


def test_exhausted_status_overloaded_and_generic():
    overloaded = [AttemptFailure("a", "retry_provider", 529, "Overloaded")]
    generic = [AttemptFailure("a", "retry_provider", None, "connect error")]
    auth_only = [AttemptFailure("a", "retry_credential", 401, "invalid api key")]
    assert upstream.exhausted_status(overloaded) == 529
    assert upstream.exhausted_status(generic) == 502
    assert upstream.exhausted_status(auth_only) == 502
    assert upstream.exhausted_status([]) == 502


def test_error_bodies_and_summary():
    failures = [AttemptFailure("a#0", "retry_credential", 429, "rate limit"), AttemptFailure("b#0", "retry_provider", None, "connect error")]
    summary = upstream.failures_summary(failures, lambda s: s)
    assert summary == "a#0: 429 rate limit; b#0: connect error"
    assert upstream.anthropic_error_body(429, "x") == {"type": "error", "error": {"type": "rate_limit_error", "message": "x"}}
    assert upstream.anthropic_error_body(529, "x")["error"]["type"] == "overloaded_error"
    assert upstream.anthropic_error_body(400, "x")["error"]["type"] == "invalid_request_error"
    assert upstream.anthropic_error_body(502, "x")["error"]["type"] == "api_error"
    assert upstream.openai_error_body(429, "x") == {"error": {"message": "x", "type": "rate_limit_error", "code": 429}}
