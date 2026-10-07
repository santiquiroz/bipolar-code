"""Endpoint MCP: protocolo JSON-RPC y herramientas sobre el broker."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.models.delegate import Attempt, Job
from app.services.cli_agents import broker

# /mcp es plano de control: el host por defecto "testclient" no es una IP y responde 403
LOCAL_CLIENT = ("127.0.0.1", 50000)


@pytest.fixture
def client():
    from app.core.config import get_settings
    from app.main import app
    return TestClient(app, client=LOCAL_CLIENT, headers={"x-api-key": get_settings().ui_api_key})


def _rpc(client, method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        msg["id"] = id_
    if params is not None:
        msg["params"] = params
    return client.post("/mcp", json=msg)


def test_initialize_negotiates_version(client):
    body = _rpc(client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}).json()
    assert body["result"]["protocolVersion"] == "2025-03-26"
    assert body["result"]["serverInfo"]["name"] == "bipolar-code"
    assert body["result"]["capabilities"] == {"tools": {"listChanged": False}}
    unknown = _rpc(client, "initialize", {"protocolVersion": "1999-01-01"}).json()
    assert unknown["result"]["protocolVersion"] == "2025-06-18"


def test_notification_returns_202(client):
    resp = _rpc(client, "notifications/initialized", id_=None)
    assert resp.status_code == 202 and resp.content == b""


def test_tools_list_names(client):
    tools = _rpc(client, "tools/list").json()["result"]["tools"]
    assert [t["name"] for t in tools] == ["delegate", "job_status", "job_output", "cancel_job", "list_agents"]
    delegate = tools[0]
    assert delegate["inputSchema"]["required"] == ["task", "workspace"]


def test_parse_error_and_invalid_request(client):
    resp = client.post("/mcp", content=b"{not json", headers={"content-type": "application/json"})
    assert resp.status_code == 200 and resp.json()["error"]["code"] == -32700
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1}).json()["error"]["code"] == -32600
    assert _rpc(client, "nope/method").json()["error"]["code"] == -32601


def test_get_is_405(client):
    resp = client.get("/mcp")
    assert resp.status_code == 405 and resp.headers["allow"] == "POST"


def test_delegate_missing_task_is_tool_error(client):
    result = _rpc(client, "tools/call", {"name": "delegate", "arguments": {"workspace": "C:/x"}}).json()["result"]
    assert result["isError"] is True and "task" in result["content"][0]["text"]


def test_delegate_wraps_broker_and_waits(client, monkeypatch):
    job = Job(id="j1", created_at="t", status="running", tier="standard", agent_id="muse")
    captured = {}

    async def fake_submit(req, depth_header=""):
        captured["req"] = req
        return job

    async def fake_wait(job_id, timeout_s):
        captured["wait"] = (job_id, timeout_s)
        return job

    monkeypatch.setattr(broker, "submit", fake_submit)
    monkeypatch.setattr(broker, "wait_job", fake_wait)
    args = {"task": "haz X", "workspace": "C:/repo", "tier": "standard", "verify": ["pytest -q"], "wait_s": 30}
    result = _rpc(client, "tools/call", {"name": "delegate", "arguments": args}).json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["job_id"] == "j1" and result["structuredContent"]["status"] == "running"
    assert captured["req"].verify == ["pytest -q"] and captured["req"].tier_hint == "standard"
    assert captured["wait"] == ("j1", 30)


def test_delegate_broker_rejection_is_tool_error(client, monkeypatch):
    async def reject(req, depth_header=""):
        raise broker.InvalidRequest("verify_disabled")

    monkeypatch.setattr(broker, "submit", reject)
    result = _rpc(client, "tools/call", {"name": "delegate", "arguments": {"task": "x", "workspace": "C:/r", "verify": ["pytest"]}}).json()["result"]
    assert result["isError"] is True and "verify_disabled" in result["content"][0]["text"]


def test_job_status_unknown_is_tool_error(client, monkeypatch):
    monkeypatch.setattr(broker, "get_job", lambda job_id: None)
    result = _rpc(client, "tools/call", {"name": "job_status", "arguments": {"job_id": "nope"}}).json()["result"]
    assert result["isError"] is True


def test_wait_s_out_of_range_is_tool_error(client):
    result = _rpc(client, "tools/call", {"name": "job_status", "arguments": {"job_id": "x", "wait_s": 9999}}).json()["result"]
    assert result["isError"] is True and "wait_s" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_wait_job_returns_running_on_timeout(monkeypatch):
    job = Job(id="slow", created_at="t", status="running")

    async def forever():
        await asyncio.sleep(3600)

    rt = broker.JobRuntime(job=job, request=None, workspace=None)
    rt.task = asyncio.create_task(forever())
    broker._jobs["slow"] = rt
    try:
        got = await broker.wait_job("slow", 0.05)
        assert got.status == "running" and not rt.task.cancelled()
    finally:
        rt.task.cancel()
        broker._jobs.pop("slow", None)
