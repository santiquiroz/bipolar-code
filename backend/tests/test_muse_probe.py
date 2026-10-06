import asyncio
import json

import pytest

from app.models.delegate import AgentStatus
from app.models.smart import CliAgent
from app.services.cli_agents import registry as agents_registry


def test_known_paths_muse_under_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert agents_registry.known_paths("muse") == [tmp_path / "Local" / "Programs" / "muse" / "muse.cmd"]


@pytest.fixture
def muse_install(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("MUSE_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("META_API_KEY", raising=False)
    root = tmp_path / "muse"
    root.mkdir()
    shim = root / "muse.cmd"
    shim.touch()
    binary = root / "muse-bin-1.4.3-R5018.1.exe"
    binary.touch()
    (root / ".muse-version").write_text("1.4.3-R5018.1", encoding="utf-8")
    return config_dir, shim, binary


def _fake_capture(version_result=(0, "Muse Code 1.4.3 (1.4.3-R5018.1)\n", ""), sandbox="ready"):
    calls = []

    async def run_capture(argv, timeout=15, cwd=None, env=None):
        calls.append(list(argv))
        if argv[-1] == "--version":
            return version_result
        return 0, f"implementation=windows\nstatus={sandbox}\n", ""

    return run_capture, calls


@pytest.mark.parametrize("auth_field", ["access_token", "api_key"])
def test_probe_muse_reports_version_auth_and_windows_sandbox(muse_install, monkeypatch, auth_field):
    config_dir, shim, binary = muse_install
    (config_dir / "auth.json").write_text(json.dumps({"providers": {"meta": {auth_field: "secret"}}}), encoding="utf-8")
    fake, calls = _fake_capture()
    monkeypatch.setattr(agents_registry, "run_capture", fake)
    monkeypatch.setattr(agents_registry.sys, "platform", "win32")
    status = AgentStatus(id="muse")
    asyncio.run(agents_registry._probe_muse(str(shim), status))
    assert status.version == "1.4.3"
    assert status.auth == "ok"
    assert status.quota == {"sandbox": "ready"}
    assert status.error == ""
    assert calls == [[str(binary), "--version"], [str(binary), "sandbox", "windows", "check"]]


def test_probe_muse_api_key_is_authenticated(muse_install, monkeypatch):
    _, shim, _ = muse_install
    monkeypatch.setenv("META_API_KEY", "meta-secret")
    monkeypatch.setattr(agents_registry.sys, "platform", "linux")
    fake, _ = _fake_capture()
    monkeypatch.setattr(agents_registry, "run_capture", fake)
    status = AgentStatus(id="muse")
    asyncio.run(agents_registry._probe_muse(str(shim), status))
    assert status.auth == "ok"


def test_probe_muse_without_credentials_is_auth_error(muse_install, monkeypatch):
    _, shim, _ = muse_install
    fake, _ = _fake_capture()
    monkeypatch.setattr(agents_registry, "run_capture", fake)
    status = AgentStatus(id="muse")
    asyncio.run(agents_registry._probe_muse(str(shim), status))
    assert status.auth == "auth_error"


def test_probe_muse_skips_windows_sandbox_on_other_platforms(muse_install, monkeypatch):
    _, shim, binary = muse_install
    monkeypatch.setattr(agents_registry.sys, "platform", "linux")
    fake, calls = _fake_capture()
    monkeypatch.setattr(agents_registry, "run_capture", fake)
    status = AgentStatus(id="muse")
    asyncio.run(agents_registry._probe_muse(str(shim), status))
    assert status.quota == {"sandbox": "n/a"}
    assert calls == [[str(binary), "--version"]]


def test_probe_muse_reports_unready_sandbox(muse_install, monkeypatch):
    _, shim, _ = muse_install
    monkeypatch.setattr(agents_registry.sys, "platform", "win32")
    fake, _ = _fake_capture(sandbox="setup_required")
    monkeypatch.setattr(agents_registry, "run_capture", fake)
    status = AgentStatus(id="muse")
    asyncio.run(agents_registry._probe_muse(str(shim), status))
    assert status.quota == {"sandbox": "setup_required"}


def test_probe_muse_version_failure_sets_error(muse_install, monkeypatch):
    _, shim, _ = muse_install
    fake, _ = _fake_capture(version_result=(1, "", "boom"))
    monkeypatch.setattr(agents_registry, "run_capture", fake)
    status = AgentStatus(id="muse")
    asyncio.run(agents_registry._probe_muse(str(shim), status))
    assert status.error == "boom"
    assert status.version == ""


def test_probe_dispatches_muse_to_its_probe(muse_install, monkeypatch):
    _, shim, binary = muse_install
    monkeypatch.setattr(agents_registry.sys, "platform", "linux")
    fake, calls = _fake_capture()
    monkeypatch.setattr(agents_registry, "run_capture", fake)
    agent = CliAgent(id="muse", name="Muse", exe_path=str(shim))
    status = asyncio.run(agents_registry.probe(agent, force=True))
    assert status.installed is True
    assert status.version == "1.4.3"
    assert status.auth == "auth_error"
    assert status.quota == {"sandbox": "n/a"}
    assert calls == [[str(binary), "--version"]]
