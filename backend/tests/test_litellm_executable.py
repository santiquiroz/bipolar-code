import sys
from pathlib import Path

import pytest

from app.services import providers_service


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


def _use_python(monkeypatch: pytest.MonkeyPatch, python: Path, which: str | None) -> None:
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(providers_service.shutil, "which", lambda _name: which)


def test_venv_layout_prefers_sibling_over_path(windows, tmp_path, monkeypatch) -> None:
    scripts = tmp_path / ".venv" / "Scripts"
    litellm = _touch(scripts / "litellm.exe")
    _use_python(monkeypatch, scripts / "python.exe", r"C:\Global\Scripts\litellm.exe")

    assert providers_service._litellm_executable() == str(litellm)


def test_base_layout_picks_scripts_dir(windows, tmp_path, monkeypatch) -> None:
    litellm = _touch(tmp_path / "Python311" / "Scripts" / "litellm.exe")
    _use_python(monkeypatch, tmp_path / "Python311" / "python.exe", r"C:\Other\litellm.exe")

    assert providers_service._litellm_executable() == str(litellm)


def test_nothing_local_uses_which(windows, tmp_path, monkeypatch) -> None:
    _use_python(monkeypatch, tmp_path / "python.exe", r"C:\Global\Scripts\litellm.exe")

    assert providers_service._litellm_executable() == r"C:\Global\Scripts\litellm.exe"


def test_nothing_at_all_windows_falls_back_to_scripts(windows, tmp_path, monkeypatch) -> None:
    _use_python(monkeypatch, tmp_path / "python.exe", None)

    assert providers_service._litellm_executable() == str(tmp_path / "Scripts" / "litellm.exe")


def test_nothing_at_all_posix_falls_back_to_sibling(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _use_python(monkeypatch, tmp_path / "bin" / "python", None)

    assert providers_service._litellm_executable() == str(tmp_path / "bin" / "litellm")
