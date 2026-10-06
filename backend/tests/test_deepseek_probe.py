"""Sondeo del agente DeepSeek Harness (dsh): rutas conocidas, versión y credenciales."""
import asyncio

from app.models.delegate import AgentStatus
from app.services.cli_agents import registry as agents_registry
from app.services.cli_agents.registry import _probe_deepseek, known_paths


def test_known_paths_deepseek_under_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    paths = known_paths("deepseek")
    assert paths == [tmp_path / "Local" / "Programs" / "DeepSeek Harness" / "resources" / "runtime" / "cli" / "bin" / "dsh.cmd"]
    assert paths[0].as_posix().endswith("Programs/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd")


def _dsh_home(tmp_path, monkeypatch, *, account_line: bool, api_key: bool) -> None:
    home = tmp_path / "dsh-home"
    home.mkdir()
    if account_line:
        (home / ".credentials.yaml").write_text("  deepseek-account-platform/default:\n    token: secret\n", encoding="utf-8")
    monkeypatch.setenv("DSH_HOME", str(home))
    if api_key:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abc")
    else:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def _fake_capture(result):
    calls: list[list[str]] = []

    async def run_capture(argv, timeout=15, cwd=None, env=None):
        calls.append(list(argv))
        return result

    return run_capture, calls


def test_probe_deepseek_reports_version_auth_and_quota(tmp_path, monkeypatch):
    fake, calls = _fake_capture((0, "0.2.0-rc.2\n", ""))
    monkeypatch.setattr(agents_registry, "run_capture", fake)
    _dsh_home(tmp_path, monkeypatch, account_line=True, api_key=False)
    status = AgentStatus(id="deepseek")
    asyncio.run(_probe_deepseek("dsh.exe", status))
    assert status.version == "0.2.0-rc.2"
    assert status.auth == "ok"
    assert status.quota == {"provider": "deepseek-account"}
    assert status.error == ""
    assert calls[0][-1] == "--version"


def test_probe_deepseek_without_credentials_is_auth_error(tmp_path, monkeypatch):
    fake, _ = _fake_capture((0, "0.2.0-rc.2\n", ""))
    monkeypatch.setattr(agents_registry, "run_capture", fake)
    _dsh_home(tmp_path, monkeypatch, account_line=False, api_key=False)
    status = AgentStatus(id="deepseek")
    asyncio.run(_probe_deepseek("dsh.exe", status))
    assert status.auth == "auth_error"
    assert status.quota == {"provider": "deepseek-account"}


def test_probe_deepseek_failure_sets_error(tmp_path, monkeypatch):
    fake, _ = _fake_capture((1, "", "boom"))
    monkeypatch.setattr(agents_registry, "run_capture", fake)
    _dsh_home(tmp_path, monkeypatch, account_line=True, api_key=False)
    status = AgentStatus(id="deepseek")
    asyncio.run(_probe_deepseek("dsh.exe", status))
    assert status.error == "boom"
    assert status.version == ""
