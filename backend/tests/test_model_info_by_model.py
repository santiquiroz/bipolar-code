from app.api import messages
from app.models.provider import Provider, ProviderRegistry
from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent
from app.services import providers_service, token_service
from app.services.copilot_catalog import DEFAULT_MAX_INPUT
from app.services.credentials import CredentialSlot
from app.services.smart_router import PlanStep

ACTIVE = "openai/gpt-oss-20b"
MINIMAX = "minimaxai/minimax-m3.1-flash"
NEMOTRON = "nvidia/nemotron-3-ultra-550b-a55b"
GLM = "z-ai/glm-5.3-flash"


def _nvidia_provider(**overrides) -> Provider:
    base = {
        "id": "nvidia_nim",
        "name": "NVIDIA NIM",
        "api_base": "https://integrate.api.nvidia.com/v1",
        "active_model": ACTIVE,
        "model_info": {"context_window": 32768},
    }
    base.update(overrides)
    return Provider(**base)


def _step(provider: Provider, model: str) -> PlanStep:
    slot = CredentialSlot(provider.id, 0, "NVIDIA_NIM_API_KEY", f"provider:{provider.id}")
    return PlanStep(provider, model, slot, False)


def test_model_info_for_active_model():
    p = _nvidia_provider()
    assert providers_service.model_info_for(p, ACTIVE) == {"context_window": 32768}


def test_model_info_for_empty_model_returns_active_info():
    p = _nvidia_provider()
    assert providers_service.model_info_for(p, "") == {"context_window": 32768}


def test_model_info_for_other_model_with_info():
    p = _nvidia_provider(model_info_by_model={MINIMAX: {"supports_tools": True}})
    assert providers_service.model_info_for(p, MINIMAX) == {"supports_tools": True}


def test_model_info_for_other_model_without_info():
    p = _nvidia_provider()
    assert providers_service.model_info_for(p, MINIMAX) == {}


def test_oai_request_minimax_gets_tools_and_ignores_active_context_window(monkeypatch):
    p = _nvidia_provider(model_info_by_model={MINIMAX: {"supports_tools": True}})
    monkeypatch.setattr(token_service, "count_tokens", lambda _msgs: 30000)
    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 16000,
        "tools": [{"name": "probe", "description": "p", "input_schema": {"type": "object", "properties": {}}}],
    }
    _url, _headers, oai_body = messages._oai_request(_step(p, MINIMAX), body, MINIMAX)
    assert oai_body["model"] == MINIMAX
    assert [t["function"]["name"] for t in oai_body.get("tools", [])] == ["probe"]
    assert oai_body["max_tokens"] == 16000


def test_oai_request_active_model_keeps_active_info(monkeypatch):
    p = _nvidia_provider()
    monkeypatch.setattr(token_service, "count_tokens", lambda _msgs: 30000)
    body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 16000}
    _url, _headers, oai_body = messages._oai_request(_step(p, ACTIVE), body, ACTIVE)
    assert "tools" not in oai_body
    assert oai_body["max_tokens"] == 2512


def _seed_registry(**overrides) -> ProviderRegistry:
    agent = CliAgent(**next(d for d in DEFAULT_CLI_AGENTS if d["id"] == "nvidia"))
    registry = ProviderRegistry(
        active_provider_id="nvidia_nim",
        providers=[_nvidia_provider()],
        cli_agents=[agent],
    )
    for key, value in overrides.items():
        setattr(registry, key, value)
    return registry


def test_seed_adds_supports_tools_to_nvidia_models():
    registry = _seed_registry()
    seeded = providers_service._seed_nvidia_model_info(registry)
    provider = next(p for p in registry.providers if p.id == "nvidia_nim")
    assert provider.model_info_by_model == {
        MINIMAX: {"supports_tools": True},
        NEMOTRON: {"supports_tools": True},
        GLM: {"supports_tools": True},
    }
    assert len(seeded) == 3


def test_seed_does_not_overwrite_existing_supports_tools():
    registry = _seed_registry()
    provider = next(p for p in registry.providers if p.id == "nvidia_nim")
    provider.model_info_by_model = {MINIMAX: {"supports_tools": False}}
    seeded = providers_service._seed_nvidia_model_info(registry)
    assert provider.model_info_by_model[MINIMAX] == {"supports_tools": False}
    assert provider.model_info_by_model[NEMOTRON] == {"supports_tools": True}
    assert provider.model_info_by_model[GLM] == {"supports_tools": True}
    assert len(seeded) == 2


def test_seed_without_provider_or_agent_does_nothing():
    assert providers_service._seed_nvidia_model_info(_seed_registry(providers=[])) == []
    assert providers_service._seed_nvidia_model_info(_seed_registry(cli_agents=[])) == []


def test_copilot_context_window_non_active_with_info():
    p = _nvidia_provider(model_info_by_model={"other-model": {"context_window": 64000}})
    assert messages._copilot_context_window(p, "other-model") == 64000


def test_copilot_context_window_non_active_without_info():
    p = _nvidia_provider()
    assert messages._copilot_context_window(p, MINIMAX) == DEFAULT_MAX_INPUT


def test_copilot_context_window_active_model():
    p = _nvidia_provider()
    assert messages._copilot_context_window(p, ACTIVE) == 32768
    assert messages._copilot_context_window(p, "") == 32768
    assert messages._copilot_context_window(None, MINIMAX) == DEFAULT_MAX_INPUT
