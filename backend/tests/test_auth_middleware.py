import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.middleware.auth import APIKeyMiddleware
from app.middleware.rate_limit import RateLimitMiddleware

VALID_KEY = "bc-testkey123"


def make_app():
    app = FastAPI()
    app.add_middleware(APIKeyMiddleware, ui_key=VALID_KEY, proxy_key="sk-proxy")

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/protected")
    def protected():
        return {"secret": "data"}

    @app.post("/v1/messages")
    def messages():
        return {"ok": True}

    return app


client = TestClient(make_app(), raise_server_exceptions=False)


def test_health_is_public():
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_protected_without_key_returns_401():
    resp = client.get("/api/protected")
    assert resp.status_code == 401


def test_mcp_without_key_returns_401():
    resp = client.post("/mcp")
    assert resp.status_code == 401


def test_protected_with_bearer_token():
    resp = client.get("/api/protected", headers={"Authorization": f"Bearer {VALID_KEY}"})
    assert resp.status_code == 200


def test_protected_with_x_api_key():
    resp = client.get("/api/protected", headers={"X-API-Key": VALID_KEY})
    assert resp.status_code == 200


def test_protected_with_lowercase_x_api_key():
    resp = client.get("/api/protected", headers={"x-api-key": VALID_KEY})
    assert resp.status_code == 200


def test_wrong_key_returns_401():
    resp = client.get("/api/protected", headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 401


def test_messages_endpoint_without_key_returns_401():
    # /v1/* requiere auth: expuesto en LAN, sin key cualquiera lo usaría
    resp = client.post("/v1/messages")
    assert resp.status_code == 401


def test_messages_endpoint_with_ui_key():
    resp = client.post("/v1/messages", headers={"x-api-key": VALID_KEY})
    assert resp.status_code == 200


def test_messages_endpoint_with_proxy_key():
    # Compat: clientes configurados antes del cierre tienen el proxy_key escrito
    resp = client.post("/v1/messages", headers={"Authorization": "Bearer sk-proxy"})
    assert resp.status_code == 200


def test_api_does_not_accept_proxy_key():
    resp = client.get("/api/protected", headers={"Authorization": "Bearer sk-proxy"})
    assert resp.status_code == 401


def make_app_no_key():
    app = FastAPI()
    app.add_middleware(APIKeyMiddleware, ui_key="", proxy_key="")

    @app.get("/api/protected")
    def protected():
        return {"secret": "data"}

    return app


def test_empty_key_returns_503():
    c = TestClient(make_app_no_key(), raise_server_exceptions=False)
    resp = c.get("/api/protected")
    assert resp.status_code == 503


def make_rate_limited_app(rpm: int):
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, rpm=rpm)

    @app.get("/api/test")
    def test_route():
        return {"ok": True}

    return app


def test_rate_limit_allows_within_limit():
    c = TestClient(make_rate_limited_app(rpm=5), raise_server_exceptions=False)
    for _ in range(5):
        resp = c.get("/api/test")
        assert resp.status_code == 200


def test_rate_limit_blocks_over_limit():
    c = TestClient(make_rate_limited_app(rpm=2), raise_server_exceptions=False)
    c.get("/api/test")
    c.get("/api/test")
    resp = c.get("/api/test")
    assert resp.status_code == 429


def test_rate_limit_zero_disables():
    c = TestClient(make_rate_limited_app(rpm=0), raise_server_exceptions=False)
    for _ in range(20):
        resp = c.get("/api/test")
        assert resp.status_code == 200


def make_rate_limited_app_with_proxies(rpm: int, trusted_proxies: str = "", max_keys: int = 10_000):
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, rpm=rpm, trusted_proxies=trusted_proxies, max_keys=max_keys)

    @app.get("/api/test")
    def test_route():
        return {"ok": True}

    return app


def _statuses_with_forwarded_ips(c: TestClient, ips: list[str]) -> list[int]:
    return [c.get("/api/test", headers={"X-Forwarded-For": ip}).status_code for ip in ips]


FAKE_IPS = ["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"]


def test_rate_limit_ignores_forwarded_for_without_trusted_proxies():
    c = TestClient(make_rate_limited_app_with_proxies(rpm=2), raise_server_exceptions=False)
    assert _statuses_with_forwarded_ips(c, FAKE_IPS) == [200, 200, 429, 429]


def test_rate_limit_ignores_forwarded_for_from_untrusted_peer():
    app = make_rate_limited_app_with_proxies(rpm=2, trusted_proxies="10.0.0.0/8, 127.0.0.1")
    c = TestClient(app, raise_server_exceptions=False)
    assert _statuses_with_forwarded_ips(c, FAKE_IPS) == [200, 200, 429, 429]


def test_rate_limit_honors_forwarded_for_from_trusted_proxy():
    c = TestClient(make_rate_limited_app_with_proxies(rpm=2, trusted_proxies="testclient"),
                   raise_server_exceptions=False)
    assert _statuses_with_forwarded_ips(c, FAKE_IPS) == [200, 200, 200, 200]


def test_rate_limit_trusted_proxy_uses_rightmost_untrusted_hop():
    c = TestClient(make_rate_limited_app_with_proxies(rpm=2, trusted_proxies="testclient, 10.0.0.0/8"),
                   raise_server_exceptions=False)
    spoofed_chains = [f"{ip}, 9.9.9.9, 10.0.0.5" for ip in FAKE_IPS]
    assert _statuses_with_forwarded_ips(c, spoofed_chains) == [200, 200, 429, 429]


def test_rate_limit_bucket_count_stays_under_cap():
    mw = RateLimitMiddleware(FastAPI(), rpm=5, max_keys=10_000)
    for i in range(20_000):
        mw.admit(f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}", now=1000.0)
    assert len(mw._buckets) <= 10_000


def test_rate_limit_evicts_least_recent_bucket_at_cap():
    mw = RateLimitMiddleware(FastAPI(), rpm=5, max_keys=2)
    mw.admit("1.1.1.1", now=1000.0)
    mw.admit("2.2.2.2", now=1000.0)
    mw.admit("1.1.1.1", now=1000.0)
    mw.admit("3.3.3.3", now=1000.0)
    assert list(mw._buckets) == ["1.1.1.1", "3.3.3.3"]


def test_rate_limit_sweeps_idle_buckets():
    mw = RateLimitMiddleware(FastAPI(), rpm=5)
    for ip in FAKE_IPS:
        mw.admit(ip, now=1000.0)
    mw.admit("5.5.5.5", now=1000.0 + 61)
    assert list(mw._buckets) == ["5.5.5.5"]
