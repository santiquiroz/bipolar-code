"""/v1/chat/completions: failover entre llaves y providers antes del primer byte."""
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

    def json(self):
        import json as _json
        return _json.loads(self._body or b"{}")

    @property
    def text(self):
        return (self._body or b"").decode()

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

    async def post(self, url, json=None, headers=None):
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
    monkeypatch.setattr("app.api.openai_compat._record_usage", lambda *a, **k: None)
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
        monkeypatch.setattr("app.api.openai_compat.httpx.AsyncClient", lambda *a, **k: fake)
        return fake

    yield client, install
    health_service.reload_for_tests()
    smart_router.clear_sticky()


BODY = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hola"}]}
P1 = "https://p1.example.com/v1/chat/completions"
P2 = "https://p2.example.com/v1/chat/completions"
OK_JSON = b'{"choices":[{"message":{"role":"assistant","content":"hola"}}],"usage":{"prompt_tokens":3,"completion_tokens":1}}'


def test_non_stream_falls_to_second_key(harness):
    client, install = harness
    fake = install({P1: [FakeResp(429, body=b"rate limit"), FakeResp(200, body=OK_JSON)]})
    resp = client.post("/v1/chat/completions", json=BODY)
    assert resp.status_code == 200 and resp.json()["choices"][0]["message"]["content"] == "hola"
    assert resp.headers["x-bipolar-target"] == "p1#1"
    assert [c["headers"]["Authorization"] for c in fake.calls] == ["Bearer key-one", "Bearer key-two"]


def test_stream_falls_to_next_provider(harness):
    client, install = harness
    install({P1: [FakeResp(503, body=b"unavailable")], P2: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/chat/completions", json={**BODY, "stream": True})
    assert resp.status_code == 200
    assert resp.headers["x-bipolar-target"] == "p2#0"
    assert "hola" in resp.text


def test_all_failed_returns_openai_error_with_real_status(harness):
    client, install = harness
    install({P1: [FakeResp(429, body=b"rate limit"), FakeResp(429, body=b"rate limit")], P2: [FakeResp(529, body=b"Overloaded")]})
    resp = client.post("/v1/chat/completions", json=BODY)
    assert resp.status_code == 429
    assert resp.json()["error"]["type"] == "rate_limit_error"


def test_fatal_is_returned_without_retry(harness):
    client, install = harness
    fake = install({P1: [FakeResp(400, body=b'{"error":{"message":"bad"}}')], P2: [FakeResp(200, body=OK_JSON)]})
    resp = client.post("/v1/chat/completions", json=BODY)
    assert resp.status_code == 400
    assert len(fake.calls) == 1
