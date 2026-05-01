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


def test_messages_endpoint_is_public():
    # /v1/* is public — Claude Code authenticates directly with the provider
    resp = client.post("/v1/messages")
    assert resp.status_code == 200


def test_messages_endpoint_with_valid_key():
    # Works with or without key since /v1/* is public
    resp = client.post("/v1/messages", headers={"x-api-key": VALID_KEY})
    assert resp.status_code == 200


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
