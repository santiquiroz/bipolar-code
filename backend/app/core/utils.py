import re


def sanitize_error(msg: str) -> str:
    """Elimina URLs internas y rutas del sistema de mensajes de error."""
    msg = re.sub(r'https?://127\.0\.0\.1:\d+\S*', '[proxy]', msg)
    msg = re.sub(r'https?://localhost:\d+\S*', '[proxy]', msg)
    msg = re.sub(r'[A-Za-z]:\\[^\s"\']+', '[path]', msg)
    msg = re.sub(r'\\\\[^\s"\']+', '[path]', msg)  # UNC paths \\server\share
    msg = re.sub(r'/(?:home|usr|var|etc|tmp)/\S+', '[path]', msg)
    return msg
