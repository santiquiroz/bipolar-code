"""Política de gasto del broker: modelos por tier, revisor barato, semáforos vivos, carriles medidos y BLOCKED de entorno."""
import pytest

from app.models.delegate import AgentStatus, JobRequest
from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent
from app.services import providers_service
from app.services.cli_agents import broker
from app.services.cli_agents import registry as agents_registry
from tests.test_broker_quality_gate import _ok, _script
from tests.test_delegation_broker import _agents, _wait, env  # noqa: F401  (fixture)

TIER_MODELS = {"trivial": "haiku", "simple": "sonnet", "standard": "sonnet", "complex": "opus"}


# ── 1. modelos por tier de Claude Code ───────────────────────────────────────

def _claude_defaults() -> dict:
    return next(d for d in DEFAULT_CLI_AGENTS if d["id"] == "claude")


def test_claude_sembrado_trae_mapa_y_concurrencia_2():
    agent = CliAgent(**_claude_defaults())
    assert agent.model_by_tier == TIER_MODELS and agent.max_concurrency == 2


def _registry_with(env, *agents: CliAgent):
    env["registry"].cli_agents = list(agents)
    return env["registry"]


def test_migracion_llena_claude_sin_modelos(env):
    registry = _registry_with(env, CliAgent(id="claude", enabled=True))
    assert providers_service._seed_claude_tier_models(registry) == ["cli:claude.model_by_tier"]
    assert registry.cli_agents[0].model_by_tier == TIER_MODELS


def test_migracion_no_toca_claude_con_modelo(env):
    fixed = CliAgent(id="claude", default_model="opus")
    own = CliAgent(id="claude-2", model_by_tier={"standard": "sonnet"})
    registry = _registry_with(env, fixed, own)
    assert providers_service._seed_claude_tier_models(registry) == []
    assert fixed.model_by_tier == {} and own.model_by_tier == {"standard": "sonnet"}


# ── 2. revisor barato ────────────────────────────────────────────────────────

def test_review_model_por_base():
    agents = {a.id: a for a in _agents("anthropic", "claude", "codex", codex={"default_model": "gpt-x"})}
    assert broker._review_model(agents["anthropic"], "standard") == "claude-sonnet-5-5"
    assert broker._review_model(agents["claude"], "standard") == "sonnet"
    assert broker._review_model(agents["codex"], "standard") == "gpt-x"


# ── 3. semáforos que siguen la config ────────────────────────────────────────

def test_sems_se_recrean_solo_si_cambia_el_limite(env):
    registry = env["registry"]
    glob1, agent1 = broker._sems(registry, "claude")
    assert broker._sems(registry, "claude") == (glob1, agent1)
    next(a for a in registry.cli_agents if a.id == "claude").max_concurrency = 3
    registry.delegation.max_parallel_jobs = 7
    glob2, agent2 = broker._sems(registry, "claude")
    assert agent2 is not agent1 and glob2 is not glob1
    assert agent2._value == 3 and glob2._value == 7
    assert broker._sems(registry, "claude") == (glob2, agent2)


# ── 4. no derramar a un carril pago ──────────────────────────────────────────

def _choose(env, *ids, preferred="", busy=("claude",), **overrides):
    registry = env["registry"]
    registry.cli_agents = _agents(*ids, claude={"max_concurrency": 1}, **overrides)
    registry.delegation.tier_order = {"standard": list(ids)}
    statuses = {a.id: AgentStatus(id=a.id, installed=True, quota={"sandbox": "ready"}) for a in registry.cli_agents}
    for aid in busy:
        agents_registry.adjust_running(aid, +1)
    try:
        return broker.choose_agent("standard", registry, statuses, preferred=preferred)
    finally:
        for aid in busy:
            agents_registry.adjust_running(aid, -1)


@pytest.mark.parametrize("metered", ["anthropic", "deepseek"])
def test_claude_ocupado_se_encola_en_vez_de_pagar(env, metered):
    agent, model, reasons, _ = _choose(env, "claude", metered)
    assert agent.id == "claude" and model == "sonnet"
    assert reasons == ["agent:claude:queued_busy:avoid_metered"]


def test_claude_ocupado_pasa_a_muse_si_esta_libre(env, monkeypatch):
    monkeypatch.setattr(broker.session, "interactive_session", lambda: True)
    agent, _, reasons, _ = _choose(env, "claude", "muse")
    assert agent.id == "muse" and reasons == ["agent:muse:standard"]


def test_agente_medido_preferido_se_respeta(env):
    agent, _, reasons, _ = _choose(env, "claude", "anthropic", preferred="anthropic")
    assert agent.id == "anthropic" and reasons == ["agent:anthropic:preferred"]


def test_is_metered_por_base_o_tope_mensual():
    assert broker._is_metered(CliAgent(id="anthropic")) and broker._is_metered(CliAgent(id="deepseek"))
    assert broker._is_metered(CliAgent(id="codex", max_monthly_usd=5))
    assert not broker._is_metered(CliAgent(id="codex"))


# ── 5. BLOCKED de entorno ────────────────────────────────────────────────────

def _blocked():
    return _ok("no arranca el sandbox\nSTATUS: BLOCKED: sandbox")


async def _run(env, monkeypatch, plan, touched=(), **request):
    failures = []
    monkeypatch.setattr(broker.health_service, "mark_failure", lambda *a, **kw: failures.append(a))
    monkeypatch.setattr(broker, "_files_touched", lambda *a, **kw: list(touched))
    calls = _script(monkeypatch, plan)
    req = JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", **request)
    return await _wait((await broker.submit(req)).id), calls, failures


@pytest.mark.asyncio
async def test_blocked_sin_cambios_escala_una_vez_y_marca_salud(env, monkeypatch):
    job, calls, failures = await _run(env, monkeypatch, [("codex", False, _blocked()), ("claude", False, _ok())])
    assert job.status == "succeeded" and job.escalations == 1
    assert [c[0] for c in calls] == ["codex", "claude"]
    assert failures == [("cli:codex", "worker_blocked")]
    events = [ev for ev in broker._jobs[job.id].lines if ev.get("event") == "escalated"]
    assert events == [{"event": "escalated", "from": "codex", "to": "claude", "reason": "blocked_no_changes"}]


@pytest.mark.asyncio
async def test_blocked_con_archivos_cambiados_corta(env, monkeypatch):
    job, calls, failures = await _run(env, monkeypatch, [("codex", False, _blocked())], touched=["a.py"])
    assert job.status == "blocked" and job.escalations == 0 and len(calls) == 1 and not failures


@pytest.mark.asyncio
async def test_segundo_blocked_en_el_mismo_job_corta(env, monkeypatch):
    job, calls, _ = await _run(env, monkeypatch, [("codex", False, _blocked()), ("claude", False, _blocked())])
    assert job.status == "blocked" and job.escalations == 1 and len(calls) == 2


@pytest.mark.asyncio
async def test_blocked_con_agente_fijado_corta(env, monkeypatch):
    job, calls, failures = await _run(env, monkeypatch, [("codex", False, _blocked())], agent_id="codex")
    assert job.status == "blocked" and job.escalations == 0 and len(calls) == 1 and not failures
