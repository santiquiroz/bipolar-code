import asyncio
import os
import pytest
from app.services import proxy_service

@pytest.mark.asyncio
async def test_set_get_route_mode():
    await proxy_service.set_route_mode('direct')
    assert proxy_service.get_route_mode() == 'direct'
    await proxy_service.set_route_mode('proxy')
    assert proxy_service.get_route_mode() == 'proxy'

@pytest.mark.asyncio
async def test_enable_direct_routing_does_not_crash(monkeypatch):
    """_set_user_env no debe lanzar excepción en ninguna plataforma."""
    async def fake_status():
        return {"running": False}
    monkeypatch.setattr("app.services.proxy_service.get_proxy_status", fake_status)

    result = await proxy_service.enable_direct_routing()
    assert result["mode"] == "direct"
    assert result["applied"] is True

@pytest.mark.asyncio
async def test_check_and_fallback(monkeypatch):
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
