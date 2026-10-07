"""Slots de credencial: llave principal + pool, claves de salud estables por nombre de variable."""
import pytest

from app.core.quota_signals import QuotaSignal
from app.models.provider import Provider
from app.services import credentials, health_service


@pytest.fixture
def env(tmp_path, monkeypatch):
    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(health_service, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    yield monkeypatch
    health_service.reload_for_tests()


def _provider(**kw) -> Provider:
    return Provider(id="p1", name="p1", api_base="https://p1.example.com/v1", **kw)


def test_single_slot_keeps_legacy_health_key(env):
    env.setenv("P1_KEY", "k1")
    slots = credentials.credential_slots(_provider(auth_env_var="P1_KEY"))
    assert [(s.slot, s.env_var, s.health_key) for s in slots] == [(0, "P1_KEY", "provider:p1")]


def test_keyless_provider_has_one_slot_without_key(env):
    slots = credentials.credential_slots(_provider())
    assert [(s.slot, s.env_var, s.health_key) for s in slots] == [(0, "", "provider:p1")]
    assert credentials.api_key_for(slots[0]) == ""


def test_pool_slots_use_env_var_name_in_health_key(env):
    env.setenv("P1_KEY", "k1")
    env.setenv("P1_KEY_2", "k2")
    slots = credentials.credential_slots(_provider(auth_env_var="P1_KEY", extra_auth_env_vars=["P1_KEY_2"]))
    assert [(s.slot, s.health_key) for s in slots] == [(0, "provider:p1#P1_KEY"), (1, "provider:p1#P1_KEY_2")]
    assert credentials.api_key_for(slots[1]) == "k2"


def test_extra_without_value_is_skipped_and_keys_stay_stable(env):
    env.setenv("P1_KEY", "k1")
    env.setenv("P1_KEY_3", "k3")
    env.delenv("P1_KEY_2", raising=False)
    slots = credentials.credential_slots(_provider(auth_env_var="P1_KEY", extra_auth_env_vars=["P1_KEY_2", "P1_KEY_3"]))
    assert [s.health_key for s in slots] == ["provider:p1#P1_KEY", "provider:p1#P1_KEY_3"]


def test_available_slots_skip_exhausted_slot(env):
    env.setenv("P1_KEY", "k1")
    env.setenv("P1_KEY_2", "k2")
    provider = _provider(auth_env_var="P1_KEY", extra_auth_env_vars=["P1_KEY_2"])
    health_service.mark_signal("provider:p1#P1_KEY", QuotaSignal(kind="quota_exhausted", retry_after_s=600, excerpt="quota"))
    assert [s.env_var for s in credentials.available_slots(provider)] == ["P1_KEY_2"]


def test_provider_key_format():
    assert credentials.provider_key("p1") == "provider:p1"
