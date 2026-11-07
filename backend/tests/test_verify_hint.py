import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.services.cli_agents import broker
from app.services.cli_agents.adapters import AdapterUnsafe, verify_hint


def test_verify_hint_is_empty_without_commands():
    assert verify_hint(None) == ""
    assert verify_hint([]) == ""


def test_verify_hint_lists_each_command_verbatim_with_the_allowed_form():
    hint = verify_hint(["cd front-end && npx tsc -p tsconfig.app.json --noEmit", "cd front-end && npx ng test --watch=false"])
    assert "- `cd front-end && npx tsc -p tsconfig.app.json --noEmit`" in hint
    assert "- `cd front-end && npx ng test --watch=false`" in hint
    assert "ruta relativa" in hint and "sin PowerShell" in hint


def _capture_task(monkeypatch, verify, read_only):
    captured = {}

    class FakeAdapter:
        def build(self, agent, exe, job_id, task, *args, **kwargs):
            captured["task"] = task
            raise AdapterUnsafe("stop")

    monkeypatch.setattr(broker, "adapter_for", lambda base: FakeAdapter())
    monkeypatch.setattr(broker.agents_registry, "resolve_exe", lambda agent: "claude")
    rt = SimpleNamespace(
        request=SimpleNamespace(task="Haz X", verify=verify),
        job=SimpleNamespace(id="job1", tier="standard"),
        workspace=Path("."),
    )
    agent = SimpleNamespace(base="claude", id="claude")
    outcome = asyncio.run(broker._run_attempt(rt, agent, "", 60, read_only=read_only))
    assert outcome.error == "stop"
    return captured["task"]


def test_attempt_task_includes_the_verify_commands(monkeypatch):
    task = _capture_task(monkeypatch, ["cd front-end && npx tsc --noEmit"], read_only=False)
    assert task.startswith("Haz X")
    assert "- `cd front-end && npx tsc --noEmit`" in task


def test_read_only_attempt_task_has_no_verify_hint(monkeypatch):
    assert _capture_task(monkeypatch, ["cd front-end && npx tsc --noEmit"], read_only=True) == "Haz X"


def test_attempt_task_unchanged_without_verify(monkeypatch):
    assert _capture_task(monkeypatch, [], read_only=False) == "Haz X"
