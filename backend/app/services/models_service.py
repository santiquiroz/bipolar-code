import httpx
from typing import List
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import ModelEntry
from app.services import providers_service

log = get_logger(__name__)


async def list_active_models() -> List[ModelEntry]:
    """
    Obtiene los modelos disponibles desde /v1/models (aliases públicos configurados en litellm)
    y el estado de salud desde /health para marcar cada uno como healthy/unhealthy.
    """
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            models_resp = await client.get(
                f"{settings.proxy_url}/v1/models",
                headers={"Authorization": f"Bearer {settings.proxy_api_key}"},
            )
            models_resp.raise_for_status()
            models_data = models_resp.json()

            try:
                health_resp = await client.get(f"{settings.proxy_url}/health")
                health_data = health_resp.json() if health_resp.status_code == 200 else {}
            except Exception:
                health_data = {}

        unhealthy = {
            ep.get("model_group") or ep.get("model", "")
            for ep in health_data.get("unhealthy_endpoints", [])
        }

        provider = providers_service.get_active_provider()
        provider_id = provider.id if provider else "unknown"
        api_base = provider.api_base if provider else ""

        models = []
        for item in models_data.get("data", []):
            model_id = item.get("id", "")
            if not model_id:
                continue
            models.append(ModelEntry(
                model_name=model_id,
                provider_id=provider_id,
                api_base=api_base,
                is_healthy=model_id not in unhealthy,
            ))

        log.info("active_models_listed", total=len(models))
        return models
    except Exception as e:
        log.error("list_active_models_error", error=str(e))
        return []
