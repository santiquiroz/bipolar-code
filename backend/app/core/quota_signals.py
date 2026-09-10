"""
Detección de señales de cuota/auth/sobrecarga en respuestas HTTP y salidas de CLIs.
Compartido por el router (/v1) y el broker de delegación.
"""
import re
from dataclasses import dataclass
from typing import Literal, Optional

SignalKind = Literal["rate_limit", "quota_exhausted", "auth", "overloaded"]

RATE_LIMIT_RE = re.compile(r"rate.?limit|\b429\b|too many requests|slow down", re.I)
EXHAUSTED_RE = re.compile(
    r"quota|usage limit|insufficient_quota|resource_exhausted|out of (?:ai )?credits"
    r"|credits? (?:limit|exhausted)|weekly limit|hit your limit|limit reached",
    re.I,
)
AUTH_RE = re.compile(
    r"not (?:logged in|authenticated)|authentication required|invalid api key|\b401\b"
    r"|login required|unauthori[sz]ed|please (?:run|sign in)",
    re.I,
)
OVERLOADED_RE = re.compile(r"overloaded|\b529\b|\b503\b|service unavailable", re.I)
RETRY_AFTER_RE = re.compile(
    r"(?:retry.?after|try again in|resets? in)[:= ]*(\d+)\s*(h|hours?|m|min|minutes?|s|sec|seconds?)?",
    re.I,
)
# agy con el pool en 0 %: reintenta con backoff hasta --print-timeout y termina así.
AGY_INTERRUPTED_RE = re.compile(r"stream was interrupted", re.I)

_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1}
TAIL_LINES = 40
SHORT_OUTPUT_CHARS = 400
EXCERPT_CHARS = 200


@dataclass(frozen=True)
class QuotaSignal:
    kind: SignalKind
    retry_after_s: Optional[int]
    excerpt: str


def parse_retry_after(text: str) -> Optional[int]:
    match = RETRY_AFTER_RE.search(text or "")
    if not match:
        return None
    amount = int(match.group(1))
    unit = (match.group(2) or "s")[0].lower()
    return amount * _UNIT_SECONDS.get(unit, 1)


def cooldown_from_header(value: Optional[str], default: int) -> int:
    if value and value.strip().isdigit():
        return max(30, min(3600, int(value.strip())))
    return default


def make_excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    compact = " ".join((text or "").split())
    return compact[-limit:] if len(compact) > limit else compact


def _kind_for(text: str, status: Optional[int]) -> Optional[SignalKind]:
    if status == 401 or AUTH_RE.search(text):
        return "auth"
    if EXHAUSTED_RE.search(text):
        return "quota_exhausted"
    if status == 429 or RATE_LIMIT_RE.search(text):
        return "rate_limit"
    if status in (503, 529) or OVERLOADED_RE.search(text):
        return "overloaded"
    return None


def detect_signal(text: str, status: Optional[int] = None) -> Optional[QuotaSignal]:
    text = text or ""
    kind = _kind_for(text, status)
    if kind is None:
        return None
    return QuotaSignal(kind=kind, retry_after_s=parse_retry_after(text), excerpt=make_excerpt(text))


def _tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join((text or "").splitlines()[-lines:])


def signal_from_attempt(
    returncode: Optional[int],
    stdout: str,
    stderr: str,
    structured_error: bool = False,
) -> Optional[QuotaSignal]:
    """Solo mira el final de la salida y solo cuenta cuando el intento falló o la
    salida es corta: una tarea que HABLA de cuotas no debe envenenar la disponibilidad."""
    combined = _tail(stderr) + "\n" + _tail(stdout)
    total_len = len(stdout or "") + len(stderr or "")
    failed = bool(returncode) or structured_error or total_len < SHORT_OUTPUT_CHARS
    if not failed:
        return None
    if AGY_INTERRUPTED_RE.search(combined):
        return QuotaSignal(kind="quota_exhausted", retry_after_s=None, excerpt=make_excerpt(combined))
    return detect_signal(combined)
