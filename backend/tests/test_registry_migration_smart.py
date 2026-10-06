"""Compatibilidad del registro: un providers.json viejo carga con defaults y se siembran agentes/tiers."""
import json

import pytest

from app.models.provider import ProviderRegistry
from app.models.smart import AGENT_IDS, DEFAULT_CLI_AGENTS
from app.services import providers_service
from app.services.cli_agents.registry import parse_cursor_about


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
    cursor = next(a for a in registry.cli_agents if a.id == "cursor")
    assert cursor.enabled is False
    assert cursor.name == "Cursor Agent CLI" and cursor.default_model == "auto"
    assert cursor.supported_tiers == ["trivial", "simple", "standard"]
    deepseek = next(a for a in registry.cli_agents if a.id == "deepseek")
    assert deepseek.enabled is False
    assert deepseek.name == "DeepSeek Harness (dsh)"
    assert deepseek.default_model == "deepseek-flash"
    assert deepseek.supported_tiers == ["trivial", "simple", "standard", "complex"]
    assert deepseek.model_by_tier == {"standard": "deepseek-v4-pro", "complex": "deepseek-v4-pro"}
    assert deepseek.priority == 5
    muse = next(a for a in registry.cli_agents if a.id == "muse")
    assert muse.name == "Muse (Meta)" and muse.enabled is False
    assert muse.supported_tiers == ["trivial", "simple", "standard", "complex"]
    assert muse.default_model == "" and muse.model_by_tier == {}
    assert muse.quota_reset == "none" and muse.cost_weight == 0.4
    assert muse.priority == 8 and muse.timeout_s == 900
    assert registry.delegation.tier_order == {
        "trivial": ["deepseek", "muse", "ollama", "copilot", "cursor", "antigravity", "claude"],
        "simple": ["deepseek", "muse", "copilot", "cursor", "antigravity", "codex", "claude"],
        "standard": ["deepseek", "muse", "codex", "claude", "antigravity", "copilot", "cursor"],
        "complex": ["deepseek", "codex", "claude", "antigravity", "muse"],
    }
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


def _previous_version_json(tier_order: dict, *, with_deepseek: bool, with_muse: bool = True) -> dict:
    data = _legacy_json()
    excluded = set()
    if not with_deepseek:
        excluded.add("deepseek")
    if not with_muse:
        excluded.add("muse")
    data["cli_agents"] = [dict(d) for d in DEFAULT_CLI_AGENTS if d["id"] not in excluded]
    data["delegation"] = {"enabled": True, "tier_order": tier_order}
    return data


def test_previous_version_registry_gets_deepseek_prepended_to_custom_tier_order(config_dir):
    tier_order = {"simple": ["cursor", "copilot"], "complex": ["codex"]}
    (config_dir / "providers.json").write_text(
        json.dumps(_previous_version_json(tier_order, with_deepseek=False)), encoding="utf-8")
    registry = providers_service.load_registry()
    assert registry.delegation.tier_order == {
        "simple": ["deepseek", "cursor", "copilot"],
        "complex": ["deepseek", "codex"],
    }
    assert "deepseek" in {a.id for a in registry.cli_agents}


def test_deepseek_already_registered_leaves_tier_order_untouched(config_dir):
    tier_order = {"simple": ["cursor", "copilot"], "complex": ["codex"]}
    (config_dir / "providers.json").write_text(
        json.dumps(_previous_version_json(tier_order, with_deepseek=True)), encoding="utf-8")
    registry = providers_service.load_registry()
    assert registry.delegation.tier_order == tier_order


def test_deepseek_reseeded_agent_keeps_user_tier_order_that_already_has_it(config_dir):
    tier_order = {"simple": ["cursor", "deepseek"], "complex": ["codex"]}
    (config_dir / "providers.json").write_text(
        json.dumps(_previous_version_json(tier_order, with_deepseek=False)), encoding="utf-8")
    registry = providers_service.load_registry()
    assert registry.delegation.tier_order == tier_order


def test_version_216_registry_gets_muse_after_deepseek_and_last_in_complex(config_dir):
    tier_order = {
        "trivial": ["deepseek", "ollama", "copilot", "cursor", "antigravity", "claude"],
        "simple": ["deepseek", "copilot", "cursor", "antigravity", "codex", "claude"],
        "standard": ["deepseek", "codex", "claude", "antigravity", "copilot", "cursor"],
        "complex": ["deepseek", "codex", "claude", "antigravity"],
    }
    (config_dir / "providers.json").write_text(
        json.dumps(_previous_version_json(tier_order, with_deepseek=True, with_muse=False)), encoding="utf-8")
    registry = providers_service.load_registry()
    for tier in ("trivial", "simple", "standard"):
        assert registry.delegation.tier_order[tier] == ["deepseek", "muse", *tier_order[tier][1:]]
    assert registry.delegation.tier_order["complex"] == [*tier_order["complex"], "muse"]
    assert "muse" in {a.id for a in registry.cli_agents}


def test_muse_seeded_into_custom_tiers_without_deepseek_goes_first_except_complex(config_dir):
    tier_order = {"simple": ["cursor", "copilot"], "standard": [], "complex": ["codex"]}
    (config_dir / "providers.json").write_text(
        json.dumps(_previous_version_json(tier_order, with_deepseek=True, with_muse=False)), encoding="utf-8")
    registry = providers_service.load_registry()
    assert registry.delegation.tier_order == {
        "simple": ["muse", "cursor", "copilot"], "standard": ["muse"], "complex": ["codex", "muse"],
    }


def test_muse_seeded_after_deepseek_preserves_custom_agent_order(config_dir):
    tier_order = {"simple": ["cursor", "deepseek", "copilot"], "complex": ["claude", "deepseek", "codex"]}
    (config_dir / "providers.json").write_text(
        json.dumps(_previous_version_json(tier_order, with_deepseek=True, with_muse=False)), encoding="utf-8")
    registry = providers_service.load_registry()
    assert registry.delegation.tier_order == {
        "simple": ["cursor", "deepseek", "muse", "copilot"],
        "complex": ["claude", "deepseek", "codex", "muse"],
    }


def test_muse_reseeded_agent_keeps_user_tier_order_that_already_names_it(config_dir):
    tier_order = {"simple": ["cursor", "muse", "deepseek"], "complex": ["codex"]}
    (config_dir / "providers.json").write_text(
        json.dumps(_previous_version_json(tier_order, with_deepseek=True, with_muse=False)), encoding="utf-8")
    registry = providers_service.load_registry()
    assert registry.delegation.tier_order == tier_order


def test_muse_already_registered_leaves_tier_order_untouched(config_dir):
    tier_order = {"simple": ["cursor", "copilot"], "complex": ["codex"]}
    (config_dir / "providers.json").write_text(
        json.dumps(_previous_version_json(tier_order, with_deepseek=True)), encoding="utf-8")
    registry = providers_service.load_registry()
    assert registry.delegation.tier_order == tier_order


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


def test_parse_cursor_about_signed_in_and_logged_out():
    signed_in = parse_cursor_about(
        "CLI Version         2026.09.28-64d2043\r\n"
        "Subscription Tier   Free\r\n"
        "User Email          dev@example.com\r\n"
    )
    assert signed_in == {"version": "2026.09.28-64d2043", "tier": "Free", "email": "dev@example.com"}
    logged_out = parse_cursor_about("User Email          Not logged in\r\n")
    assert logged_out == {"version": "", "tier": "", "email": "Not logged in"}
