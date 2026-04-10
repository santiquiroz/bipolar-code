import httpx
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import providers_service

log = get_logger(__name__)


async def get_proxy_health() -> dict:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.proxy_url}/health")
            resp.raise_for_status()
            data = resp.json()
            log.info("proxy_health_ok", healthy=data.get("healthy_count"), unhealthy=data.get("unhealthy_count"))
            return data
    except httpx.ConnectError:
        log.warning("proxy_unreachable", url=settings.proxy_url)
        return {}
    except Exception as e:
        log.error("proxy_health_error", error=str(e))
        return {}


async def get_proxy_status() -> dict:
    settings = get_settings()
    health = await get_proxy_health()
    running = bool(health)
    active_provider_id = providers_service.detect_active_provider_from_health(health)

    return {
        "running": running,
        "port": int(settings.proxy_url.split(":")[-1]),
        "active_provider_id": active_provider_id,
        "healthy_models": health.get("healthy_count", 0),
        "unhealthy_models": health.get("unhealthy_count", 0),
    }
