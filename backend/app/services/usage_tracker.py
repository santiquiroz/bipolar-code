import aiosqlite
from datetime import datetime, timezone
from pathlib import Path
import app.core.config as _cfg


def _db_path() -> Path:
    return Path(_cfg.get_settings().litellm_config_dir) / "usage.db"


async def init_db() -> None:
    async with aiosqlite.connect(str(_db_path())) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT    NOT NULL,
                provider_id   TEXT    NOT NULL,
                model         TEXT    NOT NULL,
                input_tokens  INTEGER,
                output_tokens INTEGER,
                cost_usd      REAL,
                truncated     INTEGER DEFAULT 0
            )
        """)
        await db.commit()


async def record(
    provider_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None,
    truncated: bool = False,
) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(str(_db_path())) as db:
        await db.execute(
            "INSERT INTO requests (timestamp, provider_id, model, input_tokens, output_tokens, cost_usd, truncated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, provider_id, model, input_tokens, output_tokens, cost_usd, int(truncated)),
        )
        await db.commit()


async def get_history(
    provider_id: str | None = None,
    model: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = 100,
) -> list[dict]:
    conditions, params = [], []
    if provider_id:
        conditions.append("provider_id = ?")
        params.append(provider_id)
    if model:
        conditions.append("model = ?")
        params.append(model)
    if from_ts:
        conditions.append("timestamp >= ?")
        params.append(from_ts)
    if to_ts:
        conditions.append("timestamp <= ?")
        params.append(to_ts)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    async with aiosqlite.connect(str(_db_path())) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM requests {where} ORDER BY timestamp DESC LIMIT ?", params
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_summary(period: str = "day") -> dict:
    period_expr = {
        "day": "strftime('%Y-%m-%d', timestamp)",
        "week": "strftime('%Y-W%W', timestamp)",
        "month": "strftime('%Y-%m', timestamp)",
    }.get(period, "strftime('%Y-%m-%d', timestamp)")

    async with aiosqlite.connect(str(_db_path())) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT provider_id, model, SUM(input_tokens) as ti, SUM(output_tokens) as to_, "
            "SUM(cost_usd) as tc, COUNT(*) as rc FROM requests GROUP BY provider_id, model"
        )
        by_provider: dict = {}
        for row in await cur.fetchall():
            d = dict(row)
            pid = d["provider_id"]
            by_provider.setdefault(pid, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            by_provider[pid]["requests"] += d["rc"]
            by_provider[pid]["input_tokens"] += d["ti"] or 0
            by_provider[pid]["output_tokens"] += d["to_"] or 0
            by_provider[pid]["cost_usd"] += d["tc"] or 0.0

        cur2 = await db.execute(
            f"SELECT {period_expr} as period, provider_id, SUM(cost_usd) as cost "
            "FROM requests GROUP BY period, provider_id ORDER BY period"
        )
        series = [dict(r) for r in await cur2.fetchall()]

    return {"by_provider": by_provider, "series": series}
