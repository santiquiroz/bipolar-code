import httpx
from typing import List
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import ModelEntry
from app.services import providers_service

log = get_logger(__name__)


async def list_active_models() -> List[ModelEntry]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{settings.proxy_url}/health")
            resp.raise_for_status()
            data = resp.json()

        registry = providers_service.load_registry()

        def _infer_provider(api_base: str) -> str:
            for p in registry.providers:
                if p.api_base.rstrip("/") in api_base or api_base in p.api_base.rstrip("/"):
                    return p.id
            return "unknown"

        models = []
        for ep in data.get("healthy_endpoints", []):
            models.append(ModelEntry(
                model_name=ep.get("model", "unknown"),
                provider_id=_infer_provider(ep.get("api_base", "")),
                api_base=ep.get("api_base", ""),
                is_healthy=True,
            ))
        for ep in data.get("unhealthy_endpoints", []):
            models.append(ModelEntry(
                model_name=ep.get("model", "unknown"),
                provider_id=_infer_provider(ep.get("api_base", "")),
                api_base=ep.get("api_base", ""),
                is_healthy=False,
            ))

        log.info("active_models_listed", total=len(models))
        return models
    except Exception as e:
        log.error("list_active_models_error", error=str(e))
        return []
