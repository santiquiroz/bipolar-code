from types import SimpleNamespace

import pytest

from app.services.cli_agents import broker
from app.services.cli_agents import verifier
from app.services.cli_agents.verifier import CheckResult


def _runtime(output_bytes=0):
    job = broker.Job(id="cap", created_at=broker._now())
    rt = broker.JobRuntime(job=job, request=SimpleNamespace(verify=[]), workspace=None)
    rt.output_bytes = output_bytes
    return rt


def test_capture_keeps_final_line_over_cap():
    capture = broker._StreamCapture()
    line = "x" * 1000
    for _ in range(broker.OUTPUT_CAP // len(line) + 10):
        capture.add(line, len(line))
    capture.add("FINAL", 5)
    assert capture.text().splitlines()[-1] == "FINAL"
    assert broker.TRUNCATED_MARK in capture.text()


def test_capture_without_overflow_has_no_marker():
    capture = broker._StreamCapture()
    lines = ["uno", "dos", "tres"]
    for line in lines:
        capture.add(line, len(line))
    assert capture.text() == "\n".join(lines)
    assert broker.TRUNCATED_MARK not in capture.text()


def test_consume_line_records_capture_when_display_capped():
    rt = _runtime(output_bytes=broker.OUTPUT_CAP + 1)
    capture = broker._StreamCapture()
    broker._consume_line(rt, "línea de revisión\n".encode(), "stdout", capture)
    assert "línea de revisión" in capture.text().splitlines()
    assert not [e for e in rt.lines if e.get("event") == "line"]


@pytest.mark.asyncio
async def test_verify_event_text_contains_output_tail(monkeypatch):
    rt = _runtime()
    rt.request = SimpleNamespace(verify=["pytest -q"])
    registry = SimpleNamespace(delegation=SimpleNamespace(verify_timeout_s=30))

    async def fake(commands, workspace, timeout_s):
        return [CheckResult("pytest -q", 1, 1.0, "falló: test_x")]

    monkeypatch.setattr(verifier, "run_checks", fake)
    await broker._record_verify_checks(rt, registry)
    events = [e for e in rt.lines if e.get("event") == "verify_result"]
    assert events and "falló: test_x" in events[0].get("text", "")
