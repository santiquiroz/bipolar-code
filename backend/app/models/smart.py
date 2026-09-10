"""
Modelos de la gestión inteligente: routing por complejidad (tiers, presupuestos)
y agentes CLI delegables. Todo con defaults neutros: un providers.json viejo carga
sin cambios y `smart.enabled=False` reproduce el comportamiento previo.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

Tier = Literal["trivial", "simple", "standard", "complex"]
TIER_ORDER: tuple[str, ...] = ("trivial", "simple", "standard", "complex")

AgentId = Literal["claude", "codex", "copilot", "antigravity", "ollama"]
AGENT_IDS: tuple[str, ...] = ("claude", "codex", "copilot", "antigravity", "ollama")

TargetState = Literal["available", "cooling", "exhausted", "unavailable"]
QuotaReset = Literal["none", "5h", "daily", "weekly"]


def tier_index(tier: str) -> int:
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else -1


class RouteTarget(BaseModel):
    provider_id: str
    model: str = ""  # "" = active_model del provider

    @property
    def key(self) -> str:
        return f"provider:{self.provider_id}"


class TierPolicy(BaseModel):
    tier: Tier
    targets: list[RouteTarget] = Field(default_factory=list)  # orden = preferencia


class BudgetWindow(BaseModel):
    target_key: str  # "provider:<id>" | "cli:<id>"
    window: Literal["day", "week", "month"] = "day"
    max_requests: int = 0  # 0 = sin límite
    max_cost_usd: float = 0.0


class SmartRoutingConfig(BaseModel):
    enabled: bool = False
    mode: Literal["shadow", "active"] = "shadow"
    thresholds: dict[str, int] = Field(default_factory=lambda: {"simple": 25, "standard": 50, "complex": 75})
    tiers: list[TierPolicy] = Field(default_factory=list)
    budgets: list[BudgetWindow] = Field(default_factory=list)
    honor_tier_header: bool = True
    sticky_tool_loops: bool = True
    sticky_ttl_seconds: int = 1800
    skip_cooling_providers: bool = True
    respect_capabilities: bool = True

    def policy_for(self, tier: str) -> Optional[TierPolicy]:
        return next((p for p in self.tiers if p.tier == tier), None)


class CliAgent(BaseModel):
    id: AgentId
    name: str = ""
    enabled: bool = False
    exe_path: str = ""
    default_model: str = ""
    model_by_tier: dict[str, str] = Field(default_factory=dict)
    alt_model_on_quota: str = ""
    supported_tiers: list[str] = Field(default_factory=lambda: ["simple", "standard", "complex"])
    agentic: bool = True
    priority: int = 50
    cost_weight: float = 1.0
    max_concurrency: int = 1
    timeout_s: int = 600
    cooldown_s: int = 900
    quota_reset: QuotaReset = "none"
    max_credits: int = 0
    extra_args: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"cli:{self.id}"

    def model_for(self, tier: str, requested: str = "") -> str:
        return requested or self.model_by_tier.get(tier, "") or self.default_model


class DelegationConfig(BaseModel):
    enabled: bool = False
    workspace_allowlist: list[str] = Field(default_factory=list)
    tier_order: dict[str, list[str]] = Field(default_factory=lambda: {
        "trivial": ["ollama", "copilot", "antigravity", "claude"],
        "simple": ["copilot", "antigravity", "codex", "claude"],
        "standard": ["codex", "claude", "antigravity", "copilot"],
        "complex": ["codex", "claude", "antigravity"],
    })
    max_parallel_jobs: int = 3
    max_attempts: int = 3
    job_retention: int = 200


DEFAULT_CLI_AGENTS: list[dict] = [
    {
        "id": "claude", "name": "Claude Code", "supported_tiers": ["simple", "standard", "complex"],
        "quota_reset": "5h", "cost_weight": 1.0, "priority": 40,
    },
    {
        "id": "codex", "name": "OpenAI Codex CLI", "supported_tiers": ["simple", "standard", "complex"],
        "quota_reset": "5h", "cost_weight": 1.0, "priority": 30, "timeout_s": 900,
    },
    {
        "id": "copilot", "name": "GitHub Copilot CLI", "supported_tiers": ["trivial", "simple", "standard"],
        "quota_reset": "weekly", "cost_weight": 0.5, "priority": 50, "max_credits": 10,
    },
    {
        "id": "antigravity", "name": "Google Antigravity CLI (agy)",
        "supported_tiers": ["trivial", "simple", "standard", "complex"],
        "default_model": "gemini-3.1-pro-high",
        "model_by_tier": {"trivial": "gemini-3.8-flash-low", "simple": "gemini-3.8-flash-low",
                          "standard": "gemini-3.1-pro-high", "complex": "gemini-3.1-pro-high"},
        "alt_model_on_quota": "claude-sonnet-4-6",
        "quota_reset": "weekly", "cost_weight": 1.0, "priority": 45,
    },
    {
        "id": "ollama", "name": "Ollama (solo texto)", "supported_tiers": ["trivial"],
        "agentic": False, "cost_weight": 0.0, "priority": 10, "timeout_s": 900,
    },
]


def default_tier_table(provider_ids: set[str]) -> list[TierPolicy]:
    """Tabla inicial que solo referencia providers registrados. Local y barato primero,
    frontier al final; el tier complex prefiere frontier."""
    plan = {
        "trivial": ["ollama", "llamacpp", "lmstudio", "copilot"],
        "simple": ["llamacpp", "ollama", "copilot", "nvidia_nim"],
        "standard": ["copilot", "llamacpp", "deepseek", "anthropic"],
        "complex": ["anthropic", "copilot", "llamacpp"],
    }
    return [
        TierPolicy(tier=tier, targets=[RouteTarget(provider_id=p) for p in ids if p in provider_ids])
        for tier, ids in plan.items()
    ]
