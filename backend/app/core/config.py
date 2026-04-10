from pydantic_settings import BaseSettings
from functools import lru_cache


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
    litellm_config_dir: str = "C:/litellm"

    model_config = {"env_file": "C:/litellm/.env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
