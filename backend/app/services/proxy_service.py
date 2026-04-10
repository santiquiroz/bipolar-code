import httpx
from typing import Optional
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import ProxyStatus, BackendType

log = get_logger(__name__)

BACKEND_CONFIGS = {
    BackendType.copilot: "config-copilot.yaml",
    BackendType.claude: "config-claude.yaml",
    BackendType.gemma: "config-gemma.yaml",
}


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


async def get_proxy_status() -> ProxyStatus:
    settings = get_settings()
    health = await get_proxy_health()
    running = bool(health)

    active_backend = _detect_backend(health)
    config_file = BACKEND_CONFIGS.get(active_backend, "unknown")

    return ProxyStatus(
        running=running,
        port=int(settings.proxy_url.split(":")[-1]),
        active_backend=active_backend,
        config_file=config_file,
        healthy_models=health.get("healthy_count", 0),
        unhealthy_models=health.get("unhealthy_count", 0),
    )


def _detect_backend(health: dict) -> BackendType:
    endpoints = health.get("healthy_endpoints", [])
    if not endpoints:
        return BackendType.copilot
    first_base = endpoints[0].get("api_base", "")
    if "githubcopilot" in first_base:
        return BackendType.copilot
    if "anthropic" in first_base or "api.anthropic" in first_base:
        return BackendType.claude
    if "localhost:1234" in first_base:
        return BackendType.gemma
    return BackendType.custom


async def switch_backend(backend: BackendType) -> dict:
    import subprocess, sys
    config_file = BACKEND_CONFIGS.get(backend)
    if not config_file:
        log.error("switch_backend_unknown", backend=backend)
        raise ValueError(f"Unknown backend: {backend}")

    settings = get_settings()
    config_path = f"{settings.litellm_config_dir}/{config_file}"
    log.info("switching_backend", backend=backend, config=config_path)

    result = subprocess.run(
        ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File",
         f"{settings.litellm_config_dir}/switch.ps1", backend.value],
        capture_output=True, text=True, timeout=30
    )

    if result.returncode != 0:
        log.error("switch_backend_failed", stderr=result.stderr, stdout=result.stdout)
        raise RuntimeError(f"Switch failed: {result.stderr}")

    log.info("switch_backend_success", backend=backend, output=result.stdout.strip())
    return {"switched_to": backend, "output": result.stdout.strip()}
