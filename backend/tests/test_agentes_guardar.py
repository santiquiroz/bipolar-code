"""El «habilitado» de los agentes CLI no se pierde: el PUT persiste y el registro es robusto."""
import json

import pytest
from fastapi.testclient import TestClient

from app.models.provider import ProviderRegistry
from app.models.smart import DEFAULT_CLI_AGENTS
from app.services import providers_service

# /api/* solo acepta clientes de loopback/LAN/Tailscale; el host por defecto "testclient" no es una IP
LOCAL_CLIENT = ("127.0.0.1", 50000)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    from app.core import config as cfg

    real_settings = cfg.get_settings()

    class FakeSettings:
        litellm_config_dir = str(tmp_path)
        ui_api_key = real_settings.ui_api_key

    monkeypatch.setattr(cfg, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(providers_service, "get_settings", lambda: FakeSettings())
    return tmp_path


@pytest.fixture
def client():
    from app.core.config import get_settings
    from app.main import app

    return TestClient(app, client=LOCAL_CLIENT, headers={"x-api-key": get_settings().ui_api_key})


def _legacy_json() -> dict:
    return {
        "active_provider_id": "copilot",
        "providers": [
            {"id": "copilot", "name": "Copilot", "api_base": "https://api.business.githubcopilot.com", "active_model": "gpt-4o"},
            {"id": "ollama", "name": "Ollama", "api_base": "http://127.0.0.1:11434", "active_model": "llama3.2"},
        ],
    }


def test_put_config_persists_enabled_agents_across_restart(client, config_dir):
    agents = [dict(d, enabled=(d["id"] in ("claude", "codex"))) for d in DEFAULT_CLI_AGENTS]
    resp = client.put("/api/smart/config", json={
        "cli_agents": agents,
        "delegation": {"enabled": True, "workspace_allowlist": []},
    })
    assert resp.status_code == 200
    assert {a["id"] for a in resp.json()["cli_agents"] if a["enabled"]} == {"claude", "codex"}
    fresh = providers_service.load_registry()  # simula reinicio: nueva lectura desde disco
    assert {a.id for a in fresh.cli_agents if a.enabled} == {"claude", "codex"}
    assert fresh.delegation.enabled is True


def test_save_registry_writes_valid_json_without_temp_leftovers(config_dir):
    registry = providers_service.load_registry()
    providers_service.save_registry(registry)
    raw = (config_dir / "providers.json").read_text(encoding="utf-8")
    assert ProviderRegistry(**json.loads(raw)).active_provider_id == registry.active_provider_id
    assert list(config_dir.glob("providers.json.tmp-*")) == []


def test_load_registry_corrupt_file_creates_backup_and_keeps_original(config_dir):
    bad = "{ este providers.json está corrupto {{{"
    target = config_dir / "providers.json"
    target.write_text(bad, encoding="utf-8")
    registry = providers_service.load_registry()
    assert registry.providers  # devuelve la configuración por defecto
    backups = sorted(config_dir.glob("providers.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == bad
    assert target.read_text(encoding="utf-8") == bad  # no pisa el original
    providers_service.load_registry()  # segunda lectura: no destruye el respaldo ni el original
    rest = sorted(config_dir.glob("providers.json.corrupt-*"))
    assert rest and all(b.read_text(encoding="utf-8") == bad for b in rest)
    assert target.read_text(encoding="utf-8") == bad


def test_load_registry_retries_once_after_transient_read_error(config_dir, monkeypatch):
    from pathlib import Path

    (config_dir / "providers.json").write_text(json.dumps(_legacy_json()), encoding="utf-8")
    real_read_text = Path.read_text
    attempts = {"n": 0}

    def flaky_read_text(self, *args, **kwargs):
        if self.name == "providers.json" and attempts["n"] == 0:
            attempts["n"] += 1
            raise OSError("lectura a mitad de una escritura")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    registry = providers_service.load_registry()
    assert attempts["n"] == 1
    assert {p.id for p in registry.providers} >= {"copilot", "ollama"}
    assert list(config_dir.glob("providers.json.corrupt-*")) == []
