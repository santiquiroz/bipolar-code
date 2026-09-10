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
from app.models.delegate import AgentStatus, Attempt, Job, JobRequest
from app.models.provider import ProviderRegistry
from app.models.smart import CliAgent
from app.services import health_service, providers_service, usage_tracker
from app.services.cli_agents import registry as agents_registry
from app.services.cli_agents.adapters import TASK_CONSTRAINTS, AdapterResult, AdapterUnsafe, AntigravityAdapter, adapter_for
from app.services.route_classifier import classify_task

log = get_logger(__name__)

LINE_RING = 2000
OUTPUT_CAP = 2_000_000
LOG_CAP = 5_000_000
QUEUE_MULTIPLIER = 4
GIT_TIMEOUT = 10


class WorkspaceNotAllowed(Exception):
    pass


class DelegationDisabled(Exception):
    pass


class NoAgentAvailable(Exception):
    def __init__(self, reasons: list[str], skipped: list[tuple[str, str]]):
        super().__init__("no_agent_available")
        self.reasons = reasons
        self.skipped = skipped


@dataclass
class AttemptOutcome:
    ok: bool
    returncode: Optional[int] = None
    signal: Optional[QuotaSignal] = None
    result: Optional[AdapterResult] = None
    error: str = ""
    timed_out: bool = False


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
    return AntigravityAdapter.pool_key(model) if agent.id == "antigravity" else agent.key


def _reject_agent(agent: CliAgent, tier: str, mode: str, status: Optional[AgentStatus], model: str, preferred: bool) -> Optional[str]:
    if not agent.enabled:
        return "disabled"
    if status is not None and not status.installed:
        return "not_installed"
    if mode == "task" and not agent.agentic:
        return "text_only"
    if not preferred and tier not in agent.supported_tiers:
        return "tier_unsupported"
    if status is not None and status.auth == "auth_error":
        return "auth_error"
    if agent.id == "antigravity" and not AntigravityAdapter().deny_list_present():
        return "agy_deny_list_missing"
    health = health_service.get(_pool_key(agent, model))
    if health.state != "available":
        return f"{health.state}:{health.last_signal or 'failures'}"
    if agents_registry.running_count(agent.id) >= agent.max_concurrency:
        return "busy"
    return None


def _candidate_order(tier: str, registry: ProviderRegistry, preferred: str) -> list[str]:
    order = [preferred] if preferred else []
    order += registry.delegation.tier_order.get(tier, [])
    order += [a.id for a in sorted(registry.cli_agents, key=lambda a: a.priority)]
    seen, ordered = set(), []
    for aid in order:
        if aid and aid not in seen:
            seen.add(aid)
            ordered.append(aid)
    return ordered


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
        reason = _reject_agent(agent, tier, mode, statuses.get(aid), model, preferred == aid)
        if reason:
            skipped.append((aid, reason))
            continue
        reasons.append(f"agent:{aid}" + (":preferred" if preferred == aid else f":{tier}"))
        return agent, model, reasons, skipped
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
    if not registry.delegation.enabled and not req.agent_id:
        raise DelegationDisabled("delegation_disabled")
    queued = sum(1 for rt in _jobs.values() if rt.job.status in ("queued", "running"))
    if queued >= registry.delegation.max_parallel_jobs * QUEUE_MULTIPLIER:
        raise DelegationDisabled("too_many_jobs")
    job_id = uuid.uuid4().hex[:12]
    workspace = validate_workspace(req.workspace, registry.delegation.workspace_allowlist) if req.mode == "task" else scratch_dir(job_id)
    cls = classify_task(req.task, thresholds=registry.smart.thresholds)
    tier = req.tier_hint or cls.tier
    statuses = await _statuses(registry, req.agent_id)
    agent, model, reasons, skipped = choose_agent(tier, registry, statuses, preferred=req.agent_id, mode=req.mode, model_override=req.model)
    job = Job(
        id=job_id, created_at=_now(), mode=req.mode, workspace=str(workspace), task_preview=req.task[:200],
        tier=tier, score=cls.score, reasons=list(cls.reasons) + reasons, skipped=[list(s) for s in skipped],
        agent_id=agent.id if agent else None, model=model,
    )
    if agent is None:
        raise NoAgentAvailable(job.reasons, skipped)
    if req.dry_run:
        return job
    runtime = JobRuntime(job=job, request=req, workspace=workspace)
    _jobs[job_id] = runtime
    _trim_jobs(registry.delegation.job_retention)
    runtime.task = asyncio.create_task(_run_job(runtime, registry, statuses))
    return job


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


async def _pump(rt: JobRuntime, stream, name: str, sink: list[str]) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        rt.output_bytes += len(line)
        if rt.output_bytes <= OUTPUT_CAP:
            sink.append(text)
            _emit(rt, {"event": "line", "stream": name, "text": text[:4000]})


async def _run_subprocess(rt: JobRuntime, spec) -> tuple[Optional[int], str, str, bool]:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = await asyncio.create_subprocess_exec(
        *spec.argv, cwd=spec.cwd, env=spec.env,
        stdin=asyncio.subprocess.PIPE if spec.stdin_payload else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **kwargs,
    )
    rt.proc = proc
    if spec.stdin_payload:
        proc.stdin.write(spec.stdin_payload)
        await proc.stdin.drain()
        proc.stdin.close()
    out_lines: list[str] = []
    err_lines: list[str] = []
    pumps = asyncio.gather(_pump(rt, proc.stdout, "stdout", out_lines), _pump(rt, proc.stderr, "stderr", err_lines))
    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), spec.timeout_s)
    except asyncio.TimeoutError:
        timed_out = True
        _kill_tree(proc)
        await proc.wait()
    await pumps
    rt.proc = None
    return proc.returncode, "\n".join(out_lines), "\n".join(err_lines), timed_out


async def _run_ollama(rt: JobRuntime, agent: CliAgent, model: str, task: str, timeout_s: int) -> AttemptOutcome:
    if not model or not model.split(":")[0].endswith(("-32k", "-mechanical")):
        return AttemptOutcome(ok=False, error="ollama_model_not_context_capped")
    provider = providers_service.get_provider("ollama")
    api_base = (provider.api_base if provider else "http://127.0.0.1:11434").rstrip("/").removesuffix("/v1")
    chunks: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=timeout_s, write=10.0, pool=5.0)) as client:
            async with client.stream("POST", f"{api_base}/api/generate",
                                     json={"model": model, "prompt": task + TASK_CONSTRAINTS, "stream": True,
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


async def _run_attempt(rt: JobRuntime, agent: CliAgent, model: str, timeout_s: int) -> AttemptOutcome:
    if agent.id == "ollama":
        return await _run_ollama(rt, agent, model, rt.request.task, timeout_s)
    exe = agents_registry.resolve_exe(agent)
    if not exe:
        return AttemptOutcome(ok=False, error="not_installed")
    try:
        spec = adapter_for(agent.id).build(agent, exe, rt.job.id, rt.request.task, model, rt.workspace, rt.job.tier, timeout_s)
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
    result = adapter_for(agent.id).parse(stdout, stderr, rc, spec.out_file)
    signal = signal_from_attempt(rc, stdout, stderr, result.structured_error)
    ok = rc == 0 and not result.structured_error and signal is None
    return AttemptOutcome(ok=ok, returncode=rc, signal=signal, result=result, error="" if ok else (result.text[-300:] or f"exit {rc}"))


def _files_touched(workspace: Path) -> list[str]:
    if not (workspace / ".git").exists():
        return []
    try:
        out = subprocess.run(["git", "-C", str(workspace), "status", "--porcelain"], capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line[3:].strip() for line in out.stdout.splitlines() if line.strip() and not line[3:].startswith(".bipolar/")]


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
    except OSError as e:
        log.warning("job_log_write_failed", job=rt.job.id, error=str(e))
        return ""
    return str(path)


async def _record(job: Job) -> None:
    try:
        await usage_tracker.record(f"cli:{job.agent_id}", job.model or "default", job.tokens_in, job.tokens_out, job.cost_usd, False)
    except Exception as e:
        log.warning("job_usage_record_failed", job=job.id, error=str(e))


def _apply_signal(agent: CliAgent, model: str, signal: QuotaSignal) -> None:
    health_service.mark_signal(_pool_key(agent, model), signal, cooldown_s=agent.cooldown_s, quota_reset=agent.quota_reset)
    agents_registry.invalidate(agent.id)


async def _run_job(rt: JobRuntime, registry: ProviderRegistry, statuses: dict[str, AgentStatus]) -> None:
    job = rt.job
    agent = next((a for a in registry.cli_agents if a.id == job.agent_id), None)
    model = job.model
    tried: list[str] = []
    alt_pool_tried = False
    global_sem, _ = _sems(registry, agent.id if agent else "")
    async with global_sem:
        job.status = "running"
        job.started_at = _now()
        while agent is not None and len(job.attempts) < max(1, registry.delegation.max_attempts):
            _, agent_sem = _sems(registry, agent.id)
            attempt = Attempt(agent_id=agent.id, model=model, started_at=_now())
            job.attempts.append(attempt)
            job.agent_id, job.model = agent.id, model
            t0 = time.monotonic()
            agents_registry.adjust_running(agent.id, +1)
            try:
                async with agent_sem:
                    outcome = await _run_attempt(rt, agent, model, rt.request.timeout_s or agent.timeout_s)
            finally:
                agents_registry.adjust_running(agent.id, -1)
            attempt.finished_at, attempt.duration_s, attempt.returncode = _now(), round(time.monotonic() - t0, 2), outcome.returncode
            attempt.error = outcome.error[:300]
            if outcome.ok:
                _finish_ok(job, outcome, agent, model)
                break
            if outcome.timed_out:
                job.status, job.error = "timeout", "timeout"
                health_service.mark_failure(_pool_key(agent, model), "timeout")
                break
            if outcome.signal is None:
                job.status, job.error = "failed", outcome.error
                health_service.mark_failure(_pool_key(agent, model), outcome.error)
                break
            attempt.signal = outcome.signal.kind
            _apply_signal(agent, model, outcome.signal)
            _emit(rt, {"event": "attempt", "agent_id": agent.id, "signal": outcome.signal.kind, "excerpt": outcome.signal.excerpt})
            if outcome.signal.kind == "auth":
                job.status, job.error = "auth_error", outcome.signal.excerpt
                break
            if agent.id == "antigravity" and agent.alt_model_on_quota and not alt_pool_tried and rt.request.model == "":
                alt_pool_tried = True
                model = agent.alt_model_on_quota
                continue
            tried.append(agent.id)
            if rt.request.agent_id:
                job.status, job.error = "quota", outcome.signal.excerpt
                break
            agent, model, more_reasons, skipped = choose_agent(job.tier, registry, statuses, exclude=tuple(tried), mode=rt.request.mode, model_override=rt.request.model)
            job.reasons += more_reasons
            job.skipped += [list(s) for s in skipped]
            if agent is None:
                job.status, job.error = "quota", outcome.signal.excerpt
        if job.status == "running":
            job.status, job.error = "failed", job.error or "max_attempts"
    job.finished_at = _now()
    if rt.request.mode == "task":
        job.files_touched = _files_touched(rt.workspace)
    job.log_path = _write_log(rt)
    if job.agent_id:
        await _record(job)
    rt.done = True
    _emit(rt, {"event": "done", "status": job.status, "files_touched": job.files_touched, "error": job.error})
    log.info("delegate_job_finished", job=job.id, status=job.status, agent=job.agent_id, attempts=len(job.attempts))


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
    return [j.model_copy(update={"output_tail": j.output_tail[-2000:]}) for j in jobs[:limit]]


def get_job(job_id: str) -> Optional[Job]:
    rt = _jobs.get(job_id)
    return rt.job if rt else None


def job_output(job_id: str) -> Optional[str]:
    rt = _jobs.get(job_id)
    if rt is None:
        return None
    return "\n".join(f"[{ev.get('stream', ev.get('event'))}] {ev.get('text', ev.get('status', ''))}" for ev in rt.lines)


async def cancel(job_id: str) -> Optional[Job]:
    rt = _jobs.get(job_id)
    if rt is None:
        return None
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
