import json

import pytest

from app.models.delegate import JobRequest
from app.services import usage_tracker
from app.services.cli_agents import adapters, broker
from tests.test_anthropic_lane import _agent
from tests.test_delegation_broker import env, _wait  # noqa: F401  (env es un fixture)


def test_input_with_cache_sums_three_keys_and_tolerates_missing():
    assert adapters._input_with_cache({"input_tokens": 1, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3}) == 6
    assert adapters._input_with_cache({"input_tokens": None, "cache_read_input_tokens": 5}) == 5
    assert adapters._input_with_cache({}) == 0


CLAUDE_JSON = json.dumps({
    "result": "ok", "is_error": False, "total_cost_usd": 5.85,
    "usage": {"input_tokens": 194, "cache_creation_input_tokens": 241052,
              "cache_read_input_tokens": 13454368, "output_tokens": 61419},
})


def test_claude_parse_counts_cache_tokens():
    result = adapters.ClaudeAdapter().parse(CLAUDE_JSON, "", 0, None)
    assert result.usage["input_tokens"] == 13695614
    assert result.usage["output_tokens"] == 61419
    assert result.usage["cost_usd"] == 5.85


def test_openrouter_parse_reports_zero_cost():
    payload = json.dumps({"result": "ok", "total_cost_usd": 1.23, "usage": {"input_tokens": 10, "output_tokens": 5}})
    assert adapters.OpenRouterAdapter().parse(payload, "", 0, None).usage["cost_usd"] == 0.0


def test_log_line_formats_events():
    status = {"event": "status", "status": "running", "agent_id": "claude", "model": "sonnet", "argv": ["x"]}
    assert broker._log_line(status) == "[status] running agent_id=claude model=sonnet"
    attempt = {"event": "attempt", "agent_id": "muse", "signal": "quota_exhausted", "excerpt": "limit"}
    assert broker._log_line(attempt) == "[attempt] agent_id=muse signal=quota_exhausted excerpt=limit"
    assert broker._log_line({"event": "line", "stream": "stdout", "text": "hola"}) == "[stdout] hola"


def test_log_line_truncates_joins_and_flattens():
    line = broker._log_line({"event": "x", "big": "a" * 400, "files": ["a", "b"], "msg": "uno\ndos"})
    assert f"big={'a' * 300} " in line and "a" * 301 not in line
    assert "files=a,b" in line and "msg=uno dos" in line


async def test_job_records_usage_for_work_and_review_and_sums_it(env, monkeypatch):
    configured = env["registry"]
    configured.cli_agents = [_agent("codex"), _agent()]
    configured.delegation.thinkers = ["anthropic"]
    configured.delegation.tier_order = {"complex": ["codex", "anthropic"]}
    configured.delegation.review_default = True
    monkeypatch.setattr(adapters, "env_value", lambda _: "credits-secret")

    async def run(rt, spec):
        if "--read-only" in spec.argv:
            out = {"text": '{"verdict":"approve","issues":[]}', "is_error": False, "total_cost_usd": 0.2,
                   "usage": {"input_tokens": 30, "cache_read_input_tokens": 70, "output_tokens": 7}}
            return 0, json.dumps(out), "", False
        events = [{"type": "item.completed", "item": {"type": "agent_message", "text": "hecho"}},
                  {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 10}}]
        return 0, "\n".join(json.dumps(e) for e in events), "", False

    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="complex"))).id)
    assert job.status == "succeeded" and job.review_status == "passed"
    assert usage_tracker.record.await_count == 2
    assert (job.tokens_in, job.tokens_out) == (200, 17)
    assert job.cost_usd == pytest.approx(0.2)


def test_claude_lane_has_no_out_of_workspace_or_exec_capable_grants():
    from app.services.cli_agents.adapters import CLAUDE_BUILD_TOOLS
    forbidden = ("Bash(cat", "Bash(grep", "Bash(rg", "Bash(ls", "Bash(wc", "Bash(find", "Bash(sed", "Bash(cmake",
                 "PowerShell(Get-Content", "PowerShell(Get-ChildItem", "PowerShell(Select-String")
    assert not [tool for tool in CLAUDE_BUILD_TOOLS if tool.startswith(forbidden)]
