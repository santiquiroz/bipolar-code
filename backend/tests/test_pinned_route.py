from unittest.mock import AsyncMock

import pytest

from app.core.quota_signals import QuotaSignal
from app.models.provider import Provider, ProviderRegistry, RoutingRule
from app.services import health_service, providers_service, smart_router
from app.services.credentials import credential_slots, provider_key

BODY = {"model": "m1", "messages": [{"role": "user", "content": "hola"}]}


def _p(pid: str, auth: str = "", extras: list[str] | None = None, api_base: str | None = None) -> Provider:
    return Provider(id=pid, name=pid, api_base=api_base or f"https://{pid}.example.com/v1",
                    active_model=f"{pid}-model", auth_env_var=auth, extra_auth_env_vars=extras or [])


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app.core import config as cfg

    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(cfg, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(health_service, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    smart_router.clear_sticky()
    monkeypatch.setattr(providers_service, "_is_reachable", AsyncMock(return_value=True))
    monkeypatch.setattr(smart_router, "_spawn", lambda coro: coro.close())
    yield monkeypatch
    health_service.reload_for_tests()
    smart_router.clear_sticky()


def _use(env, registry: ProviderRegistry) -> None:
    env.setattr(providers_service, "load_registry", lambda: registry)


def _labels(plan) -> list[str]:
    return [s.label for s in plan]


@pytest.mark.asyncio
async def test_unknown_provider_raises_400(env):
    _use(env, ProviderRegistry(active_provider_id="p1", providers=[_p("p1")]))
    with pytest.raises(smart_router.PinnedRouteError) as exc:
        await smart_router.decide_pinned(BODY, "nope", "m1", 10)
    assert exc.value.status == 400
    assert str(exc.value) == exc.value.message


@pytest.mark.asyncio
async def test_cloud_provider_without_key_raises_400(env):
    env.delenv("P1_KEY", raising=False)
    _use(env, ProviderRegistry(active_provider_id="p1", providers=[_p("p1", "P1_KEY")]))
    with pytest.raises(smart_router.PinnedRouteError) as exc:
        await smart_router.decide_pinned(BODY, "p1", "m1", 10)
    assert exc.value.status == 400
    assert "credencial" in exc.value.message


@pytest.mark.asyncio
async def test_local_provider_without_key_is_allowed(env):
    local = _p("local", api_base="http://127.0.0.1:4002")
    _use(env, ProviderRegistry(active_provider_id="local", providers=[local]))
    decision = await smart_router.decide_pinned(BODY, "local", "m1", 10)
    assert _labels(decision.plan) == ["local#0"]


@pytest.mark.asyncio
async def test_plan_uses_only_pinned_provider_slots(env):
    env.setenv("P1_KEY", "k1")
    env.setenv("P1_KEY_2", "k2")
    env.setenv("P2_KEY", "k3")
    p1, p2 = _p("p1", "P1_KEY", ["P1_KEY_2"]), _p("p2", "P2_KEY")
    _use(env, ProviderRegistry(active_provider_id="p1", providers=[p1, p2], fallback_provider_ids=["p2"]))
    decision = await smart_router.decide_pinned(BODY, "p1", "m1", 10)
    assert _labels(decision.plan) == ["p1#0", "p1#1"]
    assert decision.source == "pinned"


@pytest.mark.asyncio
async def test_cooling_pinned_provider_still_tried(env):
    env.setenv("P1_KEY", "k1")
    _use(env, ProviderRegistry(active_provider_id="p1", providers=[_p("p1", "P1_KEY")]))
    health_service.mark_signal(provider_key("p1"), QuotaSignal(kind="rate_limit", retry_after_s=600, excerpt=""))
    decision = await smart_router.decide_pinned(BODY, "p1", "m1", 10)
    assert _labels(decision.plan) == ["p1#0"]


@pytest.mark.asyncio
async def test_budget_exceeded_raises_429(env):
    env.setenv("P1_KEY", "k1")
    _use(env, ProviderRegistry(active_provider_id="p1", providers=[_p("p1", "P1_KEY")]))
    env.setattr(smart_router.budget_service, "check_budget", AsyncMock(return_value="daily_usd"))
    with pytest.raises(smart_router.PinnedRouteError) as exc:
        await smart_router.decide_pinned(BODY, "p1", "m1", 10)
    assert exc.value.status == 429
    assert "daily_usd" in exc.value.message


@pytest.mark.asyncio
async def test_routing_rule_is_ignored(env):
    env.setenv("P1_KEY", "k1")
    env.setenv("P2_KEY", "k2")
    p1, p2 = _p("p1", "P1_KEY"), _p("p2", "P2_KEY")
    registry = ProviderRegistry(active_provider_id="p1", providers=[p1, p2], routing_enabled=True,
                                routing_rules=[RoutingRule(pattern="m1", provider_id="p2")])
    _use(env, registry)
    decision = await smart_router.decide_pinned(BODY, "p1", "m1", 10)
    assert _labels(decision.plan) == ["p1#0"]


@pytest.mark.asyncio
async def test_pinned_never_touches_sticky(env):
    env.setenv("P1_KEY", "k1")
    env.setenv("P2_KEY", "k2")
    p1, p2 = _p("p1", "P1_KEY"), _p("p2", "P2_KEY")
    _use(env, ProviderRegistry(active_provider_id="p1", providers=[p1, p2]))
    decision = await smart_router.decide_pinned(BODY, "p1", "m1", 10)
    other = smart_router.PlanStep(p2, None, credential_slots(p2)[0], False)
    smart_router.note_success(decision, other, BODY)
    assert len(smart_router._sticky) == 0


@pytest.mark.asyncio
async def test_model_passes_through(env):
    env.setenv("P1_KEY", "k1")
    _use(env, ProviderRegistry(active_provider_id="p1", providers=[_p("p1", "P1_KEY")]))
    decision = await smart_router.decide_pinned(BODY, "p1", "other-model", 10)
    assert decision.chosen_model == "other-model"
    assert decision.plan[0].model == "other-model"
    empty = await smart_router.decide_pinned(BODY, "p1", "", 10)
    assert empty.plan[0].model is None
