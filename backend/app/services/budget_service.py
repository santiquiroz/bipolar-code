"""
Presupuestos por destino sobre usage.db: requests y costo dentro de una ventana
(day/week/month). Cache corto para no consultar SQLite en cada decisión.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

from app.models.smart import BudgetWindow
from app.services import usage_tracker

CACHE_TTL_SECONDS = 10.0
_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def window_start(window: str, now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    local = now.astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "week":
        midnight -= timedelta(days=local.weekday())
    elif window == "month":
        midnight = midnight.replace(day=1)
    return midnight.astimezone(timezone.utc)


def provider_id_of(target_key: str) -> str:
    if target_key.startswith("provider:"):
        return target_key[len("provider:"):]
    return target_key  # "cli:<id>" se registra tal cual en usage.db


def clear_cache() -> None:
    _cache.clear()


async def spend_since(provider_id: str, since_iso: str) -> dict:
    cache_key = (provider_id, since_iso)
    hit = _cache.get(cache_key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    result = {"requests": 0, "cost_usd": 0.0}
    try:
        async with aiosqlite.connect(str(usage_tracker._db_path())) as db:
            cur = await db.execute(
                "SELECT COUNT(*), COALESCE(SUM(cost_usd), 0) FROM requests WHERE provider_id = ? AND timestamp >= ?",
                (provider_id, since_iso),
            )
            row = await cur.fetchone()
            if row:
                result = {"requests": int(row[0] or 0), "cost_usd": float(row[1] or 0.0)}
    except Exception:
        pass  # sin tabla todavía → sin gasto
    _cache[cache_key] = (time.monotonic(), result)
    return result


def _exceeded(budget: BudgetWindow, spend: dict) -> Optional[str]:
    if budget.max_requests and spend["requests"] >= budget.max_requests:
        return f"budget_exceeded:{budget.window}:requests"
    if budget.max_cost_usd and spend["cost_usd"] >= budget.max_cost_usd:
        return f"budget_exceeded:{budget.window}:cost"
    return None


async def check_budget(budgets: list[BudgetWindow], target_key: str, now: Optional[datetime] = None) -> Optional[str]:
    """None = dentro de presupuesto; si no, el motivo del primer límite superado."""
    for budget in budgets:
        if budget.target_key != target_key:
            continue
        since = window_start(budget.window, now).isoformat()
        spend = await spend_since(provider_id_of(target_key), since)
        reason = _exceeded(budget, spend)
        if reason:
            return reason
    return None


async def usage_for(budgets: list[BudgetWindow], now: Optional[datetime] = None) -> dict[str, dict]:
    report: dict[str, dict] = {}
    for budget in budgets:
        since = window_start(budget.window, now).isoformat()
        spend = await spend_since(provider_id_of(budget.target_key), since)
        report[f"{budget.target_key}:{budget.window}"] = {
            **spend,
            "max_requests": budget.max_requests,
            "max_cost_usd": budget.max_cost_usd,
            "exceeded": _exceeded(budget, spend),
        }
    return report
