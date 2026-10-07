"""Cuentas como entradas de cli_agents: validación del id, adaptador y base."""
import pytest
from pydantic import ValidationError

from app.models.provider import ProviderRegistry
from app.models.smart import CliAgent, DelegationConfig
from app.services.cli_agents import broker
from app.services.cli_agents.adapters import ClaudeAdapter, adapter_for


def test_base_agent_keeps_id_as_base():
    agent = CliAgent(id="claude")
    assert agent.base == "claude" and agent.adapter == "" and not agent.is_account


def test_account_id_fills_adapter_from_prefix():
    agent = CliAgent(id="claude-2", account_dir="C:/litellm/accounts/claude-2")
    assert agent.adapter == "claude" and agent.base == "claude" and agent.is_account
    assert agent.key == "cli:claude-2"


@pytest.mark.parametrize("bad", ["../evil", "Claude-2", "claude_2", "claude-", "gpt-2", "claude-" + "x" * 25])
def test_invalid_ids_are_rejected(bad):
    with pytest.raises(ValidationError):
        CliAgent(id=bad)


def test_adapter_must_match_prefix():
    with pytest.raises(ValidationError):
        CliAgent(id="claude-2", adapter="codex")


def test_legacy_registry_without_new_fields_loads():
    raw = {"cli_agents": [{"id": "claude", "enabled": True}, {"id": "muse"}], "delegation": {"enabled": True}}
    registry = ProviderRegistry(**raw)
    assert [a.base for a in registry.cli_agents] == ["claude", "muse"]
    assert registry.delegation.thinkers == ["claude", "codex"]
    assert registry.delegation.account_exhausted_pct == 98


def test_adapter_lookup_uses_base():
    assert isinstance(adapter_for(CliAgent(id="claude-2").base), ClaudeAdapter)


def test_pool_key_of_account_is_its_own():
    agent = CliAgent(id="claude-2")
    assert broker._pool_key(agent, "") == "cli:claude-2"
