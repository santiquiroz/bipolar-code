from unittest.mock import patch

import psutil

from app.services import process_identity
from app.services.process_identity import ProcessIdentity, cmdline_has_port, matches_identity, owned_process

LLAMA = ProcessIdentity(name_prefixes=("llama-server",), port=4002)
LITELLM = ProcessIdentity(name_prefixes=("python", "litellm"), port=4001, cmdline_marker="litellm")


def test_cmdline_has_port_accepts_separate_and_inline_forms():
    assert cmdline_has_port(["llama-server", "--port", "4002"], 4002)
    assert cmdline_has_port(["llama-server", "--port=4002"], 4002)


def test_cmdline_has_port_ignores_same_number_in_other_flags():
    assert not cmdline_has_port(["llama-server", "--ctx-size", "4002", "--port", "9999"], 4002)
    assert not cmdline_has_port(["llama-server", "--port"], 4002)


def test_matches_identity_requires_name_prefix_case_insensitively():
    assert matches_identity("Llama-Server.exe", ["x", "--port", "4002"], LLAMA)
    assert not matches_identity("notepad.exe", ["x", "--port", "4002"], LLAMA)


def test_matches_identity_requires_cmdline_marker():
    assert matches_identity("python.exe", ["python.exe", "litellm.exe", "--port", "4001"], LITELLM)
    assert not matches_identity("python.exe", ["python.exe", "-m", "http.server", "--port", "4001"], LITELLM)


def test_owned_process_returns_none_when_process_is_gone():
    with patch.object(process_identity.psutil, "Process", side_effect=psutil.NoSuchProcess(4321)):
        assert owned_process(4321, LLAMA) is None


def test_owned_process_returns_none_when_cmdline_is_access_denied():
    with patch.object(process_identity.psutil, "Process") as process_mock:
        process_mock.return_value.name.return_value = "llama-server.exe"
        process_mock.return_value.cmdline.side_effect = psutil.AccessDenied(4321)
        assert owned_process(4321, LLAMA) is None
