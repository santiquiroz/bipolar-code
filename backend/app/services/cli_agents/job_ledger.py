from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

from app.core.logging import get_logger
from app.models.delegate import Job
from app.models.smart import CliAgent
from app.services import budget_service, usage_tracker

log = get_logger(__name__)

POOL_LABELS = {
    "anthropic_account": "Suscripción Claude",
    "anthropic_api": "API Anthropic (pago)",
    "subscription": "Suscripciones externas",
    "prepaid": "Prepago por token",
    "free": "Gratis o local",
}
_POOL_BY_BASE = {
    "claude": "anthropic_account",
    "anthropic": "anthropic_api",
    "openrouter": "free",
    "ollama": "free",
    "deepseek": "prepaid",
}
_ANTHROPIC_POOLS = ("anthropic_account", "anthropic_api")

_initialized_for: Optional[str] = None

_CREATE = """
CREATE TABLE IF NOT EXISTS delegate_jobs (
    id TEXT PRIMARY KEY,
    finished_at TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    escalations INTEGER NOT NULL DEFAULT 0,
    duration_s REAL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL
)
"""
_INDEX = "CREATE INDEX IF NOT EXISTS idx_delegate_jobs_finished_at ON delegate_jobs(finished_at)"


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


def _duration_s(created_at: str, finished_at: str) -> Optional[float]:
    try:
        delta = datetime.fromisoformat(finished_at) - datetime.fromisoformat(created_at)
        return round(delta.total_seconds(), 3)
    except Exception:
        return None  # fechas ausentes o mezcla naive/aware


async def record_job(job: Job) -> None:
    try:
        await _ensure()
        finished_at = job.finished_at or datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(usage_tracker._db_path())) as db:
            await db.execute(
                "INSERT OR REPLACE INTO delegate_jobs (id, finished_at, agent_id, tier, status, attempts, "
                "escalations, duration_s, tokens_in, tokens_out, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job.id, finished_at, job.agent_id or "none", job.tier, job.status, len(job.attempts),
                 job.escalations, _duration_s(job.created_at, finished_at), job.tokens_in, job.tokens_out,
                 job.cost_usd),
            )
            await db.commit()
    except Exception as e:
        log.warning("delegate_job_record_failed", job=job.id, error=str(e))


def pool_of(agent: CliAgent) -> str:
    return _POOL_BY_BASE.get(agent.base, "subscription")


def _pct(part: float, total: float) -> Optional[float]:
    return round(100 * part / total, 1) if total else None


async def _job_statuses(db: aiosqlite.Connection, since: str) -> dict[str, dict[str, int]]:
    cur = await db.execute(
        "SELECT agent_id, status, COUNT(*) FROM delegate_jobs WHERE finished_at >= ? GROUP BY agent_id, status",
        (since,),
    )
    result: dict[str, dict[str, int]] = {}
    for agent_id, status, n in await cur.fetchall():
        result.setdefault(agent_id, {})[status] = n
    return result


async def _usage_by_provider(db: aiosqlite.Connection, since: str) -> dict[str, dict]:
    cur = await db.execute(
        "SELECT provider_id, model, COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), "
        "SUM(cost_usd), COUNT(cost_usd) FROM requests WHERE timestamp >= ? GROUP BY provider_id, model",
        (since,),
    )
    result: dict[str, dict] = {}
    for provider_id, model, tin, tout, cost, priced in await cur.fetchall():
        item = result.setdefault(provider_id, {"tokens_in": 0, "tokens_out": 0, "cost": 0.0, "priced": 0, "models": []})
        item["tokens_in"] += int(tin)
        item["tokens_out"] += int(tout)
        item["cost"] += float(cost or 0.0)
        item["priced"] += int(priced)
        if model and model not in item["models"]:
            item["models"].append(model)
    return result


async def _accept_by_agent(db: aiosqlite.Connection, since: str) -> dict[str, tuple[int, int]]:
    cur = await db.execute(
        "SELECT agent_id, COUNT(*), SUM(CASE WHEN result IN ('first_pass', 'revised') THEN 1 ELSE 0 END) "
        "FROM delegate_outcomes WHERE timestamp >= ? GROUP BY agent_id",
        (since,),
    )
    return {agent_id: (int(n), int(ok or 0)) for agent_id, n, ok in await cur.fetchall()}


async def _read_window(since: str) -> tuple[dict, dict, dict]:
    from app.services.cli_agents import outcome_stats
    await _ensure()
    await outcome_stats._ensure()
    await usage_tracker.init_db()  # instalación nueva: aún no existe `requests`
    async with aiosqlite.connect(str(usage_tracker._db_path())) as db:
        return await _job_statuses(db, since), await _usage_by_provider(db, since), await _accept_by_agent(db, since)


def _agent_row(agent: CliAgent, statuses: dict[str, int], usage: Optional[dict], accept: tuple[int, int]) -> dict:
    jobs = sum(statuses.values())
    succeeded = statuses.get("succeeded", 0)
    usage = usage or {}
    cost = round(usage["cost"], 4) if usage.get("priced") else None
    return {
        "agent_id": agent.id, "name": agent.name or agent.id, "pool": pool_of(agent),
        "jobs": jobs, "succeeded": succeeded, "success_pct": _pct(succeeded, jobs),
        "statuses": dict(statuses), "accept_pct": _pct(accept[1], accept[0]),
        "cost_usd": cost, "tokens_in": usage.get("tokens_in", 0), "tokens_out": usage.get("tokens_out", 0),
        "cost_per_success": round(cost / succeeded, 4) if cost is not None and succeeded else None,
        "models": usage.get("models", []), "_accept_n": accept[0],
    }


def _totals(rows: list[dict]) -> dict:
    jobs = sum(r["jobs"] for r in rows)
    succeeded = sum(r["succeeded"] for r in rows)
    anthropic = sum(r["succeeded"] for r in rows if r["pool"] in _ANTHROPIC_POOLS)
    return {
        "jobs": jobs, "succeeded": succeeded, "success_pct": _pct(succeeded, jobs),
        "cost_usd": round(sum(r["cost_usd"] or 0.0 for r in rows), 4),
        "tokens_in": sum(r["tokens_in"] for r in rows), "tokens_out": sum(r["tokens_out"] for r in rows),
        "anthropic_share_pct": _pct(anthropic, succeeded),
    }


def _pools(rows: list[dict]) -> list[dict]:
    return [
        {"pool": pool, "label": label,
         "jobs": sum(r["jobs"] for r in rows if r["pool"] == pool),
         "succeeded": sum(r["succeeded"] for r in rows if r["pool"] == pool),
         "cost_usd": round(sum(r["cost_usd"] or 0.0 for r in rows if r["pool"] == pool), 4)}
        for pool, label in POOL_LABELS.items()
    ]


async def _month_spend(agents: list[CliAgent]) -> dict[str, float]:
    since = budget_service.window_start("month").isoformat()
    spend = {}
    for agent in agents:
        if agent.max_monthly_usd > 0:
            found = await budget_service.spend_since(budget_service.provider_id_of(agent.key), since)
            spend[agent.id] = found["cost_usd"]
    return spend


async def efficiency(days: int, agents: list[CliAgent]) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    job_map, usage_map, accept_map = await _read_window(since)
    rows = []
    for agent in agents:
        statuses = job_map.get(agent.id, {})
        usage = usage_map.get(budget_service.provider_id_of(agent.key))
        if statuses or usage:
            rows.append(_agent_row(agent, statuses, usage, accept_map.get(agent.id, (0, 0))))
    rows.sort(key=lambda r: r["jobs"], reverse=True)
    totals = _totals(rows)
    warnings = efficiency_warnings(rows, agents, totals, await _month_spend(agents))
    for row in rows:
        row.pop("_accept_n")
    return {"days": days, "since": since, "totals": totals, "pools": _pools(rows), "agents": rows,
            "warnings": warnings}


def _warn(level: str, code: str, message: str, agent_id: Optional[str] = None) -> dict:
    return {"level": level, "code": code, "message": message, "agent_id": agent_id}


def _same_account_opus(agents: list[CliAgent]) -> list[dict]:
    msg = ("Claude Code delega con Opus en tu misma cuenta: cada job gasta el cupo de tu sesión al precio "
           "más alto. Configura modelos por tier (haiku / sonnet / opus).")
    return [
        _warn("high", "same_account_opus", msg, a.id) for a in agents
        if a.enabled and a.base == "claude" and not a.account_dir
        and (not a.model_for("standard") or "opus" in a.model_for("standard").lower())
    ]


def _anthropic_share(totals: dict) -> list[dict]:
    pct = totals.get("anthropic_share_pct")
    if pct is None or pct < 50 or totals.get("succeeded", 0) < 10:
        return []
    return [_warn("medium", "anthropic_share_high",
                  f"El {pct:g} % de los jobs exitosos los hizo Claude (tu cuenta o la API): la delegación casi "
                  "no está ahorrando cupo de Anthropic. Revisa por qué fallan los carriles externos.")]


def _never_succeeds(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        if row["jobs"] < 5 or row["succeeded"] != 0:
            continue
        ordered = sorted(row["statuses"].items(), key=lambda kv: kv[1], reverse=True)
        detail = ", ".join(f"{status} {n}" for status, n in ordered)
        result.append(_warn("medium", "lane_never_succeeds",
                            f"{row['name']}: 0 de {row['jobs']} jobs terminaron bien ({detail}). "
                            "Revisa login o saldo, o sácalo del orden.", row["agent_id"]))
    return result


def _monthly_budget(agents: list[CliAgent], month_spend: dict[str, float]) -> list[dict]:
    result = []
    for agent in agents:
        cap = agent.max_monthly_usd
        spent = month_spend.get(agent.id, 0.0)
        if cap <= 0 or spent < 0.5 * cap:
            continue
        level = "medium" if spent >= 0.8 * cap else "info"
        result.append(_warn(level, "monthly_budget",
                            f"{agent.name or agent.id} lleva US${spent:.2f} de US${cap:.0f} este mes.", agent.id))
    return result


def _low_acceptance(rows: list[dict]) -> list[dict]:
    return [
        _warn("info", "low_acceptance",
              f"{r['name']}: el revisor aprueba solo el {r['accept_pct']:g} % de sus entregas.", r["agent_id"])
        for r in rows
        if r.get("accept_pct") is not None and r["accept_pct"] < 50 and r.get("_accept_n", 0) >= 5
    ]


def efficiency_warnings(rows: list[dict], agents: list[CliAgent], totals: dict,
                        month_spend: dict[str, float]) -> list[dict]:
    return (_same_account_opus(agents) + _anthropic_share(totals) + _never_succeeds(rows)
            + _monthly_budget(agents, month_spend) + _low_acceptance(rows))


def reset_for_tests() -> None:
    global _initialized_for
    _initialized_for = None
