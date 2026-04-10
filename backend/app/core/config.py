from pydantic_settings import BaseSettings
from functools import lru_cache
import os

# Allow override via env var; default to C:/litellm for backwards compatibility
_DEFAULT_CONFIG_DIR = os.environ.get("LITELLM_CONFIG_DIR", "C:/litellm")
_ENV_FILE = os.path.join(_DEFAULT_CONFIG_DIR, ".env")


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
