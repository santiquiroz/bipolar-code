from unittest.mock import AsyncMock

import pytest

from app.models.provider import Provider, ProviderRegistry, RoutingRule
from app.services import providers_service


def _provider(provider_id: str, api_base: str, active_model: str = "active-model") -> Provider:
    return Provider(
        id=provider_id,
        name=provider_id,
        api_base=api_base,
        active_model=active_model,
    )


def _patch_registry(monkeypatch, registry: ProviderRegistry) -> None:
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)


@pytest.mark.asyncio
async def test_pick_provider_without_fallback_returns_active_without_reachability_check(
    monkeypatch,
):
    active = _provider("active", "http://127.0.0.1:4000/v1")
    registry = ProviderRegistry(
        active_provider_id=active.id,
        providers=[active],
        fallback_provider_ids=[],
    )
    _patch_registry(monkeypatch, registry)
    is_reachable = AsyncMock()
    monkeypatch.setattr(providers_service, "_is_reachable", is_reachable)

    provider, model_override, is_active = await providers_service.pick_provider(
        "requested-model"
    )

    assert provider is active
    assert model_override is None
    assert is_active is True
    is_reachable.assert_not_awaited()


@pytest.mark.asyncio
async def test_pick_provider_fails_over_without_leaking_routed_model(monkeypatch):
    primary = _provider("primary", "http://127.0.0.1:4000/v1")
    fallback = _provider("fallback", "http://localhost:4001/v1")
    registry = ProviderRegistry(
        active_provider_id=primary.id,
        providers=[primary, fallback],
        routing_enabled=True,
        routing_rules=[
            RoutingRule(
                pattern="sonnet",
                provider_id=primary.id,
                model="routed-primary-model",
            )
        ],
        fallback_provider_ids=[fallback.id],
    )
    _patch_registry(monkeypatch, registry)
    is_reachable = AsyncMock(
        side_effect=lambda api_base: api_base == fallback.api_base
    )
    monkeypatch.setattr(providers_service, "_is_reachable", is_reachable)

    provider, model_override, is_active = await providers_service.pick_provider(
        "claude-sonnet"
    )

    assert provider is fallback
    assert model_override is None
    assert is_active is False
    assert [call.args[0] for call in is_reachable.await_args_list] == [
        primary.api_base,
        fallback.api_base,
    ]


@pytest.mark.asyncio
async def test_pick_provider_returns_reachable_routed_primary_with_routed_model(
    monkeypatch,
):
    primary = _provider("primary", "http://127.0.0.1:4000/v1")
    fallback = _provider("fallback", "http://localhost:4001/v1")
    registry = ProviderRegistry(
        active_provider_id=primary.id,
        providers=[primary, fallback],
        routing_enabled=True,
        routing_rules=[
            RoutingRule(
                pattern="haiku",
                provider_id=primary.id,
                model="routed-primary-model",
            )
        ],
        fallback_provider_ids=[fallback.id],
    )
    _patch_registry(monkeypatch, registry)
    is_reachable = AsyncMock(return_value=True)
    monkeypatch.setattr(providers_service, "_is_reachable", is_reachable)

    provider, model_override, is_active = await providers_service.pick_provider(
        "claude-haiku"
    )

    assert provider is primary
    assert model_override == "routed-primary-model"
    assert is_active is True
    is_reachable.assert_awaited_once_with(primary.api_base)


@pytest.mark.asyncio
async def test_pick_provider_assumes_non_local_primary_is_reachable(monkeypatch):
    primary = _provider("cloud", "https://api.example.com/v1")
    fallback = _provider("fallback", "http://localhost:4001/v1")
    registry = ProviderRegistry(
        active_provider_id=primary.id,
        providers=[primary, fallback],
        fallback_provider_ids=[fallback.id],
    )
    _patch_registry(monkeypatch, registry)
    is_reachable = AsyncMock()
    monkeypatch.setattr(providers_service, "_is_reachable", is_reachable)

    provider, model_override, is_active = await providers_service.pick_provider(
        "requested-model"
    )

    assert provider is primary
    assert model_override is None
    assert is_active is True
    is_reachable.assert_not_awaited()


@pytest.mark.asyncio
async def test_pick_provider_returns_primary_when_all_candidates_are_unreachable(
    monkeypatch,
):
    primary = _provider("primary", "http://127.0.0.1:4000/v1")
    fallback = _provider("fallback", "http://localhost:4001/v1")
    registry = ProviderRegistry(
        active_provider_id=primary.id,
        providers=[primary, fallback],
        fallback_provider_ids=[fallback.id],
    )
    _patch_registry(monkeypatch, registry)
    is_reachable = AsyncMock(return_value=False)
    monkeypatch.setattr(providers_service, "_is_reachable", is_reachable)

    provider, model_override, is_active = await providers_service.pick_provider(
        "requested-model"
    )

    assert provider is primary
    assert provider is not None
    assert model_override is None
    assert is_active is True
    assert is_reachable.await_count == 2


@pytest.mark.parametrize(
    ("active_provider_id", "expected_is_active"),
    [
        ("fallback", True),
        ("other", False),
    ],
)
@pytest.mark.asyncio
async def test_pick_provider_sets_is_active_from_returned_candidate_id(
    monkeypatch,
    active_provider_id,
    expected_is_active,
):
    primary = _provider("routed-primary", "http://127.0.0.1:4000/v1")
    fallback = _provider("fallback", "https://fallback.example.com/v1")
    other = _provider("other", "https://other.example.com/v1")
    registry = ProviderRegistry(
        active_provider_id=active_provider_id,
        providers=[primary, fallback, other],
        routing_enabled=True,
        routing_rules=[
            RoutingRule(pattern="sonnet", provider_id=primary.id)
        ],
        fallback_provider_ids=[fallback.id],
    )
    _patch_registry(monkeypatch, registry)
    is_reachable = AsyncMock(return_value=False)
    monkeypatch.setattr(providers_service, "_is_reachable", is_reachable)

    provider, model_override, is_active = await providers_service.pick_provider(
        "claude-sonnet"
    )

    assert provider is fallback
    assert model_override is None
    assert is_active is expected_is_active
    is_reachable.assert_awaited_once_with(primary.api_base)
