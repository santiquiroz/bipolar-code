"""Tests de la sesión de Windows: Muse y Codex quedan fuera cuando bipolar corre en la sesión 0."""
import pytest

from app.models.delegate import AgentStatus
from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent
from app.services import health_service
from app.services.cli_agents import broker, session

SANDBOX_REASON = "sandbox_requires_interactive_session"


def _agent(agent_id: str) -> CliAgent:
    return next(CliAgent(**{**d, "enabled": True}) for d in DEFAULT_CLI_AGENTS if d["id"] == agent_id)


def _reject(agent_id: str):
    status = AgentStatus(id=agent_id, installed=True, auth="ok")
    return broker._reject_agent(_agent(agent_id), "standard", "task", status, "m", False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app.core import config as cfg

    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    for mod in (cfg, health_service, broker):
        monkeypatch.setattr(mod, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    broker.reset_for_tests()
    yield
    broker.reset_for_tests()
    health_service.reload_for_tests()


@pytest.mark.parametrize("session_id, expected", [(0, False), (1, True), (None, True)])
def test_interactive_session_by_session_id(monkeypatch, session_id, expected):
    monkeypatch.setattr(session.sys, "platform", "win32")
    monkeypatch.setattr(session, "windows_session_id", lambda: session_id)
    session.interactive_session.cache_clear()
    assert session.interactive_session() is expected


def test_interactive_session_outside_windows(monkeypatch):
    monkeypatch.setattr(session.sys, "platform", "linux")
    monkeypatch.setattr(session, "windows_session_id", lambda: 0)
    session.interactive_session.cache_clear()
    assert session.interactive_session() is True


@pytest.mark.parametrize("agent_id", ["muse", "codex"])
def test_reject_os_sandbox_agents_in_session_zero(env, monkeypatch, agent_id):
    monkeypatch.setattr(session, "interactive_session", lambda: False)
    assert _reject(agent_id) == SANDBOX_REASON


@pytest.mark.parametrize("agent_id", ["claude", "anthropic"])
def test_session_zero_keeps_other_agents(env, monkeypatch, agent_id):
    monkeypatch.setattr(session, "interactive_session", lambda: False)
    assert _reject(agent_id) != SANDBOX_REASON


@pytest.mark.parametrize("agent_id", ["muse", "codex"])
def test_interactive_session_keeps_os_sandbox_agents(env, monkeypatch, agent_id):
    monkeypatch.setattr(session, "interactive_session", lambda: True)
    assert _reject(agent_id) != SANDBOX_REASON
