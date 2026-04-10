import pytest
from unittest.mock import patch, AsyncMock
from app.services import proxy_service


@pytest.mark.asyncio
async def test_get_proxy_status_running():
    mock_health = {"healthy_count": 2, "unhealthy_count": 0, "healthy_endpoints": []}
    with patch("app.services.proxy_service.get_proxy_health", AsyncMock(return_value=mock_health)):
        with patch("app.services.providers_service.detect_active_provider_from_health", return_value="copilot"):
            status = await proxy_service.get_proxy_status()
    assert status["running"] is True
    assert status["healthy_models"] == 2


@pytest.mark.asyncio
async def test_get_proxy_status_unreachable():
    with patch("app.services.proxy_service.get_proxy_health", AsyncMock(return_value={})):
        with patch("app.services.providers_service.detect_active_provider_from_health", return_value="copilot"):
            status = await proxy_service.get_proxy_status()
    assert status["running"] is False
