import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from app.models.provider import Provider, ProviderRegistry
from app.services.providers_service import (
    generate_litellm_config,
    detect_active_provider_from_health,
    PROXY_ALIASES,
    _DEFAULTS,
)


def test_proxy_aliases_not_empty():
    assert len(PROXY_ALIASES) > 0


def test_defaults_have_required_fields():
    for d in _DEFAULTS:
        p = Provider(**d)
        assert p.id
        assert p.name
        assert p.api_base


def test_generate_config_creates_all_aliases(tmp_path):
    provider = Provider(
        id="testprovider",
        name="Test",
        api_base="https://test.example.com",
        litellm_prefix="openai",
        auth_env_var="TEST_KEY",
        active_model="test-model-1",
    )
    with patch("app.services.providers_service._config_dir", return_value=tmp_path):
        config_path = generate_litellm_config(provider)

    import yaml
    data = yaml.safe_load(config_path.read_text())
    model_names = [e["model_name"] for e in data["model_list"]]
    assert set(model_names) == set(PROXY_ALIASES)


def test_generate_config_model_ref(tmp_path):
    provider = Provider(
        id="myprov",
        name="My Prov",
        api_base="https://api.example.com",
        litellm_prefix="anthropic",
        auth_env_var="MY_KEY",
        active_model="my-model",
    )
    with patch("app.services.providers_service._config_dir", return_value=tmp_path):
        config_path = generate_litellm_config(provider)

    import yaml
    data = yaml.safe_load(config_path.read_text())
    assert data["model_list"][0]["litellm_params"]["model"] == "anthropic/my-model"


def test_detect_active_provider_by_api_base():
    registry = ProviderRegistry(
        active_provider_id="copilot",
        providers=[
            Provider(id="copilot", name="Copilot", api_base="https://api.business.githubcopilot.com"),
            Provider(id="openai", name="OpenAI", api_base="https://api.openai.com/v1"),
        ],
    )
    health = {"healthy_endpoints": [{"api_base": "https://api.openai.com/v1"}]}
    with patch("app.services.providers_service.load_registry", return_value=registry):
        result = detect_active_provider_from_health(health)
    assert result == "openai"


def test_detect_active_provider_empty_health_returns_active():
    registry = ProviderRegistry(active_provider_id="copilot", providers=[])
    with patch("app.services.providers_service.load_registry", return_value=registry):
        result = detect_active_provider_from_health({})
    assert result == "copilot"


def test_start_litellm_ps1_skips_env_names_that_are_not_valid_identifiers(tmp_path, monkeypatch):
    import subprocess
    import sys
    from types import SimpleNamespace
    from app.services import providers_service

    (tmp_path / ".env").write_text(
        "GOOD_API_KEY=ok\nX; Start-Process calc; $y_TOKEN=evil\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        providers_service, "get_settings", lambda: SimpleNamespace(litellm_config_dir=str(tmp_path))
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    popen = MagicMock()
    monkeypatch.setattr(subprocess, "Popen", popen)

    providers_service._start_litellm(tmp_path / "config.yaml")

    script = (tmp_path / "_start_litellm.ps1").read_text(encoding="utf-8")
    assert "$env:GOOD_API_KEY = 'ok'" in script
    assert "start-process calc" not in script.lower()
    popen.assert_called_once()


def _fake_process(pid, name, cmdline):
    proc = MagicMock(pid=pid)
    proc.name.return_value = name
    proc.cmdline.return_value = cmdline
    proc.info = {"pid": pid, "name": name, "cmdline": cmdline}
    return proc


def _litellm_cmdline(port):
    return ["python.exe", "C:/venv/Scripts/litellm.exe", "--config", "c.yaml", "--port", str(port)]


@pytest.fixture
def litellm_pid_file(tmp_path):
    from app.services import providers_service

    path = tmp_path / "litellm.pid"
    path.write_text("4321", encoding="utf-8")
    refused = AsyncMock(side_effect=ConnectionRefusedError)
    with (
        patch.object(providers_service, "_pid_file_path", return_value=path),
        patch.object(providers_service.asyncio, "open_connection", refused),
    ):
        yield path


@pytest.mark.asyncio
async def test_kill_litellm_does_not_kill_foreign_process_behind_stale_pid(litellm_pid_file):
    from app.services import process_identity, providers_service

    notepad = _fake_process(4321, "notepad.exe", ["notepad.exe"])

    with (
        patch.object(process_identity.psutil, "Process", return_value=notepad),
        patch.object(process_identity.psutil, "process_iter", return_value=[notepad]),
    ):
        await providers_service._kill_litellm()

    notepad.kill.assert_not_called()
    assert not litellm_pid_file.exists()


@pytest.mark.asyncio
async def test_kill_litellm_kills_owned_process_from_pid_file(litellm_pid_file):
    from app.services import process_identity, providers_service

    launcher = _fake_process(4321, "litellm.exe", ["litellm.exe", "--config", "c.yaml", "--port", "4001"])

    with (
        patch.object(process_identity.psutil, "Process", return_value=launcher),
        patch.object(process_identity.psutil, "process_iter", return_value=[]),
    ):
        await providers_service._kill_litellm()

    launcher.kill.assert_called_once_with()
    assert not litellm_pid_file.exists()


@pytest.mark.asyncio
async def test_kill_litellm_fallback_only_kills_litellm_on_bipolar_port(litellm_pid_file):
    from app.services import process_identity, providers_service

    litellm_pid_file.unlink()
    ours = _fake_process(100, "python.exe", _litellm_cmdline(4001))
    other_litellm = _fake_process(200, "python.exe", _litellm_cmdline(5555))
    unrelated = _fake_process(300, "python.exe", ["python.exe", "-m", "http.server", "--port", "4001"])

    with patch.object(process_identity.psutil, "process_iter", return_value=[ours, other_litellm, unrelated]):
        await providers_service._kill_litellm()

    ours.kill.assert_called_once_with()
    other_litellm.kill.assert_not_called()
    unrelated.kill.assert_not_called()
