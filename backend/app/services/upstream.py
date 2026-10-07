"""Failover antes del primer byte: recorre un plan de pasos (provider + llave), abre el
upstream del primero que responde bien y clasifica las fallas para decidir si sigue."""
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional

from app.core.quota_signals import detect_signal

FailureKind = Literal["retry_credential", "retry_provider", "fatal"]

CREDENTIAL_STATUSES = frozenset({401, 402, 403, 429})
PROVIDER_STATUSES = frozenset({404, 408})
CREDENTIAL_SIGNALS = frozenset({"rate_limit", "quota_exhausted", "auth"})
RATE_LIMIT_STATUSES = frozenset({402, 429})
OVERLOADED_STATUSES = frozenset({503, 529})
SUMMARY_MESSAGE_CHARS = 120

_ANTHROPIC_ERROR_TYPES = {429: "rate_limit_error", 529: "overloaded_error"}


@dataclass
class Opened:
    upstream: Any


@dataclass
class Failed:
    status: Optional[int]
    message: str
    exc: Optional[BaseException] = None


@dataclass
class AttemptFailure:
    step: Any
    kind: FailureKind
    status: Optional[int]
    message: str


@dataclass
class PlanResult:
    step: Any = None
    upstream: Any = None
    failures: list[AttemptFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.upstream is not None


def classify_failure(status: Optional[int], text: str = "", exc: Optional[BaseException] = None) -> FailureKind:
    if exc is not None:
        return "retry_provider"
    if status in CREDENTIAL_STATUSES:
        return "retry_credential"
    signal = detect_signal(text or "", status)
    if signal and signal.kind in CREDENTIAL_SIGNALS:
        return "retry_credential"
    if signal and signal.kind == "overloaded":
        return "retry_provider"
    if status is not None and (status in PROVIDER_STATUSES or status >= 500):
        return "retry_provider"
    return "fatal"


async def attempt_plan(
    plan: list,
    open_attempt: Callable[[Any], Awaitable[Opened | Failed]],
    provider_of: Callable[[Any], str],
    on_failure: Callable[[Any, AttemptFailure], None],
) -> PlanResult:
    result = PlanResult()
    dead_providers: set[str] = set()
    for step in plan:
        if provider_of(step) in dead_providers:
            continue
        outcome = await open_attempt(step)
        if isinstance(outcome, Opened):
            result.step, result.upstream = step, outcome.upstream
            return result
        failure = AttemptFailure(step, classify_failure(outcome.status, outcome.message, outcome.exc), outcome.status, outcome.message)
        on_failure(step, failure)
        result.failures.append(failure)
        if failure.kind == "fatal":
            return result
        if failure.kind == "retry_provider":
            dead_providers.add(provider_of(step))
    return result


def _is_rate_limited(failure: AttemptFailure) -> bool:
    if failure.status in RATE_LIMIT_STATUSES:
        return True
    signal = detect_signal(failure.message or "", failure.status)
    return bool(signal and signal.kind in ("rate_limit", "quota_exhausted"))


def _is_overloaded(failure: AttemptFailure) -> bool:
    if failure.status in OVERLOADED_STATUSES:
        return True
    signal = detect_signal(failure.message or "", failure.status)
    return bool(signal and signal.kind == "overloaded")


def exhausted_status(failures: list[AttemptFailure]) -> int:
    if failures and failures[-1].kind == "fatal":
        return failures[-1].status or 502
    if any(_is_rate_limited(f) for f in failures):
        return 429
    if any(_is_overloaded(f) for f in failures):
        return 529
    return 502


def failures_summary(failures: list[AttemptFailure], describe: Callable[[Any], str]) -> str:
    parts = []
    for f in failures:
        status = f"{f.status} " if f.status else ""
        parts.append(f"{describe(f.step)}: {status}{(f.message or '')[:SUMMARY_MESSAGE_CHARS]}")
    return "; ".join(parts)


def _anthropic_error_type(status: int) -> str:
    if status in _ANTHROPIC_ERROR_TYPES:
        return _ANTHROPIC_ERROR_TYPES[status]
    return "invalid_request_error" if 400 <= status < 500 else "api_error"


def anthropic_error_body(status: int, message: str) -> dict:
    return {"type": "error", "error": {"type": _anthropic_error_type(status), "message": message}}


def openai_error_body(status: int, message: str) -> dict:
    return {"error": {"message": message, "type": _anthropic_error_type(status), "code": status}}
