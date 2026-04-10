"""
Lee y escribe configuración sensible (.env y YAMLs de config).
Nunca devuelve valores completos de tokens/keys — siempre enmascarados.
"""
import re
import yaml
from pathlib import Path
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

COPILOT_API_BASE = "https://api.business.githubcopilot.com"
COPILOT_HEADERS = {
    "Copilot-Integration-Id": "vscode-chat",
    "Editor-Version": "vscode/1.85.0",
}
MODEL_INFO = {"supports_response_api": False, "supports_vision": False}

# Alias que el proxy siempre expone internamente
PROXY_MODEL_ALIASES = ["claude-sonnet-4-6", "claude-opus-4-6", "gpt-4o"]


def _env_path() -> Path:
    return Path(get_settings().litellm_config_dir) / ".env"


def _config_path(name: str) -> Path:
    return Path(get_settings().litellm_config_dir) / f"config-{name}.yaml"


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]


def read_env_masked() -> dict[str, str]:
    """Devuelve todas las variables del .env con valores enmascarados."""
    result = {}
    try:
        for line in _env_path().read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            result[key.strip()] = _mask(val.strip())
    except Exception as e:
        log.error("read_env_masked_error", error=str(e))
    return result


def write_env_key(key: str, value: str) -> None:
    """Actualiza o agrega una variable en el .env."""
    env_path = _env_path()
    content = env_path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(content):
        content = pattern.sub(f"{key}={value}", content)
        log.info("env_key_updated", key=key)
    else:
        content = content.rstrip("\n") + f"\n{key}={value}\n"
        log.info("env_key_added", key=key)
    env_path.write_text(content, encoding="utf-8")


def set_copilot_model(copilot_model_id: str) -> None:
    """
    Reescribe config-copilot.yaml apuntando todos los alias al modelo indicado.
    copilot_model_id: el id que devuelve la API de Copilot, ej. 'claude-sonnet-4.6'
    """
    config = {
        "model_list": [
            {
                "model_name": alias,
                "litellm_params": {
                    "model": f"openai/{copilot_model_id}",
                    "api_base": COPILOT_API_BASE,
                    "api_key": "os.environ/COPILOT_SESSION_TOKEN",
                    "extra_headers": COPILOT_HEADERS,
                },
                "model_info": MODEL_INFO,
            }
            for alias in PROXY_MODEL_ALIASES
        ],
        "litellm_settings": {
            "drop_params": True,
            "use_chat_completions_url_for_anthropic_messages": True,
        },
    }
    path = _config_path("copilot")
    path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True), encoding="utf-8")
    log.info("copilot_config_updated", model=copilot_model_id)


def get_active_copilot_model() -> str:
    """Lee el modelo activo en config-copilot.yaml."""
    try:
        raw = yaml.safe_load(_config_path("copilot").read_text(encoding="utf-8"))
        first = raw["model_list"][0]["litellm_params"]["model"]
        # "openai/claude-sonnet-4.6" → "claude-sonnet-4.6"
        return first.removeprefix("openai/")
    except Exception as e:
        log.error("get_active_copilot_model_error", error=str(e))
        return "unknown"
