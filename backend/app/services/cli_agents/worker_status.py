"""Estado final que declara el worker en su última línea `STATUS: ...`."""
import re

STOP_STATUSES = ("NEEDS_CONTEXT", "BLOCKED")
PARSE_WINDOW_CHARS = 4000
DETAIL_CAP = 1000

_STATUS_RE = re.compile(
    r"^[\s>*`_-]*STATUS:\s*(DONE_WITH_CONCERNS|DONE|NEEDS_CONTEXT|BLOCKED)\b[*`_]*\s*(?:[:\-—]\s*)?(.*?)[\s*`_]*$",
    re.IGNORECASE,
)


def parse_worker_status(text: str) -> tuple[str, str]:
    if not isinstance(text, str) or not text:
        return "", ""
    # el estado va al final; acotar evita confundirlo con un STATUS citado en medio de la salida
    for line in reversed(text[-PARSE_WINDOW_CHARS:].splitlines()):
        match = _STATUS_RE.match(line)
        if match:
            return match.group(1).upper(), match.group(2).strip()[:DETAIL_CAP]
    return "", ""
