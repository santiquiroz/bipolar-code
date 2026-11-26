import pytest
from app.services import proxy_service

CLEARED_USER_ENV = [
    ("registry", "ANTHROPIC_BASE_URL", None),
    ("registry", "ANTHROPIC_API_KEY", None),
    ("claude_settings", {"ANTHROPIC_BASE_URL": None, "ANTHROPIC_API_KEY": None}),
]

@pytest.mark.asyncio
async def test_set_get_route_mode():
    await proxy_service.set_route_mode('direct')
    assert proxy_service.get_route_mode() == 'direct'
    await proxy_service.set_route_mode('proxy')
    assert proxy_service.get_route_mode() == 'proxy'

@pytest.mark.asyncio
async def test_enable_direct_routing_clears_user_env(monkeypatch, user_env_calls):
    async def fake_status():
        return {"running": False}
    monkeypatch.setattr("app.services.proxy_service.get_proxy_status", fake_status)

    result = await proxy_service.enable_direct_routing()
    assert result["mode"] == "direct"
    assert result["applied"] is True
    assert user_env_calls == CLEARED_USER_ENV

@pytest.mark.asyncio
async def test_check_and_fallback(monkeypatch, user_env_calls):
    # _check_and_fallback_once llama get_proxy_health directamente (no get_proxy_status)
    async def fake_get_proxy_health():
        return {}  # vacío = litellm no corriendo

    async def fake_status():
        return {"running": False}

    monkeypatch.setattr('app.services.proxy_service.get_proxy_health', fake_get_proxy_health)
    monkeypatch.setattr('app.services.proxy_service.get_proxy_status', fake_status)
    await proxy_service.set_route_mode('proxy')
    await proxy_service._check_and_fallback_once()
    assert proxy_service.get_route_mode() == 'direct'
    assert user_env_calls == CLEARED_USER_ENV


@pytest.mark.asyncio
async def test_start_proxy_kills_old_litellm_before_starting(monkeypatch):
    from types import SimpleNamespace
    from app.api import proxy as proxy_api

    calls = []

    async def fake_kill():
        calls.append("kill")

    monkeypatch.setattr(proxy_api.providers_service, "get_active_provider", lambda: SimpleNamespace(id="anthropic"))
    monkeypatch.setattr(proxy_api.providers_service, "generate_litellm_config", lambda provider: "config-anthropic.yaml")
    monkeypatch.setattr(proxy_api.providers_service, "_kill_litellm", fake_kill)
    monkeypatch.setattr(proxy_api.providers_service, "_start_litellm", lambda path: calls.append(f"start:{path}"))

    result = await proxy_api.start_proxy()

    assert calls == ["kill", "start:config-anthropic.yaml"]
    assert result == {"started": True, "provider": "anthropic", "config": "config-anthropic.yaml"}
