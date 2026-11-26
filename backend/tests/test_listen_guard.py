import asyncio
from unittest.mock import Mock

from app.core.listen_guard import (
    install_listen_guard,
    is_listen_socket_lost,
    make_listen_guard,
)


def test_is_listen_socket_lost_matches_only_accept_failure_message():
    assert is_listen_socket_lost({"message": "Accept failed on a socket"}) is True
    assert is_listen_socket_lost({"message": "Task exception was never retrieved"}) is False
    assert is_listen_socket_lost({}) is False


def test_handler_chains_previous_and_exits_for_lost_listen_socket():
    loop = object()
    context = {"message": "Accept failed on a socket"}
    previous = Mock()
    on_lost = Mock()
    exit_process = Mock()

    handler = make_listen_guard(previous, on_lost, exit_process)
    handler(loop, context)

    previous.assert_called_once_with(loop, context)
    on_lost.assert_called_once_with(context)
    exit_process.assert_called_once_with(3)


def test_handler_does_not_exit_for_other_contexts():
    loop = object()
    context = {"message": "Task exception was never retrieved"}
    previous = Mock()
    on_lost = Mock()
    exit_process = Mock()

    handler = make_listen_guard(previous, on_lost, exit_process)
    handler(loop, context)

    previous.assert_called_once_with(loop, context)
    on_lost.assert_not_called()
    exit_process.assert_not_called()


def test_handler_uses_default_exception_handler_when_no_previous_handler():
    class FakeLoop:
        def __init__(self):
            self.contexts = []

        def default_exception_handler(self, context):
            self.contexts.append(context)

    loop = FakeLoop()
    context = {"message": "Task exception was never retrieved"}

    handler = make_listen_guard(None, Mock(), Mock())
    handler(loop, context)

    assert loop.contexts == [context]


def test_install_listen_guard_handles_accept_failure_on_real_loop():
    loop = asyncio.new_event_loop()
    contexts = []
    exit_codes = []
    context = {
        "message": "Accept failed on a socket",
        "exception": OSError(64, "red no disponible"),
    }
    try:
        install_listen_guard(loop, contexts.append, exit_process=exit_codes.append)
        loop.call_exception_handler(context)
    finally:
        loop.close()

    assert exit_codes == [3]
    assert len(contexts) == 1
    assert contexts[0]["message"] == "Accept failed on a socket"


def test_install_listen_guard_chains_existing_loop_handler():
    loop = asyncio.new_event_loop()
    contexts = []
    exit_codes = []
    context = {"message": "Accept failed on a socket"}
    try:
        loop.set_exception_handler(lambda current_loop, received: contexts.append(received))
        install_listen_guard(loop, Mock(), exit_process=exit_codes.append)
        loop.call_exception_handler(context)
    finally:
        loop.close()

    assert contexts == [context]
    assert exit_codes == [3]