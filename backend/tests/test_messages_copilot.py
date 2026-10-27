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
    monkeypatch.delenv("P3_KEY", raising=False)
    p1 = Provider(id="p1", name="p1", api_base="https://p1.example.com/v1", active_model="m1",
                  auth_env_var="P1_KEY", extra_auth_env_vars=["P1_KEY_2"])
    p2 = Provider(id="p2", name="p2", api_base="https://p2.example.com/v1", active_model="m2", auth_env_var="P2_KEY")
    p3 = Provider(id="p3", name="p3", api_base="https://p3.example.com/v1", active_model="m3", auth_env_var="P3_KEY")
    registry = ProviderRegistry(active_provider_id="p1", providers=[p1, p2, p3], fallback_provider_ids=["p2"])
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)

    from app.core.config import get_settings
    from app.main import app
    client = TestClient(app, headers={"x-api-key": get_settings().ui_api_key})

    def install(routes):
        fake = FakeClient(routes)
        monkeypatch.setattr("app.api.messages.httpx.AsyncClient", lambda *a, **k: fake)
        return fake

    yield client, install, registry
    health_service.reload_for_tests()
    smart_router.clear_sticky()


BODY = {"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hola"}], "max_tokens": 50}
P1 = "https://p1.example.com/v1/chat/completions"
P2 = "https://p2.example.com/v1/chat/completions"
LARGE_TEXT = "palabra " * 20000


def _body(model: str, content: str = "hola") -> dict:
    return {**BODY, "model": model, "messages": [{"role": "user", "content": content}]}


def _user_contents(call: dict) -> list:
    return [m["content"] for m in call["json"]["messages"] if m["role"] == "user"]


def test_pinned_goes_only_to_pinned_provider(harness):
    client, install, _ = harness
    fake = install({P1: [FakeResp(200, OK_LINES)], P2: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=_body("m2"), headers={"X-Bipolar-Provider": "p2"})
    assert resp.status_code == 200
    assert [c["url"] for c in fake.calls] == [P2]
    assert fake.calls[0]["json"]["model"] == "m2"
    assert resp.headers["x-bipolar-target"] == "p2#0"


def test_pinned_failure_does_not_fall_to_other_provider(harness):
    client, install, _ = harness
    fake = install({
        P1: [FakeResp(503, body=b"service unavailable"), FakeResp(503, body=b"service unavailable")],
        P2: [FakeResp(200, OK_LINES)],
    })
    resp = client.post("/v1/messages", json=_body("m1"), headers={"X-Bipolar-Provider": "p1"})
    assert resp.status_code >= 500
    assert resp.json()["type"] == "error"
    assert all(c["url"] == P1 for c in fake.calls)


def test_pinned_unknown_provider_returns_400_anthropic_error(harness):
    client, install, _ = harness
    fake = install({})
    resp = client.post("/v1/messages", json=_body("m1"), headers={"X-Bipolar-Provider": "nope"})
    assert resp.status_code == 400
    assert resp.json()["type"] == "error"
    assert fake.calls == []


def test_pinned_without_credential_returns_400(harness):
    client, install, _ = harness
    fake = install({})
    resp = client.post("/v1/messages", json=_body("m3"), headers={"X-Bipolar-Provider": "p3"})
    assert resp.status_code == 400
    assert fake.calls == []


def test_auto_sends_active_model_never_literal(harness):
    client, install, _ = harness
    fake = install({P1: [FakeResp(200, OK_LINES)], P2: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=_body("bipolar-auto"))
    assert resp.status_code == 200
    assert fake.calls[0]["json"]["model"] == "m1"
    assert all(c["json"]["model"] != "bipolar-auto" for c in fake.calls)


def test_auto_without_any_model_returns_400(harness):
    client, install, registry = harness
    for provider in registry.providers[:2]:
        provider.active_model = ""
    fake = install({P1: [FakeResp(200, OK_LINES)], P2: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=_body("bipolar-auto"))
    assert resp.status_code == 400
    assert "Bipolar Auto" in resp.json()["error"]["message"]
    assert fake.calls == []


def test_pinned_large_prompt_not_truncated(harness):
    client, install, _ = harness
    fake = install({P2: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=_body("m2", LARGE_TEXT), headers={"X-Bipolar-Provider": "p2"})
    assert resp.status_code == 200
    assert LARGE_TEXT in _user_contents(fake.calls[0])


def test_auto_large_prompt_not_truncated(harness):
    client, install, _ = harness
    fake = install({P1: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=_body("bipolar-auto", LARGE_TEXT))
    assert resp.status_code == 200
    assert LARGE_TEXT in _user_contents(fake.calls[0])


def test_no_header_claude_model_path_unchanged(harness):
    client, install, _ = harness
    fake = install({P1: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 200
    assert fake.calls[0]["url"] == P1
    assert resp.headers["x-bipolar-target"] == "p1#0"
