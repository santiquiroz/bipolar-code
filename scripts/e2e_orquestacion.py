"""Prueba de punta a punta de la orquestación transparente (Task G2).

Levanta un upstream falso OpenAI-compatible, crea un LITELLM_CONFIG_DIR temporal
con el provider "fake" (slot 0 = llave mala, slot 1 = llave buena), arranca el
backend real en el puerto 8100 y verifica failover, MCP, delegación y cuentas.

Solo stdlib. No toca C:\\litellm ni el bipolar del usuario.
Uso desde la raíz del repo:  python scripts/e2e_orquestacion.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

API_KEY = "bc-e2e"
BACKEND_PORT = 8100
UPSTREAM_PORT = 8291
BASE = f"http://127.0.0.1:{BACKEND_PORT}"
ROOT = Path(__file__).resolve().parent.parent


# --- Upstream falso OpenAI-compatible ---------------------------------------

class FakeUpstream(BaseHTTPRequestHandler):
    """POST /v1/chat/completions: 429 con la llave mala, SSE con la buena."""

    calls = {"bad": 0, "good": 0, "other": 0}
    calls_lock = threading.Lock()

    def log_message(self, *args):
        pass

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # drenar el body
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "not found"}})
            return
        key = (self.headers.get("Authorization", "") or "").removeprefix("Bearer ").strip()
        bucket = key if key in ("bad", "good") else "other"
        with FakeUpstream.calls_lock:
            FakeUpstream.calls[bucket] += 1
        if key == "bad":
            self._send_json(429, {"error": {"message": "rate limit"}})
        elif key == "good":
            self._send_sse()
        else:
            self._send_json(401, {"error": {"message": "invalid key"}})

    def _send_sse(self):
        chunks = [
            {"choices": [{"delta": {"content": "hola"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}},
        ]
        body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def start_fake_upstream():
    server = ThreadingHTTPServer(("127.0.0.1", UPSTREAM_PORT), FakeUpstream)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop_fake_upstream(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


def upstream_calls():
    with FakeUpstream.calls_lock:
        return dict(FakeUpstream.calls)


# --- Config temporal --------------------------------------------------------

def write_temp_config(config_dir, workspace):
    (config_dir / ".env").write_text(
        "UI_API_KEY=bc-e2e\nFAKE_KEY=bad\nFAKE_KEY_2=good\n", encoding="utf-8"
    )
    # load_registry() fusiona los providers por defecto (_DEFAULTS) y Provider no tiene flag de apagado: se neutralizan con routing apagado, sin fallbacks y smart apagado.
    registry = {
        "active_provider_id": "fake",
        "providers": [{
            "id": "fake",
            "name": "Fake",
            "api_base": f"http://127.0.0.1:{UPSTREAM_PORT}/v1",
            "litellm_prefix": "openai",
            "auth_env_var": "FAKE_KEY",
            "extra_auth_env_vars": ["FAKE_KEY_2"],
            "active_model": "fake-model",
            "model_info": {"supports_tools": True, "supports_vision": True},
        }],
        "routing_enabled": False,
        "fallback_provider_ids": [],
        "smart": {"enabled": False},
        "delegation": {
            "enabled": True,
            "workspace_allowlist": [str(workspace)],
            "allow_request_verify": True,
        },
    }
    (config_dir / "providers.json").write_text(
        json.dumps(registry, indent=2), encoding="utf-8"
    )


# --- Backend real ------------------------------------------------------------

def start_backend(config_dir):
    log_path = config_dir / "uvicorn-e2e.log"
    log_file = open(log_path, "wb")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(BACKEND_PORT)],
            cwd=ROOT / "backend",
            env={**os.environ, "LITELLM_CONFIG_DIR": str(config_dir)},
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_file.close()
    return proc, log_path


def stop_backend(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def wait_for_backend(timeout_s=30):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            status, _, _ = http_call("GET", "/api/health", timeout=2)
        except OSError:
            status = 0
        if 200 <= status < 300:
            return True
        time.sleep(0.5)
    return False


def backend_log_tail(log_path, lines=20):
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "sin log"
    return " | ".join(text[-lines:])[:1000]


# --- HTTP --------------------------------------------------------------------

def http_call(method, path, payload=None, extra_headers=None, timeout=30):
    """Todas las llamadas a /api y /mcp llevan x-api-key (loopback permitido)."""
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"x-api-key": API_KEY}
    if data is not None:
        headers["Content-Type"] = "application/json"
    headers.update(extra_headers or {})
    req = urlrequest.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except urlerror.HTTPError as e:
        return e.code, e.headers, e.read()


def mcp_call(method, params):
    status, _, body = http_call("POST", "/mcp", {
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
    })
    if status != 200:
        raise AssertionError(f"HTTP {status}: {body[:200]!r}")
    msg = json.loads(body)
    if "error" in msg:
        raise AssertionError(f"JSON-RPC error: {msg['error']}")
    return msg["result"]


# --- Afirmaciones --------------------------------------------------------------

def expect(cond, detail):
    if not cond:
        raise AssertionError(detail)


def check_messages():
    status, headers, body = http_call("POST", "/v1/messages", {
        "model": "fake-model",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "di hola"}],
    }, extra_headers={"anthropic-version": "2023-06-01"}, timeout=60)
    target = headers.get("x-bipolar-target")
    attempts = headers.get("x-bipolar-attempts")
    expect(status == 200, f"HTTP {status}: {body[:300]!r} (upstream: {upstream_calls()})")
    expect(target == "fake#1", f"x-bipolar-target={target!r} (upstream: {upstream_calls()})")
    expect(attempts == "2", f"x-bipolar-attempts={attempts!r} (upstream: {upstream_calls()})")
    expect(b"hola" in body, f"sin 'hola' en {body[:300]!r}")


def check_credentials():
    status, _, body = http_call("GET", "/api/providers/fake/credentials")
    expect(status == 200, f"HTTP {status}: {body[:300]!r}")
    slots = {s["slot"]: s["state"] for s in json.loads(body)["slots"]}
    expect(slots.get(0) == "cooling", f"slot 0 = {slots.get(0)!r}")
    expect(slots.get(1) == "available", f"slot 1 = {slots.get(1)!r}")


def check_mcp_initialize():
    result = mcp_call("initialize", {"protocolVersion": "2025-06-18"})
    name = result.get("serverInfo", {}).get("name")
    expect(name == "bipolar-code", f"serverInfo.name={name!r}")


def check_mcp_tools():
    result = mcp_call("tools/list", {})
    tools = result.get("tools", [])
    expect(len(tools) == 5, f"{len(tools)} herramientas: {[t.get('name') for t in tools]}")


def check_delegate_invalid_verify(workspace):
    # broker.submit valida verify antes de elegir/lanzar agentes: si falla, no corre ningún CLI.
    result = mcp_call("tools/call", {"name": "delegate", "arguments": {
        "task": "e2e: no debe ejecutarse",
        "workspace": str(workspace),
        "verify": ["definitely-not-a-real-binary-xyz"],
    }})
    expect(result.get("isError") is True, f"isError={result.get('isError')!r}: {result}")
    err = result.get("structuredContent", {}).get("error", "")
    expect("invalid_verify" in err, f"error={err!r}")


def check_accounts_pick():
    status, _, body = http_call("GET", "/api/accounts/pick?adapter=claude")
    expect(status == 200, f"HTTP {status}: {body[:300]!r}")
    mode = json.loads(body).get("mode")
    expect(mode == "proxy", f"mode={mode!r}")


def parse_dry_run_output(proc):
    # bipolar_claude.py --bc-dry-run imprime el JSON en stderr (out=sys.stderr).
    for raw in (proc.stderr, proc.stdout):
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            continue
    raise AssertionError(f"sin JSON: stdout={proc.stdout[:200]!r} stderr={proc.stderr[:200]!r}")


def check_claude_dry_run():
    proc = subprocess.run(
        [sys.executable, "scripts/bipolar_claude.py", "--bc-dry-run"],
        cwd=ROOT,
        env={**os.environ, "BIPOLAR_URL": BASE, "BIPOLAR_API_KEY": API_KEY},
        capture_output=True, text=True, timeout=30,
    )
    expect(proc.returncode == 0, f"exit={proc.returncode} stderr={proc.stderr[:300]!r}")
    mode = parse_dry_run_output(proc).get("mode")
    expect(mode == "proxy", f"mode={mode!r}")


def run_check(name, fn):
    try:
        fn()
    except Exception as e:
        print(f"FAIL {name}: {e}")
        return False
    print(f"PASS {name}")
    return True


# --- Orquestación ---------------------------------------------------------------

def main():
    upstream, thread = start_fake_upstream()
    config_dir = Path(tempfile.mkdtemp(prefix="bipolar-e2e-config-"))
    workspace = Path(tempfile.mkdtemp(prefix="bipolar-e2e-ws-"))
    proc, log_path = None, config_dir / "uvicorn-e2e.log"
    ok = True
    try:
        write_temp_config(config_dir, workspace)
        proc, log_path = start_backend(config_dir)
        if not wait_for_backend():
            print(f"FAIL backend_listo: sin /api/health en 30 s ({backend_log_tail(log_path)})")
            return 1
        ok &= run_check("messages_failover", check_messages)
        ok &= run_check("credentials_cooling", check_credentials)
        ok &= run_check("mcp_initialize", check_mcp_initialize)
        ok &= run_check("mcp_tools_list", check_mcp_tools)
        ok &= run_check("delegate_verify_invalido", lambda: check_delegate_invalid_verify(workspace))
        ok &= run_check("accounts_pick_proxy", check_accounts_pick)
        ok &= run_check("claude_dry_run", check_claude_dry_run)
        return 0 if ok else 1
    finally:
        stop_backend(proc)
        stop_fake_upstream(upstream, thread)
        shutil.rmtree(config_dir, ignore_errors=True)
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
