import re
from pathlib import Path


def tail_file(path: Path, lines: int) -> list[str]:
    """Últimas N líneas leyendo solo el bloque final (64KB) del archivo."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            data = f.read().decode("utf-8", errors="replace")
        return data.splitlines()[-lines:]
    except OSError:
        return []


def sanitize_error(msg: str) -> str:
    """Elimina URLs internas y rutas del sistema de mensajes de error."""
    msg = re.sub(r'https?://127\.0\.0\.1:\d+\S*', '[proxy]', msg)
    msg = re.sub(r'https?://localhost:\d+\S*', '[proxy]', msg)
    msg = re.sub(r'[A-Za-z]:\\[^\s"\']+', '[path]', msg)
    msg = re.sub(r'\\\\[^\s"\']+', '[path]', msg)  # UNC paths \\server\share
    msg = re.sub(r'/(?:home|usr|var|etc|tmp)/\S+', '[path]', msg)
    return msg
