import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx
import pytest

from app.api import mcp
from app.core.quota_signals import detect_signal
from app.models.delegate import AgentStatus, Job, JobRequest
from app.models.smart import DEFAULT_CLI_AGENTS, CliAgent, DelegationConfig
from app.services import budget_service, health_service, providers_service, usage_tracker
from app.services.cli_agents import adapters, broker, registry, verifier
from app.services.cli_agents.verifier import CheckResult
from tests.test_registry_migration_smart import config_dir
from tests.test_delegation_broker import env, _wait


def _agent(agent_id="anthropic", **overrides):
    default = next(a for a in DEFAULT_CLI_AGENTS if a["id"] == agent_id)
    return CliAgent(**{**default, "enabled": True, **overrides})


@pytest.fixture(autouse=True)
def isolated_budget(monkeypatch):
    monkeypatch.setattr(budget_service, "spend_since", AsyncMock(return_value={"requests": 0, "cost_usd": 0}))


@pytest.mark.parametrize("tier,turns,budget", [("trivial", 15, 0.5), ("simple", 30, 1), ("standard", 60, 3), ("complex", 120, 8)])
@pytest.mark.parametrize("read_only", [False, True])
def test_anthropic_build_isolates_credentials_and_limits(tmp_path, monkeypatch, tier, turns, budget, read_only):
    monkeypatch.setattr(adapters, "env_value", lambda name: "credits-secret" if name == "ANTHROPIC_CREDITS_API_KEY" else "")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "proxy-secret")
    agent = _agent()
    spec = adapters.AnthropicAdapter().build(agent, "ignored.exe", "j", "haz X", agent.model_for(tier), tmp_path, tier, 900, read_only=read_only)
    assert spec.argv[0] == sys.executable
    assert Path(spec.argv[1]).is_absolute() and Path(spec.argv[1]).name == "anthropic_runner.py"
    assert spec.argv[spec.argv.index("--model") + 1] == agent.model_for(tier)
    assert spec.argv[spec.argv.index("--cwd") + 1] == str(tmp_path)
    assert int(spec.argv[spec.argv.index("--max-turns") + 1]) == turns
    assert float(spec.argv[spec.argv.index("--max-budget-usd") + 1]) == budget
    assert ("--read-only" in spec.argv) is read_only
    assert spec.env["ANTHROPIC_API_KEY"] == "credits-secret"
    assert spec.env["CLAUDE_CONFIG_DIR"] == str(tmp_path / ".bipolar" / "anthropic-sdk")
    for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_CREDITS_API_KEY", "OPENAI_API_KEY"):
        assert name not in spec.env
    assert "haz X" in spec.stdin_payload.decode()
    assert "credits-secret" not in str(spec.argv) + str(spec.redacted)
    assert "proxy-secret" not in str(spec.env)


def test_anthropic_build_without_credits_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(adapters, "env_value", lambda _: "")
    with pytest.raises(adapters.AdapterUnsafe, match="ANTHROPIC_CREDITS_API_KEY"):
        adapters.AnthropicAdapter().build(_agent(), sys.executable, "j", "t", "", tmp_path, "standard", 900)


def test_anthropic_parse_last_result_preserves_cost_and_session():
    payload = {"text": "listo", "is_error": False, "total_cost_usd": 0.12, "usage": {"input_tokens": 11, "output_tokens": 4}, "session_id": "session-one", "error": ""}
    result = adapters.AnthropicAdapter().parse('noise\n{"text":"parcial"}\n' + json.dumps(payload), "", 0, None)
    assert result.text == "listo" and not result.structured_error
    assert result.usage == {"input_tokens": 11, "output_tokens": 4, "cost_usd": 0.12}
    assert result.session_id == "session-one"


@pytest.mark.parametrize("message,kind", [("Your credit balance is too low to access the Anthropic API", "quota_exhausted"), ("401 Unauthorized", "auth"), ("429 rate limit", "rate_limit"), ("529 overloaded_error", "overloaded")])
def test_anthropic_parse_error_keeps_failure_signal(message, kind):
    payload = {"text": "parcial", "is_error": True, "error": message, "total_cost_usd": 0.2}
    result = adapters.AnthropicAdapter().parse(json.dumps(payload), "", 1, None)
    assert result.structured_error and message in result.text
    assert result.usage["cost_usd"] == 0.2
    assert detect_signal(result.text).kind == kind


@pytest.mark.parametrize("output", ["basura", "", "[]", '{"text":"sin resultado"}'])
def test_anthropic_parse_missing_result_is_structured_error(output):
    assert adapters.AnthropicAdapter().parse(output, "", 0, None).structured_error


@pytest.mark.parametrize("text", ["credit balance too low", "Your credit balance is too low", "credit_balance_too_low"])
def test_anthropic_credit_signal(text):
    assert detect_signal(text).kind == "quota_exhausted"


@pytest.fixture
def probe_env(tmp_path, monkeypatch):
    monkeypatch.setattr(health_service, "get_settings", lambda: SimpleNamespace(litellm_config_dir=str(tmp_path)))
    health_service.reload_for_tests()
    registry.invalidate()
    monkeypatch.setattr(registry, "env_value", lambda _: "credits-secret")
    monkeypatch.setattr(budget_service, "spend_since", AsyncMock(return_value={"requests": 2, "cost_usd": 12.5}))
    yield
    registry.invalidate()
    health_service.reload_for_tests()


def _install_client(monkeypatch, error=None):
    calls = {}

    def create(**kwargs):
        calls["request"] = kwargs
        calls["thread"] = threading.get_ident()
        if error:
            raise error
        return SimpleNamespace(content=[])

    class Client:
        messages = SimpleNamespace(create=create)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def client(**kwargs):
        calls["client"] = kwargs
        return Client()

    monkeypatch.setattr(anthropic, "Anthropic", client)
    return calls


@pytest.mark.asyncio
async def test_anthropic_probe_uses_credits_key_in_worker_and_reports_month_spend(probe_env, monkeypatch):
    calls = _install_client(monkeypatch)
    status = await registry.probe(_agent(), force=True)
    assert status.installed and status.exe == sys.executable and status.auth == "ok"
    assert calls["client"] == {"api_key": "credits-secret", "base_url": "https://api.anthropic.com", "max_retries": 0, "timeout": 15}
    assert calls["request"] == {"model": "claude-haiku-5-5", "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}]}
    assert calls["thread"] != threading.get_ident()
    assert status.quota["month_spend_usd"] == 12.5 and status.quota["max_monthly_usd"] == 200
    args = budget_service.spend_since.await_args.args
    assert args[0] == budget_service.provider_id_of("cli:anthropic")
    assert args[1] == budget_service.window_start("month").isoformat()


@pytest.mark.asyncio
async def test_anthropic_probe_missing_key_does_not_call_api(probe_env, monkeypatch):
    monkeypatch.setattr(registry, "env_value", lambda _: "")
    calls = _install_client(monkeypatch)
    status = await registry.probe(_agent(), force=True)
    assert status.installed and status.auth == "auth_error"
    assert status.error == "falta ANTHROPIC_CREDITS_API_KEY"
    assert calls == {}


@pytest.mark.parametrize("error_type,status_code,message,expected_auth,signal", [
    (anthropic.AuthenticationError, 401, "invalid key", "auth_error", None),
    (anthropic.BadRequestError, 400, "Your credit balance is too low", "ok", "quota_exhausted"),
    (anthropic.RateLimitError, 429, "rate limit", "ok", "rate_limit"),
    (anthropic.InternalServerError, 503, "overloaded", "unknown", None),
])
@pytest.mark.asyncio
async def test_anthropic_probe_classifies_errors(probe_env, monkeypatch, error_type, status_code, message, expected_auth, signal):
    response = httpx.Response(status_code, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"), headers={"retry-after": "45"})
    _install_client(monkeypatch, error_type(message, response=response, body={"error": {"message": message}}))
    status = await registry.probe(_agent(), force=True)
    assert status.auth == expected_auth
    if signal:
        assert status.last_signal == signal
    if signal == "quota_exhausted":
        assert status.state != "available" and 21500 < status.seconds_left <= 21600
    elif signal == "rate_limit":
        assert status.state == "cooling" and 0 < status.seconds_left <= 60
    elif signal is None:
        assert status.error and status.state == "available"


@pytest.mark.parametrize("spend,cap,expected", [(199.99, 200, None), (200, 200, "monthly_budget"), (210, 200, "monthly_budget"), (210, 0, None)])
def test_monthly_budget_rejection(env, spend, cap, expected):
    agent = _agent(max_monthly_usd=cap)
    status = AgentStatus(id=agent.id, installed=True, auth="ok", quota={"month_spend_usd": spend})
    assert broker._reject_agent(agent, "standard", "task", status, agent.default_model, False) == expected


def test_monthly_budget_falls_to_next_lane(env):
    configured = env["registry"]
    configured.cli_agents = [_agent(), _agent("codex")]
    configured.delegation.tier_order = {"standard": ["anthropic", "codex"]}
    statuses = {a.id: AgentStatus(id=a.id, installed=True, auth="ok", quota={"month_spend_usd": 200}) for a in configured.cli_agents}
    agent, _, _, skipped = broker.choose_agent("standard", configured, statuses)
    assert agent.id == "codex" and ("anthropic", "monthly_budget") in skipped


@pytest.mark.asyncio
async def test_anthropic_credit_failover_records_spent_cost(env, monkeypatch):
    configured = env["registry"]
    configured.cli_agents = [_agent(), _agent("claude")]
    configured.delegation.tier_order = {"standard": ["anthropic", "claude"]}
    monkeypatch.setattr(adapters, "env_value", lambda _: "credits-secret")
    script = [json.dumps({"text": "", "is_error": True, "error": "Your credit balance is too low", "total_cost_usd": 0.12}), json.dumps({"result": "listo", "is_error": False})]

    async def run(rt, spec):
        return 0, script.pop(0), "", False

    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard"))).id)
    assert job.status == "succeeded" and job.agent_id == "claude"
    assert job.attempts[0].signal == "quota_exhausted"
    assert any(call.args[0] == "cli:anthropic" and call.args[4] == 0.12 for call in usage_tracker.record.await_args_list)


def test_anthropic_default_models_budget_priority_and_thinkers():
    agent = _agent()
    assert agent.default_model == "claude-sonnet-5-5"
    assert [agent.model_for(t) for t in ("trivial", "simple", "standard", "complex")] == ["claude-haiku-5-5", "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
    assert agent.priority < min(a["priority"] for a in DEFAULT_CLI_AGENTS if a["id"] != "anthropic")
    assert agent.max_monthly_usd == 200 and agent.cooldown_s == 21600 and agent.timeout_s == 900
    assert agent.cost_weight == 0.1 and agent.quota_reset == "none" and agent.agentic
    assert DelegationConfig().thinkers == ["anthropic", "claude", "codex"]


@pytest.mark.asyncio
async def test_anthropic_exhaustion_survives_probe_cache_invalidation(probe_env, monkeypatch):
    calls = _install_client(monkeypatch)
    from app.core.quota_signals import QuotaSignal
    health_service.mark_signal("cli:anthropic", QuotaSignal("quota_exhausted", 21600, "credit balance too low"), cooldown_s=21600)
    registry.invalidate("anthropic")
    status = await registry.probe(_agent())
    assert status.state == "exhausted" and calls == {}
    await registry.probe(_agent(), force=True)
    assert "request" in calls and health_service.get("cli:anthropic").state == "available"


@pytest.mark.asyncio
async def test_anthropic_rate_limit_without_retry_header_has_short_cooldown(probe_env, monkeypatch):
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    _install_client(monkeypatch, anthropic.RateLimitError("rate limit", response=response, body={}))
    status = await registry.probe(_agent(), force=True)
    assert status.last_signal == "rate_limit" and 0 < status.seconds_left <= 60


@pytest.mark.asyncio
async def test_cached_anthropic_status_refreshes_monthly_spend(probe_env, monkeypatch):
    calls = _install_client(monkeypatch)
    first = await registry.probe(_agent())
    monkeypatch.setattr(budget_service, "spend_since", AsyncMock(return_value={"requests": 3, "cost_usd": 200}))
    calls.clear()
    second = await registry.probe(_agent())
    assert first.quota["month_spend_usd"] == 12.5 and second.quota["month_spend_usd"] == 200
    assert calls == {}


@pytest.mark.parametrize("output", ["garbage", json.dumps({"text": "", "is_error": True, "error": "error_max_budget_usd", "total_cost_usd": 0.15}), json.dumps({"text": "", "is_error": True, "error": "error_max_turns", "total_cost_usd": 0.15}), json.dumps({"text": "", "is_error": True, "error": "529 overloaded_error", "total_cost_usd": 0.15})])
@pytest.mark.asyncio
async def test_anthropic_failed_job_fails_over_and_records_cost(env, monkeypatch, output):
    configured = env["registry"]
    configured.cli_agents = [_agent(), _agent("claude")]
    configured.delegation.tier_order = {"standard": ["anthropic", "claude"]}
    monkeypatch.setattr(adapters, "env_value", lambda _: "credits-secret")
    script = [output, json.dumps({"result": "listo", "is_error": False})]

    async def run(rt, spec):
        return 0, script.pop(0), "", False

    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard"))).id)
    assert job.status == "succeeded" and [a.agent_id for a in job.attempts] == ["anthropic", "claude"]
    if output != "garbage":
        records = [call for call in usage_tracker.record.await_args_list if call.args[0] == "cli:anthropic"]
        assert len(records) == 1 and records[0].args[4] == 0.15


@pytest.mark.asyncio
async def test_anthropic_review_uses_sonnet_read_only_and_records_cost(env, monkeypatch):
    configured = env["registry"]
    configured.cli_agents = [_agent(), _agent("codex")]
    configured.delegation.thinkers = ["anthropic", "codex"]
    configured.delegation.tier_order = {"complex": ["codex", "anthropic"]}
    configured.delegation.review_default = True
    monkeypatch.setattr(adapters, "env_value", lambda _: "credits-secret")
    calls = []

    async def run(rt, spec):
        calls.append(spec)
        if "--read-only" in spec.argv:
            return 0, json.dumps({"text": '{"verdict":"approve","issues":[]}', "is_error": False, "total_cost_usd": 0.2}), "", False
        return 0, json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "hecho"}}), "", False

    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="complex"))).id)
    assert job.status == "succeeded" and job.review_status == "passed"
    review = calls[-1]
    assert "--read-only" in review.argv
    assert review.argv[review.argv.index("--model") + 1] == "claude-sonnet-5-5"
    records = [call for call in usage_tracker.record.await_args_list if call.args[0] == "cli:anthropic"]
    assert len(records) == 1 and records[0].args[4] == 0.2


@pytest.mark.asyncio
async def test_mcp_anthropic_delegation_runs_verification(env, monkeypatch):
    configured = env["registry"]
    configured.cli_agents = [_agent()]
    configured.delegation.allow_request_verify = True
    configured.delegation.tier_order = {"standard": ["anthropic"]}
    monkeypatch.setattr(adapters, "env_value", lambda _: "credits-secret")
    monkeypatch.setattr(broker, "_run_subprocess", AsyncMock(return_value=(0, json.dumps({"text": "listo", "is_error": False, "total_cost_usd": 0.1}), "", False)))
    checks = AsyncMock(return_value=[CheckResult("python --version", 0, 0.01, "Python 3.11")])
    monkeypatch.setattr(verifier, "run_checks", checks)
    response = await mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "delegate", "arguments": {"task": "t", "workspace": str(env["workspace"]), "tier": "standard", "verify": ["python --version"], "review": False, "wait_s": 5}}})
    result = response["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["status"] == "succeeded"
    assert result["structuredContent"]["agent_id"] == "anthropic"
    job = broker.get_job(result["structuredContent"]["job_id"])
    assert job.verification_status == "passed"
    assert checks.await_args.args[0] == ["python --version"]


def test_anthropic_seeding_preserves_custom_order_and_separate_proxy_key(config_dir):
    from tests.test_registry_migration_smart import _previous_version_json
    old = _previous_version_json({"standard": ["claude", "codex"]}, with_deepseek=True)
    old["cli_agents"] = [a for a in old["cli_agents"] if a["id"] != "anthropic"]
    old["delegation"]["thinkers"] = ["codex", "claude"]
    old["providers"].append({"id": "anthropic", "name": "proxy", "api_base": "https://api.anthropic.com", "auth_env_var": "ANTHROPIC_API_KEY"})
    (config_dir / "providers.json").write_text(json.dumps(old), encoding="utf-8")
    configured = providers_service.load_registry()
    assert configured.delegation.tier_order["standard"] == ["anthropic", "claude", "codex"]
    assert configured.delegation.thinkers == ["anthropic", "codex", "claude"]
    assert next(p for p in configured.providers if p.id == "anthropic").auth_env_var == "ANTHROPIC_API_KEY"
    providers_service.save_registry(configured)
    assert providers_service.load_registry().delegation.tier_order == configured.delegation.tier_order


def test_anthropic_installation_requires_importable_sdk(monkeypatch):
    def missing_sdk(name):
        assert name == "claude_agent_sdk"
        raise ImportError("SDK ausente")

    monkeypatch.setattr(registry.importlib, "import_module", missing_sdk)
    assert registry.resolve_exe(_agent()) == ""


@pytest.mark.parametrize("kind", ["work", "revision", "review"])
@pytest.mark.asyncio
async def test_paid_anthropic_attempt_blocks_next_attempt_at_monthly_cap(env, monkeypatch, kind):
    configured = env["registry"]
    agent = _agent(max_monthly_usd=0.2)
    configured.cli_agents = [agent]
    rt = broker.JobRuntime(job=Job(id="budget", created_at="t", tier="standard"), request=JobRequest(task="t", workspace=str(env["workspace"])), workspace=env["workspace"])
    spend = {"requests": 0, "cost_usd": 0}

    async def current_spend(*args):
        return dict(spend)

    async def record(*args):
        spend["cost_usd"] += args[4]

    outcome = broker.AttemptOutcome(ok=False, returncode=1, result=adapters.AdapterResult(text="error_max_budget_usd", structured_error=True, usage={"cost_usd": 0.2}))
    run = AsyncMock(return_value=outcome)
    monkeypatch.setattr(budget_service, "spend_since", current_spend)
    monkeypatch.setattr(usage_tracker, "record", record)
    monkeypatch.setattr(broker, "_run_attempt", run)
    first = await broker._run_attempt_guarded(rt, configured, agent, agent.default_model, 900, "t", read_only=kind == "review")
    assert first.result.usage["cost_usd"] == 0.2
    if kind == "work":
        second = await broker._run_work_attempt(rt, configured, agent, agent.default_model)
    else:
        second = await broker._run_attempt_guarded(rt, configured, agent, agent.default_model, 900, "t", read_only=kind == "review")
    assert second.error == "monthly_budget" and run.await_count == 1
