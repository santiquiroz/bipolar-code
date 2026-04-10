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

def _set_user_env(key: str, value: str | None) -> None:
    """Escribe o borra una variable de entorno en:
    1. HKCU\\Environment (registry de usuario)
    2. ~/.claude/settings.json sección 'env' (leída directamente por Claude Code)
    """
    import winreg
    import ctypes
    import json

    # 1. Registry
    reg_key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    )
    try:
        if value is None:
            try:
                winreg.DeleteValue(reg_key, key)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(reg_key, key, 0, winreg.REG_EXPAND_SZ, value)
    finally:
        winreg.CloseKey(reg_key)

    # Broadcast para que Explorer actualice su entorno
    HWND_BROADCAST = 0xFFFF
    WM_SETTINGCHANGE = 0x001A
    ctypes.windll.user32.SendMessageTimeoutW(
        HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", 2, 5000, None
    )

    # 2. ~/.claude/settings.json — Claude Code lo lee al arrancar
    settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        env_section = settings.setdefault("env", {})
        if value is None:
            env_section.pop(key, None)
        else:
            env_section[key] = value
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        log.info("claude_settings_env_written", key=key, has_value=value is not None)
    except Exception as e:
        log.warning("claude_settings_env_failed", key=key, error=str(e))

    log.info("user_env_written", key=key, has_value=value is not None)


async def enable_proxy_routing() -> dict:
    """Inicia litellm si no está corriendo y persiste ANTHROPIC_BASE_URL/API_KEY
    como variables de usuario en el registry. Requiere reiniciar Claude Code."""
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
            for _ in range(10):
                await asyncio.sleep(0.5)
                status = await get_proxy_status()
                if status.get('running'):
                    break

        proxy_url = settings.proxy_url or 'http://localhost:4001'
        api_key = settings.proxy_api_key or 'sk-litellm'
        _set_user_env('ANTHROPIC_BASE_URL', proxy_url)
        _set_user_env('ANTHROPIC_API_KEY', api_key)
        await set_route_mode('proxy')
        log.info('route_apply_success', mode='proxy')
        return {
            'applied': True,
            'mode': 'proxy',
            'proxy_status': status,
            'restart_required': True,
        }
    except Exception as e:
        log.error('route_apply_failed', requested='proxy', error=str(e))
        raise

async def enable_direct_routing(stop_litellm: bool = False) -> dict:
    """Borra ANTHROPIC_BASE_URL/API_KEY del registry de usuario.
    Requiere reiniciar Claude Code para volver a apuntar directo a Anthropic."""
    log.info('route_apply_attempt', requested='direct')
    try:
        _set_user_env('ANTHROPIC_BASE_URL', None)
        _set_user_env('ANTHROPIC_API_KEY', None)
        if stop_litellm:
            providers_service._kill_litellm()
        await set_route_mode('direct')
        status = await get_proxy_status()
        log.info('route_apply_success', mode='direct')
        return {
            'applied': True,
            'mode': 'direct',
            'proxy_status': status,
            'restart_required': True,
        }
    except Exception as e:
        log.error('route_apply_failed', requested='direct', error=str(e))
        raise

async def _check_and_fallback_once() -> None:
    """Check litellm health and if route_mode is 'proxy' but litellm not running, fallback to direct."""
    try:
        if get_route_mode() != 'proxy':
            return
        health = await get_proxy_health()  # direct call — avoids recursion via get_proxy_status
        if not bool(health):
            await enable_direct_routing(stop_litellm=False)
            log.warning('route_fallback_to_direct', reason='litellm_down')
    except Exception as e:
        log.error('route_fallback_error', error=str(e))
        # do not raise
        return
