"""/v1/messages: failover entre llaves y providers antes del primer byte."""
import json

import pytest
from fastapi.testclient import TestClient

from app.models.provider import Provider, ProviderRegistry
from app.services import health_service, providers_service, smart_router

OK_LINES = [
    'data: {"choices":[{"delta":{"content":"hola"},"finish_reason":null}]}',
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1}}',
    "data: [DONE]",
]


class FakeResp:
    def __init__(self, status=200, lines=None, body=b""):
        self.status_code = status
        self._lines = lines or []
        self._body = body
        self.closed = False

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        return self.routes[url].pop(0)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(health_service, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    smart_router.clear_sticky()
    monkeypatch.setattr(smart_router, "_spawn", lambda coro: coro.close())
    monkeypatch.setattr("app.api.messages._record_usage", lambda *a, **k: None)
    monkeypatch.setenv("P1_KEY", "key-one")
    monkeypatch.setenv("P1_KEY_2", "key-two")
    monkeypatch.setenv("P2_KEY", "key-p2")
    p1 = Provider(id="p1", name="p1", api_base="https://p1.example.com/v1", active_model="m1",
                  auth_env_var="P1_KEY", extra_auth_env_vars=["P1_KEY_2"])
    p2 = Provider(id="p2", name="p2", api_base="https://p2.example.com/v1", active_model="m2", auth_env_var="P2_KEY")
    registry = ProviderRegistry(active_provider_id="p1", providers=[p1, p2], fallback_provider_ids=["p2"])
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)

    from app.core.config import get_settings
    from app.main import app
    client = TestClient(app, headers={"x-api-key": get_settings().ui_api_key})

    def install(routes):
        fake = FakeClient(routes)
        monkeypatch.setattr("app.api.messages.httpx.AsyncClient", lambda *a, **k: fake)
        return fake

    yield client, install
    health_service.reload_for_tests()
    smart_router.clear_sticky()


BODY = {"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hola"}], "max_tokens": 50}
P1 = "https://p1.example.com/v1/chat/completions"
P2 = "https://p2.example.com/v1/chat/completions"


def test_rate_limited_key_falls_to_second_key_of_same_provider(harness):
    client, install = harness
    fake = install({P1: [FakeResp(429, body=b'{"error":{"message":"rate limit"}}'), FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 200
    assert resp.headers["x-bipolar-target"] == "p1#1"
    assert resp.headers["x-bipolar-attempts"] == "2"
    assert "hola" in resp.text
    assert [c["headers"]["Authorization"] for c in fake.calls] == ["Bearer key-one", "Bearer key-two"]
    assert health_service.get("provider:p1#P1_KEY").state == "cooling"


def test_provider_down_falls_to_next_provider(harness):
    client, install = harness
    install({P1: [FakeResp(503, body=b"service unavailable")], P2: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 200
    assert resp.headers["x-bipolar-target"] == "p2#0"


def test_all_rate_limited_returns_http_429_with_anthropic_error(harness):
    client, install = harness
    install({
        P1: [FakeResp(429, body=b"rate limit"), FakeResp(429, body=b"rate limit")],
        P2: [FakeResp(429, body=b"rate limit")],
    })
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 429
    payload = resp.json()
    assert payload["type"] == "error" and payload["error"]["type"] == "rate_limit_error"
    assert "p1#0" in payload["error"]["message"] and "p2#0" in payload["error"]["message"]
    assert "key-one" not in resp.text


def test_fatal_400_is_not_retried_elsewhere(harness):
    client, install = harness
    fake = install({P1: [FakeResp(400, body=b'{"error":{"message":"messages: field required"}}')], P2: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "invalid_request_error"
    assert len(fake.calls) == 1


def test_original_body_is_not_mutated_between_attempts(harness):
    client, install = harness
    fake = install({P1: [FakeResp(429, body=b"rate limit"), FakeResp(429, body=b"rate limit")], P2: [FakeResp(200, OK_LINES)]})
    client.post("/v1/messages", json=BODY)
    assert [c["json"]["model"] for c in fake.calls] == ["m1", "m1", "m2"]


def test_upstream_stream_is_closed_after_relay(harness):
    client, install = harness
    winner = FakeResp(200, OK_LINES)
    install({P1: [winner]})
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 200
    assert winner.closed
