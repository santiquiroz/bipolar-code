import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.middleware.network_guard import (
    ControlPlaneGuardMiddleware,
    is_allowed_client,
    parse_allowed_networks,
)
from app.models.provider import ProviderRegistry
from app.services import providers_service

DEFAULT_NETWORKS = parse_allowed_networks("")


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.8.9.10", "::1",
    "10.1.2.3", "172.16.0.1", "172.31.255.254", "192.168.1.5",
    "100.64.0.1", "100.101.1.1", "100.127.255.254",
    "169.254.10.20", "fe80::1", "fe80::1%eth0",
    "fd7a:115c:a1e0::1", "fc00::1",
    "::ffff:192.168.1.5", "::ffff:127.0.0.1",
])
def test_default_allows_loopback_private_link_local_and_tailscale(host):
    assert is_allowed_client(host, DEFAULT_NETWORKS)


@pytest.mark.parametrize("host", [
    "8.8.8.8", "172.32.0.1", "100.63.255.255", "100.128.0.1", "2001:4860:4860::8888",
    "::ffff:8.8.8.8", "testclient", "", "not-an-ip",
])
def test_default_rejects_public_and_non_ip_hosts(host):
    assert not is_allowed_client(host, DEFAULT_NETWORKS)


def test_custom_cidrs_replace_the_default():
    networks = parse_allowed_networks("127.0.0.1/32, 203.0.113.0/24")

    assert is_allowed_client("203.0.113.9", networks)
    assert is_allowed_client("127.0.0.1", networks)
    assert not is_allowed_client("192.168.1.5", networks)


def test_invalid_cidr_fails_fast():
    with pytest.raises(ValueError, match="CONTROL_PLANE_ALLOWED_CIDRS"):
        parse_allowed_networks("192.168.0.0/16, lan")


def _guarded_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ControlPlaneGuardMiddleware, allowed_cidrs="")

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.get("/")
    def spa():
        return {"spa": True}

    return app


def test_unknown_client_is_rejected_on_api():
    scope_without_client = TestClient(_guarded_app(), client=("", 0))

    assert scope_without_client.get("/api/ping").status_code == 403


def test_non_api_paths_are_not_guarded():
    public = TestClient(_guarded_app(), client=("8.8.8.8", 1234))

    assert public.get("/").status_code == 200


@pytest.fixture
def ui_key(monkeypatch) -> str:
    monkeypatch.setattr(providers_service, "load_registry", lambda: ProviderRegistry())
    return get_settings().ui_api_key


def _client(host: str, key: str, **headers: str) -> TestClient:
    from app.main import app
    return TestClient(app, client=(host, 1234), raise_server_exceptions=False,
                      headers={"x-api-key": key, **headers})


def test_public_ip_gets_403_on_control_plane(ui_key):
    resp = _client("8.8.8.8", ui_key).get("/api/providers")

    assert resp.status_code == 403


def test_public_ip_gets_403_on_health(ui_key):
    assert _client("8.8.8.8", ui_key).get("/api/health").status_code == 403


def test_lan_client_with_key_reaches_control_plane(ui_key):
    assert _client("192.168.1.5", ui_key).get("/api/providers").status_code == 200


def test_tailscale_client_with_key_reaches_control_plane(ui_key):
    assert _client("100.101.1.1", ui_key).get("/api/providers").status_code == 200


def test_forwarded_for_does_not_bypass_the_guard(ui_key):
    resp = _client("8.8.8.8", ui_key, **{"X-Forwarded-For": "127.0.0.1"}).get("/api/providers")

    assert resp.status_code == 403


def test_gateway_v1_is_not_guarded(ui_key, monkeypatch):
    from app.api import openai_compat
    monkeypatch.setattr(openai_compat.providers_service, "load_registry", lambda: ProviderRegistry())

    resp = _client("8.8.8.8", ui_key).get("/v1/models")

    assert resp.status_code != 403
