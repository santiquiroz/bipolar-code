"""Servicio de cuentas: crear, borrar, elegir y registrar uso."""
from datetime import datetime, timezone

import pytest

from app.models.provider import ProviderRegistry
from app.models.smart import CliAgent, DelegationConfig
from app.services import accounts_service, health_service, providers_service


@pytest.fixture
def env(tmp_path, monkeypatch):
    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(accounts_service, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(health_service, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    accounts_service._usage.clear()
    state = {"registry": ProviderRegistry(
        cli_agents=[CliAgent(id="claude"), CliAgent(id="codex"), CliAgent(id="muse")],
        delegation=DelegationConfig(tier_order={"standard": ["muse", "codex", "claude"], "complex": ["codex", "claude"]},
                                    thinkers=["claude", "codex"]),
    )}
    monkeypatch.setattr(providers_service, "load_registry", lambda: state["registry"].model_copy(deep=True))
    monkeypatch.setattr(providers_service, "save_registry", lambda r: state.__setitem__("registry", r))
    monkeypatch.setattr(accounts_service, "_scripts_dir", lambda: tmp_path / "scripts")
    (tmp_path / "scripts").mkdir()
    yield state, tmp_path
    health_service.reload_for_tests()


def test_create_account_inserts_after_base_everywhere(env):
    state, tmp = env
    agent, login = accounts_service.create_account("claude", "Personal 2")
    assert agent.id == "claude-2" and agent.adapter == "claude" and not agent.enabled
    assert agent.account_dir == str(tmp / "accounts" / "claude-2")
    assert (tmp / "accounts" / "claude-2").is_dir()
    reg = state["registry"]
    assert reg.delegation.tier_order["standard"] == ["muse", "codex", "claude", "claude-2"]
    assert reg.delegation.tier_order["complex"] == ["codex", "claude", "claude-2"]
    assert reg.delegation.thinkers == ["claude", "claude-2", "codex"]
    assert "CLAUDE_CONFIG_DIR" in login["powershell"] and "claude-2" in login["bash"]
    settings = (tmp / "accounts" / "claude-2" / "settings.json").read_text(encoding="utf-8")
    assert "bipolar-statusline.py" in settings and "--agent-id claude-2" in settings


def test_second_account_gets_next_id(env):
    accounts_service.create_account("claude", "")
    agent, _ = accounts_service.create_account("claude", "")
    assert agent.id == "claude-3"


def test_unsupported_adapter_is_rejected(env):
    with pytest.raises(accounts_service.AccountError):
        accounts_service.create_account("muse", "x")


def test_delete_account_removes_everywhere_but_keeps_dir(env):
    state, tmp = env
    accounts_service.create_account("claude", "")
    accounts_service.delete_account("claude-2")
    reg = state["registry"]
    assert all(a.id != "claude-2" for a in reg.cli_agents)
    assert "claude-2" not in reg.delegation.thinkers and "claude-2" not in reg.delegation.tier_order["standard"]
    assert (tmp / "accounts" / "claude-2").is_dir()


def test_delete_base_agent_is_rejected(env):
    with pytest.raises(accounts_service.AccountError):
        accounts_service.delete_account("claude")


def test_pick_skips_accounts_without_login_and_exhausted(env):
    state, tmp = env
    accounts_service.create_account("claude", "")
    accounts_service.create_account("claude", "")
    assert accounts_service.pick_account("claude") == {"mode": "proxy"}
    (tmp / "accounts" / "claude-2" / ".credentials.json").write_text("{}", encoding="utf-8")
    (tmp / "accounts" / "claude-3" / ".credentials.json").write_text("{}", encoding="utf-8")
    assert accounts_service.pick_account("claude")["agent_id"] == "claude-2"
    health_service.set_exhausted_until("cli:claude-2", datetime(2099, 1, 1, tzinfo=timezone.utc), "5h 100%")
    assert accounts_service.pick_account("claude")["agent_id"] == "claude-3"


def test_record_usage_marks_exhausted_at_threshold(env):
    accounts_service.create_account("claude", "")
    resets = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp())
    out = accounts_service.record_usage("claude-2", {"five_hour": {"used_percentage": 99, "resets_at": resets},
                                                     "seven_day": {"used_percentage": 40, "resets_at": resets}})
    assert out["state"] == "exhausted"
    assert out["usage"]["five_hour"]["used_percentage"] == 99


def test_record_usage_without_rate_limits_marks_nothing(env):
    accounts_service.create_account("claude", "")
    out = accounts_service.record_usage("claude-2", {})
    assert out["state"] == "available" and "received_at" in out["usage"]


def test_record_usage_ignores_absurd_reset_timestamp(env):
    accounts_service.create_account("claude", "")
    out = accounts_service.record_usage("claude-2", {"five_hour": {"used_percentage": 100, "resets_at": 1e20}})
    assert out["state"] == "available"


def test_record_usage_ignores_unknown_windows(env):
    accounts_service.create_account("claude", "")
    resets = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp())
    rate_limits = {f"bogus_{i}": {"used_percentage": 99, "resets_at": resets} for i in range(1000)}
    rate_limits["five_hour"] = {"used_percentage": 10, "resets_at": resets}
    rate_limits["seven_day"] = {"used_percentage": "mucho", "resets_at": resets}
    out = accounts_service.record_usage("claude-2", rate_limits)
    assert set(out["usage"].keys()) == {"received_at", "five_hour"}
