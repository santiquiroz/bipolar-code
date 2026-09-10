"""API de la gestión inteligente: configuración, explicación de decisiones, salud, log y agentes."""
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.models.smart import TIER_ORDER, CliAgent, DelegationConfig, SmartRoutingConfig, default_tier_table
from app.services import budget_service, decisions_log, health_service, providers_service, smart_router
from app.services.cli_agents import registry as agents_registry
from app.services.cli_agents.adapters import AdapterUnsafe, validate_extra_args
from app.services.route_classifier import classify_request, classify_task

router = APIRouter(prefix="/smart", tags=["smart"])


class SmartConfigUpdate(BaseModel):
    smart: Optional[SmartRoutingConfig] = None
    cli_agents: Optional[list[CliAgent]] = None
    delegation: Optional[DelegationConfig] = None


class ClassifyRequest(BaseModel):
    task: Optional[str] = None
    body: Optional[dict] = None
    prompt_tokens: int = 0
    surface: str = "messages"


class ExplainRequest(BaseModel):
    body: dict
    prompt_tokens: int = 0
    surface: str = "messages"
    headers: dict = {}


def _validate_smart(smart: SmartRoutingConfig, provider_ids: set[str]) -> None:
    th = smart.thresholds
    values = [th.get("simple", 25), th.get("standard", 50), th.get("complex", 75)]
    if not (0 < values[0] < values[1] < values[2] <= 100):
        raise HTTPException(400, "thresholds deben ser crecientes: simple < standard < complex <= 100")
    for policy in smart.tiers:
        if policy.tier not in TIER_ORDER:
            raise HTTPException(400, f"tier desconocido: {policy.tier}")
        unknown = [t.provider_id for t in policy.targets if t.provider_id not in provider_ids]
        if unknown:
            raise HTTPException(400, f"Providers no registrados en tier {policy.tier}: {unknown}")


def _validate_agents(agents: list[CliAgent]) -> None:
    for agent in agents:
        try:
            validate_extra_args(agent.extra_args)
        except AdapterUnsafe as e:
            raise HTTPException(400, f"{agent.id}: {e}")
        if not (60 <= agent.timeout_s <= 3600):
            raise HTTPException(400, f"{agent.id}: timeout_s fuera de 60..3600")
        if agent.exe_path and not Path(agent.exe_path).exists():
            raise HTTPException(400, f"{agent.id}: exe_path no existe")


def _validate_delegation(delegation: DelegationConfig) -> list[str]:
    warnings = []
    for entry in delegation.workspace_allowlist:
        path = Path(entry)
        if not path.is_absolute():
            raise HTTPException(400, f"workspace no absoluto: {entry}")
        if not path.exists():
            warnings.append(f"workspace no existe todavía: {entry}")
    for tier, ids in delegation.tier_order.items():
        if tier not in TIER_ORDER:
            raise HTTPException(400, f"tier desconocido en tier_order: {tier}")
    return warnings


async def _agent_statuses(registry, refresh: bool = False) -> list[dict]:
    results = await asyncio.gather(*(agents_registry.probe(a, force=refresh) for a in registry.cli_agents), return_exceptions=True)
    return [r.model_dump() for r in results if not isinstance(r, Exception)]


def _recommendations(registry, statuses: list[dict]) -> list[dict]:
    recs = []
    ollama = next((p for p in registry.providers if p.id == "ollama"), None)
    ollama_status = next((s for s in statuses if s["id"] == "ollama"), None)
    if ollama and not ollama.anthropic_native and ollama_status and ollama_status.get("auth") == "ok":
        recs.append({
            "code": "ollama_anthropic_native",
            "message": "Ollama responde en local: desde 0.33 habla Anthropic Messages nativo; activarlo evita la traducción OpenAI.",
            "patch": {"provider_id": "ollama", "updates": {"anthropic_native": True}},
        })
    if registry.smart.enabled and registry.smart.mode == "shadow":
        recs.append({"code": "shadow_mode", "message": "Routing inteligente en modo shadow: revisa el log de decisiones y pásalo a activo cuando el acuerdo te convenza."})
    return recs


@router.get("/config")
async def get_config(refresh: bool = False):
    registry = providers_service.load_registry()
    statuses = await _agent_statuses(registry, refresh)
    return {
        "smart": registry.smart.model_dump(),
        "cli_agents": [a.model_dump() for a in registry.cli_agents],
        "delegation": registry.delegation.model_dump(),
        "agents": statuses,
        "health": health_service.snapshot(),
        "budgets": await budget_service.usage_for(registry.smart.budgets),
        "recommendations": _recommendations(registry, statuses),
        "tiers": list(TIER_ORDER),
    }


@router.put("/config")
async def put_config(update: SmartConfigUpdate):
    registry = providers_service.load_registry()
    provider_ids = {p.id for p in registry.providers}
    warnings: list[str] = []
    if update.smart is not None:
        _validate_smart(update.smart, provider_ids)
    if update.cli_agents is not None:
        _validate_agents(update.cli_agents)
    if update.delegation is not None:
        warnings = _validate_delegation(update.delegation)
    registry = providers_service.update_smart_config(update.smart, update.cli_agents, update.delegation)
    if update.cli_agents is not None:
        agents_registry.invalidate()
    return {
        "smart": registry.smart.model_dump(),
        "cli_agents": [a.model_dump() for a in registry.cli_agents],
        "delegation": registry.delegation.model_dump(),
        "warnings": warnings,
    }


@router.post("/presets/default")
async def preset_default(save: bool = False):
    registry = providers_service.load_registry()
    tiers = default_tier_table({p.id for p in registry.providers})
    if save:
        smart = registry.smart.model_copy(update={"tiers": tiers})
        providers_service.update_smart_config(smart=smart)
    return {"tiers": [t.model_dump() for t in tiers], "saved": save}


@router.post("/classify")
async def classify(req: ClassifyRequest):
    registry = providers_service.load_registry()
    if req.body is not None:
        cls = classify_request(req.body, req.prompt_tokens, registry.smart.thresholds, req.surface)
    elif req.task:
        cls = classify_task(req.task, thresholds=registry.smart.thresholds)
    else:
        raise HTTPException(400, "Se requiere task o body")
    return cls.to_dict()


@router.post("/explain")
async def explain(req: ExplainRequest):
    model = str(req.body.get("model", ""))
    decision = await smart_router.decide(req.body, model, req.prompt_tokens, req.headers, surface=req.surface, dry_run=True)
    return decision.to_dict()


@router.get("/health")
async def get_health():
    return {"targets": health_service.snapshot()}


@router.post("/health/reset")
async def reset_health(target: Optional[str] = None):
    cleared = health_service.reset(target)
    agents_registry.invalidate()
    return {"reset": cleared}


@router.get("/decisions")
async def list_decisions(limit: int = Query(100, ge=1, le=1000), tier: Optional[str] = None,
                         target: Optional[str] = None, source: Optional[str] = None, since: Optional[str] = None):
    return {"decisions": await decisions_log.list_decisions(limit, tier, target, source, since)}


@router.get("/decisions/summary")
async def decisions_summary(period: str = "day"):
    return await decisions_log.summary(period)


@router.get("/agents")
async def list_agents(refresh: bool = False):
    registry = providers_service.load_registry()
    return {"agents": await _agent_statuses(registry, refresh)}


@router.post("/agents/{agent_id}/probe")
async def probe_agent(agent_id: str):
    registry = providers_service.load_registry()
    agent = next((a for a in registry.cli_agents if a.id == agent_id), None)
    if agent is None:
        raise HTTPException(404, f"Agente '{agent_id}' no encontrado")
    status = await agents_registry.probe(agent, force=True)
    return status.model_dump()
