"""
Broker de delegación: valida el workspace, clasifica la tarea, elige el agente CLI
disponible según tier/cuota/concurrencia, lo ejecuta como subproceso acotado y
reintenta en el siguiente agente cuando el intento muere por una señal de cuota.
"""
import asyncio
import os
import subprocess
import sys
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx
import psutil

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.quota_signals import QuotaSignal, detect_signal, signal_from_attempt
from app.core.utils import sanitize_error
from app.models.delegate import AgentStatus, Attempt, Job, JobRequest
from app.models.provider import ProviderRegistry
from app.models.smart import CliAgent
from app.services import budget_service, health_service, providers_service, usage_tracker
from app.services.cli_agents import deletions, outcome_stats, registry as agents_registry, reviewer, session, verifier
from app.services.cli_agents.worker_status import STOP_STATUSES, parse_worker_status
from app.services.cli_agents.adapters import OPENROUTER_FREE_MODEL_RE, TEXT_CONSTRAINTS, AdapterResult, AdapterUnsafe, AntigravityAdapter, CursorAdapter, account_env, adapter_for, supports_read_only
from app.services.route_classifier import classify_task

log = get_logger(__name__)

LINE_RING = 2000
OUTPUT_CAP = 2_000_000
LOG_CAP = 5_000_000
QUEUE_MULTIPLIER = 4
GIT_TIMEOUT = 10
STREAM_LIMIT = 16 * 1024 * 1024
OVERSIZED_LINE_MARK = "[línea omitida: supera el límite de lectura]"
PARSE_TAIL_LINES = 2000
PARSE_TAIL_BYTES = 2_000_000
TRUNCATED_MARK = "[bipolar: salida intermedia recortada]"
RESULT_OUTPUT_CAP = 20_000
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "timeout", "cancelled", "quota", "auth_error", "needs_context", "blocked"})
WORKER_STOP_JOB_STATUSES = tuple(s.lower() for s in STOP_STATUSES)
# su sandbox de Windows no arranca en la sesión 0 (tarea programada sin escritorio)
OS_SANDBOX_BASES = ("muse", "codex")


# sentinel: el revisor modificó el árbol de trabajo
class ReviewTampered:
    pass


class WorkspaceNotAllowed(Exception):
    pass


class DelegationDisabled(Exception):
    pass


class InvalidRequest(ValueError):
    pass


class NoAgentAvailable(Exception):
    def __init__(self, reasons: list[str], skipped: list[tuple[str, str]]):
        super().__init__("no_agent_available")
        self.reasons = reasons
        self.skipped = skipped


def no_agent_text(reasons: list[str], skipped: list[tuple[str, str]]) -> str:
    text = "no_agent_available: " + "; ".join(reasons)
    if skipped:
        text += " | skipped: " + ", ".join(f"{aid}={reason}" for aid, reason in skipped)
    return text


@dataclass
class AttemptOutcome:
    ok: bool
    returncode: Optional[int] = None
    signal: Optional[QuotaSignal] = None
    result: Optional[AdapterResult] = None
    error: str = ""
    timed_out: bool = False


@dataclass
class GateResult:
    accepted: bool
    reason: str = ""
    outcome: Optional["AttemptOutcome"] = None


@dataclass
class JobRuntime:
    job: Job
    request: JobRequest
    workspace: Path
    lines: deque = field(default_factory=lambda: deque(maxlen=LINE_RING))
    subscribers: set = field(default_factory=set)
    proc: Optional[asyncio.subprocess.Process] = None
    task: Optional[asyncio.Task] = None
    output_bytes: int = 0
    done: bool = False
    current_task: str = ""
    status_before: set = field(default_factory=set)


_jobs: "OrderedDict[str, JobRuntime]" = OrderedDict()
_global_sem: Optional[asyncio.Semaphore] = None
_agent_sems: dict[str, asyncio.Semaphore] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config_dir() -> Path:
    return Path(get_settings().litellm_config_dir)


def jobs_dir() -> Path:
    path = _config_dir() / "delegate" / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── workspace ────────────────────────────────────────────────────────────────

def validate_workspace(path: str, allowlist: list[str]) -> Path:
    if not allowlist:
        raise WorkspaceNotAllowed("workspace_allowlist_empty")
    if not path:
        raise WorkspaceNotAllowed("workspace_required")
    candidate = Path(path)
    if not candidate.is_absolute():
        raise WorkspaceNotAllowed("workspace_not_absolute")
    try:
        real = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise WorkspaceNotAllowed("workspace_missing")
    if not real.is_dir():
        raise WorkspaceNotAllowed("workspace_not_dir")
    if real == Path(real.anchor) or real == Path.home().resolve():
        raise WorkspaceNotAllowed("workspace_forbidden")
    config = _config_dir().resolve()
    if real == config or config in real.parents:
        raise WorkspaceNotAllowed("workspace_forbidden")
    if ".git" in real.parts:
        raise WorkspaceNotAllowed("workspace_forbidden")
    for entry in allowlist:
        try:
            allowed = Path(entry).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if real == allowed or allowed in real.parents:
            return real
    raise WorkspaceNotAllowed("workspace_not_allowed")


def scratch_dir(job_id: str) -> Path:
    path = _config_dir() / "delegate" / "scratch" / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── elección de agente ───────────────────────────────────────────────────────

def _pool_key(agent: CliAgent, model: str) -> str:
    return AntigravityAdapter.pool_key(model) if agent.base == "antigravity" else agent.key


def _muse_sandbox_verified(status: Optional[AgentStatus]) -> bool:
    # Falla cerrado: sin sondeo no hay sandbox verificado. "n/a" = plataforma sin chequeo (fuera de Windows).
    sandbox = (status.quota or {}).get("sandbox") if status is not None else None
    return sandbox in ("ready", "n/a")


def _agent_workspace_allowed(agent: CliAgent, workspace: Optional[Path]) -> bool:
    if workspace is None or not agent.workspace_allowlist:
        return False
    try:
        validate_workspace(str(workspace), agent.workspace_allowlist)
    except WorkspaceNotAllowed:
        return False
    return True


def _reject_agent(agent: CliAgent, tier: str, mode: str, status: Optional[AgentStatus], model: str, preferred: bool,
                  workspace: Optional[Path] = None) -> Optional[str]:
    if not agent.enabled:
        return "disabled"
    # los modelos gratis de OpenRouter pueden guardar los prompts: solo workspaces que el usuario liberó para eso
    if agent.base == "openrouter" and not _agent_workspace_allowed(agent, workspace):
        return "agent_workspace_not_allowed"
    if status is not None and not status.installed:
        return "not_installed"
    if mode == "task" and not agent.agentic:
        return "text_only"
    if not preferred and tier not in agent.supported_tiers:
        return "tier_unsupported"
    if status is not None and status.auth == "auth_error":
        return "auth_error"
    if agent.base == "anthropic" and status is not None and status.error and status.auth == "unknown":
        return "probe_error"
    spend = (status.quota or {}).get("month_spend_usd", 0) if status is not None else 0
    if agent.max_monthly_usd > 0 and spend >= agent.max_monthly_usd:
        return "monthly_budget"
    if agent.base in OS_SANDBOX_BASES and not session.interactive_session():
        return "sandbox_requires_interactive_session"
    if agent.base == "muse" and not _muse_sandbox_verified(status):
        return "muse_sandbox_not_ready"
    if agent.base == "antigravity" and not AntigravityAdapter().deny_list_present():
        return "agy_deny_list_missing"
    if agent.base == "cursor" and not CursorAdapter().deny_list_present():
        return "cursor_deny_list_missing"
    health = health_service.get(_pool_key(agent, model))
    if health.state != "available":
        return f"{health.state}:{health.last_signal or 'failures'}"
    if agents_registry.running_count(agent.id) >= agent.max_concurrency:
        return "busy"
    return None


def _demoted_set(tier: str, registry: ProviderRegistry, preferred: str, ordered: list[str]) -> set[str]:
    demoted_ids = set()
    for aid in ordered:
        if aid == preferred:
            continue
        pct = outcome_stats.accept_pct(aid, tier, registry.delegation.demote_min_samples)
        if pct is not None and pct < registry.delegation.demote_below_pct:
            demoted_ids.add(aid)
    return demoted_ids


def _candidate_order(tier: str, registry: ProviderRegistry, preferred: str) -> list[str]:
    order = [preferred] if preferred else []
    order += registry.delegation.tier_order.get(tier, [])
    order += [a.id for a in sorted(registry.cli_agents, key=lambda a: a.priority)]
    seen, ordered = set(), []
    for aid in order:
        if aid and aid not in seen:
            seen.add(aid)
            ordered.append(aid)
    if registry.delegation.demote_low_acceptance:
        demoted_ids = _demoted_set(tier, registry, preferred, ordered)
        kept = [aid for aid in ordered if aid == preferred or aid not in demoted_ids]
        demoted_order = [aid for aid in ordered if aid in demoted_ids]
        return kept + demoted_order
    return ordered


def _queued_busy(agents: dict[str, CliAgent], skipped: list[tuple[str, str]], tier: str,
                model_override: str, alt_pool: bool) -> tuple[Optional[CliAgent], str]:
    for aid, reason in skipped:
        if reason != "busy":
            continue
        agent = agents.get(aid)
        if agent is None:
            continue
        model = agent.model_for(tier, model_override)
        if alt_pool and agent.alt_model_on_quota:
            model = agent.alt_model_on_quota
        return agent, model
    return None, ""


def choose_agent(
    tier: str,
    registry: ProviderRegistry,
    statuses: dict[str, AgentStatus],
    *,
    preferred: str = "",
    exclude: tuple[str, ...] = (),
    mode: str = "task",
    model_override: str = "",
    alt_pool: bool = False,
    workspace: Optional[Path] = None,
) -> tuple[Optional[CliAgent], str, list[str], list[tuple[str, str]]]:
    agents = {a.id: a for a in registry.cli_agents}
    reasons: list[str] = []
    skipped: list[tuple[str, str]] = []
    for aid in _candidate_order(tier, registry, preferred):
        agent = agents.get(aid)
        if agent is None:
            skipped.append((aid, "unknown"))
            continue
        if aid in exclude:
            skipped.append((aid, "already_tried"))
            continue
        model = agent.model_for(tier, model_override)
        if alt_pool and agent.alt_model_on_quota:
            model = agent.alt_model_on_quota
        reason = _reject_agent(agent, tier, mode, statuses.get(aid), model, preferred == aid, workspace)
        if reason == "busy" and aid == preferred:
            # el agente pedido a propósito espera su turno: saltar a otro puede mandar el código a un carril que no se eligió
            reasons.append(f"agent:{aid}:queued_busy:preferred")
            return agent, model, reasons, skipped
        if reason:
            skipped.append((aid, reason))
            continue
        reasons.append(f"agent:{aid}" + (":preferred" if preferred == aid else f":{tier}"))
        pct = outcome_stats.accept_pct(aid, tier, registry.delegation.demote_min_samples)
        if registry.delegation.demote_low_acceptance and aid != preferred and pct is not None and pct < registry.delegation.demote_below_pct:
            reasons.append(f"demoted:{aid}:{pct}")
        return agent, model, reasons, skipped
    queued, queued_model = _queued_busy(agents, skipped, tier, model_override, alt_pool)
    if queued is not None:
        # El semáforo por agente encola el trabajo; un agente ocupado recibe el job en vez de rechazarlo.
        reasons.append(f"agent:{queued.id}:queued_busy")
        return queued, queued_model, reasons, skipped
    reasons.append("no_agent_available")
    return None, "", reasons, skipped


# ── envío ────────────────────────────────────────────────────────────────────

def _sems(registry: ProviderRegistry, agent_id: str) -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
    global _global_sem
    if _global_sem is None:
        _global_sem = asyncio.Semaphore(max(1, registry.delegation.max_parallel_jobs))
    if agent_id not in _agent_sems:
        agent = next((a for a in registry.cli_agents if a.id == agent_id), None)
        _agent_sems[agent_id] = asyncio.Semaphore(max(1, agent.max_concurrency if agent else 1))
    return _global_sem, _agent_sems[agent_id]


async def _statuses(registry: ProviderRegistry, preferred: str) -> dict[str, AgentStatus]:
    wanted = [a for a in registry.cli_agents if a.enabled or a.id == preferred]
    results = await asyncio.gather(*(agents_registry.probe(a) for a in wanted), return_exceptions=True)
    return {a.id: r for a, r in zip(wanted, results) if isinstance(r, AgentStatus)}


def _recursion_guard() -> None:
    if os.environ.get("BIPOLAR_DELEGATION_DEPTH"):
        raise DelegationDisabled("recursion_guard")


async def submit(req: JobRequest, depth_header: str = "") -> Job:
    _recursion_guard()
    if depth_header and depth_header.strip().isdigit() and int(depth_header) >= 1:
        raise DelegationDisabled("recursion_guard")
    registry = providers_service.load_registry()
    if registry.delegation.demote_low_acceptance:
        await outcome_stats.refresh()
    if not registry.delegation.enabled and not req.agent_id:
        raise DelegationDisabled("delegation_disabled")
    queued = sum(1 for rt in _jobs.values() if rt.job.status in ("queued", "running"))
    if queued >= registry.delegation.max_parallel_jobs * QUEUE_MULTIPLIER:
        raise DelegationDisabled("too_many_jobs")
    job_id = uuid.uuid4().hex[:12]
    workspace = validate_workspace(req.workspace, registry.delegation.workspace_allowlist) if req.mode == "task" else scratch_dir(job_id)
    _validate_verify(req, registry, workspace)
    cls = classify_task(req.task, thresholds=registry.smart.thresholds)
    tier = req.tier_hint or cls.tier
    statuses = await _statuses(registry, req.agent_id)
    agent, model, reasons, skipped = choose_agent(tier, registry, statuses, preferred=req.agent_id, mode=req.mode, model_override=req.model,
                                                workspace=workspace)
    job = Job(
        id=job_id, created_at=_now(), mode=req.mode, workspace=str(workspace), task_preview=req.task[:200],
        tier=tier, score=cls.score, reasons=list(cls.reasons) + reasons, skipped=[list(s) for s in skipped],
        agent_id=agent.id if agent else None, model=model,
        redirect=_redirect_note(req, tier, agent, skipped, registry),
    )
    if job.redirect:
        job.reasons.append(f"redirected:{req.agent_id}->{agent.id}")
    if agent is None:
        raise NoAgentAvailable(job.reasons, skipped)
    if req.dry_run:
        return job
    runtime = JobRuntime(job=job, request=req, workspace=workspace, current_task=req.task)
    _jobs[job_id] = runtime
    _trim_jobs(registry.delegation.job_retention)
    runtime.task = asyncio.create_task(_run_job(runtime, registry, statuses))
    return job


def _redirect_note(req: JobRequest, tier: str, agent: Optional[CliAgent], skipped: list[tuple[str, str]],
                   registry: ProviderRegistry) -> str:
    """Si el agente pedido a propósito se saltó, el resultado tiene que decirlo y decir hasta cuándo."""
    preferred = req.agent_id
    if not preferred or agent is None or agent.id == preferred:
        return ""
    reason = next((r for aid, r in skipped if aid == preferred), "unavailable")
    note = f"el agente pedido '{preferred}' no estaba disponible ({reason})"
    wanted = next((a for a in registry.cli_agents if a.id == preferred), None)
    if wanted is not None:
        until = health_service.get(_pool_key(wanted, wanted.model_for(tier, req.model))).until
        if until:
            note += f" hasta {until}"
    return note + f"; se usó '{agent.id}'"


def _validate_verify(req: JobRequest, registry, workspace: Path) -> None:
    if not req.verify:
        return
    if req.mode != "task":
        raise InvalidRequest("verify_requires_task_mode")
    if not registry.delegation.allow_request_verify:
        raise InvalidRequest("verify_disabled")
    for cmd in req.verify:
        try:
            verifier.parse_command(cmd, workspace)
        except verifier.InvalidCommand as e:
            raise InvalidRequest(f"invalid_verify: {e}")


def _trim_jobs(retention: int) -> None:
    while len(_jobs) > max(10, retention):
        oldest_id = next(iter(_jobs))
        if _jobs[oldest_id].job.status in ("queued", "running"):
            break
        _jobs.pop(oldest_id)


# ── ejecución ────────────────────────────────────────────────────────────────

def _emit(rt: JobRuntime, event: dict) -> None:
    rt.lines.append(event)
    for queue in list(rt.subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    try:
        parent = psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except psutil.Error:
        pass


@dataclass
class _StreamCapture:
    head: list[str] = field(default_factory=list)
    head_bytes: int = 0
    tail: deque = field(default_factory=deque)
    tail_bytes: int = 0

    def add(self, text: str, size: int) -> None:
        if self.head_bytes + size <= OUTPUT_CAP:
            self.head.append(text)
            self.head_bytes += size
            return
        self.tail.append((text, size))
        self.tail_bytes += size
        # acotada por bytes además de por líneas: una sola línea del agente puede medir hasta STREAM_LIMIT
        while len(self.tail) > 1 and (self.tail_bytes > PARSE_TAIL_BYTES or len(self.tail) > PARSE_TAIL_LINES):
            _, dropped = self.tail.popleft()
            self.tail_bytes -= dropped

    def text(self) -> str:
        if not self.tail:
            return "\n".join(self.head)
        return "\n".join([*self.head, TRUNCATED_MARK, *(text for text, _ in self.tail)])


def _consume_line(rt: JobRuntime, line: bytes, name: str, capture: _StreamCapture) -> None:
    text = line.decode("utf-8", "replace").rstrip("\r\n")
    rt.output_bytes += len(line)
    capture.add(text, len(line))
    if rt.output_bytes <= OUTPUT_CAP:
        _emit(rt, {"event": "line", "stream": name, "text": text[:4000]})


async def _pump(rt: JobRuntime, stream, name: str, capture: _StreamCapture) -> None:
    while True:
        try:
            line = await stream.readline()
        except ValueError:
            # readline ya descartó el trozo que supera STREAM_LIMIT; seguir drenando evita bloquear al hijo
            _emit(rt, {"event": "line", "stream": name, "text": OVERSIZED_LINE_MARK})
            continue
        if not line:
            return
        _consume_line(rt, line, name, capture)


async def _run_subprocess(rt: JobRuntime, spec) -> tuple[Optional[int], str, str, bool]:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = await asyncio.create_subprocess_exec(
        *spec.argv, cwd=spec.cwd, env=spec.env,
        stdin=asyncio.subprocess.PIPE if spec.stdin_payload else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=STREAM_LIMIT, **kwargs,
    )
    rt.proc = proc
    if spec.stdin_payload:
        proc.stdin.write(spec.stdin_payload)
        await proc.stdin.drain()
        proc.stdin.close()
    out_capture = _StreamCapture()
    err_capture = _StreamCapture()
    pumps = asyncio.gather(_pump(rt, proc.stdout, "stdout", out_capture), _pump(rt, proc.stderr, "stderr", err_capture))
    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), spec.timeout_s)
    except asyncio.TimeoutError:
        timed_out = True
        _kill_tree(proc)
        await proc.wait()
    await pumps
    rt.proc = None
    return proc.returncode, out_capture.text(), err_capture.text(), timed_out


async def _run_ollama(rt: JobRuntime, agent: CliAgent, model: str, task: str, timeout_s: int) -> AttemptOutcome:
    if not model or not model.split(":")[0].endswith(("-32k", "-mechanical")):
        return AttemptOutcome(ok=False, error="ollama_model_not_context_capped")
    provider = providers_service.get_provider("ollama")
    api_base = (provider.api_base if provider else "http://127.0.0.1:11434").rstrip("/").removesuffix("/v1")
    chunks: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=timeout_s, write=10.0, pool=5.0)) as client:
            async with client.stream("POST", f"{api_base}/api/generate",
                                     json={"model": model, "prompt": task + TEXT_CONSTRAINTS, "stream": True,
                                           "options": {"num_predict": 4096}}) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    return AttemptOutcome(ok=False, returncode=resp.status_code, error=body[:300], signal=detect_signal(body, resp.status_code))
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        piece = httpx.Response(200, text=line).json().get("response", "")
                    except ValueError:
                        continue
                    if piece:
                        chunks.append(piece)
                        _emit(rt, {"event": "line", "stream": "stdout", "text": piece})
    except httpx.HTTPError as e:
        return AttemptOutcome(ok=False, error=f"ollama_unreachable:{e.__class__.__name__}")
    return AttemptOutcome(ok=True, returncode=0, result=AdapterResult(text="".join(chunks)))


async def _run_attempt(rt: JobRuntime, agent: CliAgent, model: str, timeout_s: int, task: Optional[str] = None, read_only: bool = False) -> AttemptOutcome:
    if agent.base == "ollama":
        return await _run_ollama(rt, agent, model, task or rt.request.task, timeout_s)
    exe = agents_registry.resolve_exe(agent)
    if not exe:
        return AttemptOutcome(ok=False, error="not_installed")
    try:
        spec = adapter_for(agent.base).build(agent, exe, rt.job.id, task or rt.request.task, model, rt.workspace, rt.job.tier, timeout_s, read_only=read_only)
        spec.env.update(account_env(agent))
    except AdapterUnsafe as e:
        return AttemptOutcome(ok=False, error=str(e))
    _emit(rt, {"event": "status", "status": "running", "agent_id": agent.id, "model": model, "argv": spec.redacted})
    try:
        rc, stdout, stderr, timed_out = await _run_subprocess(rt, spec)
    finally:
        if spec.pointer_file is not None:
            try:
                spec.pointer_file.unlink(missing_ok=True)
            except OSError:
                pass
    if timed_out:
        return AttemptOutcome(ok=False, returncode=rc, error="timeout", timed_out=True)
    result = adapter_for(agent.base).parse(stdout, stderr, rc, spec.out_file)
    signal = signal_from_attempt(rc, stdout, stderr, result.structured_error)
    ok = rc == 0 and not result.structured_error and signal is None
    return AttemptOutcome(ok=ok, returncode=rc, signal=signal, result=result, error="" if ok else (result.text[-300:] or f"exit {rc}"))


def _status_lines(workspace: Path) -> list[str]:
    if not (workspace / ".git").exists():
        return []
    try:
        out = subprocess.run([
            "git", "-c", "core.quotePath=false", "-C", str(workspace), "status", "--porcelain"
        ], capture_output=True, encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in out.stdout.splitlines() if line.strip() and not line[3:].startswith(".bipolar/")]


def _files_touched(workspace: Path, before: set[str]) -> list[str]:
    """Solo lo que cambió durante el job: líneas de `git status` que no existían antes."""
    return [line[3:].strip() for line in _status_lines(workspace) if line not in before]


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _same_workspace(left: Optional[Path], right: Optional[Path]) -> bool:
    if left is None or right is None:
        return False
    try:
        return Path(left).resolve() == Path(right).resolve()
    except (OSError, RuntimeError):
        return left == right


def _overlapping_jobs(rt: JobRuntime) -> list[str]:
    start = _parse_time(rt.job.started_at)
    if start is None:
        return []
    end = _parse_time(rt.job.finished_at)
    found = []
    for other_id, other in _jobs.items():
        if other_id == rt.job.id:
            continue
        if not _same_workspace(rt.workspace, other.workspace):
            continue
        other_start = _parse_time(other.job.started_at)
        if other_start is None:
            continue
        # ventanas semiabiertas: un job que empieza justo al terminar otro no tocó sus archivos
        if end is not None and other_start >= end:
            continue
        other_end = _parse_time(other.job.finished_at)
        if other_end is not None and other_end <= start:
            continue
        found.append(other_id)
    return found


def _overlap_note(rt: JobRuntime) -> str:
    ids = _overlapping_jobs(rt)
    if not ids:
        return ""
    previews = "; ".join(f"{jid}: {(_jobs[jid].job.task_preview or '').strip()[:120]}" for jid in ids if jid in _jobs)
    return (f"Hay jobs concurrentes en el mismo workspace ({previews}). "
            "Los cambios que correspondan a esas tareas NO son parte de este job y no deben juzgarse.")


def _result_section(text: str) -> str:
    return f"\n[result]\n{text}" if text else ""


def _keep_result(job: Job, outcome: AttemptOutcome) -> None:
    # las líneas del log se recortan a 4000 caracteres; el texto completo del adaptador va aparte
    if outcome.result is not None:
        job.result_text = outcome.result.text


def _apply_job_deletions(rt: JobRuntime) -> None:
    if rt.request.mode != "task":
        return
    created_during_job: set[str] = set()
    for line in _status_lines(rt.workspace):
        if not line.startswith("??") or line in rt.status_before:
            continue
        path = line[3:].strip().replace("\\", "/").rstrip("/")
        candidate = rt.workspace / path
        if candidate.is_dir() and not candidate.is_symlink():
            created_during_job.update(
                child.relative_to(rt.workspace).as_posix()
                for child in candidate.rglob("*")
                if child.is_file() and not child.is_symlink()
            )
        else:
            created_during_job.add(path)
    report = deletions.apply_deletions(rt.workspace, rt.job.id, created_during_job)
    if not report.deleted and not report.rejected:
        return
    _emit(rt, {
        "event": "files_deleted",
        "deleted": report.deleted,
        "rejected": [[path, reason] for path, reason in report.rejected],
    })
    attempt = next((item for item in reversed(rt.job.attempts) if item.kind == "work"), None)
    if attempt is not None:
        attempt.detail["deleted"] = report.deleted
        attempt.detail["deletion_rejected"] = report.rejected


def _write_log(rt: JobRuntime) -> str:
    path = jobs_dir() / f"{rt.job.id}.log"
    try:
        with path.open("w", encoding="utf-8") as fh:
            written = 0
            for ev in rt.lines:
                line = f"[{ev.get('stream', ev.get('event'))}] {ev.get('text', ev.get('status', ''))}\n"
                written += len(line)
                if written > LOG_CAP:
                    break
                fh.write(line)
            fh.write(_result_section(rt.job.result_text))
    except OSError as e:
        log.warning("job_log_write_failed", job=rt.job.id, error=str(e))
        return ""
    return str(path)


async def _record(job: Job) -> None:
    if job.agent_id.split("-", 1)[0] == "anthropic":
        return
    try:
        await usage_tracker.record(f"cli:{job.agent_id}", job.model or "default", job.tokens_in, job.tokens_out, job.cost_usd, False)
    except Exception as e:
        log.warning("job_usage_record_failed", job=job.id, error=str(e))


def _apply_signal(agent: CliAgent, model: str, signal: QuotaSignal) -> None:
    if agent.base == "anthropic" and signal.kind == "quota_exhausted":
        signal = QuotaSignal(signal.kind, agent.cooldown_s, signal.excerpt)
    if agent.base == "anthropic" and signal.kind == "rate_limit" and signal.retry_after_s is None:
        signal = QuotaSignal(signal.kind, 60, signal.excerpt)
    health_service.mark_signal(_pool_key(agent, model), signal, cooldown_s=agent.cooldown_s, quota_reset=agent.quota_reset)
    agents_registry.invalidate(agent.id)


# un fallo de UN modelo :free (rate limit por minuto, sobrecarga, modelo retirado) no dice nada del resto del carril;
# el cupo diario (quota_exhausted) sí es de la cuenta y nunca se reintenta con otro modelo
OPENROUTER_MODEL_SIGNALS = ("rate_limit", "overloaded", "model_unavailable")


def _openrouter_fallback_eligible(agent: CliAgent, signal: QuotaSignal) -> bool:
    return agent.base == "openrouter" and bool(agent.fallback_models) and signal.kind in OPENROUTER_MODEL_SIGNALS


def _next_openrouter_model(agent: CliAgent, tried: set[str]) -> Optional[str]:
    for candidate in agent.fallback_models:
        if candidate in tried:
            continue
        tried.add(candidate)
        if not OPENROUTER_FREE_MODEL_RE.match(candidate):
            continue  # el adaptador rechaza modelos no :free; aquí se saltan sin gastar un intento
        return candidate
    return None


async def _run_job(rt: JobRuntime, registry: ProviderRegistry, statuses: dict[str, AgentStatus]) -> None:
    rt.status_before = set()
    try:
        rt.status_before = set(_status_lines(rt.workspace)) if rt.request.mode == "task" else set()
        await _run_attempts(rt, registry, statuses)
    except Exception as e:
        _kill_live_process(rt)
        _mark_crashed(rt.job, e)
    await _finalize_job(rt, rt.status_before)


def _kill_live_process(rt: JobRuntime) -> None:
    if rt.proc is not None and rt.proc.returncode is None:
        _kill_tree(rt.proc)
    rt.proc = None


def _mark_crashed(job: Job, error: Exception) -> None:
    job.status = "failed"
    job.error = sanitize_error(f"internal_error:{error.__class__.__name__}: {error}")[:300]
    log.warning("delegate_job_crashed", job=job.id, error=job.error)


async def _finalize_job(rt: JobRuntime, status_before: set[str]) -> None:
    job = rt.job
    job.finished_at = _now()
    try:
        await _collect_job_artifacts(rt, status_before)
    except Exception as e:
        log.warning("delegate_job_artifacts_failed", job=job.id, error=sanitize_error(str(e)))
    rt.done = True
    _emit(rt, {"event": "done", "status": job.status, "files_touched": job.files_touched, "error": job.error})
    log.info("delegate_job_finished", job=job.id, status=job.status, agent=job.agent_id, attempts=len(job.attempts))


async def _collect_job_artifacts(rt: JobRuntime, status_before: set[str]) -> None:
    job = rt.job
    if rt.request.mode == "task":
        job.files_touched = _files_touched(rt.workspace, status_before)
        job.concurrent_jobs = _overlapping_jobs(rt)
        job.files_touched_exact = not job.concurrent_jobs
    job.log_path = _write_log(rt)
    if job.agent_id:
        await _record(job)


def _work_count(job: Job) -> int:
    return sum(1 for a in job.attempts if a.kind == "work" and not a.detail.get("revision"))


async def _monthly_budget_reached(agent: CliAgent) -> bool:
    if agent.max_monthly_usd <= 0:
        return False
    spend = await budget_service.spend_since(budget_service.provider_id_of(agent.key),
                                            budget_service.window_start("month").isoformat())
    return spend["cost_usd"] >= agent.max_monthly_usd


async def _record_anthropic_attempt(agent: CliAgent, model: str, outcome: AttemptOutcome) -> None:
    if agent.base != "anthropic" or outcome.result is None:
        return
    usage = outcome.result.usage
    try:
        await usage_tracker.record(budget_service.provider_id_of(agent.key), model,
                                   int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0),
                                   usage.get("cost_usd"), False)
    except Exception as error:
        log.warning("agent_usage_record_failed", agent=agent.id, error=sanitize_error(str(error)))
    budget_service.clear_cache()


async def _refresh_budget_statuses(registry: ProviderRegistry, statuses: dict[str, AgentStatus]) -> None:
    for agent in registry.cli_agents:
        if agent.id in statuses and agent.max_monthly_usd > 0:
            statuses[agent.id] = await agents_registry.refresh_monthly_spend(statuses[agent.id], agent)


async def _run_work_attempt(rt: JobRuntime, registry: ProviderRegistry, agent: CliAgent, model: str) -> AttemptOutcome:
    job = rt.job
    _, agent_sem = _sems(registry, agent.id)
    attempt = Attempt(agent_id=agent.id, model=model, started_at=_now())
    job.attempts.append(attempt)
    job.agent_id, job.model = agent.id, model
    t0 = time.monotonic()
    agents_registry.adjust_running(agent.id, +1)
    try:
        async with agent_sem:
            if await _monthly_budget_reached(agent):
                outcome = AttemptOutcome(ok=False, error="monthly_budget")
            else:
                outcome = await _run_attempt(rt, agent, model, rt.request.timeout_s or agent.timeout_s, task=rt.current_task)
            await _record_anthropic_attempt(agent, model, outcome)
    finally:
        agents_registry.adjust_running(agent.id, -1)
    attempt.finished_at, attempt.duration_s, attempt.returncode = _now(), round(time.monotonic() - t0, 2), outcome.returncode
    attempt.error = outcome.error[:300]
    if outcome.signal is not None:
        attempt.signal = outcome.signal.kind
    _keep_result(job, outcome)
    _apply_job_deletions(rt)
    return outcome


def _review_enabled(rt: JobRuntime, registry: ProviderRegistry) -> bool:
    if rt.request.review is not None:
        return rt.request.review
    return registry.delegation.review_default


async def _record_verify_checks(rt: JobRuntime, registry: ProviderRegistry) -> list:
    job = rt.job
    checks = await verifier.run_checks(rt.request.verify, rt.workspace, registry.delegation.verify_timeout_s)
    for check in checks:
        job.attempts.append(Attempt(agent_id="bipolar", started_at=_now(), finished_at=_now(),
                                    returncode=check.returncode, kind="verify",
                                    detail={"command": check.command, "returncode": check.returncode,
                                            "timed_out": check.timed_out, "error": check.error,
                                            "environment_error": check.environment_error}))
        _emit(rt, {"event": "verify_result", "command": check.command, "returncode": check.returncode,
                   "timed_out": check.timed_out, "error": check.error[:300], "ok": check.ok,
                   "environment_error": check.environment_error,
                   "text": f"{check.command} -> exit {check.returncode}{' (timeout)' if check.timed_out else ''}\n{check.output_tail[-3000:]}"})
    return checks


async def _run_attempt_guarded(rt: JobRuntime, registry: ProviderRegistry, agent: CliAgent, model: str,
                               timeout_s: int, task: str, read_only: bool = False) -> AttemptOutcome:
    # revisiones y revisores respetan max_concurrency y cuentan como "running", igual que el trabajo normal
    _, agent_sem = _sems(registry, agent.id)
    agents_registry.adjust_running(agent.id, +1)
    try:
        async with agent_sem:
            if await _monthly_budget_reached(agent):
                return AttemptOutcome(ok=False, error="monthly_budget")
            outcome = await _run_attempt(rt, agent, model, timeout_s, task=task, read_only=read_only)
            await _record_anthropic_attempt(agent, model, outcome)
            return outcome
    finally:
        agents_registry.adjust_running(agent.id, -1)


async def _revise(rt: JobRuntime, registry: ProviderRegistry, worker: CliAgent, model: str, issues: list, failed_check) -> AttemptOutcome:
    job = rt.job
    task = reviewer.revision_task(rt.request.task, issues, failed_check)
    attempt = Attempt(agent_id=worker.id, model=model, started_at=_now(), kind="work", detail={"revision": True})
    job.attempts.append(attempt)
    t0 = time.monotonic()
    outcome = await _run_attempt_guarded(rt, registry, worker, model, rt.request.timeout_s or worker.timeout_s, task)
    attempt.finished_at, attempt.duration_s, attempt.returncode = _now(), round(time.monotonic() - t0, 2), outcome.returncode
    attempt.error = outcome.error[:300]
    if outcome.signal is not None:
        attempt.signal = outcome.signal.kind
    _keep_result(job, outcome)
    _apply_job_deletions(rt)
    _emit(rt, {"event": "revision", "agent_id": worker.id, "ok": outcome.ok, "error": outcome.error[:300]})
    return outcome


def _review_model(agent: CliAgent, tier: str) -> str:
    return "claude-sonnet-5-5" if agent.base == "anthropic" else agent.model_for(tier)


def _worker_stop(rt: JobRuntime, agent: CliAgent, outcome: AttemptOutcome) -> str:
    if rt.request.mode != "task" or outcome.result is None:
        return ""
    status, detail = parse_worker_status(outcome.result.text)
    rt.job.worker_status, rt.job.worker_detail = status, detail
    if status not in STOP_STATUSES:
        return ""
    _emit(rt, {"event": "worker_stop", "agent_id": agent.id, "status": status, "detail": detail[:300]})
    return f"{status.lower()}: {detail}"[:300]


def _stop_job(job: Job, reason: str) -> None:
    job.status, job.error = reason.split(":", 1)[0], reason[:300]


def _review_candidates(registry: ProviderRegistry, statuses: dict, worker: CliAgent, tier: str,
                       workspace: Optional[Path] = None) -> list:
    agents = {a.id: a for a in registry.cli_agents}
    candidates = []
    for thinker_id in registry.delegation.thinkers:
        cand = agents.get(thinker_id)
        if cand is None or cand.id == worker.id or not supports_read_only(cand.base):
            continue
        if _reject_agent(cand, tier, "task", statuses.get(thinker_id), _review_model(cand, tier), False, workspace):
            continue
        candidates.append(cand)
    candidates.sort(key=lambda c: c.base == worker.base)
    return candidates


async def _review(rt: JobRuntime, registry: ProviderRegistry, statuses: dict, worker: CliAgent, checks: list):
    job = rt.job
    await _refresh_budget_statuses(registry, statuses)
    candidates = _review_candidates(registry, statuses, worker, job.tier, rt.workspace)
    if not candidates:
        return None
    diff = reviewer.collect_diff(rt.workspace, _files_touched(rt.workspace, rt.status_before))
    concerns = job.worker_detail if job.worker_status == "DONE_WITH_CONCERNS" else ""
    prompt = reviewer.review_prompt(rt.request.task, diff, checks, _overlap_note(rt), worker_concerns=concerns)
    for cand in candidates:
        model_cand = _review_model(cand, job.tier)
        before = reviewer.tree_fingerprint(rt.workspace) if rt.workspace else ""
        outcome = await _run_attempt_guarded(rt, registry, cand, model_cand, registry.delegation.review_timeout_s, prompt, read_only=True)
        after = reviewer.tree_fingerprint(rt.workspace) if rt.workspace else ""
        changed = bool(before) and before != after
        # otro job en el mismo workspace también puede escribir durante la revisión: no es culpa del revisor
        concurrent = changed and bool(_overlapping_jobs(rt))
        tampered = changed and not concurrent
        if concurrent:
            _emit(rt, {"event": "review_tree_changed_concurrent", "agent_id": cand.id})
        verdict = reviewer.parse_verdict(outcome.result.text) if outcome.ok and outcome.result else None
        if verdict is not None:
            verdict = reviewer.resolve_citations(verdict, rt.workspace, diff)
        if tampered:
            verdict = None  # un veredicto de un revisor que tocó el árbol no cuenta
        # revise/reject sin hallazgos válidos: el revisor no sostiene su veredicto, se prueba el siguiente
        unsupported = verdict is not None and verdict.verdict in ("revise", "reject") and not verdict.issues and bool(verdict.dropped)
        dropped = verdict.dropped if verdict else []
        job.attempts.append(Attempt(agent_id=cand.id, model=model_cand, started_at=_now(), finished_at=_now(),
                                    returncode=outcome.returncode, kind="review", error=outcome.error[:300],
                                    detail={"verdict": verdict.verdict if verdict else "",
                                            "issues": verdict.issues if verdict else [],
                                            "dropped": dropped, "tampered": tampered, "unsupported": unsupported}))
        if tampered:
            _emit(rt, {"event": "review_tampered", "agent_id": cand.id})
            health_service.mark_failure(_pool_key(cand, model_cand), "review_mutated_tree")
            return ReviewTampered()
        if unsupported:
            _emit(rt, {"event": "review_unsupported", "agent_id": cand.id, "dropped": dropped})
        else:
            _emit(rt, {"event": "review_result", "agent_id": cand.id, "verdict": verdict.verdict if verdict else "",
                       "issues": verdict.issues if verdict else [], "ok": outcome.ok, "dropped": dropped})
        if outcome.signal is not None:
            _apply_signal(cand, model_cand, outcome.signal)
            continue
        if verdict is not None and not unsupported:
            return verdict
    return None


async def _quality_gate(rt: JobRuntime, registry: ProviderRegistry, statuses: dict, worker: CliAgent,
                        model: str, outcome: AttemptOutcome) -> GateResult:
    job = rt.job
    if rt.request.mode != "task":
        return GateResult(True, "", outcome)
    revisions = rt.request.max_revisions
    while True:
        checks = await _record_verify_checks(rt, registry)
        if checks and not verifier.checks_passed(checks):
            job.verification_status = "failed"
            check = checks[-1]
            if check.environment_error:
                return GateResult(False, f"verify_environment: {check.environment_error} ({check.command}); cierra el proceso que bloquea los archivos y vuelve a delegar"[:300], outcome)
            if revisions > 0:
                revisions -= 1
                outcome = await _revise(rt, registry, worker, model, [], checks[-1])
                if not outcome.ok:
                    return GateResult(False, f"revision_failed: {outcome.error}"[:300], outcome)
                stop = _worker_stop(rt, worker, outcome)
                if stop:
                    return GateResult(False, stop, outcome)
                continue
            return GateResult(False, f"verify_failed: {checks[-1].command}"[:300], outcome)
        job.verification_status = "passed" if checks else "n/a"
        if not _review_enabled(rt, registry):
            job.review_status = "n/a"
            return GateResult(True, "", outcome)
        verdict = await _review(rt, registry, statuses, worker, checks)
        if isinstance(verdict, ReviewTampered):
            # El revisor modificó el árbol: terminal, sin escalar
            job.review_status = "tampered"
            job.status = "failed"
            job.error = f"review_tampered: el revisor {job.attempts[-1].agent_id} modificó el árbol de trabajo durante la revisión; revisa los cambios a mano"
            return GateResult(False, job.error[:300], outcome)
        if verdict is None:
            # una revisión no sostenida (citas falsas) no cuenta como petición de cambios
            asked_changes = any(a.kind == "review" and a.detail.get("verdict") in ("revise", "reject")
                                and not a.detail.get("unsupported") for a in job.attempts)
            if asked_changes:
                job.review_status = "unreviewed"
                return GateResult(False, "review_unavailable: la revisión anterior pidió cambios y no hubo revisor para validar la corrección", outcome)
            job.review_status = "skipped"
            return GateResult(True, "", outcome)
        if verdict.verdict == "approve":
            job.review_status = "passed"
            return GateResult(True, "", outcome)
        if verdict.verdict == "revise" and revisions > 0:
            revisions -= 1
            outcome = await _revise(rt, registry, worker, model, verdict.issues, None)
            if not outcome.ok:
                return GateResult(False, f"revision_failed: {outcome.error}"[:300], outcome)
            stop = _worker_stop(rt, worker, outcome)
            if stop:
                return GateResult(False, stop, outcome)
            continue
        job.review_status = "failed"
        reason = f"review_rejected: {'; '.join(verdict.issues)}" if verdict.issues else "review_rejected"
        return GateResult(False, reason[:300], outcome)


def _escalation_target(rt: JobRuntime, registry: ProviderRegistry, statuses: dict, tried: list) -> tuple:
    job = rt.job
    agents = {a.id: a for a in registry.cli_agents}
    for thinker_id in registry.delegation.thinkers:
        if thinker_id in tried:
            continue
        cand = agents.get(thinker_id)
        if cand is None or not cand.agentic:
            continue
        model = cand.model_for(job.tier)
        if _reject_agent(cand, job.tier, "task", statuses.get(thinker_id), model, False, rt.workspace):
            continue
        return cand, model
    agent, model, _, _ = choose_agent(job.tier, registry, statuses, exclude=tuple(tried), mode="task", workspace=rt.workspace)
    return agent, model


async def _handle_gate_ok(rt: JobRuntime, registry: ProviderRegistry, statuses: dict, agent: CliAgent,
                          model: str, outcome: AttemptOutcome, tried: list, history: list):
    job = rt.job
    gate = await _quality_gate(rt, registry, statuses, agent, model, outcome)
    if rt.request.mode == "task":
        if gate.accepted:
            # Intentos con error que pasan por gate_after_failure no se registran:
            # aunque dejen cambios, el intento falló y no cuenta como resultado del agente.
            if outcome.ok:
                revised = any(
                    attempt.agent_id == agent.id and attempt.kind == "work" and attempt.detail.get("revision")
                    for attempt in job.attempts
                )
                await outcome_stats.record(agent.id, job.tier, "revised" if revised else "first_pass")
        elif gate.reason.startswith(("review_rejected", "verify_failed", "revision_failed")):
            # Solo registrar rechazo si el fallo no es cuota/auth/timeout/red del worker de revisión
            if gate.outcome is not None and gate.outcome.signal is None and not gate.outcome.timed_out:
                await outcome_stats.record(agent.id, job.tier, "rejected")
    if gate.accepted:
        _finish_ok(job, gate.outcome or outcome, agent, model)
        return None
    # verify_environment: otro agente chocaría con el mismo bloqueo de archivos
    if gate.reason.startswith(("review_unavailable", "review_tampered", "verify_environment")):
        job.status, job.error = "failed", gate.reason[:300]
        return None
    if gate.reason.startswith(WORKER_STOP_JOB_STATUSES):
        _stop_job(job, gate.reason)
        return None
    history.append(f"{agent.id}: {gate.reason}")
    tried.append(agent.id)
    await _refresh_budget_statuses(registry, statuses)
    nxt, nxt_model = _escalation_target(rt, registry, statuses, tried)
    if nxt is None or _work_count(job) >= max(1, registry.delegation.max_attempts):
        job.status, job.error = "failed", gate.reason[:300]
        return None
    job.escalations += 1
    _emit(rt, {"event": "escalated", "from": agent.id, "to": nxt.id, "reason": gate.reason[:300]})
    rt.current_task = reviewer.escalation_task(rt.request.task, history)
    return nxt, nxt_model


async def _next_available_agent(rt: JobRuntime, registry: ProviderRegistry, statuses: dict, tried: list) -> tuple:
    await _refresh_budget_statuses(registry, statuses)
    agent, model, reasons, skipped = choose_agent(rt.job.tier, registry, statuses, exclude=tuple(tried),
                                                 mode=rt.request.mode, model_override=rt.request.model,
                                                 workspace=rt.workspace)
    rt.job.reasons += reasons
    rt.job.skipped += [list(skip) for skip in skipped]
    return agent, model


async def _run_attempts(rt: JobRuntime, registry: ProviderRegistry, statuses: dict[str, AgentStatus]) -> None:
    job = rt.job
    agent = next((a for a in registry.cli_agents if a.id == job.agent_id), None)
    model = job.model
    tried: list[str] = []
    history: list[str] = []
    alt_pool_tried = False
    openrouter_models_tried: set[str] = set()
    network_retried: set[str] = set()
    global_sem, _ = _sems(registry, agent.id if agent else "")
    async with global_sem:
        job.status = "running"
        job.started_at = _now()
        while agent is not None and _work_count(job) < max(1, registry.delegation.max_attempts):
            outcome = await _run_work_attempt(rt, registry, agent, model)
            if outcome.ok:
                # el worker pidió parar: el problema está en la tarea, escalar solo gastaría cuota de otro agente
                stop = _worker_stop(rt, agent, outcome)
                if stop:
                    _stop_job(job, stop)
                    break
                nxt = await _handle_gate_ok(rt, registry, statuses, agent, model, outcome, tried, history)
                if nxt is None:
                    break
                agent, model = nxt
                continue
            # un agente en sandbox suele no poder compilar y sale con error aunque dejó el trabajo hecho:
            # el verify corre en el host, así que se le da la oportunidad (y las revisiones) antes de fallar
            if (not outcome.ok and outcome.signal is None and rt.request.mode == "task"
                    and rt.request.verify and _files_touched(rt.workspace, rt.status_before)):
                _emit(rt, {"event": "gate_after_failure", "agent_id": agent.id, "timed_out": outcome.timed_out,
                           "text": "el intento terminó sin éxito pero dejó cambios: se verifican"})
                nxt = await _handle_gate_ok(rt, registry, statuses, agent, model, outcome, tried, history)
                if nxt is None:
                    break
                agent, model = nxt
                continue
            if outcome.timed_out:
                job.status, job.error = "timeout", "timeout"
                health_service.mark_failure(_pool_key(agent, model), "timeout")
                break
            if outcome.signal is None:
                job.status, job.error = "failed", outcome.error
                health_service.mark_failure(_pool_key(agent, model), outcome.error)
                if agent.base != "anthropic" or rt.request.agent_id:
                    break
                tried.append(agent.id)
                agent, model = await _next_available_agent(rt, registry, statuses, tried)
                if agent is not None:
                    job.status = "running"
                continue
            # un corte de red no dice nada del estado del agente: un reintento al mismo agente, sin cooldown
            network = outcome.signal.kind == "network_error"
            if network and agent.id not in network_retried:
                network_retried.add(agent.id)
                _emit(rt, {"event": "attempt", "agent_id": agent.id, "signal": outcome.signal.kind, "excerpt": outcome.signal.excerpt, "retry": True})
                continue
            # openrouter con un modelo caído no pone en cooldown al carril: el fallback decide antes
            fallback_eligible = _openrouter_fallback_eligible(agent, outcome.signal)
            if not network and not fallback_eligible:
                _apply_signal(agent, model, outcome.signal)
            _emit(rt, {"event": "attempt", "agent_id": agent.id, "signal": outcome.signal.kind, "excerpt": outcome.signal.excerpt})
            if outcome.signal.kind == "auth":
                job.status, job.error = "auth_error", outcome.signal.excerpt
                break
            if agent.base == "antigravity" and agent.alt_model_on_quota and not alt_pool_tried and rt.request.model == "":
                alt_pool_tried = True
                model = agent.alt_model_on_quota
                continue
            if fallback_eligible:
                openrouter_models_tried.add(model)
                fallback_model = _next_openrouter_model(agent, openrouter_models_tried)
                if fallback_model is not None:
                    _emit(rt, {"event": "model_fallback", "agent_id": agent.id, "from": model, "to": fallback_model, "signal": outcome.signal.kind})
                    model = fallback_model
                    continue
                _apply_signal(agent, model, outcome.signal)  # sin modelos de respaldo: el carril sigue el flujo normal
            tried.append(agent.id)
            failed_status = "failed" if network or outcome.signal.kind == "model_unavailable" or (agent.base == "anthropic" and outcome.signal.kind == "overloaded") else "quota"
            if rt.request.agent_id:
                job.status, job.error = failed_status, outcome.signal.excerpt
                break
            agent, model = await _next_available_agent(rt, registry, statuses, tried)
            if agent is None:
                job.status, job.error = failed_status, outcome.signal.excerpt
        if job.status == "running":
            job.status, job.error = "failed", job.error or "max_attempts"


def _finish_ok(job: Job, outcome: AttemptOutcome, agent: CliAgent, model: str) -> None:
    result = outcome.result or AdapterResult()
    job.status = "succeeded"
    job.output_tail = result.text[-20_000:]
    job.tokens_in = int(result.usage.get("input_tokens") or 0)
    job.tokens_out = int(result.usage.get("output_tokens") or 0)
    job.cost_usd = result.usage.get("cost_usd")
    health_service.mark_success(_pool_key(agent, model))


# ── consulta ─────────────────────────────────────────────────────────────────

def list_jobs(limit: int = 50, status: Optional[str] = None) -> list[Job]:
    jobs = [rt.job for rt in reversed(_jobs.values()) if not status or rt.job.status == status]
    return [j.model_copy(update={"output_tail": j.output_tail[-2000:], "result_text": j.result_text[-2000:]}) for j in jobs[:limit]]


def get_job(job_id: str) -> Optional[Job]:
    rt = _jobs.get(job_id)
    return rt.job if rt else None


def job_output(job_id: str, tail_chars: Optional[int] = None) -> Optional[str]:
    rt = _jobs.get(job_id)
    if rt is None:
        return None
    lines = "\n".join(f"[{ev.get('stream', ev.get('event'))}] {ev.get('text', ev.get('status', ''))}" for ev in rt.lines)
    result = rt.job.result_text
    if tail_chars is None:
        return lines + _result_section(result)
    if not result:
        return lines[-tail_chars:]
    # el resultado completo (hasta RESULT_OUTPUT_CAP) va primero; la cola de las líneas llena lo que sobra
    block = _result_section(result[-RESULT_OUTPUT_CAP:])
    budget = tail_chars - len(block)
    return (lines[-budget:] if budget > 0 else "") + block


async def wait_job(job_id: str, timeout_s: float) -> Optional[Job]:
    rt = _jobs.get(job_id)
    if rt is None:
        return None
    if rt.task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(rt.task), timeout_s)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            if rt.task.cancelled():
                return get_job(job_id)
            raise
    return get_job(job_id)


async def cancel(job_id: str) -> Optional[Job]:
    rt = _jobs.get(job_id)
    if rt is None:
        return None
    if rt.job.status in TERMINAL_STATUSES:
        return rt.job
    if rt.proc is not None:
        _kill_tree(rt.proc)
    if rt.task is not None and not rt.task.done():
        rt.task.cancel()
    rt.job.status = "cancelled"
    rt.job.finished_at = _now()
    rt.done = True
    _emit(rt, {"event": "done", "status": "cancelled", "files_touched": [], "error": "cancelled"})
    return rt.job


async def subscribe(job_id: str) -> AsyncIterator[dict]:
    rt = _jobs.get(job_id)
    if rt is None:
        return
    queue: asyncio.Queue = asyncio.Queue(maxsize=LINE_RING)
    rt.subscribers.add(queue)
    try:
        for ev in list(rt.lines):
            yield ev
        if rt.done:
            return
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield {"event": "ping"}
                continue
            yield ev
            if ev.get("event") == "done":
                return
    finally:
        rt.subscribers.discard(queue)


async def shutdown() -> None:
    for job_id, rt in list(_jobs.items()):
        if rt.job.status in ("queued", "running"):
            await cancel(job_id)


def reset_for_tests() -> None:
    global _global_sem
    _jobs.clear()
    _agent_sems.clear()
    _global_sem = None
    os.environ.pop("BIPOLAR_DELEGATION_DEPTH", None)
