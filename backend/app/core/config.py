from pydantic_settings import BaseSettings
from functools import lru_cache
import os
import sys
from pathlib import Path


def _default_config_dir() -> str:
    if "LITELLM_CONFIG_DIR" in os.environ:
        return os.environ["LITELLM_CONFIG_DIR"]
    if sys.platform == "win32":
        return "C:/litellm"
    return str(Path.home() / ".litellm")


_DEFAULT_CONFIG_DIR = _default_config_dir()
_ENV_FILE = os.path.join(_DEFAULT_CONFIG_DIR, ".env")

# Crear el directorio si no existe (primera ejecución en Linux/Mac)
Path(_DEFAULT_CONFIG_DIR).mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    # Proxy
    proxy_url: str = "http://localhost:4001"
    proxy_api_key: str = "sk-litellm"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_real_api_key: str = ""

    # GitHub / Copilot
    github_token: str = ""
    github_oauth_token: str = ""
    copilot_session_token: str = ""

    # App
    env: str = "development"
    log_level: str = "INFO"
    litellm_config_dir: str = _DEFAULT_CONFIG_DIR

    model_config = {"env_file": _ENV_FILE, "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
