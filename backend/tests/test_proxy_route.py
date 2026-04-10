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
async def test_enable_direct_routing_clears_env(monkeypatch):
    os.environ['ANTHROPIC_BASE_URL'] = 'http://localhost:4001'
    os.environ['ANTHROPIC_API_KEY'] = 'sk-litellm'
    res = await proxy_service.enable_direct_routing()
    assert 'ANTHROPIC_BASE_URL' not in os.environ
    assert 'ANTHROPIC_API_KEY' not in os.environ
    assert res['mode'] == 'direct'

@pytest.mark.asyncio
async def test_check_and_fallback(monkeypatch):
    # simulate get_proxy_status to return running False
    async def fake_get_proxy_status():
        return {'running': False}

    monkeypatch.setattr('app.services.proxy_service.get_proxy_status', fake_get_proxy_status)
    await proxy_service.set_route_mode('proxy')
    await proxy_service._check_and_fallback_once()
    assert proxy_service.get_route_mode() == 'direct'
