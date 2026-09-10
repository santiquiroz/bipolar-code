"""Tests de presupuestos por ventana y del log de decisiones (SQLite temporal)."""
from datetime import datetime, timezone

import pytest

from app.models.smart import BudgetWindow
from app.services import budget_service, decisions_log, usage_tracker


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from app.core import config as cfg_module

    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(cfg_module, "get_settings", lambda: FakeSettings())
    budget_service.clear_cache()
    yield tmp_path
    budget_service.clear_cache()


def test_window_start_day_week_month():
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)  # jueves
    day = budget_service.window_start("day", now).astimezone()
    week = budget_service.window_start("week", now).astimezone()
    month = budget_service.window_start("month", now).astimezone()
    assert day.hour == 0 and day.day == now.astimezone().day
    assert week.weekday() == 0
    assert month.day == 1


def test_provider_id_of_strips_provider_prefix_only():
    assert budget_service.provider_id_of("provider:anthropic") == "anthropic"
    assert budget_service.provider_id_of("cli:codex") == "cli:codex"


@pytest.mark.asyncio
async def test_check_budget_requests_and_cost(tmp_db):
    await usage_tracker.init_db()
    for _ in range(3):
        await usage_tracker.record("anthropic", "claude-sonnet-4-6", 100, 50, 1.0, False)
    budgets = [BudgetWindow(target_key="provider:anthropic", window="day", max_requests=3)]
    assert await budget_service.check_budget(budgets, "provider:anthropic") == "budget_exceeded:day:requests"
    budget_service.clear_cache()
    budgets = [BudgetWindow(target_key="provider:anthropic", window="month", max_cost_usd=2.5)]
    assert await budget_service.check_budget(budgets, "provider:anthropic") == "budget_exceeded:month:cost"
    assert await budget_service.check_budget(budgets, "provider:copilot") is None


@pytest.mark.asyncio
async def test_zero_limits_never_exceed_and_missing_table_is_zero(tmp_db):
    budgets = [BudgetWindow(target_key="provider:anthropic", window="day")]
    assert await budget_service.check_budget(budgets, "provider:anthropic") is None
    spend = await budget_service.spend_since("anthropic", "2020-01-01")
    assert spend == {"requests": 0, "cost_usd": 0.0}


@pytest.mark.asyncio
async def test_decisions_insert_update_list_summary(tmp_db):
    await usage_tracker.init_db()
    await decisions_log.init_tables()
    row = {
        "id": "abc123", "surface": "messages", "tier": "standard", "score": 55, "intent": "code_edit",
        "requested_model": "claude-sonnet-4-6", "prompt_tokens": 1200, "chosen_key": "provider:copilot",
        "chosen_model": "gpt-4o", "would_key": "provider:copilot", "would_model": "gpt-4o",
        "source": "smart", "mode": "active", "reasons": ["base:+10"], "rejected": [["provider:x", "unreachable"]],
        "decision_ms": 1.5,
    }
    await decisions_log.insert(row)
    await decisions_log.insert({**row, "id": "def456", "would_key": "provider:anthropic", "source": "explicit_rule"})
    await decisions_log.update_outcome("abc123", "ok", 250.0, "")
    rows = await decisions_log.list_decisions(limit=10)
    assert {r["id"] for r in rows} == {"abc123", "def456"}
    assert rows[0]["rejected"] == [["provider:x", "unreachable"]]
    only_smart = await decisions_log.list_decisions(source="smart")
    assert [r["id"] for r in only_smart] == ["abc123"]
    summary = await decisions_log.summary("day")
    assert summary["count"] == 2
    assert summary["outcomes"] == {"ok": 1, "error": 0, "pending": 1}
    assert summary["agreement_rate"] == 0.5
    assert summary["by_source"] == {"smart": 1, "explicit_rule": 1}


@pytest.mark.asyncio
async def test_decisions_purge(tmp_db):
    await usage_tracker.init_db()
    await decisions_log.init_tables()
    await decisions_log.insert({"id": "old", "timestamp": "2020-01-01T00:00:00+00:00", "reasons": [], "rejected": []})
    assert await decisions_log.purge_older_than(30) == 1
