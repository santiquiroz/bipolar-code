"""
Estado de salud/cuota por destino de routing ("provider:<id>", "cli:<id>",
"cli:antigravity#gemini"). En memoria, persistido en <config_dir>/routing_state.json.
"""
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.quota_signals import QuotaSignal

log = get_logger(__name__)

_lock = threading.Lock()
_state: dict[str, "TargetHealth"] = {}
_loaded = False

GENERIC_FAILURES_TO_OPEN = 3
CIRCUIT_OPEN_SECONDS = 120
AUTH_UNAVAILABLE_SECONDS = 3600
OVERLOADED_COOLING_SECONDS = 120
DEFAULT_COOLDOWN_SECONDS = 900
UNKNOWN_RESET_SECONDS = 6 * 3600


@dataclass
class TargetHealth:
    key: str
    state: str = "available"  # available | cooling | exhausted | unavailable
    until: Optional[str] = None  # ISO-8601 UTC
    last_signal: str = ""
    last_excerpt: str = ""
    consecutive_failures: int = 0
    total_ok: int = 0
    total_failed: int = 0
    last_ok_at: Optional[str] = None
    last_latency_ms: Optional[float] = None
    pending_excerpt: str = field(default="", repr=False)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("pending_excerpt", None)
        return data


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _state_path() -> Path:
    return Path(get_settings().litellm_config_dir) / "routing_state.json"


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = _state_path()
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key, data in raw.items():
            data = {k: v for k, v in data.items() if k in TargetHealth.__dataclass_fields__}
            _state[key] = TargetHealth(**{"key": key, **data})
    except Exception as e:
        log.warning("routing_state_load_failed", error=str(e))


def _persist() -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({k: v.to_dict() for k, v in _state.items()}, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as e:
        log.warning("routing_state_save_failed", error=str(e))


def reload_for_tests() -> None:
    global _loaded
    with _lock:
        _state.clear()
        _loaded = False


def _entry(key: str) -> TargetHealth:
    _ensure_loaded()
    if key not in _state:
        _state[key] = TargetHealth(key=key)
    return _state[key]


def get(key: str, now: Optional[datetime] = None) -> TargetHealth:
    with _lock:
        entry = _entry(key)
        _expire(entry, _now(now))
        return TargetHealth(**{k: v for k, v in asdict(entry).items()})


def _expire(entry: TargetHealth, now: datetime) -> None:
    until = _parse(entry.until)
    if entry.state != "available" and until is not None and until <= now:
        entry.state = "available"
        entry.until = None


def effective_state(key: str, now: Optional[datetime] = None) -> str:
    return get(key, now).state


def is_available(key: str, now: Optional[datetime] = None) -> bool:
    return effective_state(key, now) == "available"


def seconds_left(key: str, now: Optional[datetime] = None) -> float:
    entry = get(key, now)
    until = _parse(entry.until)
    if entry.state == "available" or until is None:
        return 0.0
    return max(0.0, (until - _now(now)).total_seconds())


def reset_at(rule: str, now: datetime) -> datetime:
    if rule == "5h":
        return now + timedelta(hours=5)
    local = now.astimezone()
    if rule == "daily":
        nxt = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return nxt.astimezone(timezone.utc)
    if rule == "weekly":
        days_ahead = (7 - local.weekday()) % 7 or 7
        nxt = (local + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
        return nxt.astimezone(timezone.utc)
    return now + timedelta(seconds=UNKNOWN_RESET_SECONDS)


def _set(entry: TargetHealth, state: str, until: Optional[datetime], signal: str, excerpt: str) -> None:
    entry.state = state
    entry.until = _iso(until) if until else None
    entry.last_signal = signal
    entry.last_excerpt = excerpt[:200]


def mark_success(key: str, latency_ms: Optional[float] = None, now: Optional[datetime] = None) -> None:
    now = _now(now)
    with _lock:
        entry = _entry(key)
        entry.state = "available"
        entry.until = None
        entry.consecutive_failures = 0
        entry.total_ok += 1
        entry.last_ok_at = _iso(now)
        if latency_ms is not None:
            entry.last_latency_ms = latency_ms
        _persist()


def mark_failure(key: str, reason: str = "", now: Optional[datetime] = None) -> None:
    """Fallo genérico (5xx, conexión, exit != 0 sin señal). Tres seguidos abren el circuito."""
    now = _now(now)
    with _lock:
        entry = _entry(key)
        entry.consecutive_failures += 1
        entry.total_failed += 1
        entry.last_excerpt = (reason or "")[:200]
        if entry.consecutive_failures >= GENERIC_FAILURES_TO_OPEN and entry.state == "available":
            _set(entry, "unavailable", now + timedelta(seconds=CIRCUIT_OPEN_SECONDS), "failures", reason or "")
        _persist()


def mark_signal(
    key: str,
    signal: QuotaSignal,
    *,
    cooldown_s: int = DEFAULT_COOLDOWN_SECONDS,
    quota_reset: str = "none",
    now: Optional[datetime] = None,
) -> None:
    now = _now(now)
    with _lock:
        entry = _entry(key)
        entry.total_failed += 1
        if signal.kind == "rate_limit":
            secs = signal.retry_after_s or cooldown_s
            _set(entry, "cooling", now + timedelta(seconds=max(30, min(3600, secs))), "rate_limit", signal.excerpt)
        elif signal.kind == "overloaded":
            _set(entry, "cooling", now + timedelta(seconds=OVERLOADED_COOLING_SECONDS), "overloaded", signal.excerpt)
        elif signal.kind == "quota_exhausted":
            until = now + timedelta(seconds=signal.retry_after_s) if signal.retry_after_s else reset_at(quota_reset, now)
            _set(entry, "exhausted", until, "quota_exhausted", signal.excerpt)
        elif signal.kind == "auth":
            _set(entry, "unavailable", now + timedelta(seconds=AUTH_UNAVAILABLE_SECONDS), "auth", signal.excerpt)
        _persist()


def set_exhausted_until(key: str, until: datetime, excerpt: str = "", now: Optional[datetime] = None) -> None:
    """Usado cuando la cuota se conoce de antemano (agy -p "/usage" devuelve el reset exacto)."""
    with _lock:
        entry = _entry(key)
        _set(entry, "exhausted", until, "quota_exhausted", excerpt)
        _persist()


def reset(key: Optional[str] = None) -> list[str]:
    with _lock:
        _ensure_loaded()
        keys = [key] if key else list(_state.keys())
        for k in keys:
            _state.pop(k, None)
        _persist()
        return keys


def snapshot(now: Optional[datetime] = None) -> dict[str, dict]:
    now = _now(now)
    with _lock:
        _ensure_loaded()
        for entry in _state.values():
            _expire(entry, now)
        return {k: {**v.to_dict(), "seconds_left": max(0.0, ((_parse(v.until) or now) - now).total_seconds())}
                for k, v in _state.items()}
