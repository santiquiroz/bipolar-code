from pydantic_settings import BaseSettings
from functools import lru_cache
import os
import sys
import secrets
from pathlib import Path


def _default_config_dir() -> str:
    if "LITELLM_CONFIG_DIR" in os.environ:
        return os.environ["LITELLM_CONFIG_DIR"]
    if sys.platform == "win32":
        return "C:/litellm"
    return str(Path.home() / ".litellm")


_DEFAULT_CONFIG_DIR = _default_config_dir()
_ENV_FILE = os.path.join(_DEFAULT_CONFIG_DIR, ".env")

Path(_DEFAULT_CONFIG_DIR).mkdir(parents=True, exist_ok=True)


def _generate_api_key() -> str:
    """Genera una clave aleatoria segura y la persiste en el .env."""
    key = "bc-" + secrets.token_hex(24)
    env_path = Path(_ENV_FILE)
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        lines = [l for l in lines if not l.startswith("UI_API_KEY=")]
        lines.append(f"UI_API_KEY={key}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass
    return key


class Settings(BaseSettings):
    # Proxy
    proxy_url: str = "http://127.0.0.1:4001"
    proxy_api_key: str = "sk-litellm"

    # UI auth — si no está en .env, se genera y persiste automáticamente
    ui_api_key: str = ""

    # CORS origins separados por coma, ej: "http://192.168.1.10:8000,http://10.0.0.5:8000"
    # Dejar vacío o "*" para permitir cualquier origen (útil en red local)
    allowed_origins: str = "*"

    # Rate limiting: máx requests por minuto por IP (0 = desactivado)
    rate_limit_rpm: int = 120

    # Compresión semántica: resumir historial viejo con el provider activo
    # al acercarse al límite de contexto, en vez de solo truncar
    semantic_compression: bool = False

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
    s = Settings()
    if not s.ui_api_key:
        key = _generate_api_key()
        object.__setattr__(s, "ui_api_key", key)
    return s
