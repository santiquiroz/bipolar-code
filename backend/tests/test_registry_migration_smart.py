"""Compatibilidad del registro: un providers.json viejo carga con defaults y se siembran agentes/tiers."""
import json

import pytest

from app.models.provider import ProviderRegistry
from app.models.smart import AGENT_IDS
from app.services import providers_service


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    from app.core import config as cfg

    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(cfg, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(providers_service, "get_settings", lambda: FakeSettings())
    return tmp_path


def _legacy_json() -> dict:
    return {
        "active_provider_id": "copilot",
        "providers": [
            {"id": "copilot", "name": "Copilot", "api_base": "https://api.business.githubcopilot.com", "active_model": "gpt-4o"},
            {"id": "ollama", "name": "Ollama", "api_base": "http://127.0.0.1:11434", "active_model": "llama3.2"},
        ],
        "routing_enabled": True,
        "routing_rules": [{"pattern": "haiku", "provider_id": "ollama"}],
        "fallback_provider_ids": ["copilot"],
    }


def test_legacy_registry_loads_with_defaults_and_seeds(config_dir):
    (config_dir / "providers.json").write_text(json.dumps(_legacy_json()), encoding="utf-8")
    registry = providers_service.load_registry()
    assert registry.smart.enabled is False and registry.smart.mode == "shadow"
    assert {a.id for a in registry.cli_agents} == set(AGENT_IDS)
    assert all(a.enabled is False for a in registry.cli_agents)
    assert registry.routing_rules[0].tier == "" and registry.routing_rules[0].max_tokens == 0
    assert registry.delegation.workspace_allowlist == []
    tier_targets = {p.tier: [t.provider_id for t in p.targets] for p in registry.smart.tiers}
    registered = {p.id for p in registry.providers}
    assert tier_targets["trivial"][0] == "ollama" and "copilot" in tier_targets["trivial"]
    assert all(set(ids) <= registered for ids in tier_targets.values())


def test_seeding_is_idempotent_and_respects_user_edits(config_dir):
    (config_dir / "providers.json").write_text(json.dumps(_legacy_json()), encoding="utf-8")
    first = providers_service.load_registry()
    first.cli_agents[0].enabled = True
    first.smart.tiers = []
    providers_service.save_registry(first)
    second = providers_service.load_registry()
    assert second.cli_agents[0].enabled is True
    assert len(second.cli_agents) == len(AGENT_IDS)
    assert second.smart.tiers  # tabla vacía → se vuelve a sembrar


def test_update_smart_config_partial(config_dir):
    (config_dir / "providers.json").write_text(json.dumps(_legacy_json()), encoding="utf-8")
    from app.models.smart import DelegationConfig, SmartRoutingConfig
    providers_service.load_registry()
    updated = providers_service.update_smart_config(smart=SmartRoutingConfig(enabled=True, mode="active"))
    assert updated.smart.enabled is True and updated.cli_agents
    updated = providers_service.update_smart_config(delegation=DelegationConfig(enabled=True, workspace_allowlist=["C:/repo"]))
    assert updated.smart.enabled is True and updated.delegation.workspace_allowlist == ["C:/repo"]
    on_disk = ProviderRegistry(**json.loads((config_dir / "providers.json").read_text(encoding="utf-8")))
    assert on_disk.delegation.enabled is True
