"""Tests del router inteligente: shadow/active, reglas explícitas, filtros y salud."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.core.quota_signals import QuotaSignal
from app.models.provider import Provider, ProviderRegistry, RoutingRule
from app.models.smart import RouteTarget, SmartRoutingConfig, TierPolicy
from app.services import health_service, providers_service, smart_router


def _p(pid: str, api_base: str = None, **kw) -> Provider:
    return Provider(id=pid, name=pid, api_base=api_base or f"https://{pid}.example.com/v1", active_model=f"{pid}-model", **kw)


def _registry(providers, smart: SmartRoutingConfig, active="anthropic", rules=None, fallbacks=None) -> ProviderRegistry:
    return ProviderRegistry(
        active_provider_id=active, providers=providers, smart=smart,
        routing_enabled=bool(rules), routing_rules=rules or [], fallback_provider_ids=fallbacks or [],
    )


def _smart(mode="active", tiers=None, **kw) -> SmartRoutingConfig:
    tiers = tiers or [
        TierPolicy(tier="trivial", targets=[RouteTarget(provider_id="ollama")]),
        TierPolicy(tier="complex", targets=[RouteTarget(provider_id="anthropic", model="claude-opus-4-6")]),
    ]
    return SmartRoutingConfig(enabled=True, mode=mode, tiers=tiers, **kw)


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
    yield
    health_service.reload_for_tests()
    smart_router.clear_sticky()


def _install(monkeypatch, registry):
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)


def _body(text="hola", model="claude-sonnet-4-6"):
    return {"model": model, "messages": [{"role": "user", "content": text}], "max_tokens": 100}


@pytest.mark.asyncio
async def test_disabled_returns_legacy_pick_with_off_mode_and_header(env, monkeypatch):
    anthropic = _p("anthropic")
    _install(monkeypatch, _registry([anthropic], SmartRoutingConfig(enabled=False)))
    d = await smart_router.decide(_body(), "claude-sonnet-4-6", 50, {})
    assert d.as_pick() == (anthropic, None, True)
    assert d.mode == "off" and d.source == "active_default"
    assert d.to_header().startswith("tier=trivial;score=") and "mode=off" in d.to_header()


@pytest.mark.asyncio
async def test_shadow_keeps_legacy_and_records_would_choose(env, monkeypatch):
    anthropic, ollama = _p("anthropic"), _p("ollama", "http://127.0.0.1:11434")
    _install(monkeypatch, _registry([anthropic, ollama], _smart(mode="shadow")))
    d = await smart_router.decide(_body("hola", model="claude-3-5-haiku"), "claude-3-5-haiku", 20, {})
    assert d.chosen_provider is anthropic
    assert d.would_key == "provider:ollama" and d.mode == "shadow"
    assert "shadow_target=provider:ollama" in d.to_header()


@pytest.mark.asyncio
async def test_active_picks_tier_target_and_reports_legacy_as_would(env, monkeypatch):
    anthropic, ollama = _p("anthropic"), _p("ollama", "http://127.0.0.1:11434")
    _install(monkeypatch, _registry([anthropic, ollama], _smart()))
    d = await smart_router.decide(_body("hola", model="claude-3-5-haiku"), "claude-3-5-haiku", 20, {})
    assert d.chosen_provider is ollama and d.chosen_model == "ollama-model"
    assert d.source == "smart" and d.is_active is False
    assert d.would_key == "provider:anthropic"


@pytest.mark.asyncio
async def test_explicit_rule_wins_over_smart(env, monkeypatch):
    anthropic, ollama, copilot = _p("anthropic"), _p("ollama", "http://127.0.0.1:11434"), _p("copilot")
    rules = [RoutingRule(pattern="haiku", provider_id="copilot", model="gpt-4o-mini")]
    _install(monkeypatch, _registry([anthropic, ollama, copilot], _smart(), rules=rules))
    d = await smart_router.decide(_body("hola", model="claude-3-5-haiku"), "claude-3-5-haiku", 20, {})
    assert d.chosen_provider is copilot and d.chosen_model == "gpt-4o-mini"
    assert d.source == "explicit_rule" and d.would_key == "provider:ollama"


@pytest.mark.asyncio
async def test_rule_with_tier_gate_only_matches_that_tier(env, monkeypatch):
    anthropic, copilot = _p("anthropic"), _p("copilot")
    rules = [RoutingRule(pattern="", provider_id="copilot", tier="complex")]
    _install(monkeypatch, _registry([anthropic, copilot], SmartRoutingConfig(enabled=False), rules=rules))
    assert providers_service.resolve_route("x", 0, tier="trivial") is None
    assert providers_service.resolve_route("x", 0, tier="complex")[0] is copilot


@pytest.mark.asyncio
async def test_rule_max_tokens_upper_bound(env, monkeypatch):
    anthropic, copilot = _p("anthropic"), _p("copilot")
    rules = [RoutingRule(pattern="", provider_id="copilot", max_tokens=1000)]
    _install(monkeypatch, _registry([anthropic, copilot], SmartRoutingConfig(enabled=False), rules=rules))
    assert providers_service.resolve_route("x", 500)[0] is copilot
    assert providers_service.resolve_route("x", 5000) is None


@pytest.mark.asyncio
async def test_header_tier_override_changes_target(env, monkeypatch):
    anthropic, ollama = _p("anthropic"), _p("ollama", "http://127.0.0.1:11434")
    _install(monkeypatch, _registry([anthropic, ollama], _smart()))
    d = await smart_router.decide(_body("hola", model="claude-3-5-haiku"), "claude-3-5-haiku", 20, {"x-bipolar-tier": "complex"})
    assert d.tier == "complex" and d.chosen_provider is anthropic and d.chosen_model == "claude-opus-4-6"
    assert "header:complex" in d.reasons


@pytest.mark.asyncio
async def test_cooling_target_is_skipped_with_reason(env, monkeypatch):
    anthropic, ollama, llamacpp = _p("anthropic"), _p("ollama", "http://127.0.0.1:11434"), _p("llamacpp", "http://127.0.0.1:4002")
    tiers = [TierPolicy(tier="trivial", targets=[RouteTarget(provider_id="ollama"), RouteTarget(provider_id="llamacpp")])]
    _install(monkeypatch, _registry([anthropic, ollama, llamacpp], _smart(tiers=tiers)))
    health_service.mark_signal("provider:ollama", QuotaSignal("rate_limit", 300, "429"))
    d = await smart_router.decide(_body("hola", model="claude-3-5-haiku"), "claude-3-5-haiku", 20, {})
    assert d.chosen_provider is llamacpp
    assert ("provider:ollama", "cooling:rate_limit") in d.rejected


@pytest.mark.asyncio
async def test_capability_filters_tools_and_vision(env, monkeypatch):
    anthropic = _p("anthropic")
    notools = _p("notools", "http://127.0.0.1:5000", model_info={"supports_tools": False})
    novision = _p("novision", "http://127.0.0.1:5001", model_info={"supports_vision": False})
    ok = _p("ok", "http://127.0.0.1:5002")
    tiers = [TierPolicy(tier="trivial", targets=[RouteTarget(provider_id=p) for p in ("notools", "novision", "ok")])]
    _install(monkeypatch, _registry([anthropic, notools, novision, ok], _smart(tiers=tiers)))
    body = {
        "model": "claude-3-5-haiku",
        "tools": [{"name": "t", "input_schema": {}}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hola"}, {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}}]}],
    }
    d = await smart_router.decide(body, "claude-3-5-haiku", 20, {})
    assert d.chosen_provider is ok
    assert ("provider:notools", "capability_tools") in d.rejected
    assert ("provider:novision", "capability_vision") in d.rejected


@pytest.mark.asyncio
async def test_unreachable_local_target_is_skipped(env, monkeypatch):
    anthropic, ollama = _p("anthropic"), _p("ollama", "http://127.0.0.1:11434")
    _install(monkeypatch, _registry([anthropic, ollama], _smart()))
    monkeypatch.setattr(providers_service, "_is_reachable", AsyncMock(return_value=False))
    d = await smart_router.decide(_body("hola", model="claude-3-5-haiku"), "claude-3-5-haiku", 20, {})
    assert d.chosen_provider is anthropic and d.source == "failover"
    assert ("provider:ollama", "unreachable") in d.rejected
    assert "smart_no_candidate" in d.reasons


@pytest.mark.asyncio
async def test_tier_without_policy_escalates_upward(env, monkeypatch):
    anthropic, ollama = _p("anthropic"), _p("ollama", "http://127.0.0.1:11434")
    tiers = [TierPolicy(tier="complex", targets=[RouteTarget(provider_id="anthropic")])]
    _install(monkeypatch, _registry([anthropic, ollama], _smart(tiers=tiers), active="ollama"))
    d = await smart_router.decide(_body("implementa el endpoint de facturas y sus tests"), "claude-sonnet-4-6", 5000, {})
    assert d.chosen_provider is anthropic
    assert any(r.startswith("tier_fallback:") for r in d.reasons)


@pytest.mark.asyncio
async def test_oai_surface_rejects_non_active_anthropic(env, monkeypatch):
    copilot, anthropic = _p("copilot"), _p("anthropic", litellm_prefix="anthropic")
    tiers = [TierPolicy(tier="trivial", targets=[RouteTarget(provider_id="anthropic"), RouteTarget(provider_id="copilot")])]
    _install(monkeypatch, _registry([copilot, anthropic], _smart(tiers=tiers), active="copilot"))
    d = await smart_router.decide(_body("hola", model="claude-3-5-haiku"), "claude-3-5-haiku", 20, {}, surface="chat_completions")
    assert d.chosen_provider is copilot
    assert ("provider:anthropic", "oai_surface_anthropic_not_active") in d.rejected


@pytest.mark.asyncio
async def test_sticky_keeps_target_mid_tool_loop(env, monkeypatch):
    anthropic, ollama, llamacpp = _p("anthropic"), _p("ollama", "http://127.0.0.1:11434"), _p("llamacpp", "http://127.0.0.1:4002")
    tiers = [
        TierPolicy(tier="trivial", targets=[RouteTarget(provider_id="ollama")]),
        TierPolicy(tier="standard", targets=[RouteTarget(provider_id="llamacpp")]),
    ]
    _install(monkeypatch, _registry([anthropic, ollama, llamacpp], _smart(tiers=tiers)))
    first = {"model": "claude-3-5-haiku", "system": "s", "messages": [{"role": "user", "content": "hola"}]}
    d1 = await smart_router.decide(first, "claude-3-5-haiku", 20, {})
    assert d1.chosen_provider is ollama
    loop = {
        "model": "claude-3-5-haiku", "system": "s",
        "tools": [{"name": f"t{i}", "input_schema": {}} for i in range(12)],
        "messages": first["messages"] + [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "t1", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
        ],
    }
    d2 = await smart_router.decide(loop, "claude-3-5-haiku", 9000, {})
    assert d2.chosen_provider is ollama and d2.source == "sticky"


@pytest.mark.asyncio
async def test_dry_run_has_no_sticky_side_effect(env, monkeypatch):
    anthropic, ollama = _p("anthropic"), _p("ollama", "http://127.0.0.1:11434")
    _install(monkeypatch, _registry([anthropic, ollama], _smart()))
    await smart_router.decide(_body("hola", model="claude-3-5-haiku"), "claude-3-5-haiku", 20, {}, dry_run=True)
    assert len(smart_router._sticky) == 0


@pytest.mark.asyncio
async def test_report_outcome_429_marks_cooling_and_success_clears(env, monkeypatch):
    await smart_router.report_outcome(None, "provider:anthropic", ok=False, status=429, error="rate limit")
    assert health_service.effective_state("provider:anthropic") == "cooling"
    await smart_router.report_outcome(None, "provider:anthropic", ok=True, latency_ms=50)
    assert health_service.effective_state("provider:anthropic") == "available"


@pytest.mark.asyncio
async def test_pick_provider_skips_cooling_fallback_only_when_smart_enabled(env, monkeypatch):
    primary = _p("primary", "http://127.0.0.1:4000/v1")
    cool = _p("cool", "http://127.0.0.1:4001/v1")
    warm = _p("warm", "http://127.0.0.1:4002/v1")
    reach = AsyncMock(side_effect=lambda base: base != primary.api_base)
    monkeypatch.setattr(providers_service, "_is_reachable", reach)
    health_service.mark_signal("provider:cool", QuotaSignal("rate_limit", 300, "429"))
    for enabled, expected in ((True, warm), (False, cool)):
        registry = ProviderRegistry(
            active_provider_id="primary", providers=[primary, cool, warm],
            fallback_provider_ids=["cool", "warm"], smart=SmartRoutingConfig(enabled=enabled),
        )
        _install(monkeypatch, registry)
        provider, _, _ = await providers_service.pick_provider("m")
        assert provider is expected


def test_header_is_ascii_and_bounded():
    d = smart_router.RouteDecision(
        decision_id="abc", surface="messages", tier="trivial", score=1, intent="chat", requested_model="mé",
        prompt_tokens=1, chosen_provider=None, chosen_model=None, is_active=False, source="x", mode="off",
        rejected=[("provider:a", "b")] * 20,
    )
    header = d.to_header()
    assert len(header) <= smart_router.HEADER_MAX and header.isascii()
