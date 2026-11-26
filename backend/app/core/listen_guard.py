import asyncio
import os
import sys
from collections.abc import Callable
from typing import Any

LISTEN_SOCKET_LOST_EXIT_CODE = 3
ACCEPT_FAILED_MESSAGE = "Accept failed on a socket"

ExceptionHandler = Callable[[asyncio.AbstractEventLoop, dict[str, Any]], object]


def is_listen_socket_lost(context: dict[str, Any]) -> bool:
    return context.get("message") == ACCEPT_FAILED_MESSAGE


def flush_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass


def make_listen_guard(
    previous: ExceptionHandler | None,
    on_lost: Callable[[dict[str, Any]], None],
    exit_process: Callable[[int], object] = os._exit,
) -> ExceptionHandler:
    def handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if previous is None:
            loop.default_exception_handler(context)
        else:
            previous(loop, context)
        if is_listen_socket_lost(context):
            on_lost(context)
            flush_streams()
            exit_process(LISTEN_SOCKET_LOST_EXIT_CODE)

    return handler


def install_listen_guard(
    loop: asyncio.AbstractEventLoop,
    on_lost: Callable[[dict[str, Any]], None],
    exit_process: Callable[[int], object] = os._exit,
) -> None:
    loop.set_exception_handler(make_listen_guard(loop.get_exception_handler(), on_lost, exit_process))