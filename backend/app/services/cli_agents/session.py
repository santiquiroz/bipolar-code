"""Sesión de Windows del proceso: los sandbox de Muse y Codex necesitan una sesión interactiva."""
import functools
import sys
from typing import Optional


def windows_session_id() -> Optional[int]:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    session_id = wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(session_id)):
        return None
    return session_id.value


@functools.lru_cache(maxsize=1)
def interactive_session() -> bool:
    if sys.platform != "win32":
        return True
    # None = el chequeo falló: no bloquea agentes por eso
    return windows_session_id() != 0
