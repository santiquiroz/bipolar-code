"""Resultado completo del job (result_text) y reintento único ante errores de red."""
import json

import pytest

from app.models.delegate import JobRequest
from app.services import health_service
from app.services.cli_agents import broker
from tests.test_delegation_broker import _claude_ok, _codex_ok, _fake_subprocess, _wait, env  # noqa: F401 (fixture)


def _codex_text(text):
    return 0, json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}) + "\n" + json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}), "", False


def _network_error():
    return 1, "", "getaddrinfo ENOTFOUND api.anthropic.com", False


@pytest.mark.asyncio
async def test_result_text_keeps_full_adapter_text(env, monkeypatch):
    text = "resultado " * 1000  # 10000 caracteres
    run, _ = _fake_subprocess([_codex_text(text)])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="implementa el endpoint", workspace=str(env["workspace"])))
    job = await _wait(job.id)
    assert job.result_text == text
    assert broker.get_job(job.id).result_text == text


@pytest.mark.asyncio
async def test_job_output_includes_full_result(env, monkeypatch):
    text = "resultado " * 1000
    run, _ = _fake_subprocess([_codex_text(text)])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="implementa el endpoint", workspace=str(env["workspace"])))
    job = await _wait(job.id)
    assert broker.job_output(job.id).endswith("\n[result]\n" + text)
    assert broker.job_output(job.id, tail_chars=4000).endswith("\n[result]\n" + text)
    with open(job.log_path, encoding="utf-8") as fh:
        assert fh.read().endswith("\n[result]\n" + text)


def test_job_output_tail_prioritizes_result_over_lines(env):
    job = broker.Job(id="tail", created_at=broker._now(), result_text="R" * 300)
    rt = broker.JobRuntime(job=job, request=None, workspace=env["workspace"])
    broker._jobs["tail"] = rt
    try:
        for i in range(500):
            broker._emit(rt, {"event": "line", "stream": "stdout", "text": f"linea {i}"})
        out = broker.job_output("tail", tail_chars=1000)
        assert out.endswith("\n[result]\n" + "R" * 300)
        assert "linea 499" in out
        assert len(out) <= 1000
    finally:
        broker._jobs.pop("tail", None)


@pytest.mark.asyncio
async def test_network_error_retries_same_agent_once(env, monkeypatch):
    run, calls = _fake_subprocess([_network_error(), _codex_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="implementa el endpoint", workspace=str(env["workspace"])))
    job = await _wait(job.id)
    assert job.status == "succeeded" and job.agent_id == "codex"
    assert [a.agent_id for a in job.attempts] == ["codex", "codex"]
    assert job.attempts[0].signal == "network_error"
    assert health_service.effective_state("cli:codex") == "available"


@pytest.mark.asyncio
async def test_network_error_twice_moves_to_next_agent(env, monkeypatch):
    run, _ = _fake_subprocess([_network_error(), _network_error(), _claude_ok()])
    monkeypatch.setattr(broker, "_run_subprocess", run)
    job = await broker.submit(JobRequest(task="implementa el endpoint", workspace=str(env["workspace"])))
    job = await _wait(job.id)
    assert job.status == "succeeded" and job.agent_id == "claude"
    assert [a.agent_id for a in job.attempts] == ["codex", "codex", "claude"]
