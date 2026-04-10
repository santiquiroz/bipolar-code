"""
Gestión del .env: lectura enmascarada y escritura segura.
No contiene lógica específica de ningún proveedor.
"""
import re
from pathlib import Path
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


def _env_path() -> Path:
    return Path(get_settings().litellm_config_dir) / ".env"


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
