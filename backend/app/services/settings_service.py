"""
Gestión del .env: lectura enmascarada y escritura segura.
No contiene lógica específica de ningún proveedor.
"""
import re
from pathlib import Path
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_ENV_KEY_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_FORBIDDEN_VALUE_CHARS = ("\r", "\n", "\0")
_RESERVED_KEYS = frozenset({"PATH", "PYTHONPATH", "PYTHONHOME", "LITELLM_CONFIG_DIR", "COMSPEC", "PATHEXT"})


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


def is_valid_env_key(key: str) -> bool:
    return _ENV_KEY_PATTERN.fullmatch(key) is not None


def validate_env_entry(key: str, value: str) -> None:
    if not is_valid_env_key(key):
        raise ValueError(f"Nombre de variable inválido: {key!r} (usar A-Z, 0-9 y _)")
    if key in _RESERVED_KEYS:
        raise ValueError(f"La variable {key} es reservada y no se puede modificar")
    if any(ch in value for ch in _FORBIDDEN_VALUE_CHARS):
        raise ValueError("El valor no puede contener saltos de línea ni caracteres nulos")


def _read_env_content(env_path: Path) -> str:
    if not env_path.exists():
        return ""
    return env_path.read_text(encoding="utf-8")


def _upsert_env_line(content: str, key: str, value: str) -> str:
    line = f"{key}={value}"
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(content):
        log.info("env_key_updated", key=key)
        # Callable replacement: the value is literal text, not a regex template (Windows paths)
        return pattern.sub(lambda _: line, content)
    log.info("env_key_added", key=key)
    existing = content.rstrip("\n")
    return f"{existing}\n{line}\n" if existing else f"{line}\n"


def write_env_key(key: str, value: str) -> None:
    """Actualiza o agrega una variable en el .env."""
    validate_env_entry(key, value)
    env_path = _env_path()
    env_path.write_text(_upsert_env_line(_read_env_content(env_path), key, value), encoding="utf-8")

    # Aplicar en caliente: settings está lru_cache'd y pydantic no relee el .env
    import os
    from app.core.config import get_settings
    os.environ[key] = value
    get_settings.cache_clear()
