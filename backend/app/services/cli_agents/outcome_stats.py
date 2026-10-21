"""Historial de resultados del control de calidad de agentes delegados."""
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

from app.core.logging import get_logger
from app.services import usage_tracker

log = get_logger(__name__)

RESULTS = ("first_pass", "revised", "rejected")

_initialized_for: Optional[str] = None
_cache: dict[tuple[str, str], dict] = {}
_cache_at = 0.0
_cache_days: Optional[int] = None

_CREATE = """
CREATE TABLE IF NOT EXISTS delegate_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    result TEXT NOT NULL
)
"""
_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_delegate_outcomes_agent_tier_ts "
    "ON delegate_outcomes(agent_id, tier, timestamp)"
)


async def init_tables() -> None:
    global _initialized_for
    path = str(usage_tracker._db_path())
    async with aiosqlite.connect(path) as db:
        await db.execute(_CREATE)
        await db.execute(_INDEX)
        await db.commit()
    _initialized_for = path


async def _ensure() -> None:
    if _initialized_for != str(usage_tracker._db_path()):
        await init_tables()


async def record(agent_id: str, tier: str, result: str) -> None:
    if result not in RESULTS:
        log.debug("delegate_outcome_ignored", agent=agent_id, tier=tier, result=result)
        return
    try:
        await _ensure()
        async with aiosqlite.connect(str(usage_tracker._db_path())) as db:
            await db.execute(
                "INSERT INTO delegate_outcomes (timestamp, agent_id, tier, result) VALUES (?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), agent_id, tier, result),
            )
            await db.commit()
    except Exception as e:
        log.warning("delegate_outcome_record_failed", agent=agent_id, tier=tier, error=str(e))


async def summary(days: int = 30) -> list[dict]:
    await _ensure()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(str(usage_tracker._db_path())) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT agent_id, tier, COUNT(*) AS n, "
            "SUM(CASE WHEN result = 'first_pass' THEN 1 ELSE 0 END) AS first_pass, "
            "SUM(CASE WHEN result = 'revised' THEN 1 ELSE 0 END) AS revised, "
            "SUM(CASE WHEN result = 'rejected' THEN 1 ELSE 0 END) AS rejected "
            "FROM delegate_outcomes WHERE timestamp >= ? GROUP BY agent_id, tier "
            "ORDER BY agent_id, tier",
            (since,),
        )
        rows = await cur.fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["accept_pct"] = round(100 * (item["first_pass"] + item["revised"]) / item["n"])
        results.append(item)
    return results


async def refresh(days: int = 30, ttl_s: float = 60) -> None:
    global _cache, _cache_at, _cache_days
    now = time.monotonic()
    if _cache_days == days and now - _cache_at < ttl_s:
        return
    try:
        rows = await summary(days)
        _cache = {(row["agent_id"], row["tier"]): row for row in rows}
        _cache_at = time.monotonic()
        _cache_days = days
    except Exception as e:
        log.warning("delegate_outcome_refresh_failed", error=str(e))


def accept_pct(agent_id: str, tier: str, min_samples: int) -> Optional[int]:
    row = _cache.get((agent_id, tier))
    if row is None or row["n"] < min_samples:
        return None
    return row["accept_pct"]


def reset_for_tests() -> None:
    global _initialized_for, _cache, _cache_at, _cache_days
    _initialized_for = None
    _cache = {}
    _cache_at = 0.0
    _cache_days = None
