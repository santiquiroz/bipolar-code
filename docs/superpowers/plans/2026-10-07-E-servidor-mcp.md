# Plan E — Servidor MCP de delegación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que Claude Code delegue tareas a los agentes CLI llamando herramientas MCP que expone bipolar (`delegate`, `job_status`, `job_output`, `cancel_job`, `list_agents`), sin los plugins `*-plugin-cc`.

**Architecture:** Un endpoint `POST /mcp` habla JSON-RPC 2.0 sobre Streamable HTTP respondiendo siempre `application/json`: sin SSE y sin estado de sesión. Las herramientas son una capa delgada sobre `broker.submit/get_job/job_output/cancel` y `agents_registry.probe`. `/mcp` es plano de control: exige `ui_api_key` y respeta `control_plane_allowed_cidrs`, igual que `/api`.

**Tech Stack:** FastAPI, pydantic v2, pytest + pytest-asyncio. Sin SDK de MCP: el subconjunto necesario (`initialize`, `ping`, `tools/list`, `tools/call`, notificaciones) son unas pocas funciones.

**Spec:** `docs/superpowers/specs/2026-10-07-orquestacion-transparente-design.md` (sección 7). Cambio respecto de la spec: no se entrega `Mcp-Session-Id`. En Streamable HTTP es opcional y el servidor no guarda estado; la spec se ajusta en la Task E2.

**Depende de:** Plan D completo. `JobRequest.verify`/`review` existen y `broker.InvalidRequest` existe.

## Global Constraints

- Repo: `C:\personal\bipolar-code\bipolar-orq`, tests con `cd backend && python -m pytest -q`.
- Sin dependencias nuevas.
- Versiones de protocolo aceptadas: `2025-06-18`, `2025-03-26` y `2024-11-05`. Una desconocida se responde con `2025-06-18`.
- Las herramientas nunca devuelven secretos ni rutas de credenciales; el `argv` ya viene redactado por el broker.
- Estilo: funciones cortas con nombres explícitos; sin docstrings largos.
- No ejecutar `git add/commit/push/reset/checkout`: el orquestador commitea.

## Review Focus

1. **`/mcp` sin llave o desde una red fuera de `control_plane_allowed_cidrs`**: 401 o 403. `/mcp` ejecuta procesos en el host. Test en E1.
2. **Cuerpo que no es JSON o JSON-RPC sin `method`**: error JSON-RPC `-32700` o `-32600` con status 200, nunca un 500. Test en E2.
3. **`tools/call` con argumentos inválidos** (falta `task`, `wait_s` fuera de rango): `isError: true` con un texto que diga qué falta, no una excepción. Test en E2.
4. **`delegate` con `wait_s` y un job que tarda más**: devuelve el estado `running` al vencer la espera, sin cancelar el job. Test en E2.
5. **Notificación (sin `id`)**: 202 sin cuerpo, incluida `notifications/initialized`. Test en E2.

---

### Task E1: `/mcp` es plano de control

**Files:**
- Modify: `backend/app/middleware/auth.py` (`_is_public`)
- Modify: `backend/app/middleware/network_guard.py` (`_is_control_plane`)
- Test: `backend/tests/test_mcp_guard.py`

**Interfaces:**
- Produces: `_is_public("/mcp")` es `False`; `_is_control_plane("/mcp")` es `True`. Lo mismo para cualquier subruta `/mcp/...`.

- [ ] **Step 1: Escribir los tests**

```python
"""/mcp exige llave y respeta la lista de redes del plano de control."""
from app.middleware.auth import _is_public
from app.middleware.network_guard import _is_control_plane


def test_mcp_is_not_public():
    assert _is_public("/mcp") is False
    assert _is_public("/mcp/x") is False
    assert _is_public("/") is True


def test_mcp_is_control_plane():
    assert _is_control_plane("/mcp") is True
    assert _is_control_plane("/api/providers") is True
    assert _is_control_plane("/v1/messages") is False
```

Agregar en `tests/test_network_guard.py`, siguiendo sus casos existentes para `/api`, uno para `POST /mcp` desde una IP fuera de la lista que espere 403. Agregar en `tests/test_auth_middleware.py` uno que espere 401 al hacer `POST /mcp` sin llave.

- [ ] **Step 2: Correr y verificar que fallan.**

- [ ] **Step 3: Implementar**

```python
# auth.py
def _is_public(path: str) -> bool:
    if path in {"/api/health"}:
        return True
    return not path.startswith(("/api", "/v1", "/mcp"))


# network_guard.py
def _is_control_plane(path: str) -> bool:
    return path.startswith(("/api", "/mcp"))
```

- [ ] **Step 4: Correr los tests nuevos y `tests/test_network_guard.py tests/test_auth_middleware.py`** → verde.

---

> **Nota del orquestador (2026-10-07):** `/api/*` y `/mcp` pasan por el guard del plano de control. Todo `TestClient` de estos tests debe crearse con `client=("127.0.0.1", 50000)`, como en `tests/test_smart_and_delegate_api.py`; si no, responde 403.

### Task E2: Endpoint MCP y herramientas

**Files:**
- Create: `backend/app/api/mcp.py`
- Modify: `backend/app/services/cli_agents/broker.py` (agregar `wait_job`)
- Modify: `backend/app/main.py` (`app.include_router(mcp_router.router)` sin prefijo, junto a `messages_router`, antes del fallback del SPA)
- Modify: `docs/superpowers/specs/2026-10-07-orquestacion-transparente-design.md` §7 (quitar "Entrega `Mcp-Session-Id`" y anotar que no hay sesión)
- Test: `backend/tests/test_mcp_api.py`

**Interfaces:**
- Consumes: `broker.submit(JobRequest) -> Job`, `broker.get_job(id) -> Optional[Job]`, `broker.job_output(id) -> Optional[str]`, `broker.cancel(id) -> Optional[Job]`, excepciones `broker.WorkspaceNotAllowed`, `broker.DelegationDisabled`, `broker.NoAgentAvailable` e `InvalidRequest`; `agents_registry.probe(agent) -> AgentStatus`; `providers_service.load_registry()`; `APP_VERSION` de `app.main` (importarlo dentro de la función para evitar el ciclo).
- Produces:
  - `broker.wait_job(job_id: str, timeout_s: float) -> Optional[Job]`: espera `asyncio.wait_for(asyncio.shield(rt.task), timeout_s)` y, al vencer o si el job ya terminó, devuelve `get_job(job_id)`. `None` si no existe.
  - `api/mcp.py`: `router`, `TOOLS: list[dict]`, `async handle_message(msg: dict) -> Optional[dict]` (devuelve `None` para notificaciones) y `job_summary(job: Job) -> dict`.

**Protocolo:**
- `POST /mcp`:
  - Lee el body. Si no es JSON → `{"jsonrpc": "2.0", "id": null, "error": {"code": -32700, "message": "parse error"}}` con status 200.
  - Si es un objeto → `handle_message`. Si devuelve `None`, la respuesta es `Response(status_code=202)`.
  - Si es una lista (batch de 2025-03-26) → procesa cada mensaje y devuelve la lista de respuestas no nulas. Si quedó vacía, 202.
- `GET /mcp` y `DELETE /mcp` → 405 con header `Allow: POST`.
- `handle_message`:
  - Sin `"jsonrpc": "2.0"` o sin `method` → `-32600 invalid request`.
  - Sin `id` → notificación: ejecutar si se conoce, devolver `None`.
  - `initialize` → `{"protocolVersion": <negociada>, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "bipolar-code", "version": APP_VERSION}, "instructions": INSTRUCTIONS}`.
  - `ping` → `{}`.
  - `tools/list` → `{"tools": TOOLS}`.
  - `tools/call` → `{"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}], "structuredContent": payload, "isError": bool}`.
  - Método desconocido → `-32601`.
  - Excepción inesperada → `-32603` con `sanitize_error(str(e))`.

`INSTRUCTIONS` (texto en español, corto): "Delegación de tareas de código a agentes CLI (Muse, Codex, cuentas de Claude Code y otros) gestionada por bipolar-code. Usa delegate con la ruta absoluta del repo; el job corre en segundo plano con verificación (comandos en verify) y revisión. Consulta con job_status (puede esperar hasta 540 s por llamada)."

**Herramientas** (`TOOLS`, con `inputSchema` JSON Schema):

| name | inputSchema (propiedades; `required`) | Comportamiento |
|---|---|---|
| `delegate` | `task` string (req), `workspace` string (req, ruta absoluta), `tier` enum `trivial/simple/standard/complex`, `agent` string, `verify` array de string (máx 10), `review` boolean, `wait_s` integer 0..540 (default 0) | `JobRequest(task, workspace, tier_hint=tier, agent_id=agent or "", verify=verify or [], review=review)` → `broker.submit`. Si `wait_s > 0`: `broker.wait_job`. Devuelve `job_summary`. |
| `job_status` | `job_id` (req), `wait_s` integer 0..540 | Con `wait_s > 0`: `wait_job`; si no: `get_job`. Devuelve `job_summary`, o error si no existe. |
| `job_output` | `job_id` (req), `tail_chars` integer 200..20000 (default 4000) | `{"job_id", "output": broker.job_output(id)[-tail_chars:]}`. |
| `cancel_job` | `job_id` (req) | `job_summary(await broker.cancel(id))`. |
| `list_agents` | — | Para cada agente habilitado del registry: `{"id", "base", "label": account_label or name, "installed", "auth", "state", "seconds_left"}` desde `agents_registry.probe(agent)`. |

- La validación de argumentos se hace a mano en funciones `_require_str`, `_int_in_range` y `_str_list`. Un argumento inválido lanza `ToolError(mensaje)` y se responde `isError: true` con `{"error": mensaje}`.
- Las excepciones del broker (`WorkspaceNotAllowed`, `DelegationDisabled`, `NoAgentAvailable`, `InvalidRequest` y `pydantic.ValidationError`) se convierten en `isError: true` con un mensaje legible. `NoAgentAvailable` incluye sus `reasons`.

**`job_summary(job)`:** `{"job_id", "status", "tier", "agent_id", "model", "escalations", "verification_status", "review_status", "attempts": [{"kind", "agent_id", "returncode", "signal", "detail"}], "files_touched", "error", "output_tail": job.output_tail[-2000:] si el estado es terminal; si no, ""}`.

- [ ] **Step 1: Escribir los tests**

```python
"""Endpoint MCP: protocolo JSON-RPC y herramientas sobre el broker."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.models.delegate import Attempt, Job
from app.services.cli_agents import broker


@pytest.fixture
def client():
    from app.core.config import get_settings
    from app.main import app
    return TestClient(app, headers={"x-api-key": get_settings().ui_api_key})


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
```

- [ ] **Step 2: Correr y verificar que fallan.**
- [ ] **Step 3: Implementar** `broker.wait_job`, `api/mcp.py`, el registro en `main.py` y el ajuste de la spec.
- [ ] **Step 4: Correr los tests nuevos y la suite completa** → verde.
- [ ] **Step 5: Probar con un cliente real** (lo hace el orquestador): levantar el backend del worktree en otro puerto con un `LITELLM_CONFIG_DIR` temporal y llamar `initialize` y `tools/list` con `curl`. Si Claude Code está disponible, `claude mcp add --transport http bipolar-test http://127.0.0.1:<puerto>/mcp --header "x-api-key: <llave>"` y `claude mcp list` debe mostrarlo conectado.
