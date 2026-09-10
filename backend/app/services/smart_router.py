"""
Router inteligente: clasifica el request, consulta la tabla tier → destinos y filtra
por capacidad, salud/cuota, presupuesto y superficie. Las reglas explícitas ganan.
En modo shadow solo registra qué habría elegido; en modo active decide.
"""
import asyncio
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping, Optional

from app.core.logging import get_logger
from app.core.quota_signals import detect_signal
from app.models.provider import Provider, ProviderRegistry
from app.models.smart import TIER_ORDER, RouteTarget, tier_index
from app.services import budget_service, decisions_log, health_service, providers_service
from app.services.route_classifier import (
    Classification,
    classify_request,
    conversation_key,
    with_header_override,
)

log = get_logger(__name__)

_background_tasks: set[asyncio.Task] = set()
_sticky: "OrderedDict[str, tuple[str, str, float]]" = OrderedDict()
STICKY_MAX = 512
HEADER_MAX = 512
_HEADER_SAFE_RE = re.compile(r"[^\x20-\x7e]")


@dataclass
class Candidate:
    target: RouteTarget
    provider: Provider
    model: str

    @property
    def key(self) -> str:
        return f"provider:{self.provider.id}"


@dataclass
class RouteDecision:
    decision_id: str
    surface: str
    tier: str
    score: int
    intent: str
    requested_model: str
    prompt_tokens: int
    chosen_provider: Optional[Provider]
    chosen_model: Optional[str]
    is_active: bool
    source: str
    mode: str
    would_key: str = ""
    would_model: str = ""
    rejected: list[tuple[str, str]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    decision_ms: float = 0.0

    @property
    def chosen_key(self) -> str:
        return f"provider:{self.chosen_provider.id}" if self.chosen_provider else ""

    def as_pick(self) -> tuple[Optional[Provider], Optional[str], bool]:
        return self.chosen_provider, self.chosen_model, self.is_active

    def to_header(self) -> str:
        parts = [
            f"tier={self.tier}", f"score={self.score}", f"intent={self.intent}",
            f"target={self.chosen_key or 'none'}", f"model={self.chosen_model or ''}",
            f"src={self.source}", f"mode={self.mode}", f"id={self.decision_id}",
        ]
        if self.would_key:
            parts.append(f"shadow_target={self.would_key}")
        if self.rejected:
            parts.append("rejected=" + ",".join(f"{k}:{r}" for k, r in self.rejected[:6]))
        return _HEADER_SAFE_RE.sub("?", ";".join(parts))[:HEADER_MAX]

    def to_row(self) -> dict:
        return {
            "id": self.decision_id, "timestamp": datetime.now(timezone.utc).isoformat(),
            "surface": self.surface, "tier": self.tier, "score": self.score, "intent": self.intent,
            "requested_model": self.requested_model, "prompt_tokens": self.prompt_tokens,
            "chosen_key": self.chosen_key, "chosen_model": self.chosen_model or "",
            "would_key": self.would_key, "would_model": self.would_model, "source": self.source,
            "mode": self.mode, "reasons": list(self.reasons), "rejected": [list(r) for r in self.rejected],
            "decision_ms": round(self.decision_ms, 3),
        }

    def to_dict(self) -> dict:
        row = self.to_row()
        row.pop("timestamp", None)
        row["is_active"] = self.is_active
        return row


# ── candidatos ───────────────────────────────────────────────────────────────

def _tier_policy_targets(registry: ProviderRegistry, tier: str, reasons: list[str]) -> list[RouteTarget]:
    policy = registry.smart.policy_for(tier)
    if policy and policy.targets:
        return policy.targets
    idx = tier_index(tier)
    ordered = list(TIER_ORDER[idx + 1:]) + list(reversed(TIER_ORDER[:idx]))
    for other in ordered:
        alt = registry.smart.policy_for(other)
        if alt and alt.targets:
            reasons.append(f"tier_fallback:{tier}->{other}")
            return alt.targets
    return []


def candidates_for_tier(registry: ProviderRegistry, tier: str, reasons: list[str]) -> tuple[list[Candidate], list[tuple[str, str]]]:
    by_id = {p.id: p for p in registry.providers}
    cands, rejected = [], []
    for target in _tier_policy_targets(registry, tier, reasons):
        provider = by_id.get(target.provider_id)
        if provider is None:
            rejected.append((f"provider:{target.provider_id}", "target_unknown"))
            continue
        cands.append(Candidate(target=target, provider=provider, model=target.model or provider.active_model))
    return cands, rejected


async def _reject_reason(c: Candidate, cls: Classification, registry: ProviderRegistry, surface: str) -> Optional[str]:
    smart = registry.smart
    info = c.provider.model_info or {}
    s = cls.signals
    if smart.respect_capabilities:
        if s.n_tools > 0 and info.get("supports_tools") is False:
            return "capability_tools"
        if s.has_images and info.get("supports_vision") is False:
            return "capability_vision"
        ctx = int(info.get("context_window") or 0)
        if ctx > 0 and s.prompt_tokens > ctx * 0.9:
            return "context_too_small"
    health = health_service.get(c.key)
    if smart.skip_cooling_providers and health.state != "available":
        return f"{health.state}:{health.last_signal or 'failures'}"
    budget_reason = await budget_service.check_budget(smart.budgets, c.key)
    if budget_reason:
        return budget_reason
    if surface == "chat_completions" and c.provider.litellm_prefix == "anthropic" and c.provider.id != registry.active_provider_id:
        return "oai_surface_anthropic_not_active"
    if providers_service._is_local_base(c.provider.api_base) and not await providers_service._is_reachable(c.provider.api_base):
        return "unreachable"
    return None


async def filter_candidates(cands: list[Candidate], cls: Classification, registry: ProviderRegistry, surface: str) -> tuple[list[Candidate], list[tuple[str, str]]]:
    survivors, rejected = [], []
    for c in cands:
        reason = await _reject_reason(c, cls, registry, surface)
        if reason:
            rejected.append((c.key, reason))
        else:
            survivors.append(c)
    return survivors, rejected


def rank_candidates(cands: list[Candidate]) -> list[Candidate]:
    """Determinista: posición en la tabla, luego menos fallos consecutivos recientes."""
    indexed = sorted(
        enumerate(cands),
        key=lambda ic: (ic[0], health_service.get(ic[1].key).consecutive_failures),
    )
    return [c for _, c in indexed]


# ── stickiness ───────────────────────────────────────────────────────────────

def _sticky_get(key: str, ttl: int) -> Optional[tuple[str, str]]:
    hit = _sticky.get(key)
    if not hit:
        return None
    provider_id, model, ts = hit
    if time.monotonic() - ts > ttl:
        _sticky.pop(key, None)
        return None
    _sticky.move_to_end(key)
    return provider_id, model


def _sticky_put(key: str, provider_id: str, model: str) -> None:
    _sticky[key] = (provider_id, model, time.monotonic())
    _sticky.move_to_end(key)
    while len(_sticky) > STICKY_MAX:
        _sticky.popitem(last=False)


def clear_sticky() -> None:
    _sticky.clear()


# ── decisión ─────────────────────────────────────────────────────────────────

def _spawn(coro) -> None:
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:
        return
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _legacy_source(model_name: str, prompt_tokens: int) -> str:
    return "explicit_rule" if providers_service.resolve_route(model_name, prompt_tokens) else "active_default"


async def _smart_pick(
    registry: ProviderRegistry, cls: Classification, body: dict, surface: str, reasons: list[str]
) -> tuple[Optional[Candidate], list[tuple[str, str]], str]:
    smart = registry.smart
    if smart.sticky_tool_loops and cls.signals.has_tool_results:
        hit = _sticky_get(conversation_key(body), smart.sticky_ttl_seconds)
        if hit:
            provider = next((p for p in registry.providers if p.id == hit[0]), None)
            if provider:
                sticky = Candidate(target=RouteTarget(provider_id=provider.id, model=hit[1]), provider=provider, model=hit[1])
                if await _reject_reason(sticky, cls, registry, surface) is None:
                    reasons.append("sticky_conversation")
                    return sticky, [], "sticky"
                reasons.append("sticky_broken")
    cands, rejected = candidates_for_tier(registry, cls.tier, reasons)
    survivors, more_rejected = await filter_candidates(cands, cls, registry, surface)
    rejected.extend(more_rejected)
    ranked = rank_candidates(survivors)
    if not ranked:
        reasons.append("smart_no_candidate")
        return None, rejected, "failover"
    return ranked[0], rejected, "smart"


async def decide(
    body: dict,
    model_name: str,
    prompt_tokens: int,
    headers: Optional[Mapping[str, str]] = None,
    *,
    surface: str = "messages",
    dry_run: bool = False,
) -> RouteDecision:
    t0 = time.perf_counter()
    headers = headers or {}
    registry = providers_service.load_registry()
    smart = registry.smart
    legacy_provider, legacy_model, legacy_active = await providers_service.pick_provider(model_name, prompt_tokens)
    legacy_key = f"provider:{legacy_provider.id}" if legacy_provider else ""
    legacy_source = _legacy_source(model_name, prompt_tokens)

    cls = classify_request(body, prompt_tokens, smart.thresholds, surface)
    if smart.honor_tier_header:
        cls = with_header_override(cls, headers.get("x-bipolar-tier") or headers.get("X-Bipolar-Tier"))
    reasons = list(cls.reasons)
    decision = RouteDecision(
        decision_id=uuid.uuid4().hex[:12], surface=surface, tier=cls.tier, score=cls.score, intent=cls.intent,
        requested_model=model_name, prompt_tokens=prompt_tokens,
        chosen_provider=legacy_provider, chosen_model=legacy_model, is_active=legacy_active,
        source=legacy_source, mode="off", reasons=reasons,
    )

    if not smart.enabled:
        decision.decision_ms = (time.perf_counter() - t0) * 1000
        return decision

    decision.mode = smart.mode
    pick, rejected, pick_source = await _smart_pick(registry, cls, body, surface, reasons)
    decision.rejected = rejected

    explicit_rule = providers_service.resolve_route(model_name, prompt_tokens, tier=cls.tier)
    if explicit_rule:
        decision.source = "explicit_rule"
        decision.would_key, decision.would_model = (pick.key, pick.model) if pick else ("", "")
    elif smart.mode == "shadow" or pick is None:
        decision.source = "failover" if (pick is None and smart.mode == "active") else legacy_source
        decision.would_key, decision.would_model = (pick.key, pick.model) if pick else ("", "")
    else:
        decision.chosen_provider = pick.provider
        decision.chosen_model = pick.model or None
        decision.is_active = pick.provider.id == registry.active_provider_id
        decision.source = pick_source
        decision.would_key, decision.would_model = legacy_key, legacy_model or ""

    decision.decision_ms = (time.perf_counter() - t0) * 1000
    if not dry_run:
        if smart.sticky_tool_loops and decision.chosen_provider and smart.mode == "active":
            _sticky_put(conversation_key(body), decision.chosen_provider.id, decision.chosen_model or "")
        _spawn(decisions_log.insert(decision.to_row()))
        log.info("route_decision", **{k: v for k, v in decision.to_row().items() if k not in ("reasons", "rejected")})
    return decision


# ── resultado ────────────────────────────────────────────────────────────────

async def report_outcome(
    decision: Optional[RouteDecision],
    target_key: str,
    ok: bool,
    latency_ms: Optional[float] = None,
    status: Optional[int] = None,
    error: str = "",
) -> None:
    if target_key:
        if ok:
            health_service.mark_success(target_key, latency_ms)
        else:
            signal = detect_signal(error or "", status)
            if signal:
                health_service.mark_signal(target_key, signal)
            else:
                health_service.mark_failure(target_key, error or f"status {status}")
    if decision is not None:
        _spawn(decisions_log.update_outcome(decision.decision_id, "ok" if ok else "error", latency_ms, error))


def report_outcome_sync(decision: Optional[RouteDecision], target_key: str, ok: bool, **kwargs) -> None:
    _spawn(report_outcome(decision, target_key, ok, **kwargs))
