"""Cuota de Muse: el 429 trae la hora exacta de reset de la ventana y bipolar la respeta."""
from datetime import datetime, timedelta, timezone

from app.core import quota_signals
from app.core.quota_signals import detect_signal, parse_reset_at, signal_from_attempt
from app.services import health_service

MUSE_429 = ("API error 429 [request_id=5c2b3eb6-c6e9-4c8d-a928-c194f5760e8c]: Subscription quota exhausted. "
            "Your usage window resets at 2026-10-27T05:07:47Z. (rate_limit_error)")
RESET = datetime(2026, 10, 27, 5, 7, 47, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 27, 0, 16, 28, tzinfo=timezone.utc)


def test_parse_iso_reset_from_muse():
    assert parse_reset_at(MUSE_429, NOW) == RESET


def test_parse_iso_reset_with_offset_and_without_zone():
    assert parse_reset_at("window resets at 2026-10-27T00:07:47-05:00", NOW) == RESET
    assert parse_reset_at("resets 2026-10-27T05:07:47", NOW) == RESET


def test_muse_429_is_quota_with_exact_retry_after():
    signal = detect_signal(MUSE_429, None, now=NOW)
    assert signal.kind == "quota_exhausted"
    assert signal.retry_after_s == int((RESET - NOW).total_seconds())


def test_muse_attempt_marks_exhausted_until_muse_reset(tmp_path, monkeypatch):
    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)

    monkeypatch.setattr(health_service, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(quota_signals, "datetime", FrozenDatetime)
    health_service.reload_for_tests()
    try:
        signal = signal_from_attempt(1, "", "run ended with Failed: " + MUSE_429, structured_error=True)
        assert signal.kind == "quota_exhausted"
        health_service.mark_signal("cli:muse", signal, quota_reset="none", now=NOW)
        entry = health_service.get("cli:muse", now=NOW)
        assert entry.state == "exhausted"
        assert datetime.fromisoformat(entry.until) == RESET
        assert health_service.get("cli:muse", now=RESET - timedelta(seconds=1)).state == "exhausted"
        # antes caía en el reset desconocido (6 h): ahora vuelve justo cuando Muse dice
        assert health_service.get("cli:muse", now=RESET + timedelta(seconds=1)).state == "available"
    finally:
        health_service.reload_for_tests()
