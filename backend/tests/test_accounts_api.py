"""API de cuentas: rutas, códigos y base_url en modo proxy."""
import pytest
from fastapi.testclient import TestClient

from app.services import accounts_service

# /api/* solo acepta loopback/LAN; el host por defecto "testclient" no es una IP
LOCAL_CLIENT = ("127.0.0.1", 50000)


@pytest.fixture
def client(monkeypatch):
    from app.core.config import get_settings
    from app.main import app
    return TestClient(app, client=LOCAL_CLIENT, headers={"x-api-key": get_settings().ui_api_key})


def test_pick_proxy_includes_base_url(client, monkeypatch):
    monkeypatch.setattr(accounts_service, "pick_account", lambda adapter: {"mode": "proxy"})
    resp = client.get("/api/accounts/pick?adapter=claude")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "proxy" and resp.json()["base_url"].startswith("http://testserver")


def test_create_rejects_unsupported(client, monkeypatch):
    def boom(base, label):
        raise accounts_service.AccountError("no admite cuentas")
    monkeypatch.setattr(accounts_service, "create_account", boom)
    resp = client.post("/api/accounts", json={"base": "muse", "label": "x"})
    assert resp.status_code == 400


def test_usage_unknown_agent_404(client, monkeypatch):
    def missing(agent_id, rate_limits, now=None):
        raise KeyError(agent_id)
    monkeypatch.setattr(accounts_service, "record_usage", missing)
    resp = client.post("/api/accounts/claude-9/usage", json={"rate_limits": {}})
    assert resp.status_code == 404


def test_accounts_require_api_key():
    from app.main import app
    assert TestClient(app, client=LOCAL_CLIENT).get("/api/accounts").status_code == 401
