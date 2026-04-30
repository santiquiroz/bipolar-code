import httpx
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import providers_service

log = get_logger(__name__)


async def get_proxy_health() -> dict:
    """Two-phase health check: fast liveness via /health/readiness, then model counts via /health."""
    settings = get_settings()
    url_base = settings.proxy_url
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{url_base}/health/readiness",
                timeout=httpx.Timeout(5.0),
            )
            if resp.status_code >= 400:
                log.warning("proxy_not_ready", status=resp.status_code, url=url_base)
                return {}
            # Proxy is up — try to get model counts (may be slow when models are timing out)
            try:
                model_resp = await client.get(
                    f"{url_base}/health",
                    timeout=httpx.Timeout(8.0),
                )
                data = model_resp.json()
                log.info("proxy_health_ok", healthy=data.get("healthy_count"), unhealthy=data.get("unhealthy_count"))
                return data
            except Exception:
                log.info("proxy_health_model_check_slow", url=url_base)
                return {"healthy_count": 0, "unhealthy_count": 0}
    except httpx.ConnectError:
        log.warning("proxy_unreachable", url=url_base)
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

_claude_settings_lock = asyncio.Lock()


def _write_claude_settings(updates: dict[str, str | None]) -> None:
    """Atomically apply multiple env var updates to ~/.claude/settings.json."""
    import json, tempfile
    settings_path = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            claude_settings = json.load(f)
        env_section = claude_settings.setdefault("env", {})
        for key, value in updates.items():
            if value is None:
                env_section.pop(key, None)
            else:
                env_section[key] = value
        settings_dir = os.path.dirname(settings_path)
        with tempfile.NamedTemporaryFile("w", dir=settings_dir, suffix=".tmp",
                                        delete=False, encoding="utf-8") as tf:
            json.dump(claude_settings, tf, indent=2)
            tmp_name = tf.name
        os.replace(tmp_name, settings_path)
        log.info("claude_settings_env_written", keys=list(updates.keys()))
    except FileNotFoundError:
        log.debug("claude_settings_not_found", path=settings_path)
    except Exception as e:
        log.warning("claude_settings_env_failed", error=str(e))


def _set_registry_env(key: str, value: str | None) -> None:
    """Windows-only: write or delete a single env var in HKCU\\Environment."""
    import sys
    if sys.platform != "win32":
        return
    import winreg
    import ctypes
    reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE)
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
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, None)


def _set_user_env(key: str, value: str | None) -> None:
    """Write or delete a single persistent env var (registry + settings.json)."""
    _set_registry_env(key, value)
    _write_claude_settings({key: value})
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
            await providers_service._kill_litellm()
            providers_service._start_litellm(config_path)
            for _ in range(10):
                await asyncio.sleep(0.5)
                status = await get_proxy_status()
                if status.get('running'):
                    break

        fastapi_url = 'http://127.0.0.1:8000'
        api_key = settings.proxy_api_key or 'sk-litellm'
        _set_registry_env('ANTHROPIC_BASE_URL', fastapi_url)
        _set_registry_env('ANTHROPIC_API_KEY', api_key)
        async with _claude_settings_lock:
            _write_claude_settings({'ANTHROPIC_BASE_URL': fastapi_url, 'ANTHROPIC_API_KEY': api_key})
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
        _set_registry_env('ANTHROPIC_BASE_URL', None)
        _set_registry_env('ANTHROPIC_API_KEY', None)
        async with _claude_settings_lock:
            _write_claude_settings({'ANTHROPIC_BASE_URL': None, 'ANTHROPIC_API_KEY': None})
        if stop_litellm:
            await providers_service._kill_litellm()
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
