"""Plan de ruta: pasos (provider, slot) ordenados, sin duplicados, con tope y fallbacks."""
from unittest.mock import AsyncMock

import pytest

from app.core.quota_signals import QuotaSignal
from app.models.provider import Provider, ProviderRegistry
from app.models.smart import RouteTarget, SmartRoutingConfig, TierPolicy
from app.services import health_service, providers_service, smart_router
from app.services.upstream import AttemptFailure


def _p(pid, auth="", extras=None, api_base=None):
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


def _labels(plan):
    return [s.label for s in plan]


def test_build_plan_expands_pool_then_fallbacks(env):
    env.setenv("A_KEY", "a1")
    env.setenv("A_KEY_2", "a2")
    a, b = _p("a", "A_KEY", ["A_KEY_2"]), _p("b")
    plan = smart_router.build_plan((a, None, True), [(b, None, False)], max_steps=4)
    assert _labels(plan) == ["a#0", "a#1", "b#0"]
    assert plan[0].is_active and not plan[2].is_active


def test_build_plan_dedups_providers_and_caps_steps(env):
    a, b, c = _p("a"), _p("b"), _p("c")
    plan = smart_router.build_plan((a, None, True), [(a, None, True), (b, None, False), (c, None, False)], max_steps=2)
    assert _labels(plan) == ["a#0", "b#0"]


def test_build_plan_skips_cooling_fallback_but_keeps_primary_slot_when_empty(env):
    a, b = _p("a"), _p("b")
    health_service.mark_signal("provider:a", QuotaSignal(kind="rate_limit", retry_after_s=300, excerpt="429"))
    health_service.mark_signal("provider:b", QuotaSignal(kind="rate_limit", retry_after_s=300, excerpt="429"))
    plan = smart_router.build_plan((a, None, True), [(b, None, False)], max_steps=4)
    assert _labels(plan) == ["a#0"]


def test_build_plan_skips_exhausted_slot_of_primary(env):
    env.setenv("A_KEY", "a1")
    env.setenv("A_KEY_2", "a2")
    a = _p("a", "A_KEY", ["A_KEY_2"])
    health_service.mark_signal("provider:a#A_KEY", QuotaSignal(kind="quota_exhausted", retry_after_s=600, excerpt="quota"))
    plan = smart_router.build_plan((a, None, True), [], max_steps=4)
    assert _labels(plan) == ["a#1"]


@pytest.mark.asyncio
async def test_decide_with_smart_disabled_uses_registry_fallbacks(env):
    a, b = _p("a"), _p("b")
    registry = ProviderRegistry(active_provider_id="a", providers=[a, b], fallback_provider_ids=["b"])
    env.setattr(providers_service, "load_registry", lambda: registry)
    d = await smart_router.decide({"model": "x", "messages": [{"role": "user", "content": "hola"}]}, "x", 10, {})
    assert _labels(d.plan) == ["a#0", "b#0"]


@pytest.mark.asyncio
async def test_decide_active_smart_appends_other_ranked_candidates(env):
    a, b, c = _p("a"), _p("b"), _p("c")
    smart = SmartRoutingConfig(enabled=True, mode="active", tiers=[
        TierPolicy(tier=t, targets=[RouteTarget(provider_id="b"), RouteTarget(provider_id="c")])
        for t in ("trivial", "simple", "standard", "complex")
    ])
    registry = ProviderRegistry(active_provider_id="a", providers=[a, b, c], smart=smart, fallback_provider_ids=["a"])
    env.setattr(providers_service, "load_registry", lambda: registry)
    d = await smart_router.decide({"model": "x", "messages": [{"role": "user", "content": "hola"}]}, "x", 10, {})
    assert _labels(d.plan) == ["b#0", "c#0", "a#0"]


@pytest.mark.asyncio
async def test_decide_drops_unreachable_local_fallback(env):
    a, local = _p("a"), _p("local", api_base="http://127.0.0.1:4002/v1")
    registry = ProviderRegistry(active_provider_id="a", providers=[a, local], fallback_provider_ids=["local"])
    env.setattr(providers_service, "load_registry", lambda: registry)
    env.setattr(providers_service, "_is_reachable", AsyncMock(return_value=False))
    d = await smart_router.decide({"model": "x", "messages": [{"role": "user", "content": "hola"}]}, "x", 10, {})
    assert _labels(d.plan) == ["a#0"]


def test_mark_step_health_credential_marks_slot_provider_marks_provider(env):
    env.setenv("A_KEY", "a1")
    env.setenv("A_KEY_2", "a2")
    a = _p("a", "A_KEY", ["A_KEY_2"])
    step0, step1 = smart_router.build_plan((a, None, True), [], max_steps=4)
    smart_router.mark_step_health(step0, AttemptFailure(step0, "retry_credential", 429, "rate limit"))
    assert health_service.get("provider:a#A_KEY").state == "cooling"
    assert health_service.get("provider:a").state == "available"
    smart_router.mark_step_health(step1, AttemptFailure(step1, "retry_provider", 529, "Overloaded"))
    assert health_service.get("provider:a").state == "cooling"


def test_mark_step_health_ignores_fatal(env):
    a = _p("a")
    (step,) = smart_router.build_plan((a, None, True), [], max_steps=4)
    smart_router.mark_step_health(step, AttemptFailure(step, "fatal", 400, "bad request"))
    assert health_service.get("provider:a").total_failed == 0
