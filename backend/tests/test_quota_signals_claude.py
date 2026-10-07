"""Mensajes de límite de Claude Code: detección y hora de reset."""
from datetime import datetime, timedelta, timezone

from app.core.quota_signals import detect_signal, parse_reset_at


def _local(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi).astimezone()


def test_clock_later_today():
    now = _local(2026, 10, 7, 14, 0)  # miércoles 14:00
    assert parse_reset_at("You've hit your session limit · resets 3:45pm", now) == _local(2026, 10, 7, 15, 45).astimezone(timezone.utc)


def test_clock_already_passed_goes_to_tomorrow():
    now = _local(2026, 10, 7, 16, 0)
    assert parse_reset_at("5-hour limit reached ∙ resets 3pm", now) == _local(2026, 10, 8, 15, 0).astimezone(timezone.utc)


def test_24h_clock():
    now = _local(2026, 10, 7, 9, 0)
    assert parse_reset_at("limit reached, resets 15:30", now) == _local(2026, 10, 7, 15, 30).astimezone(timezone.utc)


def test_weekday_clock():
    now = _local(2026, 10, 7, 9, 0)  # miércoles
    assert parse_reset_at("weekly limit reached ∙ resets Mon 9am", now) == _local(2026, 10, 12, 9, 0).astimezone(timezone.utc)


def test_epoch_suffix():
    now = _local(2026, 10, 7, 9, 0)
    assert parse_reset_at("Claude AI usage limit reached|1760000000", now) == datetime.fromtimestamp(1760000000, tz=timezone.utc)


def test_unknown_text_returns_none():
    assert parse_reset_at("something else", _local(2026, 10, 7, 9, 0)) is None


def test_detect_signal_uses_reset_for_retry_after():
    now = _local(2026, 10, 7, 14, 0)
    signal = detect_signal("You've hit your session limit · resets 3:45pm", None, now=now)
    assert signal.kind == "quota_exhausted"
    assert signal.retry_after_s == int(timedelta(hours=1, minutes=45).total_seconds())


def test_out_of_range_clock_is_not_a_reset():
    now = _local(2026, 10, 7, 9, 0)
    assert parse_reset_at("quota exceeded, resets 45 minutes", now) is None
    assert parse_reset_at("limit reached, resets 9:75", now) is None
    signal = detect_signal("usage limit reached, resets 45 minutes", None, now=now)
    assert signal.kind == "quota_exhausted" and signal.retry_after_s is None
