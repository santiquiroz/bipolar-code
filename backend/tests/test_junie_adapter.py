import asyncio
import json

import pytest

from app.core.quota_signals import detect_signal
from app.models.delegate import AgentStatus
from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent
from app.services.cli_agents import adapters as ad
from app.services.cli_agents import registry as agents_registry

RESULT = {
    "sessionId": "session-261112-093623-gqgb",
    "taskName": "Crear archivo hola.txt",
    "result": "- Se creó el archivo `hola.txt`.\n\nSTATUS: DONE",
    "changes": [{"afterRelativePath": "hola.txt"}],
    "llmUsage": [{"model": "Qwen/Qwen3.6-27B-FP8", "calls": 7, "cost": 0.00278424, "inputTokens": 15090,
                  "cacheInputTokens": 12336, "cacheCreateTokens": 0, "outputTokens": 373}],
}
NO_AUTH = "\x1b[31mCannot find authorization. Please authenticate before running Junie.\x1b[0m\n"


def _agent(**overrides) -> CliAgent:
    base = next(d for d in DEFAULT_CLI_AGENTS if d["id"] == "junie")
    return CliAgent(**{**base, "enabled": True, **overrides})


@pytest.fixture
def junie_env(tmp_path, monkeypatch):
    for key in ("JUNIE_API_KEY", "JUNIE_HOME", "JUNIE_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JUNIE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("JUNIE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def _install(data, version="4020.7"):
    binary = data / "versions" / "4020.7" / "junie" / "junie.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    (data / "current").write_text(version + "\n", encoding="utf-8")
    return binary


def _build(tmp_path, **kwargs):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    params = {"agent": _agent(), "exe": "junie", "job_id": "j1", "task": "TAREA-SECRETA", "model": "",
              "workspace": ws, "tier": "simple", "timeout_s": 900}
    return ws, ad.JunieAdapter().build(**{**params, **kwargs})


def test_build_passes_fixed_flags_and_task_only_by_stdin(junie_env):
    ws, spec = _build(junie_env)
    assert spec.argv[spec.argv.index("--project") + 1] == str(ws)
    for flag in ad.JUNIE_FIXED_FLAGS:
        assert flag in spec.argv
    out = spec.argv[spec.argv.index("--json-output-file") + 1]
    assert out == str(ws / ".bipolar" / "jobs" / "j1" / "junie-result.json") and spec.out_file == ws / ".bipolar" / "jobs" / "j1" / "junie-result.json"
    assert spec.argv[spec.argv.index("--model") + 1] == "junie-lite" and "--effort" not in spec.argv
    assert not any("TAREA-SECRETA" in arg for arg in spec.argv)
    payload = spec.stdin_payload.decode("utf-8")
    assert payload.startswith("TAREA-SECRETA") and "STATUS: DONE" in payload and "junie" in payload
    assert (ws / ".bipolar" / ".gitignore").read_text(encoding="utf-8") == "*\n"


def test_credit_model_gets_effort_by_tier(junie_env):
    _, spec = _build(junie_env, model="gemini-3.7-flash", tier="standard")
    assert spec.argv[spec.argv.index("--model") + 1] == "gemini-3.7-flash"
    assert spec.argv[spec.argv.index("--effort") + 1] == "medium"


def test_read_only_is_refused(junie_env):
    with pytest.raises(ad.AdapterUnsafe, match="read_only_unsupported"):
        _build(junie_env, read_only=True)


def test_bat_shim_resolves_to_the_current_versioned_exe(junie_env):
    binary = _install(junie_env / "data")
    assert ad.JunieAdapter().binary_for(str(junie_env / "junie.bat")) == [str(binary)]


def test_bat_shim_with_unsafe_current_falls_back_to_the_shim(junie_env, monkeypatch):
    _install(junie_env / "data", version="..\\evil")
    monkeypatch.setattr(ad, "exe_argv", lambda exe: ["shim", exe])
    shim = str(junie_env / "junie.bat")
    assert ad.JunieAdapter().binary_for(shim) == ["shim", shim]


def test_skill_locations_must_be_existing_folders(junie_env):
    with pytest.raises(ad.AdapterUnsafe, match="junie_skill_location_invalid"):
        _build(junie_env, agent=_agent(skill_locations=[str(junie_env / "missing")]))
    skills = junie_env / "skills"
    skills.mkdir()
    _, spec = _build(junie_env, agent=_agent(skill_locations=[str(skills)]))
    assert spec.argv[spec.argv.index("--skill-location") + 1] == str(skills)


@pytest.mark.parametrize("arg", ["--brave", "--skill-location=x", "--auth=x", "--project=x", "--provider=openai"])
def test_extra_args_cannot_open_junie_permissions(junie_env, arg):
    with pytest.raises(ad.AdapterUnsafe):
        _build(junie_env, agent=_agent(extra_args=[arg]))


def test_parse_reads_result_usage_and_session_from_out_file(tmp_path):
    out = tmp_path / "junie-result.json"
    out.write_text(json.dumps(RESULT), encoding="utf-8")
    result = ad.JunieAdapter().parse("transcript", "", 0, out)
    assert result.text.endswith("STATUS: DONE") and result.structured_error is False
    assert result.usage == {"input_tokens": 27426, "output_tokens": 373}
    assert result.session_id == "session-261112-093623-gqgb"


def test_parse_without_out_file_surfaces_missing_login_as_auth(tmp_path):
    result = ad.JunieAdapter().parse(NO_AUTH, "Junie 26.11.1 (4020.7) (pid=1): log file: x", 0, tmp_path / "none.json")
    assert result.structured_error is True and "\x1b" not in result.text
    assert "Cannot find authorization" in result.text
    assert detect_signal(result.text).kind == "auth"


def test_parse_errors_list_is_a_structured_error(tmp_path):
    out = tmp_path / "junie-result.json"
    message = "Model 'gemini-3.7-flash' is unavailable with Junie Lite-only access. Choose Junie Lite."
    out.write_text(json.dumps({"errors": [message]}), encoding="utf-8")
    result = ad.JunieAdapter().parse("", "", 1, out)
    assert result.structured_error is True and result.text == message
    assert detect_signal(result.text).kind == "model_unavailable"


def test_signed_in_checks_dpapi_files_or_api_key(junie_env, monkeypatch):
    adapter = ad.JunieAdapter()
    assert adapter.signed_in() is False
    creds = junie_env / "home" / "dpapi_credentials"
    creds.mkdir(parents=True)
    (creds / "abc.dpapi").touch()
    assert adapter.signed_in() is True
    (creds / "abc.dpapi").unlink()
    monkeypatch.setenv("JUNIE_API_KEY", "k")
    assert adapter.signed_in() is True
    assert ad.JunieAdapter.extra_env()["JUNIE_API_KEY"] == "k"


def test_known_paths_junie_is_the_user_shim(tmp_path, monkeypatch):
    monkeypatch.setattr(agents_registry, "_home", lambda: tmp_path)
    assert agents_registry.known_paths("junie") == [tmp_path / ".local" / "bin" / "junie.bat"]


def test_probe_reads_version_login_and_free_model(junie_env, monkeypatch):
    binary = _install(junie_env / "data")
    creds = junie_env / "home" / "dpapi_credentials"
    creds.mkdir(parents=True)
    (creds / "abc.dpapi").touch()
    calls = []

    async def run_capture(argv, timeout=15, cwd=None, env=None):
        calls.append(list(argv))
        return 0, "Junie version: 26.11.1 (4020.7)\n", "Junie 26.11.1 (4020.7) (pid=1): log file: x\n"

    monkeypatch.setattr(agents_registry, "run_capture", run_capture)
    status = AgentStatus(id="junie", checked_at="t")
    asyncio.run(agents_registry._probe_junie(str(junie_env / "junie.bat"), status))
    assert calls == [[str(binary), "--version"]]
    assert status.version == "26.11.1" and status.auth == "ok" and status.default_model == "junie-lite"


def test_probe_without_login_is_auth_error(junie_env, monkeypatch):
    async def run_capture(argv, timeout=15, cwd=None, env=None):
        return 0, "Junie version: 26.11.1 (4020.7)\n", ""

    monkeypatch.setattr(agents_registry, "run_capture", run_capture)
    status = AgentStatus(id="junie", checked_at="t")
    asyncio.run(agents_registry._probe_junie("junie", status))
    assert status.auth == "auth_error"
