# Plan D — Verificación, revisión y escalado en el broker

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un job delegado solo termine `succeeded` si pasa sus comandos de verificación y la revisión de un "pensador" en solo lectura; si no, el trabajador corrige una vez y, si sigue mal, la tarea escala a otro agente.

**Architecture:** Dos módulos puros y probables: `verifier.py` corre comandos sin shell y `reviewer.py` arma diffs y prompts y lee el veredicto. El broker suma una "puerta de calidad" después de cada trabajo exitoso. Los adaptadores aprenden un modo `read_only` con los flags de cada CLI.

**Tech Stack:** Python 3.11, asyncio, psutil (ya instalado), pydantic v2, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-07-orquestacion-transparente-design.md` (sección 6).

**Depende de:** Plan B, tareas B1 y B2 (`CliAgent.base`, `DelegationConfig.thinkers`, `account_env`). Verificar que estén commiteadas antes de empezar D1.

## Global Constraints

- Repo: `C:\personal\bipolar-code\bipolar-orq`, tests con `cd backend && python -m pytest -q`.
- Sin dependencias nuevas.
- Los comandos de verificación **nunca** corren con shell: argumentos con `shlex.split`, ejecutable resuelto y `.cmd`/`.bat` envueltos con `exe_argv`.
- Nada se revierte en el workspace: ni `git checkout`, ni `git stash`, ni borrados.
- Semántica que no cambia: los fallos del trabajador (timeout, error sin señal, auth, cuota) siguen como hoy. El escalado nuevo ocurre **solo** cuando la puerta de calidad rechaza.
- Los jobs `mode="text"` no pasan por la puerta (verificación y revisión `n/a`).
- Estilo: funciones cortas con nombres explícitos; sin docstrings largos; comentario de una línea solo para un porqué no obvio.
- No ejecutar `git add/commit/push/reset/checkout`: el orquestador commitea.

## Review Focus

1. **Comando de verificación con ruta relativa al workspace** (`backend/.venv/Scripts/python -m pytest`): se resuelve contra el workspace, no contra el cwd de bipolar. Test en D3.
2. **Revisor que responde texto sin JSON o con un veredicto desconocido**: no aprueba por defecto. Prueba el siguiente pensador y, si no queda ninguno, `review_status = "skipped"`, nunca `passed`. Tests en D4 y D5.
3. **El revisor es la misma cuenta que trabajó**: se excluye por id. Si hay otro pensador de otra familia (`base` distinto), va primero. Test en D5.
4. **Comando de verificación que cuelga** (`python -c "while True: pass"`): se mata el árbol de procesos al timeout, el resultado es `timed_out=True` y no se cuelga el job. Test en D3.
5. **Escalado sin candidatos** (todos los pensadores agotados o ya probados): el job termina `failed` con el motivo de la última puerta (`verify_failed` o `review_rejected`), no queda `running`. Test en D5.

---

### Task D1: Modo solo lectura en los adaptadores

**Files:**
- Modify: `backend/app/services/cli_agents/adapters.py`
- Test: `backend/tests/test_adapters_read_only.py`

**Interfaces:**
- Produces:
  - Cada clase de adaptador gana el atributo `supports_read_only: bool`.
  - Cada `build(...)` gana el parámetro final `read_only: bool = False`.
  - `adapters.supports_read_only(base: str) -> bool`.
  - `TASK_CONSTRAINTS` no cambia; constante nueva `READ_ONLY_CONSTRAINTS`.

**Flags por adaptador cuando `read_only=True`:**

| Adaptador | `supports_read_only` | Cambio |
|---|---|---|
| ClaudeAdapter | True | `--permission-mode plan` en vez de `acceptEdits`; `--disallowedTools` pasa a `"Task,Agent,WebFetch,WebSearch,Edit,Write,MultiEdit,NotebookEdit,Bash"` |
| CodexAdapter | True | `--sandbox read-only` en vez de `workspace-write`; el stdin usa `READ_ONLY_CONSTRAINTS` en vez de `TASK_CONSTRAINTS` |
| DeepseekAdapter | True | `DSH_PERMISSION_MODE=read-only`; stdin con `READ_ONLY_CONSTRAINTS` |
| MuseAdapter | True | agrega `--disable-write`, `--disable-shell` (los usa muse-plugin-cc) |
| CursorAdapter | True | agrega `--mode ask` y **no** pasa `--force` |
| CopilotAdapter, AntigravityAdapter | False | con `read_only=True`, `build` lanza `AdapterUnsafe("read_only_unsupported")` |

```python
READ_ONLY_CONSTRAINTS = (
    "\n\nRestricciones: ejecución de solo lectura. No edites archivos ni ejecutes comandos que cambien el repositorio; "
    "responde solo con texto. No invoques otros CLIs de IA."
)

def supports_read_only(base: str) -> bool:
    adapter = ADAPTERS.get(base)
    return bool(adapter and getattr(adapter, "supports_read_only", False))
```

- [ ] **Step 1: Escribir los tests**

```python
"""Modo solo lectura por adaptador: flags exactos y rechazo donde no hay garantía."""
from pathlib import Path

import pytest

from app.models.smart import CliAgent
from app.services.cli_agents import adapters
from app.services.cli_agents.adapters import AdapterUnsafe, ADAPTERS


def _build(base, tmp_path, read_only, monkeypatch=None):
    return ADAPTERS[base].build(CliAgent(id=base), f"{base}.exe", "job1", "revisa esto", "", tmp_path, "standard", 600, read_only=read_only)


def test_claude_read_only_uses_plan_mode_and_blocks_edit_tools(tmp_path):
    argv = _build("claude", tmp_path, True).argv
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    blocked = argv[argv.index("--disallowedTools") + 1].split(",")
    assert {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"} <= set(blocked)
    argv_rw = _build("claude", tmp_path, False).argv
    assert argv_rw[argv_rw.index("--permission-mode") + 1] == "acceptEdits"


def test_codex_read_only_sandbox(tmp_path):
    spec = _build("codex", tmp_path, True)
    assert spec.argv[spec.argv.index("--sandbox") + 1] == "read-only"
    assert b"solo lectura" in spec.stdin_payload


def test_muse_read_only_flags(tmp_path):
    argv = _build("muse", tmp_path, True).argv
    assert "--disable-write" in argv and "--disable-shell" in argv


@pytest.mark.parametrize("base", ["copilot", "antigravity"])
def test_unsupported_adapters_refuse_read_only(base, tmp_path):
    with pytest.raises(AdapterUnsafe, match="read_only_unsupported"):
        _build(base, tmp_path, True)


def test_supports_read_only_table():
    assert adapters.supports_read_only("claude") and adapters.supports_read_only("codex")
    assert adapters.supports_read_only("muse") and adapters.supports_read_only("deepseek") and adapters.supports_read_only("cursor")
    assert not adapters.supports_read_only("copilot") and not adapters.supports_read_only("antigravity")
```

Para DeepseekAdapter y CursorAdapter, `build` necesita cosas del sistema (launcher de dsh, deny list y bundle de cursor). Agregar un test por cada uno siguiendo cómo `tests/test_cli_adapters.py` ya los construye (buscar ahí los fixtures o monkeypatches de `DeepseekAdapter.launcher`, `CursorAdapter.deny_list_present` y `bundle_for`). Deben afirmar `DSH_PERMISSION_MODE=read-only` en `spec.env`, y `--mode ask` presente sin `--force` en el argv de cursor.

- [ ] **Step 2: Correr y verificar que fallan** (`build()` no acepta `read_only`).

- [ ] **Step 3: Implementar** según la tabla. Mantener el comportamiento por defecto idéntico.

- [ ] **Step 4: Correr los tests nuevos y `tests/test_cli_adapters.py`** → verde.

---

### Task D2: Contrato de requests, config y jobs

**Files:**
- Modify: `backend/app/models/delegate.py`
- Modify: `backend/app/models/smart.py` (`DelegationConfig`)
- Test: `backend/tests/test_delegate_models.py`

**Interfaces:**
- Produces:
  - `JobRequest.verify: list[str] = Field(default_factory=list, max_length=10)` (cada comando con `max_length=500`, validado por un `field_validator`)
  - `JobRequest.review: Optional[bool] = None`
  - `JobRequest.max_revisions: int = Field(default=1, ge=0, le=3)`
  - `DelegationConfig.allow_request_verify: bool = False`, `review_default: bool = True`, `verify_timeout_s: int = Field(default=600, ge=30, le=3600)`, `review_timeout_s: int = Field(default=900, ge=60, le=3600)`
  - `Attempt.kind: Literal["work", "verify", "review"] = "work"` y `Attempt.detail: dict = Field(default_factory=dict)`
  - `Job.verification_status: Literal["n/a", "passed", "failed", "skipped"] = "n/a"`, `Job.review_status` con los mismos valores y `Job.escalations: int = 0`

- [ ] **Step 1: Escribir los tests**

```python
"""Contrato de delegación: campos nuevos con defaults neutros y límites."""
import pytest
from pydantic import ValidationError

from app.models.delegate import Attempt, Job, JobRequest
from app.models.smart import DelegationConfig


def test_job_request_defaults_are_neutral():
    req = JobRequest(task="x")
    assert req.verify == [] and req.review is None and req.max_revisions == 1


def test_job_request_limits():
    with pytest.raises(ValidationError):
        JobRequest(task="x", verify=["pytest"] * 11)
    with pytest.raises(ValidationError):
        JobRequest(task="x", verify=["a" * 501])
    with pytest.raises(ValidationError):
        JobRequest(task="x", max_revisions=4)


def test_delegation_config_defaults():
    cfg = DelegationConfig()
    assert cfg.allow_request_verify is False and cfg.review_default is True
    assert cfg.verify_timeout_s == 600 and cfg.review_timeout_s == 900


def test_attempt_and_job_defaults():
    attempt = Attempt(agent_id="claude", started_at="t")
    assert attempt.kind == "work" and attempt.detail == {}
    job = Job(id="j", created_at="t")
    assert job.verification_status == "n/a" and job.review_status == "n/a" and job.escalations == 0
```

- [ ] **Step 2: Correr y verificar que fallan.**
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Correr los tests nuevos y la suite completa** → verde.

---

### Task D3: Verificador de comandos

**Files:**
- Create: `backend/app/services/cli_agents/verifier.py`
- Test: `backend/tests/test_verifier.py`

**Interfaces:**
- Consumes: `adapters.exe_argv(exe) -> list[str]`, `adapters.child_env(base, extra=None) -> dict`, `psutil`.
- Produces:
  - `class InvalidCommand(ValueError)`
  - `parse_command(command: str, workspace: Path) -> list[str]`
  - `CheckResult(command: str, returncode: Optional[int], duration_s: float, output_tail: str, timed_out: bool = False, error: str = "")` con propiedad `ok`
  - `async run_checks(commands: list[str], workspace: Path, timeout_s: int) -> list[CheckResult]`: corre en orden y se detiene en el primero que falla.
  - `checks_passed(results: list[CheckResult]) -> bool`: `True` si la lista está vacía o todos están ok.
  - `OUTPUT_TAIL_CHARS = 8000`

**Reglas de `parse_command`:**
1. En Windows, reemplazar `\` por `/` antes de `shlex.split(command, posix=True)`: `shlex` en modo posix se come las barras invertidas.
2. Lista vacía → `InvalidCommand("comando vacío")`.
3. Si el primer token contiene `/`, se resuelve relativo al workspace: `shutil.which(str(workspace / token))`. Si no, `shutil.which(token)`.
4. No encontrado → `InvalidCommand(f"no se encontró el ejecutable: {token}")`.
5. Devuelve `exe_argv(found) + resto`.

**Ejecución:**
- `asyncio.create_subprocess_exec(*argv, cwd=str(workspace), env=child_env(os.environ), stdin=DEVNULL, stdout=PIPE, stderr=STDOUT)`.
- En Windows, `creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`; en otros, `start_new_session=True`.
- `await asyncio.wait_for(proc.communicate(), timeout_s)`. Si vence, se mata el árbol con psutil (hijos recursivos y padre), `await proc.wait()` y el resultado lleva `timed_out=True`.
- `output_tail` = los últimos `OUTPUT_TAIL_CHARS` de la salida decodificada con `utf-8`/`replace`.
- `InvalidCommand` al parsear produce `CheckResult(command, None, 0.0, "", error=str(e))` y se detiene.

- [ ] **Step 1: Escribir los tests**

```python
"""Verificador: comandos sin shell, resolución relativa al workspace, timeout que mata el árbol."""
import sys
from pathlib import Path

import pytest

from app.services.cli_agents import verifier

PY = sys.executable.replace("\\", "/")


def test_parse_command_resolves_path_and_splits(tmp_path):
    argv = verifier.parse_command(f'"{PY}" -c "print(1)"', tmp_path)
    assert Path(argv[-3]).resolve() == Path(sys.executable).resolve() or argv[0].lower().endswith(("cmd.exe", "python.exe", "python"))
    assert argv[-2:] == ["-c", "print(1)"]


def test_parse_command_relative_to_workspace(tmp_path):
    tool = tmp_path / "tools" / ("run.cmd" if sys.platform == "win32" else "run.sh")
    tool.parent.mkdir()
    tool.write_text("@echo ok\n" if sys.platform == "win32" else "#!/bin/sh\necho ok\n", encoding="utf-8")
    if sys.platform != "win32":
        tool.chmod(0o755)
    argv = verifier.parse_command(f"tools/{tool.name} --flag", tmp_path)
    assert str(tool.resolve()).lower() in " ".join(argv).lower().replace("/", "\\") or str(tool) in " ".join(argv)
    assert argv[-1] == "--flag"


@pytest.mark.parametrize("bad", ["", "   ", "definitely-not-a-real-binary-xyz --x"])
def test_parse_command_rejects(bad, tmp_path):
    with pytest.raises(verifier.InvalidCommand):
        verifier.parse_command(bad, tmp_path)


@pytest.mark.asyncio
async def test_run_checks_stops_at_first_failure(tmp_path):
    results = await verifier.run_checks(
        [f'"{PY}" -c "print(\'uno\')"', f'"{PY}" -c "import sys; print(\'dos\'); sys.exit(3)"', f'"{PY}" -c "print(\'tres\')"'],
        tmp_path, timeout_s=30)
    assert [r.returncode for r in results] == [0, 3]
    assert "dos" in results[1].output_tail
    assert not verifier.checks_passed(results)


@pytest.mark.asyncio
async def test_run_checks_timeout_kills_process(tmp_path):
    results = await verifier.run_checks([f'"{PY}" -c "import time; time.sleep(60)"'], tmp_path, timeout_s=1)
    assert results[0].timed_out and not results[0].ok


@pytest.mark.asyncio
async def test_invalid_command_is_a_failed_check(tmp_path):
    results = await verifier.run_checks(["definitely-not-a-real-binary-xyz"], tmp_path, timeout_s=5)
    assert results[0].error and not verifier.checks_passed(results)


def test_checks_passed_empty_is_true():
    assert verifier.checks_passed([])
```

- [ ] **Step 2: Correr y verificar que fallan.**
- [ ] **Step 3: Implementar `verifier.py`.**
- [ ] **Step 4: Correr los tests** → verde. Correrlos de verdad: crean procesos reales.

---

### Task D4: Revisión (diff, prompts y veredicto)

**Files:**
- Create: `backend/app/services/cli_agents/reviewer.py`
- Test: `backend/tests/test_reviewer.py`

**Interfaces:**
- Consumes: `verifier.CheckResult`.
- Produces:
  - `MAX_DIFF_CHARS = 60_000`
  - `Verdict(verdict: Literal["approve", "revise", "reject"], issues: list[str])` (dataclass)
  - `collect_diff(workspace: Path, files: list[str]) -> str`
  - `review_prompt(task: str, diff: str, checks: list[CheckResult]) -> str`
  - `parse_verdict(text: str) -> Optional[Verdict]`
  - `revision_task(task: str, issues: list[str], failed_check: Optional[CheckResult]) -> str`
  - `escalation_task(task: str, history: list[str]) -> str`

**Reglas:**
- `collect_diff`:
  - Con `.git` en el workspace: `git -C <ws> diff -- <files>` (timeout 10 s) más, para cada archivo marcado `??` en `git -C <ws> status --porcelain -- <files>`, un bloque `=== nuevo: <ruta> ===\n<contenido>` (texto, `errors="replace"`).
  - Sin `.git`: un bloque por archivo existente con su contenido.
  - Total recortado a `MAX_DIFF_CHARS`, con la marca `\n[diff recortado]` si se recortó.
  - Si no hay archivos, `"(el trabajador no cambió archivos)"`.
- `review_prompt` (en español), en este orden:
  1. El rol: "Eres el revisor de un cambio hecho por otro agente. Solo lectura."
  2. La tarea original.
  3. La salida de los checks: comando, código de salida y cola.
  4. El diff.
  5. Criterios: correcto y completo respecto de la tarea, sin bugs evidentes, sin secretos, sin cambios fuera de alcance.
  6. La instrucción exacta: "Termina tu respuesta con un bloque JSON en una línea: {\"verdict\": \"approve\"|\"revise\"|\"reject\", \"issues\": [\"...\"]}. Usa revise si se puede corregir; reject si el enfoque está mal."
- `parse_verdict`: busca de atrás hacia adelante el último objeto JSON que tenga la clave `verdict`. Lo intenta primero con un bloque ```json; después, cada `{...}` balanceado. `verdict` debe estar en los tres valores (en minúsculas); `issues` debe ser lista de strings (si falta, `[]`). Cualquier otra cosa → `None`.
- `revision_task`: la tarea original, más "Corrige lo siguiente sin rehacer lo que ya está bien:", la lista de issues y, si hay check fallido, su comando y cola.
- `escalation_task`: la tarea original, más "Un intento anterior dejó cambios en el árbol de trabajo y no pasó la revisión. Parte de ese estado; no reviertas nada que no entiendas.", más el historial (una línea por intento).

- [ ] **Step 1: Escribir los tests**

```python
"""Revisor: diff acotado, prompts y lectura estricta del veredicto."""
import subprocess
from pathlib import Path

from app.services.cli_agents import reviewer
from app.services.cli_agents.verifier import CheckResult


def _git(ws: Path, *args):
    subprocess.run(["git", "-C", str(ws), *args], check=True, capture_output=True)


def test_collect_diff_includes_modified_and_new_files(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / "a.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 3\n", encoding="utf-8")
    diff = reviewer.collect_diff(tmp_path, ["a.py", "b.py"])
    assert "-x = 1" in diff and "+x = 2" in diff
    assert "=== nuevo: b.py ===" in diff and "y = 3" in diff


def test_collect_diff_caps_size(tmp_path):
    (tmp_path / "big.txt").write_text("z" * (reviewer.MAX_DIFF_CHARS + 500), encoding="utf-8")
    diff = reviewer.collect_diff(tmp_path, ["big.txt"])
    assert len(diff) <= reviewer.MAX_DIFF_CHARS + 50 and diff.endswith("[diff recortado]")


def test_collect_diff_without_files():
    assert reviewer.collect_diff(Path("."), []) == "(el trabajador no cambió archivos)"


def test_parse_verdict_variants():
    assert reviewer.parse_verdict('ok\n```json\n{"verdict": "approve", "issues": []}\n```') == reviewer.Verdict("approve", [])
    v = reviewer.parse_verdict('texto {"x": 1} más texto {"verdict": "revise", "issues": ["falta test"]}')
    assert v == reviewer.Verdict("revise", ["falta test"])
    assert reviewer.parse_verdict('{"verdict": "REJECT"}') == reviewer.Verdict("reject", [])
    assert reviewer.parse_verdict("me parece bien") is None
    assert reviewer.parse_verdict('{"verdict": "maybe"}') is None
    assert reviewer.parse_verdict('{"verdict": "approve", "issues": "no es lista"}') is None


def test_prompts_carry_the_context():
    check = CheckResult("pytest -q", 1, 2.0, "1 failed")
    prompt = reviewer.review_prompt("agrega X", "+x", [check])
    assert "agrega X" in prompt and "+x" in prompt and "pytest -q" in prompt and '"verdict"' in prompt
    rev = reviewer.revision_task("agrega X", ["falta test"], check)
    assert "falta test" in rev and "1 failed" in rev
    esc = reviewer.escalation_task("agrega X", ["muse: verify_failed pytest -q"])
    assert "muse: verify_failed" in esc and "agrega X" in esc
```

- [ ] **Step 2: Correr y verificar que fallan.**
- [ ] **Step 3: Implementar `reviewer.py`.**
- [ ] **Step 4: Correr los tests** → verde.

---

### Task D5: Puerta de calidad y escalado en el broker

**Files:**
- Modify: `backend/app/services/cli_agents/broker.py`
- Modify: `backend/app/api/delegate.py`
- Test: `backend/tests/test_broker_quality_gate.py`

**Interfaces:**
- Consumes: todo lo de D1-D4, más `CliAgent.base`, `DelegationConfig.thinkers` y `account_env` (plan B).
- Produces:
  - `class InvalidRequest(ValueError)` en broker. La API la mapea a 400.
  - `_run_attempt(rt, agent, model, timeout_s, task: Optional[str] = None, read_only: bool = False) -> AttemptOutcome`. Usa `task or rt.request.task` y pasa `read_only` a `build`. Ollama ignora `read_only` y la usa solo como texto.
  - Eventos SSE nuevos: `verify_result`, `review_result`, `revision` y `escalated`.

**Cambios en `submit`:**
- `req.verify` con `mode != "task"` → `InvalidRequest("verify_requires_task_mode")`.
- `req.verify` con `allow_request_verify=False` → `InvalidRequest("verify_disabled")`.
- Cada comando se valida con `verifier.parse_command(cmd, workspace)`. Un `InvalidCommand` se convierte en `InvalidRequest(f"invalid_verify: {e}")`.
- Todo esto va antes de crear el job.

**Ciclo nuevo en `_run_attempts`.** El bucle existente se conserva para los fallos del trabajador; cuando `outcome.ok`, en vez de llamar directamente `_finish_ok`:

```python
if outcome.ok:
    gate = await _quality_gate(rt, registry, statuses, agent, model, outcome)
    if gate.accepted:
        _finish_ok(job, gate.outcome, agent, model)
        break
    history.append(f"{agent.id}: {gate.reason}")
    tried.append(agent.id)
    nxt, nxt_model = _escalation_target(rt, registry, statuses, tried)
    if nxt is None:
        job.status, job.error = "failed", gate.reason[:300]
        break
    job.escalations += 1
    _emit(rt, {"event": "escalated", "from": agent.id, "to": nxt.id, "reason": gate.reason[:300]})
    agent, model = nxt, nxt_model
    rt.current_task = reviewer.escalation_task(rt.request.task, history)
    continue
```

El intento de trabajo usa `rt.current_task`, un campo nuevo de `JobRuntime` que inicia en `rt.request.task`. El tope `max_attempts` cuenta solo los `Attempt` con `kind == "work"` y `not detail.get("revision")`.

**`_quality_gate(rt, registry, statuses, worker, model, outcome) -> GateResult(accepted: bool, reason: str, outcome: AttemptOutcome)`:**

```
si rt.request.mode != "task": accepted (verificación y revisión quedan "n/a")
revisiones = rt.request.max_revisions
bucle:
    checks = await verifier.run_checks(rt.request.verify, rt.workspace, registry.delegation.verify_timeout_s)   # [] si no hay comandos
        → registrar un Attempt(kind="verify", agent_id="bipolar", detail={"command", "returncode", "timed_out", "error"}) por check
        → emitir verify_result por check
    si checks y no checks_passed(checks):
        job.verification_status = "failed"
        si revisiones > 0: revisiones -= 1; outcome = await _revise(rt, registry, worker, model, issues=[], failed_check=último); si not outcome.ok → rechazo("revision_failed: " + outcome.error); continuar el bucle
        rechazo(f"verify_failed: {último.command}")
    job.verification_status = "passed" si checks, si no "n/a"
    si not _review_enabled(rt, registry): job.review_status = "n/a"; accepted
    verdict = await _review(rt, registry, statuses, worker, checks)
    si verdict is None: job.review_status = "skipped"; accepted
    si verdict.verdict == "approve": job.review_status = "passed"; accepted
    si verdict.verdict == "revise" y revisiones > 0: revisiones -= 1; outcome = await _revise(..., issues=verdict.issues, failed_check=None); si not outcome.ok → rechazo; continuar el bucle
    job.review_status = "failed"; rechazo("review_rejected: " + "; ".join(verdict.issues))
```

- `_revise` corre `_run_attempt(rt, worker, model, timeout, task=reviewer.revision_task(rt.request.task, issues, failed_check))`, registra un `Attempt(kind="work", detail={"revision": True})` y emite `revision`.
- `_review_enabled`: `rt.request.review` si no es `None`; si no, `registry.delegation.review_default`.

**`_review(rt, registry, statuses, worker, checks) -> Optional[Verdict]`:**
- Candidatos, por id:
  1. En el orden de `registry.delegation.thinkers`, con `agent.id != worker.id`, `adapters.supports_read_only(agent.base)` y sin motivo de rechazo según `_reject_agent(agent, job.tier, "task", statuses.get(id), model, False)`.
  2. Orden estable: primero los de `base != worker.base`.
- Diff: `reviewer.collect_diff(rt.workspace, _files_touched(rt.workspace, rt.status_before))`. Para esto, `rt.status_before` se guarda en `JobRuntime` al empezar el job; hoy es una variable local de `_run_job`.
- Por cada candidato:
  - `outcome = await _run_attempt(rt, cand, model_cand, registry.delegation.review_timeout_s, task=reviewer.review_prompt(...), read_only=True)`.
  - Registrar `Attempt(kind="review", agent_id=cand.id, detail={"verdict": v.verdict if v else "", "issues": v.issues if v else []})` y emitir `review_result`.
  - `outcome.signal` → `_apply_signal(cand, model_cand, outcome.signal)` y seguir con el siguiente.
  - `v = reviewer.parse_verdict(outcome.result.text) if outcome.ok and outcome.result else None`.
  - Si `v`, devolverlo. Si no, seguir.
- Sin candidatos o ninguno sirvió: `None`.
- `model_cand = cand.model_for(job.tier)`.

**`_escalation_target(rt, registry, statuses, tried) -> tuple[Optional[CliAgent], str]`:**
1. Primer id de `thinkers` que no esté en `tried`, cuyo `agent.agentic` sea verdadero y sin motivo de rechazo.
2. Si no hay: `choose_agent(job.tier, registry, statuses, exclude=tuple(tried), mode="task")`.

**API (`api/delegate.py`):** capturar `broker.InvalidRequest` → `HTTPException(400, str(e))`.

- [ ] **Step 1: Escribir los tests**

Crear `backend/tests/test_broker_quality_gate.py`. Reutiliza el fixture `env` de `tests/test_delegation_broker.py`: copiarlo o importarlo con `from tests.test_delegation_broker import env, _agents, _wait`, si el paquete `tests` es importable (tiene `__init__.py`). Antes de cada test, el registry debe tener `delegation.allow_request_verify = True` y `delegation.thinkers = ["claude", "codex"]`, con `claude` y `codex` habilitados (eso ya hace el fixture).

```python
"""Puerta de calidad: verificación, revisión, revisiones y escalado."""
import pytest

from app.models.delegate import JobRequest
from app.services.cli_agents import broker, verifier
from app.services.cli_agents.adapters import AdapterResult
from app.services.cli_agents.verifier import CheckResult
from tests.test_delegation_broker import env, _wait  # noqa: F401  (fixture)


def _ok(text="hecho"):
    return broker.AttemptOutcome(ok=True, returncode=0, result=AdapterResult(text=text))


def _verdict(v, issues=()):
    import json
    return _ok("revisión\n" + json.dumps({"verdict": v, "issues": list(issues)}))


def _script(monkeypatch, plan):
    """plan: lista de (agent_id, read_only, outcome) en el orden esperado de llamadas."""
    calls = []

    async def fake(rt, agent, model, timeout_s, task=None, read_only=False):
        calls.append((agent.id, read_only, task))
        exp_agent, exp_ro, outcome = plan.pop(0)
        assert (agent.id, read_only) == (exp_agent, exp_ro), f"llamada inesperada {(agent.id, read_only)}"
        return outcome

    monkeypatch.setattr(broker, "_run_attempt", fake)
    return calls


def _checks(monkeypatch, sequence):
    async def fake(commands, workspace, timeout_s):
        return sequence.pop(0) if commands else []
    monkeypatch.setattr(verifier, "run_checks", fake)


def _setup(env):
    env["registry"].delegation.allow_request_verify = True
    env["registry"].delegation.thinkers = ["claude", "codex"]


@pytest.mark.asyncio
async def test_pass_checks_and_approve(env, monkeypatch):
    _setup(env)
    _script(monkeypatch, [("codex", False, _ok()), ("claude", True, _verdict("approve"))])
    _checks(monkeypatch, [[CheckResult("pytest", 0, 1.0, "ok")]])
    job = await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", verify=["pytest"]))
    job = await _wait(job.id)
    assert job.status == "succeeded"
    assert job.verification_status == "passed" and job.review_status == "passed"
    assert [a.kind for a in job.attempts] == ["work", "verify", "review"]


@pytest.mark.asyncio
async def test_failed_check_gets_one_revision_then_passes(env, monkeypatch):
    _setup(env)
    calls = _script(monkeypatch, [("codex", False, _ok()), ("codex", False, _ok("arreglado")), ("claude", True, _verdict("approve"))])
    _checks(monkeypatch, [[CheckResult("pytest", 1, 1.0, "1 failed")], [CheckResult("pytest", 0, 1.0, "ok")]])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", verify=["pytest"]))).id)
    assert job.status == "succeeded"
    assert "1 failed" in calls[1][2]


@pytest.mark.asyncio
async def test_reject_escalates_to_next_thinker(env, monkeypatch):
    _setup(env)
    _script(monkeypatch, [
        ("codex", False, _ok()), ("claude", True, _verdict("reject", ["enfoque equivocado"])),
        ("claude", False, _ok("rehecho")), ("codex", True, _verdict("approve")),
    ])
    _checks(monkeypatch, [])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard"))).id)
    assert job.status == "succeeded" and job.escalations == 1
    assert job.agent_id == "claude"


@pytest.mark.asyncio
async def test_unparseable_review_then_no_reviewers_is_skipped_not_passed(env, monkeypatch):
    _setup(env)
    _script(monkeypatch, [("codex", False, _ok()), ("claude", True, _ok("me parece bien"))])
    _checks(monkeypatch, [])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard"))).id)
    assert job.status == "succeeded" and job.review_status == "skipped"


@pytest.mark.asyncio
async def test_no_escalation_target_fails_with_reason(env, monkeypatch):
    _setup(env)
    env["registry"].delegation.thinkers = ["claude"]
    _script(monkeypatch, [
        ("codex", False, _ok()), ("claude", True, _verdict("reject", ["mal"])),
        ("claude", False, _ok()), ("codex", True, _verdict("reject", ["mal otra vez"])),
    ])
    _checks(monkeypatch, [])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", max_revisions=0))).id)
    assert job.status == "failed" and job.error.startswith("review_rejected")


@pytest.mark.asyncio
async def test_verify_disabled_and_invalid_commands_are_rejected(env):
    with pytest.raises(broker.InvalidRequest, match="verify_disabled"):
        await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), verify=["pytest"]))
    _setup(env)
    with pytest.raises(broker.InvalidRequest, match="invalid_verify"):
        await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), verify=["definitely-not-a-real-binary-xyz"]))


@pytest.mark.asyncio
async def test_review_disabled_skips_review(env, monkeypatch):
    _setup(env)
    _script(monkeypatch, [("codex", False, _ok())])
    _checks(monkeypatch, [])
    job = await _wait((await broker.submit(JobRequest(task="t", workspace=str(env["workspace"]), tier_hint="standard", review=False))).id)
    assert job.status == "succeeded" and job.review_status == "n/a"
```

En `test_reject_escalates_to_next_thinker`, el primer trabajador del tier `standard` según el `tier_order` del fixture es `codex`. Tras el rechazo de `claude` como revisor, el destino de escalado es el primer pensador no probado (`claude`). Después revisa `codex`, distinto del trabajador `claude`.

En `test_no_escalation_target_fails_with_reason`, con `thinkers = ["claude"]`, el escalado desde `codex` va a `claude`. Como `codex` no es pensador, `_review` no lo vería; ajustar `thinkers` del test a `["claude", "codex"]` y `max_attempts = 2` para que, tras el segundo rechazo, no quede trabajo disponible. **Redactar el test definitivo para que pruebe exactamente esto**: con los trabajadores agotados por `max_attempts` y un rechazo, el job termina `failed` con `review_rejected`.

- [ ] **Step 2: Correr y verificar que fallan.**

- [ ] **Step 3: Implementar** los cambios en broker y API según el diseño. Mantener verde `tests/test_delegation_broker.py`: los jobs de esos tests no traen `verify` y el `review_default` es `True`, así que la revisión correría y cambiaría sus llamadas. **En el fixture `env` de `test_delegation_broker.py`, poner `review_default=False` en el `DelegationConfig`**, con un comentario de una línea: "las pruebas de failover de este archivo no cubren la puerta de calidad".

- [ ] **Step 4: Correr los tests nuevos y los del broker y la API**

Run: `cd backend && python -m pytest tests/test_broker_quality_gate.py tests/test_delegation_broker.py tests/test_smart_and_delegate_api.py -q` → verde.

- [ ] **Step 5: Suite completa** → verde.
