"""API de llaves del pool: validación de nombres y estado por slot sin exponer valores."""
import pytest
from fastapi.testclient import TestClient

from app.models.provider import Provider, ProviderRegistry
from app.services import health_service, providers_service

# /api/* pasa por el guard del plano de control; "testclient" no es IP y da 403
LOCAL_CLIENT = ("127.0.0.1", 50000)


@pytest.fixture
def api(tmp_path, monkeypatch):
    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(health_service, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    monkeypatch.setenv("P1_KEY", "secret-one")
    monkeypatch.setenv("P1_KEY_2", "secret-two")
    registry = ProviderRegistry(active_provider_id="p1", providers=[
        Provider(id="p1", name="p1", api_base="https://p1.example.com/v1", auth_env_var="P1_KEY", extra_auth_env_vars=["P1_KEY_2"]),
    ])
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)
    saved = {}

    def fake_update(pid, updates):
        saved.update(updates)
        return registry.providers[0].model_copy(update=updates)

    monkeypatch.setattr(providers_service, "update_provider", fake_update)
    from app.core.config import get_settings
    from app.main import app
    yield TestClient(app, client=LOCAL_CLIENT, headers={"x-api-key": get_settings().ui_api_key}), saved
    health_service.reload_for_tests()


def test_credentials_endpoint_lists_slots_without_values(api):
    client, _ = api
    resp = client.get("/api/providers/p1/credentials")
    assert resp.status_code == 200
    slots = resp.json()["slots"]
    assert [(s["slot"], s["env_var"], s["has_value"], s["state"]) for s in slots] == [
        (0, "P1_KEY", True, "available"), (1, "P1_KEY_2", True, "available"),
    ]
    assert "secret" not in resp.text


def test_credentials_endpoint_lists_missing_extras(api, monkeypatch):
    client, _ = api
    monkeypatch.delenv("P1_KEY_2")
    resp = client.get("/api/providers/p1/credentials")
    assert resp.json()["missing"] == ["P1_KEY_2"]


def test_patch_accepts_valid_extra_env_vars(api):
    client, saved = api
    resp = client.patch("/api/providers/p1", json={"extra_auth_env_vars": ["P1_KEY_2", "P1_KEY_3"]})
    assert resp.status_code == 200
    assert saved["extra_auth_env_vars"] == ["P1_KEY_2", "P1_KEY_3"]


@pytest.mark.parametrize("names", [["bad name"], ["P1_KEY"], ["X", "X"], [f"K_{i}" for i in range(11)]])
def test_patch_rejects_invalid_extra_env_vars(api, names):
    client, _ = api
    resp = client.patch("/api/providers/p1", json={"extra_auth_env_vars": names})
    assert resp.status_code == 400


def test_credentials_unknown_provider_404(api):
    client, _ = api
    assert client.get("/api/providers/nope/credentials").status_code == 404
