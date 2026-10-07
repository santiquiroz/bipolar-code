"""Servidor MCP de delegación: JSON-RPC 2.0 sobre Streamable HTTP, sin estado."""
import json
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.utils import sanitize_error
from app.models.delegate import Job, JobRequest
from app.services import providers_service
from app.services.cli_agents import broker
from app.services.cli_agents import registry as agents_registry

router = APIRouter(tags=["mcp"])

INSTRUCTIONS = (
    "Delegación de tareas de código a agentes CLI (Muse, Codex, cuentas de Claude Code y otros) "
    "gestionada por bipolar-code. Usa delegate con la ruta absoluta del repo; el job corre en segundo "
    "plano con verificación (comandos en verify) y revisión. Consulta con job_status "
    "(puede esperar hasta 540 s por llamada)."
)

_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
_DEFAULT_PROTOCOL = "2025-06-18"
_TIERS = ("trivial", "simple", "standard", "complex")
MAX_BATCH = 20

TOOLS: list[dict] = [
    {
        "name": "delegate",
        "description": "Delega una tarea de código a un agente CLI en segundo plano.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Qué hacer, en lenguaje natural."},
                "workspace": {"type": "string", "description": "Ruta absoluta del repo."},
                "tier": {"type": "string", "enum": list(_TIERS)},
                "agent": {"type": "string", "description": "Agente preferido (id del registry)."},
                "verify": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                "review": {"type": "boolean"},
                "wait_s": {"type": "integer", "minimum": 0, "maximum": 540, "default": 0},
            },
            "required": ["task", "workspace"],
        },
    },
    {
        "name": "job_status",
        "description": "Estado de un job, con intentos, verificación y revisión.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "wait_s": {"type": "integer", "minimum": 0, "maximum": 540, "default": 0},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "job_output",
        "description": "Últimas líneas del log de un job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "tail_chars": {"type": "integer", "minimum": 200, "maximum": 20000, "default": 4000},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "cancel_job",
        "description": "Cancela un job en curso.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "list_agents",
        "description": "Agentes habilitados con su estado de cuota.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class ToolError(Exception):
    pass


def _require_str(args: dict, name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"falta '{name}': debe ser texto no vacío")
    return value


def _int_in_range(args: dict, name: str, lo: int, hi: int, default: int) -> int:
    value = args.get(name, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ToolError(f"'{name}' debe ser un entero entre {lo} y {hi}")
    return value


def _str_list(args: dict, name: str, max_items: int = 10) -> list[str]:
    value = args.get(name)
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ToolError(f"'{name}' debe ser una lista de textos")
    if len(value) > max_items:
        raise ToolError(f"'{name}' admite hasta {max_items} elementos")
    return value


def job_summary(job: Job) -> dict:
    terminal = job.status in broker.TERMINAL_STATUSES
    return {
        "job_id": job.id,
        "status": job.status,
        "tier": job.tier,
        "agent_id": job.agent_id,
        "model": job.model,
        "escalations": job.escalations,
        "verification_status": job.verification_status,
        "review_status": job.review_status,
        "attempts": [
            {"kind": a.kind, "agent_id": a.agent_id, "returncode": a.returncode,
             "signal": a.signal, "detail": a.detail}
            for a in job.attempts
        ],
        "files_touched": job.files_touched,
        "error": job.error,
        "output_tail": job.output_tail[-2000:] if terminal else "",
    }


def _tool_result(payload: dict, is_error: bool) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}],
        "structuredContent": payload,
        "isError": is_error,
    }


async def _tool_delegate(args: dict) -> dict:
    task = _require_str(args, "task")
    workspace = _require_str(args, "workspace")
    tier = args.get("tier")
    if tier is not None and tier not in _TIERS:
        raise ToolError("'tier' debe ser trivial, simple, standard o complex")
    agent = args.get("agent") or ""
    if not isinstance(agent, str):
        raise ToolError("'agent' debe ser texto")
    verify = _str_list(args, "verify")
    review = args.get("review")
    if review is not None and not isinstance(review, bool):
        raise ToolError("'review' debe ser true o false")
    wait_s = _int_in_range(args, "wait_s", 0, 540, 0)
    job = await broker.submit(JobRequest(task=task, workspace=workspace, tier_hint=tier,
                                         agent_id=agent, verify=verify, review=review))
    if wait_s > 0:
        job = await broker.wait_job(job.id, wait_s) or job
    return job_summary(job)


async def _tool_job_status(args: dict) -> dict:
    job_id = _require_str(args, "job_id")
    wait_s = _int_in_range(args, "wait_s", 0, 540, 0)
    job = await broker.wait_job(job_id, wait_s) if wait_s > 0 else broker.get_job(job_id)
    if job is None:
        raise ToolError(f"job desconocido: {job_id}")
    return job_summary(job)


async def _tool_job_output(args: dict) -> dict:
    job_id = _require_str(args, "job_id")
    tail_chars = _int_in_range(args, "tail_chars", 200, 20000, 4000)
    output = broker.job_output(job_id)
    if output is None:
        raise ToolError(f"job desconocido: {job_id}")
    return {"job_id": job_id, "output": output[-tail_chars:]}


async def _tool_cancel_job(args: dict) -> dict:
    job_id = _require_str(args, "job_id")
    job = await broker.cancel(job_id)
    if job is None:
        raise ToolError(f"job desconocido: {job_id}")
    return job_summary(job)


async def _tool_list_agents(_args: dict) -> dict:
    registry = providers_service.load_registry()
    agents = []
    for agent in registry.cli_agents:
        if not agent.enabled:
            continue
        status = await agents_registry.probe(agent)
        agents.append({"id": agent.id, "base": agent.base, "label": agent.account_label or agent.name,
                       "installed": status.installed, "auth": status.auth, "state": status.state,
                       "seconds_left": status.seconds_left})
    return {"agents": agents}


_TOOL_HANDLERS: dict[str, Callable[[dict], Awaitable[dict]]] = {
    "delegate": _tool_delegate,
    "job_status": _tool_job_status,
    "job_output": _tool_job_output,
    "cancel_job": _tool_cancel_job,
    "list_agents": _tool_list_agents,
}


def _broker_error_text(error: Exception) -> str:
    if isinstance(error, broker.NoAgentAvailable):
        return "no_agent_available: " + "; ".join(error.reasons)
    return str(error)


async def _tools_call(params: dict) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}
    if not isinstance(name, str) or not name:
        return _tool_result({"error": "falta 'name': debe ser el nombre de la herramienta"}, True)
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return _tool_result({"error": f"herramienta desconocida: {name}"}, True)
    if not isinstance(args, dict):
        return _tool_result({"error": "'arguments' debe ser un objeto"}, True)
    try:
        return _tool_result(await handler(args), False)
    except ToolError as e:
        return _tool_result({"error": str(e)}, True)
    except (broker.WorkspaceNotAllowed, broker.DelegationDisabled,
            broker.NoAgentAvailable, broker.InvalidRequest, ValidationError) as e:
        return _tool_result({"error": _broker_error_text(e)}, True)


async def _m_initialize(params: dict) -> dict:
    from app.main import APP_VERSION
    wanted = params.get("protocolVersion")
    version = wanted if wanted in _PROTOCOL_VERSIONS else _DEFAULT_PROTOCOL
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "bipolar-code", "version": APP_VERSION},
        "instructions": INSTRUCTIONS,
    }


async def _m_ping(_params: dict) -> dict:
    return {}


async def _m_tools_list(_params: dict) -> dict:
    return {"tools": TOOLS}


async def _m_tools_call(params: dict) -> dict:
    return await _tools_call(params)


_METHODS: dict[str, Callable[[dict], Awaitable[dict]]] = {
    "initialize": _m_initialize,
    "ping": _m_ping,
    "tools/list": _m_tools_list,
    "tools/call": _m_tools_call,
}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


async def _run_notification(handler: Callable[[dict], Awaitable[dict]], params: dict) -> None:
    try:
        await handler(params)
    except Exception:
        pass


def _without_wait(params: dict, method: str) -> dict:
    if method != "tools/call" or params.get("name") not in ("delegate", "job_status"):
        return params
    args = params.get("arguments")
    if not isinstance(args, dict):
        return params
    return {**params, "arguments": {**args, "wait_s": 0}}


async def handle_message(msg: dict, in_batch: bool = False) -> Optional[dict]:
    msg_id = msg.get("id")
    try:
        method = msg.get("method")
        if msg.get("jsonrpc") != "2.0" or not isinstance(method, str) or not method:
            return _error(msg_id, -32600, "invalid request")
        params = msg.get("params")
        params = params if isinstance(params, dict) else {}
        handler = _METHODS.get(method)
        if "id" not in msg:
            if method.startswith("notifications/") and handler is not None:
                await _run_notification(handler, params)
            return None
        if in_batch:
            params = _without_wait(params, method)
        if handler is None:
            return _error(msg_id, -32601, "method not found")
        return {"jsonrpc": "2.0", "id": msg_id, "result": await handler(params)}
    except Exception as e:
        return _error(msg_id, -32603, sanitize_error(str(e)))


async def _handle_batch(items: list) -> list:
    responses = []
    for item in items:
        if not isinstance(item, dict):
            responses.append(_error(None, -32600, "invalid request"))
            continue
        resp = await handle_message(item, in_batch=True)
        if resp is not None:
            responses.append(resp)
    return responses


@router.post("/mcp")
async def mcp_post(request: Request):
    try:
        body = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
    if isinstance(body, dict):
        resp = await handle_message(body)
        if resp is None:
            return Response(status_code=202)
        return JSONResponse(resp)
    if isinstance(body, list):
        if len(body) > MAX_BATCH:
            return JSONResponse(_error(None, -32600, "batch demasiado grande"))
        responses = await _handle_batch(body)
        if not responses:
            return Response(status_code=202)
        return JSONResponse(responses)
    return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}})


@router.get("/mcp")
async def mcp_get():
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
async def mcp_delete():
    return Response(status_code=405, headers={"Allow": "POST"})
