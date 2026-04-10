import httpx
from typing import List
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import ModelEntry, BackendType

log = get_logger(__name__)


async def list_active_models() -> List[ModelEntry]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.proxy_url}/health")
            resp.raise_for_status()
            data = resp.json()

        models = []
        for ep in data.get("healthy_endpoints", []):
            models.append(ModelEntry(
                model_name=ep.get("model", "unknown"),
                backend=_infer_backend(ep.get("api_base", "")),
                api_base=ep.get("api_base", ""),
                is_healthy=True,
            ))
        for ep in data.get("unhealthy_endpoints", []):
            models.append(ModelEntry(
                model_name=ep.get("model", "unknown"),
                backend=_infer_backend(ep.get("api_base", "")),
                api_base=ep.get("api_base", ""),
                is_healthy=False,
            ))

        log.info("active_models_listed", total=len(models))
        return models
    except Exception as e:
        log.error("list_active_models_error", error=str(e))
        return []


def _infer_backend(api_base: str) -> BackendType:
    if "githubcopilot" in api_base:
        return BackendType.copilot
    if "anthropic" in api_base:
        return BackendType.claude
    if "localhost:1234" in api_base:
        return BackendType.gemma
    return BackendType.custom
