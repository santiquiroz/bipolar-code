import httpx
from typing import List
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.schemas import CopilotModel

log = get_logger(__name__)

COPILOT_MODELS_URL = "https://api.business.githubcopilot.com/models"
GITHUB_MODELS_URL = "https://models.inference.ai.azure.com/models"


async def list_copilot_models() -> List[CopilotModel]:
    settings = get_settings()
    token = settings.copilot_session_token
    if not token:
        log.warning("copilot_token_missing")
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "Copilot-Integration-Id": "vscode-chat",
        "Editor-Version": "vscode/1.85.0",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(COPILOT_MODELS_URL, headers=headers)
            resp.raise_for_status()
            raw = resp.json()
            models = _parse_copilot_models(raw)
            log.info("copilot_models_fetched", count=len(models))
            return models
    except httpx.HTTPStatusError as e:
        log.error("copilot_models_http_error", status=e.response.status_code, body=e.response.text)
        return []
    except Exception as e:
        log.error("copilot_models_error", error=str(e))
        return []


async def list_github_models() -> List[CopilotModel]:
    settings = get_settings()
    token = settings.github_token
    if not token:
        log.warning("github_token_missing")
        return []

    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(GITHUB_MODELS_URL, headers=headers)
            resp.raise_for_status()
            raw = resp.json()
            models = [
                CopilotModel(id=m.get("id", ""), name=m.get("name", m.get("id", "")),
                             vendor=m.get("publisher", None))
                for m in (raw if isinstance(raw, list) else raw.get("data", []))
            ]
            log.info("github_models_fetched", count=len(models))
            return models
    except Exception as e:
        log.error("github_models_error", error=str(e))
        return []


def _parse_copilot_models(raw: dict | list) -> List[CopilotModel]:
    items = raw if isinstance(raw, list) else raw.get("data", raw.get("models", []))
    result = []
    for m in items:
        result.append(CopilotModel(
            id=m.get("id", ""),
            name=m.get("name", m.get("id", "")),
            vendor=m.get("vendor", {}).get("name") if isinstance(m.get("vendor"), dict) else m.get("vendor"),
            version=m.get("version"),
            capabilities=m.get("capabilities"),
        ))
    return result
