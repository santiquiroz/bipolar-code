"""Tests for scenario-based provider routing."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api import providers as providers_api
from app.models.provider import Provider, ProviderRegistry, RoutingRule
from app.services import providers_service


@pytest.fixture
def client():
    from app.main import app
    from app.core.config import get_settings

    api_key = get_settings().ui_api_key
    return TestClient(app, headers={"x-api-key": api_key})


def _provider(provider_id: str, active_model: str = "active-model") -> Provider:
    return Provider(
        id=provider_id,
        name=provider_id,
        api_base=f"https://{provider_id}.example.com/v1",
        active_model=active_model,
    )


def _patch_registry(
    monkeypatch,
    *,
    providers: list[Provider],
    rules: list[RoutingRule],
    enabled: bool = True,
) -> ProviderRegistry:
    registry = ProviderRegistry(
        providers=providers,
        routing_enabled=enabled,
        routing_rules=rules,
    )
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)
    return registry


def test_resolve_route_returns_none_when_routing_disabled(monkeypatch):
    target = _provider("target")
    _patch_registry(
        monkeypatch,
        providers=[target],
        rules=[RoutingRule(pattern="haiku", provider_id=target.id)],
        enabled=False,
    )

    assert providers_service.resolve_route("claude-haiku") is None


def test_resolve_route_matches_pattern_case_insensitively(monkeypatch):
    target = _provider("target")
    _patch_registry(
        monkeypatch,
        providers=[target],
        rules=[RoutingRule(pattern="haiku", provider_id=target.id)],
    )

    provider, model = providers_service.resolve_route("claude-3-5-HAIKU-latest")

    assert provider is target
    assert model == target.active_model


def test_resolve_route_uses_first_matching_rule(monkeypatch):
    first = _provider("first", "first-model")
    second = _provider("second", "second-model")
    _patch_registry(
        monkeypatch,
        providers=[first, second],
        rules=[
            RoutingRule(pattern="claude", provider_id=first.id),
            RoutingRule(pattern="claude", provider_id=second.id),
        ],
    )

    provider, model = providers_service.resolve_route("claude-sonnet")

    assert provider is first
    assert model == "first-model"


def test_resolve_route_applies_min_tokens_gate(monkeypatch):
    target = _provider("long-context")
    _patch_registry(
        monkeypatch,
        providers=[target],
        rules=[RoutingRule(min_tokens=60_000, provider_id=target.id)],
    )

    assert providers_service.resolve_route("any-model", prompt_tokens=1_000) is None
    provider, model = providers_service.resolve_route(
        "any-model", prompt_tokens=70_000
    )

    assert provider is target
    assert model == target.active_model


def test_resolve_route_empty_pattern_and_zero_min_tokens_matches_anything(monkeypatch):
    target = _provider("catch-all")
    _patch_registry(
        monkeypatch,
        providers=[target],
        rules=[RoutingRule(pattern="", min_tokens=0, provider_id=target.id)],
    )

    provider, model = providers_service.resolve_route("completely-unrelated-model")

    assert provider is target
    assert model == target.active_model


def test_resolve_route_skips_unknown_provider(monkeypatch):
    fallback = _provider("fallback")
    unknown_rule = RoutingRule(pattern="claude", provider_id="missing")
    fallback_rule = RoutingRule(pattern="claude", provider_id=fallback.id)
    registry = _patch_registry(
        monkeypatch,
        providers=[fallback],
        rules=[unknown_rule, fallback_rule],
    )

    provider, model = providers_service.resolve_route("claude-sonnet")

    assert provider is fallback
    assert model == fallback.active_model

    registry.routing_rules = [unknown_rule]
    assert providers_service.resolve_route("claude-sonnet") is None


@pytest.mark.parametrize(
    ("rule_model", "expected_model"),
    [
        ("", "provider-active-model"),
        ("rule-specific-model", "rule-specific-model"),
    ],
)
def test_resolve_route_selects_rule_model_or_provider_active_model(
    monkeypatch, rule_model, expected_model
):
    target = _provider("target", "provider-active-model")
    _patch_registry(
        monkeypatch,
        providers=[target],
        rules=[RoutingRule(provider_id=target.id, model=rule_model)],
    )

    provider, model = providers_service.resolve_route("any-model")

    assert provider is target
    assert model == expected_model


def test_get_routing_returns_enabled_and_rules(client, monkeypatch):
    rule = RoutingRule(pattern="haiku", provider_id="fast", model="fast-model")
    registry = ProviderRegistry(routing_enabled=True, routing_rules=[rule])
    mock_load_registry = Mock(return_value=registry)
    mock_set_routing = Mock()
    mock_get_provider = Mock()
    monkeypatch.setattr(
        providers_api.providers_service, "load_registry", mock_load_registry
    )
    monkeypatch.setattr(
        providers_api.providers_service, "set_routing", mock_set_routing
    )
    monkeypatch.setattr(
        providers_api.providers_service, "get_provider", mock_get_provider
    )

    response = client.get("/api/providers/routing")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "rules": [rule.model_dump()],
        "fallback_provider_ids": [],
    }
    mock_load_registry.assert_called_once_with()
    mock_set_routing.assert_not_called()
    mock_get_provider.assert_not_called()


def test_put_routing_rejects_unknown_provider(client, monkeypatch):
    mock_load_registry = Mock()
    mock_set_routing = Mock()
    mock_get_provider = Mock(return_value=None)
    monkeypatch.setattr(
        providers_api.providers_service, "load_registry", mock_load_registry
    )
    monkeypatch.setattr(
        providers_api.providers_service, "set_routing", mock_set_routing
    )
    monkeypatch.setattr(
        providers_api.providers_service, "get_provider", mock_get_provider
    )
    rule = {"pattern": "haiku", "provider_id": "missing"}

    response = client.put(
        "/api/providers/routing", json={"enabled": True, "rules": [rule]}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Providers no registrados: ['missing']"
    mock_get_provider.assert_called_once_with("missing")
    mock_set_routing.assert_not_called()
    mock_load_registry.assert_not_called()


def test_put_routing_calls_set_routing_and_returns_result(client, monkeypatch):
    target = _provider("target")
    rule_payload = {
        "pattern": "haiku",
        "min_tokens": 0,
        "provider_id": target.id,
        "model": "fast-model",
    }
    service_result = {"enabled": True, "rules": [rule_payload]}
    mock_load_registry = Mock()
    mock_set_routing = Mock(return_value=service_result)
    mock_get_provider = Mock(return_value=target)
    monkeypatch.setattr(
        providers_api.providers_service, "load_registry", mock_load_registry
    )
    monkeypatch.setattr(
        providers_api.providers_service, "set_routing", mock_set_routing
    )
    monkeypatch.setattr(
        providers_api.providers_service, "get_provider", mock_get_provider
    )

    response = client.put(
        "/api/providers/routing",
        json={"enabled": True, "rules": [rule_payload]},
    )

    assert response.status_code == 200
    assert response.json() == service_result
    mock_get_provider.assert_called_once_with(target.id)
    mock_set_routing.assert_called_once_with(
        True, [RoutingRule(**rule_payload)], None
    )
    mock_load_registry.assert_not_called()
