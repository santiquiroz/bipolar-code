"""
Log de decisiones de routing en usage.db (tabla route_decisions): qué se eligió,
qué habría elegido el modo shadow y cómo terminó.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

from app.core.logging import get_logger
from app.services import usage_tracker

log = get_logger(__name__)

_initialized_for: Optional[str] = None

_CREATE = """
CREATE TABLE IF NOT EXISTS route_decisions (
    id                 TEXT PRIMARY KEY,
    timestamp          TEXT NOT NULL,
    surface            TEXT,
    tier               TEXT,
    score              INTEGER,
    intent             TEXT,
    requested_model    TEXT,
    prompt_tokens      INTEGER,
    chosen_key         TEXT,
    chosen_model       TEXT,
    would_key          TEXT,
    would_model        TEXT,
    source             TEXT,
    mode               TEXT,
    reasons            TEXT,
    rejected           TEXT,
    decision_ms        REAL,
    outcome            TEXT,
    outcome_latency_ms REAL,
    outcome_error      TEXT
)
"""
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_route_decisions_ts ON route_decisions(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_requests_provider_ts ON requests(provider_id, timestamp)",
)


async def init_tables() -> None:
    global _initialized_for
    path = str(usage_tracker._db_path())
    async with aiosqlite.connect(path) as db:
        await db.execute(_CREATE)
        for stmt in _INDEXES:
            try:
                await db.execute(stmt)
            except Exception as e:  # requests puede no existir aún en tests aislados
                log.debug("decisions_index_skipped", error=str(e))
        await db.commit()
    _initialized_for = path


async def _ensure() -> None:
    if _initialized_for != str(usage_tracker._db_path()):
        await init_tables()


async def insert(row: dict) -> None:
    await _ensure()
    async with aiosqlite.connect(str(usage_tracker._db_path())) as db:
        await db.execute(
            "INSERT OR REPLACE INTO route_decisions (id, timestamp, surface, tier, score, intent, requested_model, "
            "prompt_tokens, chosen_key, chosen_model, would_key, would_model, source, mode, reasons, rejected, "
            "decision_ms, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
            (
                row["id"], row.get("timestamp") or datetime.now(timezone.utc).isoformat(), row.get("surface"),
                row.get("tier"), row.get("score"), row.get("intent"), row.get("requested_model"),
                row.get("prompt_tokens"), row.get("chosen_key"), row.get("chosen_model"), row.get("would_key"),
                row.get("would_model"), row.get("source"), row.get("mode"),
                json.dumps(row.get("reasons") or []), json.dumps(row.get("rejected") or []),
                row.get("decision_ms"),
            ),
        )
        await db.commit()


async def update_outcome(decision_id: str, outcome: str, latency_ms: Optional[float] = None, error: str = "") -> None:
    await _ensure()
    async with aiosqlite.connect(str(usage_tracker._db_path())) as db:
        await db.execute(
            "UPDATE route_decisions SET outcome = ?, outcome_latency_ms = ?, outcome_error = ? WHERE id = ?",
            (outcome, latency_ms, (error or "")[:300], decision_id),
        )
        await db.commit()


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("reasons", "rejected"):
        try:
            d[key] = json.loads(d.get(key) or "[]")
        except ValueError:
            d[key] = []
    return d


async def list_decisions(
    limit: int = 100,
    tier: Optional[str] = None,
    target: Optional[str] = None,
    source: Optional[str] = None,
    since: Optional[str] = None,
) -> list[dict]:
    await _ensure()
    conditions, params = [], []
    for column, value in (("tier", tier), ("chosen_key", target), ("source", source)):
        if value:
            conditions.append(f"{column} = ?")
            params.append(value)
    if since:
        conditions.append("timestamp >= ?")
        params.append(since)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(max(1, min(1000, limit)))
    async with aiosqlite.connect(str(usage_tracker._db_path())) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM route_decisions {where} ORDER BY timestamp DESC LIMIT ?", params)
        return [_row_to_dict(r) for r in await cur.fetchall()]


_PERIOD_DAYS = {"day": 1, "week": 7, "month": 30}


async def summary(period: str = "day") -> dict:
    await _ensure()
    since = (datetime.now(timezone.utc) - timedelta(days=_PERIOD_DAYS.get(period, 1))).isoformat()
    async with aiosqlite.connect(str(usage_tracker._db_path())) as db:
        db.row_factory = aiosqlite.Row
        rows = [dict(r) for r in await (await db.execute(
            "SELECT tier, chosen_key, would_key, source, mode, outcome, decision_ms FROM route_decisions WHERE timestamp >= ?",
            (since,),
        )).fetchall()]
    by_tier: dict[str, int] = {}
    by_target: dict[str, int] = {}
    by_source: dict[str, int] = {}
    outcomes = {"ok": 0, "error": 0, "pending": 0}
    agree = compared = 0
    total_ms = 0.0
    for r in rows:
        by_tier[r["tier"] or "?"] = by_tier.get(r["tier"] or "?", 0) + 1
        by_target[r["chosen_key"] or "?"] = by_target.get(r["chosen_key"] or "?", 0) + 1
        by_source[r["source"] or "?"] = by_source.get(r["source"] or "?", 0) + 1
        outcomes[r["outcome"] if r["outcome"] in outcomes else "pending"] += 1
        if r["would_key"]:
            compared += 1
            agree += int(r["would_key"] == r["chosen_key"])
        total_ms += r["decision_ms"] or 0.0
    return {
        "period": period,
        "count": len(rows),
        "by_tier": by_tier,
        "by_target": by_target,
        "by_source": by_source,
        "outcomes": outcomes,
        "agreement_rate": round(agree / compared, 3) if compared else None,
        "avg_decision_ms": round(total_ms / len(rows), 2) if rows else 0.0,
    }


async def purge_older_than(days: int) -> int:
    await _ensure()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(str(usage_tracker._db_path())) as db:
        cur = await db.execute("DELETE FROM route_decisions WHERE timestamp < ?", (cutoff,))
        await db.commit()
        return cur.rowcount or 0
