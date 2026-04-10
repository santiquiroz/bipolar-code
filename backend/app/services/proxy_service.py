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
    # before returning status, ensure route fallback is applied if needed
    try:
        await _check_and_fallback_once()
    except Exception:
        # don't let fallback errors break status reporting
        pass

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

# Runtime routing state (process-only)
import asyncio
import os
from typing import Literal

route_mode: Literal['direct', 'proxy'] = 'direct'
_route_lock = asyncio.Lock()

def get_route_mode() -> str:
    return route_mode

async def set_route_mode(new_mode: str) -> None:
    global route_mode
    async with _route_lock:
        route_mode = new_mode
        log.info('route_mode_changed', mode=new_mode)

async def enable_proxy_routing() -> dict:
    """Start litellm if needed, set runtime env overrides, and set route_mode to 'proxy'."""
    log.info('route_apply_attempt', requested='proxy')
    settings = get_settings()
    try:
        status = await get_proxy_status()
        if not status.get('running'):
            provider = providers_service.get_active_provider()
            if not provider:
                raise RuntimeError('No active provider to generate config')
            config_path = providers_service.generate_litellm_config(provider)
            providers_service._kill_litellm()
            providers_service._start_litellm(config_path)
            # poll health
            for _ in range(10):
                await asyncio.sleep(0.5)
                status = await get_proxy_status()
                if status.get('running'):
                    break
        # set runtime env vars (process-only)
        os.environ['ANTHROPIC_BASE_URL'] = settings.proxy_url or 'http://localhost:4001'
        os.environ['ANTHROPIC_API_KEY'] = settings.proxy_api_key or 'sk-litellm'
        await set_route_mode('proxy')
        log.info('route_apply_success', mode='proxy')
        return {'applied': True, 'mode': 'proxy', 'proxy_status': status}
    except Exception as e:
        log.error('route_apply_failed', requested='proxy', error=str(e))
        raise

async def enable_direct_routing(stop_litellm: bool = False) -> dict:
    """Clear runtime env overrides and set route_mode to 'direct'. Optionally stop litellm."""
    log.info('route_apply_attempt', requested='direct')
    try:
        removed = []
        for k in ('ANTHROPIC_BASE_URL', 'ANTHROPIC_API_KEY'):
            if k in os.environ:
                del os.environ[k]
                removed.append(k)
        if stop_litellm:
            providers_service._kill_litellm()
        await set_route_mode('direct')
        status = await get_proxy_status()
        log.info('route_apply_success', mode='direct')
        return {'applied': True, 'mode': 'direct', 'removed': removed, 'proxy_status': status}
    except Exception as e:
        log.error('route_apply_failed', requested='direct', error=str(e))
        raise

async def _check_and_fallback_once() -> None:
    """Check litellm health and if route_mode is 'proxy' but litellm not running, fallback to direct."""
    try:
        if get_route_mode() != 'proxy':
            return
        status = await get_proxy_status()
        running = status.get('running')
        if not running:
            # fallback
            await enable_direct_routing(stop_litellm=False)
            log.warning('route_fallback_to_direct', reason='litellm_down')
    except Exception as e:
        log.error('route_fallback_error', error=str(e))
        # do not raise
        return
