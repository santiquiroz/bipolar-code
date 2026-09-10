"""Tests de la máquina de estados de salud/cuota por destino."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.quota_signals import QuotaSignal
from app.services import health_service as hs


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    from app.core import config as cfg

    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(cfg, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(hs, "get_settings", lambda: FakeSettings())
    hs.reload_for_tests()
    yield tmp_path
    hs.reload_for_tests()


NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def test_unknown_target_is_available(state_dir):
    assert hs.effective_state("provider:x", NOW) == "available"
    assert hs.seconds_left("provider:x", NOW) == 0.0


def test_rate_limit_moves_to_cooling_and_expires(state_dir):
    hs.mark_signal("provider:a", QuotaSignal("rate_limit", None, "429"), cooldown_s=600, now=NOW)
    assert hs.effective_state("provider:a", NOW) == "cooling"
    assert 590 < hs.seconds_left("provider:a", NOW) <= 600
    assert hs.effective_state("provider:a", NOW + timedelta(seconds=601)) == "available"


def test_retry_after_overrides_default_cooldown(state_dir):
    hs.mark_signal("provider:a", QuotaSignal("rate_limit", 120, "slow down"), cooldown_s=900, now=NOW)
    assert 110 < hs.seconds_left("provider:a", NOW) <= 120


@pytest.mark.parametrize("rule,min_hours", [("5h", 5), ("none", 6)])
def test_quota_exhausted_uses_reset_rule(state_dir, rule, min_hours):
    hs.mark_signal("cli:codex", QuotaSignal("quota_exhausted", None, "out of credits"), quota_reset=rule, now=NOW)
    assert hs.effective_state("cli:codex", NOW) == "exhausted"
    assert hs.seconds_left("cli:codex", NOW) == pytest.approx(min_hours * 3600, abs=1)


def test_weekly_reset_lands_on_next_monday_midnight(state_dir):
    until = hs.reset_at("weekly", NOW)
    local = until.astimezone()
    assert local.weekday() == 0 and local.hour == 0 and local.minute == 0
    assert until > NOW


def test_auth_marks_unavailable_one_hour(state_dir):
    hs.mark_signal("cli:agy", QuotaSignal("auth", None, "authentication required"), now=NOW)
    assert hs.effective_state("cli:agy", NOW) == "unavailable"
    assert hs.seconds_left("cli:agy", NOW) == pytest.approx(3600, abs=1)


def test_three_generic_failures_open_circuit_and_success_clears(state_dir):
    for _ in range(2):
        hs.mark_failure("provider:b", "500", now=NOW)
    assert hs.effective_state("provider:b", NOW) == "available"
    hs.mark_failure("provider:b", "500", now=NOW)
    assert hs.effective_state("provider:b", NOW) == "unavailable"
    hs.mark_success("provider:b", latency_ms=12.5, now=NOW)
    entry = hs.get("provider:b", NOW)
    assert entry.state == "available" and entry.consecutive_failures == 0 and entry.last_latency_ms == 12.5


def test_pools_are_independent_keys(state_dir):
    hs.mark_signal("cli:antigravity#gemini", QuotaSignal("quota_exhausted", None, "0%"), quota_reset="weekly", now=NOW)
    assert hs.effective_state("cli:antigravity#gemini", NOW) == "exhausted"
    assert hs.effective_state("cli:antigravity#claude", NOW) == "available"


def test_state_persists_and_reloads(state_dir):
    hs.mark_signal("provider:a", QuotaSignal("rate_limit", 300, "429"), now=NOW)
    assert (state_dir / "routing_state.json").exists()
    hs.reload_for_tests()
    assert hs.effective_state("provider:a", NOW) == "cooling"


def test_reset_clears_one_or_all(state_dir):
    hs.mark_failure("provider:a", now=NOW)
    hs.mark_failure("provider:b", now=NOW)
    assert hs.reset("provider:a") == ["provider:a"]
    assert "provider:a" not in hs.snapshot(NOW) and "provider:b" in hs.snapshot(NOW)
    hs.reset()
    assert hs.snapshot(NOW) == {}


def test_set_exhausted_until_uses_known_reset_time(state_dir):
    until = NOW + timedelta(days=6)
    hs.set_exhausted_until("cli:antigravity#gemini", until, "0% remaining", now=NOW)
    assert hs.seconds_left("cli:antigravity#gemini", NOW) == pytest.approx(6 * 86400, abs=1)
