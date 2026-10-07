# Plan A — Pools de llaves y failover dentro del request

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un request a `/v1/messages` o `/v1/chat/completions` se atienda con la siguiente llave o el siguiente provider cuando el primero responde 429/402/401/403/5xx/529 o no conecta, antes de que el cliente vea un error.

**Architecture:** Cada provider tiene "slots" de credencial (llave principal + `extra_auth_env_vars`). `smart_router.decide` arma un plan ordenado de pasos `(provider, modelo, slot)`. Un módulo nuevo `services/upstream.py` recorre el plan antes del primer byte: abre el upstream, clasifica la falla y decide si sigue. Los endpoints entregan el stream del paso ganador, o devuelven un error JSON con status HTTP real si ninguno respondió.

**Tech Stack:** Python 3.11, FastAPI, httpx (async), pydantic v2, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-07-orquestacion-transparente-design.md` (sección 4).

## Global Constraints

- Repo: `C:\personal\bipolar-code\bipolar-orq` (worktree, rama `feature/orquestacion-transparente`). Backend en `backend/`; los tests corren desde `backend/` con `python -m pytest -q`.
- Sin dependencias nuevas.
- Estilo: funciones cortas (<50 líneas) con nombre que diga lo que hacen; sin docstrings largos; comentario de una línea solo cuando el porqué no es obvio. Docstring de módulo corto en español, como los existentes.
- Compatibilidad: un provider con un solo slot sigue usando la clave de salud `provider:<id>`. Con dos o más slots, la clave de cada slot es `provider:<id>#<NOMBRE_DE_VARIABLE>` (`#nokey` si la variable está vacía).
- Nunca se loguean ni se devuelven valores de llaves; los errores al cliente pasan por `sanitize_error`.
- No tocar `litellm`, ni el formato de la conversión Anthropic→OpenAI, ni el reintento por límite de contexto (se mueve, pero su lógica no cambia).
- No ejecutar `git add/commit/push/reset/checkout`: el orquestador commitea.

## Review Focus

1. **Cliente que se desconecta a mitad del stream**: el upstream abierto se cierra igual (el generador cierra su `AsyncExitStack` en `finally`). Test en A4.
2. **Error fatal (400) del primer destino**: no se reintenta en los demás. Un request mal formado no se pasea por N providers. Tests en A2 y A4.
3. **Todos agotados con fallas mezcladas** (429 en uno, conexión caída en otro): el cliente recibe 429 (`rate_limit_error`), no 502, para que aplique su backoff. Tests en A2 y A4.
4. **Llave extra borrada del `.env`** con salud vieja: los slots sin valor desaparecen del plan y la clave de salud depende del nombre de la variable, no del índice, así que borrar una llave no hereda la salud de otra. Test en A1.
5. **Provider único sin fallbacks y sin slots disponibles** (todos en cooling): el plan no queda vacío, se intenta el slot 0 del primario y el cliente ve el error real. Test en A3.

---

### Task A1: Slots de credencial

**Files:**
- Modify: `backend/app/models/provider.py` (clase `Provider`, después de `auth_env_var`)
- Create: `backend/app/services/credentials.py`
- Test: `backend/tests/test_credentials.py`

**Interfaces:**
- Consumes: `app.core.config.env_value(name) -> str`, `app.services.health_service.is_available(key) -> bool`.
- Produces:
  - `Provider.extra_auth_env_vars: list[str]`
  - `CredentialSlot(provider_id: str, slot: int, env_var: str, health_key: str)` (dataclass frozen)
  - `provider_key(provider_id: str) -> str`
  - `credential_slots(provider: Provider) -> list[CredentialSlot]`
  - `available_slots(provider: Provider) -> list[CredentialSlot]`
  - `api_key_for(slot: CredentialSlot) -> str`

- [ ] **Step 1: Agregar el campo al modelo**

En `backend/app/models/provider.py`, dentro de `class Provider`, justo debajo de `auth_env_var: str = ""`:

```python
    extra_auth_env_vars: list[str] = []  # llaves adicionales del pool (nombres de variables del .env)
```

- [ ] **Step 2: Escribir los tests**

Crear `backend/tests/test_credentials.py`:

```python
"""Slots de credencial: llave principal + pool, claves de salud estables por nombre de variable."""
import pytest

from app.core.quota_signals import QuotaSignal
from app.models.provider import Provider
from app.services import credentials, health_service


@pytest.fixture
def env(tmp_path, monkeypatch):
    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(health_service, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    yield monkeypatch
    health_service.reload_for_tests()


def _provider(**kw) -> Provider:
    return Provider(id="p1", name="p1", api_base="https://p1.example.com/v1", **kw)


def test_single_slot_keeps_legacy_health_key(env):
    env.setenv("P1_KEY", "k1")
    slots = credentials.credential_slots(_provider(auth_env_var="P1_KEY"))
    assert [(s.slot, s.env_var, s.health_key) for s in slots] == [(0, "P1_KEY", "provider:p1")]


def test_keyless_provider_has_one_slot_without_key(env):
    slots = credentials.credential_slots(_provider())
    assert [(s.slot, s.env_var, s.health_key) for s in slots] == [(0, "", "provider:p1")]
    assert credentials.api_key_for(slots[0]) == ""


def test_pool_slots_use_env_var_name_in_health_key(env):
    env.setenv("P1_KEY", "k1")
    env.setenv("P1_KEY_2", "k2")
    slots = credentials.credential_slots(_provider(auth_env_var="P1_KEY", extra_auth_env_vars=["P1_KEY_2"]))
    assert [(s.slot, s.health_key) for s in slots] == [(0, "provider:p1#P1_KEY"), (1, "provider:p1#P1_KEY_2")]
    assert credentials.api_key_for(slots[1]) == "k2"


def test_extra_without_value_is_skipped_and_keys_stay_stable(env):
    env.setenv("P1_KEY", "k1")
    env.setenv("P1_KEY_3", "k3")
    env.delenv("P1_KEY_2", raising=False)
    slots = credentials.credential_slots(_provider(auth_env_var="P1_KEY", extra_auth_env_vars=["P1_KEY_2", "P1_KEY_3"]))
    assert [s.health_key for s in slots] == ["provider:p1#P1_KEY", "provider:p1#P1_KEY_3"]


def test_available_slots_skip_exhausted_slot(env):
    env.setenv("P1_KEY", "k1")
    env.setenv("P1_KEY_2", "k2")
    provider = _provider(auth_env_var="P1_KEY", extra_auth_env_vars=["P1_KEY_2"])
    health_service.mark_signal("provider:p1#P1_KEY", QuotaSignal(kind="quota_exhausted", retry_after_s=600, excerpt="quota"))
    assert [s.env_var for s in credentials.available_slots(provider)] == ["P1_KEY_2"]


def test_provider_key_format():
    assert credentials.provider_key("p1") == "provider:p1"
```

- [ ] **Step 3: Correr los tests y verificar que fallan**

Run: `cd backend && python -m pytest tests/test_credentials.py -q`
Expected: FAIL con `ModuleNotFoundError` o `AttributeError: module 'app.services' has no attribute 'credentials'`.

- [ ] **Step 4: Implementar**

Crear `backend/app/services/credentials.py`:

```python
"""Slots de credencial por provider: llave principal (auth_env_var) más las del pool
(extra_auth_env_vars). Con dos o más slots, cada uno tiene su propia clave de salud."""
from dataclasses import dataclass

from app.core.config import env_value
from app.models.provider import Provider
from app.services import health_service


@dataclass(frozen=True)
class CredentialSlot:
    provider_id: str
    slot: int
    env_var: str
    health_key: str


def provider_key(provider_id: str) -> str:
    return f"provider:{provider_id}"


def _slot_env_vars(provider: Provider) -> list[str]:
    extras = [name for name in provider.extra_auth_env_vars if name and env_value(name)]
    return [provider.auth_env_var, *extras]


def _health_key(provider_id: str, env_var: str, n_slots: int) -> str:
    if n_slots <= 1:
        return provider_key(provider_id)
    return f"{provider_key(provider_id)}#{env_var or 'nokey'}"


def credential_slots(provider: Provider) -> list[CredentialSlot]:
    env_vars = _slot_env_vars(provider)
    n_slots = len(env_vars)
    return [
        CredentialSlot(provider.id, index, name, _health_key(provider.id, name, n_slots))
        for index, name in enumerate(env_vars)
    ]


def available_slots(provider: Provider) -> list[CredentialSlot]:
    return [slot for slot in credential_slots(provider) if health_service.is_available(slot.health_key)]


def api_key_for(slot: CredentialSlot) -> str:
    return env_value(slot.env_var) if slot.env_var else ""
```

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `cd backend && python -m pytest tests/test_credentials.py -q`
Expected: `6 passed`.

- [ ] **Step 6: Suite completa**

Run: `cd backend && python -m pytest -q`
Expected: todo en verde (el campo nuevo tiene default, así que nada más cambia).

---

### Task A2: Clasificación de fallas y recorrido del plan

**Files:**
- Create: `backend/app/services/upstream.py`
- Test: `backend/tests/test_upstream.py`

**Interfaces:**
- Consumes: `app.core.quota_signals.detect_signal(text, status) -> QuotaSignal | None` (campo `.kind` ∈ `rate_limit`, `quota_exhausted`, `auth`, `overloaded`).
- Produces:
  - `FailureKind = Literal["retry_credential", "retry_provider", "fatal"]`
  - `classify_failure(status: int | None, text: str = "", exc: BaseException | None = None) -> FailureKind`
  - `Opened(upstream: Any)` y `Failed(status: int | None, message: str, exc: BaseException | None = None)` (dataclasses)
  - `AttemptFailure(step: Any, kind: FailureKind, status: int | None, message: str)`
  - `PlanResult(step: Any = None, upstream: Any = None, failures: list[AttemptFailure])` con propiedad `ok`
  - `async attempt_plan(plan: list, open_attempt: Callable[[step], Awaitable[Opened | Failed]], provider_of: Callable[[step], str], on_failure: Callable[[step, AttemptFailure], None]) -> PlanResult`
  - `exhausted_status(failures: list[AttemptFailure]) -> int`
  - `failures_summary(failures: list[AttemptFailure], describe: Callable[[step], str]) -> str`
  - `anthropic_error_body(status: int, message: str) -> dict`
  - `openai_error_body(status: int, message: str) -> dict`

- [ ] **Step 1: Escribir los tests**

Crear `backend/tests/test_upstream.py`:

```python
"""Failover antes del primer byte: clasificación de fallas y recorrido del plan."""
import httpx
import pytest

from app.services import upstream
from app.services.upstream import AttemptFailure, Failed, Opened


@pytest.mark.parametrize("status,text,expected", [
    (401, "", "retry_credential"),
    (402, "", "retry_credential"),
    (403, "", "retry_credential"),
    (429, "", "retry_credential"),
    (400, "insufficient balance", "retry_credential"),
    (400, "You exceeded your current quota", "retry_credential"),
    (500, "", "retry_provider"),
    (503, "", "retry_provider"),
    (529, "Overloaded", "retry_provider"),
    (404, "model not found", "retry_provider"),
    (408, "", "retry_provider"),
    (400, "messages: field required", "fatal"),
    (413, "request too large", "fatal"),
    (422, "", "fatal"),
])
def test_classify_failure_by_status_and_text(status, text, expected):
    assert upstream.classify_failure(status, text) == expected


def test_classify_failure_exception_is_provider_level():
    assert upstream.classify_failure(None, "", httpx.ConnectError("boom")) == "retry_provider"


def _runner(outcomes: dict):
    calls = []

    async def open_attempt(step):
        calls.append(step)
        return outcomes[step]

    return open_attempt, calls


@pytest.mark.asyncio
async def test_credential_failure_moves_to_next_slot_same_provider():
    plan = ["a#0", "a#1", "b#0"]
    open_attempt, calls = _runner({"a#0": Failed(429, "rate limit"), "a#1": Opened("stream-a1"), "b#0": Opened("stream-b")})
    seen = []
    result = await upstream.attempt_plan(plan, open_attempt, lambda s: s.split("#")[0], lambda s, f: seen.append((s, f.kind)))
    assert result.ok and result.step == "a#1" and result.upstream == "stream-a1"
    assert calls == ["a#0", "a#1"]
    assert seen == [("a#0", "retry_credential")]


@pytest.mark.asyncio
async def test_provider_failure_skips_remaining_slots_of_that_provider():
    plan = ["a#0", "a#1", "b#0"]
    open_attempt, calls = _runner({"a#0": Failed(None, "connect", httpx.ConnectError("x")), "a#1": Opened("never"), "b#0": Opened("stream-b")})
    result = await upstream.attempt_plan(plan, open_attempt, lambda s: s.split("#")[0], lambda s, f: None)
    assert result.step == "b#0"
    assert calls == ["a#0", "b#0"]


@pytest.mark.asyncio
async def test_fatal_failure_stops_without_trying_others():
    plan = ["a#0", "b#0"]
    open_attempt, calls = _runner({"a#0": Failed(400, "messages: field required"), "b#0": Opened("never")})
    result = await upstream.attempt_plan(plan, open_attempt, lambda s: s.split("#")[0], lambda s, f: None)
    assert not result.ok
    assert calls == ["a#0"]
    assert [f.kind for f in result.failures] == ["fatal"]
    assert upstream.exhausted_status(result.failures) == 400


@pytest.mark.asyncio
async def test_all_failed_mixed_rate_limit_and_connect_maps_to_429():
    plan = ["a#0", "b#0"]
    open_attempt, _ = _runner({"a#0": Failed(429, "rate limit"), "b#0": Failed(None, "connect", httpx.ConnectError("x"))})
    result = await upstream.attempt_plan(plan, open_attempt, lambda s: s.split("#")[0], lambda s, f: None)
    assert not result.ok
    assert upstream.exhausted_status(result.failures) == 429


def test_exhausted_status_overloaded_and_generic():
    overloaded = [AttemptFailure("a", "retry_provider", 529, "Overloaded")]
    generic = [AttemptFailure("a", "retry_provider", None, "connect error")]
    auth_only = [AttemptFailure("a", "retry_credential", 401, "invalid api key")]
    assert upstream.exhausted_status(overloaded) == 529
    assert upstream.exhausted_status(generic) == 502
    assert upstream.exhausted_status(auth_only) == 502
    assert upstream.exhausted_status([]) == 502


def test_error_bodies_and_summary():
    failures = [AttemptFailure("a#0", "retry_credential", 429, "rate limit"), AttemptFailure("b#0", "retry_provider", None, "connect error")]
    summary = upstream.failures_summary(failures, lambda s: s)
    assert summary == "a#0: 429 rate limit; b#0: connect error"
    assert upstream.anthropic_error_body(429, "x") == {"type": "error", "error": {"type": "rate_limit_error", "message": "x"}}
    assert upstream.anthropic_error_body(529, "x")["error"]["type"] == "overloaded_error"
    assert upstream.anthropic_error_body(400, "x")["error"]["type"] == "invalid_request_error"
    assert upstream.anthropic_error_body(502, "x")["error"]["type"] == "api_error"
    assert upstream.openai_error_body(429, "x") == {"error": {"message": "x", "type": "rate_limit_error", "code": 429}}
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `cd backend && python -m pytest tests/test_upstream.py -q`
Expected: FAIL (`ModuleNotFoundError: app.services.upstream`).

- [ ] **Step 3: Implementar**

Crear `backend/app/services/upstream.py`:

```python
"""Failover antes del primer byte: recorre un plan de pasos (provider + llave), abre el
upstream del primero que responde bien y clasifica las fallas para decidir si sigue."""
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional

from app.core.quota_signals import detect_signal

FailureKind = Literal["retry_credential", "retry_provider", "fatal"]

CREDENTIAL_STATUSES = frozenset({401, 402, 403, 429})
PROVIDER_STATUSES = frozenset({404, 408})
CREDENTIAL_SIGNALS = frozenset({"rate_limit", "quota_exhausted", "auth"})
RATE_LIMIT_STATUSES = frozenset({402, 429})
OVERLOADED_STATUSES = frozenset({503, 529})
SUMMARY_MESSAGE_CHARS = 120

_ANTHROPIC_ERROR_TYPES = {429: "rate_limit_error", 529: "overloaded_error"}


@dataclass
class Opened:
    upstream: Any


@dataclass
class Failed:
    status: Optional[int]
    message: str
    exc: Optional[BaseException] = None


@dataclass
class AttemptFailure:
    step: Any
    kind: FailureKind
    status: Optional[int]
    message: str


@dataclass
class PlanResult:
    step: Any = None
    upstream: Any = None
    failures: list[AttemptFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.upstream is not None


def classify_failure(status: Optional[int], text: str = "", exc: Optional[BaseException] = None) -> FailureKind:
    if exc is not None:
        return "retry_provider"
    if status in CREDENTIAL_STATUSES:
        return "retry_credential"
    signal = detect_signal(text or "", status)
    if signal and signal.kind in CREDENTIAL_SIGNALS:
        return "retry_credential"
    if signal and signal.kind == "overloaded":
        return "retry_provider"
    if status is not None and (status in PROVIDER_STATUSES or status >= 500):
        return "retry_provider"
    return "fatal"


async def attempt_plan(
    plan: list,
    open_attempt: Callable[[Any], Awaitable[Opened | Failed]],
    provider_of: Callable[[Any], str],
    on_failure: Callable[[Any, AttemptFailure], None],
) -> PlanResult:
    result = PlanResult()
    dead_providers: set[str] = set()
    for step in plan:
        if provider_of(step) in dead_providers:
            continue
        outcome = await open_attempt(step)
        if isinstance(outcome, Opened):
            result.step, result.upstream = step, outcome.upstream
            return result
        failure = AttemptFailure(step, classify_failure(outcome.status, outcome.message, outcome.exc), outcome.status, outcome.message)
        on_failure(step, failure)
        result.failures.append(failure)
        if failure.kind == "fatal":
            return result
        if failure.kind == "retry_provider":
            dead_providers.add(provider_of(step))
    return result


def _is_rate_limited(failure: AttemptFailure) -> bool:
    if failure.status in RATE_LIMIT_STATUSES:
        return True
    signal = detect_signal(failure.message or "", failure.status)
    return bool(signal and signal.kind in ("rate_limit", "quota_exhausted"))


def _is_overloaded(failure: AttemptFailure) -> bool:
    if failure.status in OVERLOADED_STATUSES:
        return True
    signal = detect_signal(failure.message or "", failure.status)
    return bool(signal and signal.kind == "overloaded")


def exhausted_status(failures: list[AttemptFailure]) -> int:
    if failures and failures[-1].kind == "fatal":
        return failures[-1].status or 502
    if any(_is_rate_limited(f) for f in failures):
        return 429
    if any(_is_overloaded(f) for f in failures):
        return 529
    return 502


def failures_summary(failures: list[AttemptFailure], describe: Callable[[Any], str]) -> str:
    parts = []
    for f in failures:
        status = f"{f.status} " if f.status else ""
        parts.append(f"{describe(f.step)}: {status}{(f.message or '')[:SUMMARY_MESSAGE_CHARS]}")
    return "; ".join(parts)


def _anthropic_error_type(status: int) -> str:
    if status in _ANTHROPIC_ERROR_TYPES:
        return _ANTHROPIC_ERROR_TYPES[status]
    return "invalid_request_error" if 400 <= status < 500 else "api_error"


def anthropic_error_body(status: int, message: str) -> dict:
    return {"type": "error", "error": {"type": _anthropic_error_type(status), "message": message}}


def openai_error_body(status: int, message: str) -> dict:
    return {"error": {"message": message, "type": _anthropic_error_type(status), "code": status}}
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd backend && python -m pytest tests/test_upstream.py -q`
Expected: todos pasan (18 casos con la parametrización).

---

### Task A3: Plan de ruta en el router

**Files:**
- Modify: `backend/app/models/smart.py` (clase `SmartRoutingConfig`)
- Modify: `backend/app/services/smart_router.py`
- Test: `backend/tests/test_route_plan.py`

**Interfaces:**
- Consumes: `credentials.credential_slots`, `credentials.available_slots`, `credentials.provider_key`, `CredentialSlot`; `upstream.AttemptFailure`; `providers_service._is_local_base(api_base) -> bool`, `providers_service._is_reachable(api_base) -> Awaitable[bool]`; `health_service.mark_signal/mark_failure/is_available`; `quota_signals.detect_signal`, `QuotaSignal`.
- Produces:
  - `SmartRoutingConfig.max_failover_attempts: int = 4` (pydantic `Field(default=4, ge=1, le=10)`)
  - `smart_router.PlanStep` (dataclass: `provider: Provider`, `model: Optional[str]`, `slot: CredentialSlot`, `is_active: bool`; propiedad `label -> str` = `f"{provider.id}#{slot.slot}"`)
  - `RouteDecision.plan: list[PlanStep]` (campo nuevo, default lista vacía)
  - `smart_router.build_plan(primary: Optional[tuple[Provider, Optional[str], bool]], fallbacks: list[tuple[Provider, Optional[str], bool]], max_steps: int) -> list[PlanStep]`
  - `smart_router.mark_step_health(step: PlanStep, failure: AttemptFailure) -> None`
  - `smart_router.note_success(decision: RouteDecision, step: PlanStep, body: dict) -> None`

**Reglas de `build_plan`:**
1. Recorre `[primary, *fallbacks]` en orden, sin repetir provider.
2. Por cada provider agrega un paso por cada slot de `available_slots(provider)`.
3. Un fallback (no el primario) cuya clave de provider (`provider:<id>`) no esté disponible se salta entero.
4. Corta al llegar a `max_steps` pasos.
5. Si el plan queda vacío y hay primario: un único paso con el slot 0 del primario (`credential_slots(primary)[0]`), para que el cliente vea el error real en vez de nada.

**Fallbacks en `decide`:**
- (a) Si smart está habilitado en modo `active`: los candidatos rankeados que sobrevivieron el filtro, en orden, salvo el elegido. Cada uno como `(c.provider, c.model or None, c.provider.id == registry.active_provider_id)`.
- (b) Después, siempre, los providers de `registry.fallback_provider_ids` que existan, como `(p, None, p.id == registry.active_provider_id)`. Los que tengan base local y no respondan (`_is_local_base` y `not await _is_reachable`) se excluyen.

El plan se calcula en los dos caminos de `decide`: smart deshabilitado (antes del `return` temprano) y el normal. Para eso, `_smart_pick` pasa a devolver también la lista `ranked` completa.

- [ ] **Step 1: Escribir los tests**

Crear `backend/tests/test_route_plan.py`:

```python
"""Plan de ruta: pasos (provider, slot) ordenados, sin duplicados, con tope y fallbacks."""
from unittest.mock import AsyncMock

import pytest

from app.core.quota_signals import QuotaSignal
from app.models.provider import Provider, ProviderRegistry
from app.models.smart import RouteTarget, SmartRoutingConfig, TierPolicy
from app.services import health_service, providers_service, smart_router
from app.services.upstream import AttemptFailure


def _p(pid, auth="", extras=None, api_base=None):
    return Provider(id=pid, name=pid, api_base=api_base or f"https://{pid}.example.com/v1",
                    active_model=f"{pid}-model", auth_env_var=auth, extra_auth_env_vars=extras or [])


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app.core import config as cfg

    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(cfg, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(health_service, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    smart_router.clear_sticky()
    monkeypatch.setattr(providers_service, "_is_reachable", AsyncMock(return_value=True))
    monkeypatch.setattr(smart_router, "_spawn", lambda coro: coro.close())
    yield monkeypatch
    health_service.reload_for_tests()
    smart_router.clear_sticky()


def _labels(plan):
    return [s.label for s in plan]


def test_build_plan_expands_pool_then_fallbacks(env):
    env.setenv("A_KEY", "a1")
    env.setenv("A_KEY_2", "a2")
    a, b = _p("a", "A_KEY", ["A_KEY_2"]), _p("b")
    plan = smart_router.build_plan((a, None, True), [(b, None, False)], max_steps=4)
    assert _labels(plan) == ["a#0", "a#1", "b#0"]
    assert plan[0].is_active and not plan[2].is_active


def test_build_plan_dedups_providers_and_caps_steps(env):
    a, b, c = _p("a"), _p("b"), _p("c")
    plan = smart_router.build_plan((a, None, True), [(a, None, True), (b, None, False), (c, None, False)], max_steps=2)
    assert _labels(plan) == ["a#0", "b#0"]


def test_build_plan_skips_cooling_fallback_but_keeps_primary_slot_when_empty(env):
    a, b = _p("a"), _p("b")
    health_service.mark_signal("provider:a", QuotaSignal(kind="rate_limit", retry_after_s=300, excerpt="429"))
    health_service.mark_signal("provider:b", QuotaSignal(kind="rate_limit", retry_after_s=300, excerpt="429"))
    plan = smart_router.build_plan((a, None, True), [(b, None, False)], max_steps=4)
    assert _labels(plan) == ["a#0"]


def test_build_plan_skips_exhausted_slot_of_primary(env):
    env.setenv("A_KEY", "a1")
    env.setenv("A_KEY_2", "a2")
    a = _p("a", "A_KEY", ["A_KEY_2"])
    health_service.mark_signal("provider:a#A_KEY", QuotaSignal(kind="quota_exhausted", retry_after_s=600, excerpt="quota"))
    plan = smart_router.build_plan((a, None, True), [], max_steps=4)
    assert _labels(plan) == ["a#1"]


@pytest.mark.asyncio
async def test_decide_with_smart_disabled_uses_registry_fallbacks(env):
    a, b = _p("a"), _p("b")
    registry = ProviderRegistry(active_provider_id="a", providers=[a, b], fallback_provider_ids=["b"])
    env.setattr(providers_service, "load_registry", lambda: registry)
    d = await smart_router.decide({"model": "x", "messages": [{"role": "user", "content": "hola"}]}, "x", 10, {})
    assert _labels(d.plan) == ["a#0", "b#0"]


@pytest.mark.asyncio
async def test_decide_active_smart_appends_other_ranked_candidates(env):
    a, b, c = _p("a"), _p("b"), _p("c")
    smart = SmartRoutingConfig(enabled=True, mode="active", tiers=[
        TierPolicy(tier=t, targets=[RouteTarget(provider_id="b"), RouteTarget(provider_id="c")])
        for t in ("trivial", "simple", "standard", "complex")
    ])
    registry = ProviderRegistry(active_provider_id="a", providers=[a, b, c], smart=smart, fallback_provider_ids=["a"])
    env.setattr(providers_service, "load_registry", lambda: registry)
    d = await smart_router.decide({"model": "x", "messages": [{"role": "user", "content": "hola"}]}, "x", 10, {})
    assert _labels(d.plan) == ["b#0", "c#0", "a#0"]


@pytest.mark.asyncio
async def test_decide_drops_unreachable_local_fallback(env):
    a, local = _p("a"), _p("local", api_base="http://127.0.0.1:4002/v1")
    registry = ProviderRegistry(active_provider_id="a", providers=[a, local], fallback_provider_ids=["local"])
    env.setattr(providers_service, "load_registry", lambda: registry)
    env.setattr(providers_service, "_is_reachable", AsyncMock(return_value=False))
    d = await smart_router.decide({"model": "x", "messages": [{"role": "user", "content": "hola"}]}, "x", 10, {})
    assert _labels(d.plan) == ["a#0"]


def test_mark_step_health_credential_marks_slot_provider_marks_provider(env):
    env.setenv("A_KEY", "a1")
    env.setenv("A_KEY_2", "a2")
    a = _p("a", "A_KEY", ["A_KEY_2"])
    step0, step1 = smart_router.build_plan((a, None, True), [], max_steps=4)
    smart_router.mark_step_health(step0, AttemptFailure(step0, "retry_credential", 429, "rate limit"))
    assert health_service.get("provider:a#A_KEY").state == "cooling"
    assert health_service.get("provider:a").state == "available"
    smart_router.mark_step_health(step1, AttemptFailure(step1, "retry_provider", 529, "Overloaded"))
    assert health_service.get("provider:a").state == "cooling"


def test_mark_step_health_ignores_fatal(env):
    a = _p("a")
    (step,) = smart_router.build_plan((a, None, True), [], max_steps=4)
    smart_router.mark_step_health(step, AttemptFailure(step, "fatal", 400, "bad request"))
    assert health_service.get("provider:a").total_failed == 0
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `cd backend && python -m pytest tests/test_route_plan.py -q`
Expected: FAIL (`AttributeError: ... build_plan` y `extra_auth_env_vars`, salvo que A1 ya esté hecho).

- [ ] **Step 3: Agregar el tope de pasos al modelo**

En `backend/app/models/smart.py`, dentro de `class SmartRoutingConfig`, debajo de `respect_capabilities: bool = True`:

```python
    max_failover_attempts: int = Field(default=4, ge=1, le=10)
```

- [ ] **Step 4: Implementar en `smart_router.py`**

1. Imports nuevos al principio del archivo:

```python
from app.core.quota_signals import QuotaSignal, detect_signal
from app.services.credentials import CredentialSlot, available_slots, credential_slots, provider_key
from app.services.upstream import AttemptFailure
```

(reemplazar el import existente `from app.core.quota_signals import detect_signal`).

2. Después de la dataclass `Candidate`, agregar:

```python
@dataclass
class PlanStep:
    provider: Provider
    model: Optional[str]
    slot: CredentialSlot
    is_active: bool

    @property
    def label(self) -> str:
        return f"{self.provider.id}#{self.slot.slot}"
```

3. En `RouteDecision`, agregar el campo (después de `decision_ms`):

```python
    plan: list["PlanStep"] = field(default_factory=list)
```

4. Funciones nuevas (en una sección `# ── plan de ruta ──` antes de `# ── decisión ──`):

```python
PlanEntry = tuple[Provider, Optional[str], bool]


def _steps_for(entry: PlanEntry, is_primary: bool) -> list[PlanStep]:
    provider, model, is_active = entry
    if not is_primary and not health_service.is_available(provider_key(provider.id)):
        return []
    return [PlanStep(provider, model, slot, is_active) for slot in available_slots(provider)]


def build_plan(primary: Optional[PlanEntry], fallbacks: list[PlanEntry], max_steps: int) -> list[PlanStep]:
    plan: list[PlanStep] = []
    seen: set[str] = set()
    entries = ([(primary, True)] if primary else []) + [(f, False) for f in fallbacks]
    for entry, is_primary in entries:
        if entry[0].id in seen:
            continue
        seen.add(entry[0].id)
        plan.extend(_steps_for(entry, is_primary))
        if len(plan) >= max_steps:
            return plan[:max_steps]
    if not plan and primary:
        provider, model, is_active = primary
        plan.append(PlanStep(provider, model, credential_slots(provider)[0], is_active))
    return plan


async def _registry_fallbacks(registry: ProviderRegistry) -> list[PlanEntry]:
    by_id = {p.id: p for p in registry.providers}
    entries: list[PlanEntry] = []
    for pid in registry.fallback_provider_ids:
        provider = by_id.get(pid)
        if provider is None:
            continue
        if providers_service._is_local_base(provider.api_base) and not await providers_service._is_reachable(provider.api_base):
            continue
        entries.append((provider, None, provider.id == registry.active_provider_id))
    return entries


def _ranked_fallbacks(ranked: list[Candidate], chosen: Optional[Provider], registry: ProviderRegistry) -> list[PlanEntry]:
    return [
        (c.provider, c.model or None, c.provider.id == registry.active_provider_id)
        for c in ranked
        if chosen is None or c.provider.id != chosen.id
    ]


async def _attach_plan(decision: "RouteDecision", registry: ProviderRegistry, ranked: list[Candidate]) -> None:
    primary = (decision.chosen_provider, decision.chosen_model, decision.is_active) if decision.chosen_provider else None
    use_ranked = registry.smart.enabled and registry.smart.mode == "active"
    fallbacks = (_ranked_fallbacks(ranked, decision.chosen_provider, registry) if use_ranked else [])
    fallbacks += await _registry_fallbacks(registry)
    decision.plan = build_plan(primary, fallbacks, registry.smart.max_failover_attempts)
```

5. `_smart_pick` pasa a devolver 4 valores `(pick, rejected, source, ranked)`:
   - Rama sticky: `return sticky, [], "sticky", []`.
   - Sin candidatos: `return None, rejected, "failover", []`.
   - Normal: `return ranked[0], rejected, "smart", ranked`.

6. En `decide`:
   - Rama `if not smart.enabled:` llama `await _attach_plan(decision, registry, [])` antes del `return`.
   - Desempaquetar `pick, rejected, pick_source, ranked = await _smart_pick(...)`.
   - Justo antes del `decision.decision_ms = ...` final, llamar `await _attach_plan(decision, registry, ranked)`.

7. Salud por paso y sticky:

```python
_STATUS_SIGNALS = {401: "auth", 403: "auth", 402: "quota_exhausted", 429: "rate_limit"}


def _credential_signal(failure: AttemptFailure) -> QuotaSignal:
    detected = detect_signal(failure.message or "", failure.status)
    if detected:
        return detected
    kind = _STATUS_SIGNALS.get(failure.status or 0, "rate_limit")
    return QuotaSignal(kind=kind, retry_after_s=None, excerpt=(failure.message or "")[:200])


def mark_step_health(step: PlanStep, failure: AttemptFailure) -> None:
    if failure.kind == "retry_credential":
        health_service.mark_signal(step.slot.health_key, _credential_signal(failure))
    elif failure.kind == "retry_provider":
        signal = detect_signal(failure.message or "", failure.status)
        if signal and signal.kind == "overloaded":
            health_service.mark_signal(provider_key(step.provider.id), signal)
        else:
            health_service.mark_failure(provider_key(step.provider.id), failure.message or f"status {failure.status}")


def note_success(decision: RouteDecision, step: PlanStep, body: dict) -> None:
    if decision.mode != "active" or decision.chosen_provider is None:
        return
    if step.provider.id == decision.chosen_provider.id:
        return
    registry = providers_service.load_registry()
    if registry.smart.sticky_tool_loops:
        _sticky_put(conversation_key(body), step.provider.id, step.model or "")
```

- [ ] **Step 5: Correr y verificar que pasan**

Run: `cd backend && python -m pytest tests/test_route_plan.py tests/test_smart_router.py -q`
Expected: todos pasan. `test_smart_router.py` no debe cambiar: el plan es aditivo.

---

### Task A4: `/v1/messages` con failover antes del primer byte

**Files:**
- Modify: `backend/app/api/messages.py` (el endpoint `messages_passthrough`, líneas ~318-571, se reescribe; las funciones de conversión de arriba quedan iguales)
- Modify (si hace falta): `backend/tests/test_messages_passthrough.py`, `backend/tests/test_messages_native.py`, `backend/tests/test_failover.py`
- Test: `backend/tests/test_messages_failover.py`

**Interfaces:**
- Consumes: `smart_router.decide(...)` con `decision.plan: list[PlanStep]`, `PlanStep.provider/.model/.slot/.is_active/.label`, `smart_router.mark_step_health`, `smart_router.note_success`, `smart_router.report_outcome_sync(decision, target_key, ok, latency_ms=, status=, error=)`; `credentials.api_key_for(slot)`; `upstream.attempt_plan/Opened/Failed/exhausted_status/failures_summary/anthropic_error_body`.
- Produces: el endpoint devuelve las cabeceras `X-Bipolar-Target: <label>` y `X-Bipolar-Attempts: <n>`. Si nada respondió, devuelve `JSONResponse(status, anthropic_error_body(...))`.

**Diseño del endpoint** (reemplaza el cuerpo desde `usage_buf: dict = ...` hasta el `return StreamingResponse(...)`; lo de arriba —decide, compresión, `response_headers`, `_outcome`— queda casi igual):

1. `plan = decision.plan`. Si viene vacío y `active` existe, el plan es un único `PlanStep(active, routed_model, credential_slots(active)[0], is_active_provider)`. Si no hay provider, devolver `JSONResponse(502, anthropic_error_body(502, "Sin provider configurado"))`.
2. `stack = AsyncExitStack()`; `client = await stack.enter_async_context(httpx.AsyncClient(timeout=timeout))`.
3. `open_attempt(step)` decide el tipo de paso y abre el upstream:
   - `native = step.provider.anthropic_native or (step.provider.litellm_prefix == "anthropic" and not step.is_active)`
   - `via_litellm = step.provider.litellm_prefix == "anthropic" and not native`
   - Si no, OAI.
   - Cada intento usa su propio `AsyncExitStack` (`attempt_stack`); el response se abre con `await attempt_stack.enter_async_context(client.stream("POST", url, json=..., headers=...))`.
   - Si `resp.status_code >= 400`: leer `await resp.aread()`, extraer el mensaje con la misma lógica de hoy (`json.loads(raw).get("error", {}).get("message") or raw.decode()`), `await attempt_stack.aclose()` y devolver `Failed(status, mensaje)`.
   - Excepción `httpx.HTTPError`: cerrar `attempt_stack` y devolver `Failed(None, str(e), e)`.
   - Éxito: `Opened(OpenUpstream(kind, resp, attempt_stack, step, model_for_usage))`.
   - **Body por paso, sin mutar el original:**
     - native: `{**body, "model": step.model or step.provider.active_model or model}`, URL `f"{step.provider.api_base.rstrip('/').removesuffix('/v1')}/v1/messages"`, header `x-api-key` = `api_key_for(step.slot)` si no está vacío.
     - litellm: URL `f"{settings.proxy_url}/v1/messages"`, `Authorization: Bearer {settings.proxy_api_key}`.
     - Para ambos se reenvían `anthropic-version` y `anthropic-beta` del request.
     - OAI: el bloque actual que arma `oai_body` (capacidades, `max_tokens` capado, URL `providers_service.oai_chat_completions_url(step.provider)`, `Authorization: Bearer {api_key_for(step.slot) or 'no-key'}`, `extra_headers`). Con `provider_model = step.model or step.provider.active_model or model`.
   - **Límite de contexto (solo OAI):** si el primer response es 400 con el patrón de contexto o de salida (las mismas regex de hoy), cerrar ese intento, calcular `retry_body` igual que hoy (incluido `_save_model_info`) y abrir un segundo stream en el mismo paso. Si el segundo también falla, devolver `Failed` con su status y mensaje.
4. `result = await upstream.attempt_plan(plan, open_attempt, lambda s: s.provider.id, smart_router.mark_step_health)`.
5. Si `not result.ok`:
   - `await stack.aclose()`.
   - `status = upstream.exhausted_status(result.failures)`.
   - `message = sanitize_error("Ningún destino respondió: " + upstream.failures_summary(result.failures, lambda s: s.label))`. Si la única falla es `fatal`, el mensaje es el `message` sanitizado de esa falla, sin el prefijo.
   - `report_outcome_sync(decision, "", False, status=status, error=message)`.
   - Devolver `JSONResponse(status_code=status, content=upstream.anthropic_error_body(status, message), headers={**response_headers, "X-Bipolar-Attempts": str(len(result.failures))})`.
6. Si ok:
   - Cabeceras `X-Bipolar-Target = result.step.label` y `X-Bipolar-Attempts = str(len(result.failures) + 1)`.
   - `smart_router.note_success(decision, result.step, body)`.
   - Devolver `StreamingResponse(_relay(opened, stack, ...), media_type="text/event-stream", headers=...)`.
7. `_relay` (generador async) hace por cada tipo de paso:
   - native/litellm: reenvía cada línea (`yield f"{line}\n"`), parsea el uso de `message_start` y `message_delta` y, en `message_stop`, registra uso y outcome, como hoy.
   - OAI: delega en `_oai_stream_to_anthropic(resp.aiter_lines(), message_id, model, usage_buf)` y al final registra uso y outcome.

   En ambos casos, `report_outcome_sync(decision, opened.step.slot.health_key, True, latency_ms=...)` al terminar bien y `(..., False, error=str(e))` si hay excepción. Con excepción a mitad de stream, emitir `_sse_error(sanitize_error(str(e)))`. **Siempre**, en `finally`: `await opened.attempt_stack.aclose()` y `await stack.aclose()`.
8. Extraer en funciones chicas (cada una < 50 líneas): `_native_request(step, body, request, settings, model) -> tuple[url, headers, json]`, `_oai_request(step, body, model) -> tuple[url, headers, json]`, `_error_message(raw: bytes) -> str`, `_context_retry_body(err_msg, oai_body, body, provider) -> dict | None`, `_open_stream(client, url, headers, payload) -> Opened | Failed`, `_relay_native(...)`, `_relay_oai(...)`.

**Comportamiento que se conserva:**
- Compresión y truncado.
- `X-Context-Usage`, `X-Bipolar-Route` y `X-Bipolar-Decision-Id`.
- La conversión OAI→Anthropic y el bloque vacío mínimo.
- El registro de uso (`_record_usage(step.provider.id, model, usage_buf, truncated)`).
- El mensaje de "Servidor local no responde en … Inícialo desde Providers → llama.cpp (Start)": ahora va como texto de la falla cuando el paso native con base local falla por conexión.

- [ ] **Step 1: Escribir los tests nuevos**

Crear `backend/tests/test_messages_failover.py`:

```python
"""/v1/messages: failover entre llaves y providers antes del primer byte."""
import json

import pytest
from fastapi.testclient import TestClient

from app.models.provider import Provider, ProviderRegistry
from app.services import health_service, providers_service, smart_router

OK_LINES = [
    'data: {"choices":[{"delta":{"content":"hola"},"finish_reason":null}]}',
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1}}',
    "data: [DONE]",
]


class FakeResp:
    def __init__(self, status=200, lines=None, body=b""):
        self.status_code = status
        self._lines = lines or []
        self._body = body
        self.closed = False

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        return self.routes[url].pop(0)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(health_service, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    smart_router.clear_sticky()
    monkeypatch.setattr(smart_router, "_spawn", lambda coro: coro.close())
    monkeypatch.setattr("app.api.messages._record_usage", lambda *a, **k: None)
    monkeypatch.setenv("P1_KEY", "key-one")
    monkeypatch.setenv("P1_KEY_2", "key-two")
    monkeypatch.setenv("P2_KEY", "key-p2")
    p1 = Provider(id="p1", name="p1", api_base="https://p1.example.com/v1", active_model="m1",
                  auth_env_var="P1_KEY", extra_auth_env_vars=["P1_KEY_2"])
    p2 = Provider(id="p2", name="p2", api_base="https://p2.example.com/v1", active_model="m2", auth_env_var="P2_KEY")
    registry = ProviderRegistry(active_provider_id="p1", providers=[p1, p2], fallback_provider_ids=["p2"])
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)

    from app.core.config import get_settings
    from app.main import app
    client = TestClient(app, headers={"x-api-key": get_settings().ui_api_key})

    def install(routes):
        fake = FakeClient(routes)
        monkeypatch.setattr("app.api.messages.httpx.AsyncClient", lambda *a, **k: fake)
        return fake

    yield client, install
    health_service.reload_for_tests()
    smart_router.clear_sticky()


BODY = {"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hola"}], "max_tokens": 50}
P1 = "https://p1.example.com/v1/chat/completions"
P2 = "https://p2.example.com/v1/chat/completions"


def test_rate_limited_key_falls_to_second_key_of_same_provider(harness):
    client, install = harness
    fake = install({P1: [FakeResp(429, body=b'{"error":{"message":"rate limit"}}'), FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 200
    assert resp.headers["x-bipolar-target"] == "p1#1"
    assert resp.headers["x-bipolar-attempts"] == "2"
    assert "hola" in resp.text
    assert [c["headers"]["Authorization"] for c in fake.calls] == ["Bearer key-one", "Bearer key-two"]
    assert health_service.get("provider:p1#P1_KEY").state == "cooling"


def test_provider_down_falls_to_next_provider(harness):
    client, install = harness
    install({P1: [FakeResp(503, body=b"service unavailable")], P2: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 200
    assert resp.headers["x-bipolar-target"] == "p2#0"


def test_all_rate_limited_returns_http_429_with_anthropic_error(harness):
    client, install = harness
    install({
        P1: [FakeResp(429, body=b"rate limit"), FakeResp(429, body=b"rate limit")],
        P2: [FakeResp(429, body=b"rate limit")],
    })
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 429
    payload = resp.json()
    assert payload["type"] == "error" and payload["error"]["type"] == "rate_limit_error"
    assert "p1#0" in payload["error"]["message"] and "p2#0" in payload["error"]["message"]
    assert "key-one" not in resp.text


def test_fatal_400_is_not_retried_elsewhere(harness):
    client, install = harness
    fake = install({P1: [FakeResp(400, body=b'{"error":{"message":"messages: field required"}}')], P2: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "invalid_request_error"
    assert len(fake.calls) == 1


def test_original_body_is_not_mutated_between_attempts(harness):
    client, install = harness
    fake = install({P1: [FakeResp(429, body=b"rate limit"), FakeResp(429, body=b"rate limit")], P2: [FakeResp(200, OK_LINES)]})
    client.post("/v1/messages", json=BODY)
    assert [c["json"]["model"] for c in fake.calls] == ["m1", "m1", "m2"]


def test_upstream_stream_is_closed_after_relay(harness):
    client, install = harness
    winner = FakeResp(200, OK_LINES)
    install({P1: [winner]})
    resp = client.post("/v1/messages", json=BODY)
    assert resp.status_code == 200
    assert winner.closed
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `cd backend && python -m pytest tests/test_messages_failover.py -q`
Expected: FAIL (hoy el 429 sale como evento SSE con status 200 y no hay `x-bipolar-target`).

- [ ] **Step 3: Reescribir el endpoint según el diseño de arriba**

Mantener intactas `_content_block_to_oai`, `_anthropic_to_oai_messages`, `_anthropic_to_oai_request`, `_sse`, `_sse_error`, `_oai_stream_to_anthropic`, `_save_model_info`, `_record_usage` y `list_models_anthropic`. Agregar los imports `from contextlib import AsyncExitStack`, `from dataclasses import dataclass`, `from fastapi.responses import JSONResponse`, `from app.services import credentials, upstream` y `from app.services.smart_router import PlanStep`.

- [ ] **Step 4: Correr los tests nuevos y los existentes de messages**

Run: `cd backend && python -m pytest tests/test_messages_failover.py tests/test_messages_passthrough.py tests/test_messages_native.py tests/test_failover.py -q`
Expected: todos pasan.

Si un test existente afirmaba el comportamiento viejo (un error de upstream como evento SSE con status 200), actualizarlo para que afirme el nuevo (status HTTP real con `anthropic_error_body`) y cambiar su nombre para que lo diga. **No relajar** aserciones de otras cosas.

- [ ] **Step 5: Suite completa**

Run: `cd backend && python -m pytest -q`
Expected: todo en verde.

---

### Task A5: `/v1/chat/completions` con failover

**Files:**
- Modify: `backend/app/api/openai_compat.py`
- Modify (si hace falta): `backend/tests/test_openai_compat.py`
- Test: `backend/tests/test_openai_compat_failover.py`

**Interfaces:**
- Consumes: lo mismo que A4, más `upstream.openai_error_body`.
- Produces: cabeceras `X-Bipolar-Target` y `X-Bipolar-Attempts`; errores totales con `JSONResponse(status, openai_error_body(...))`.

**Diseño:**
- `plan = decision.plan`, filtrando los pasos con `step.provider.litellm_prefix == "anthropic" and not step.is_active`: esa superficie no puede traducir OAI→Anthropic fuera de litellm, igual que hoy. Si queda vacío, un único paso con el provider activo (`providers_service.get_active_provider()`).
- `resolve_target(active, settings)` pasa a `resolve_target(step, settings) -> tuple[url, headers, model]`:
  - Anthropic activo vía litellm: igual que hoy.
  - Si no: URL de `oai_chat_completions_url(step.provider)` y `Authorization: Bearer {api_key_for(step.slot) or 'no-key'}`, más `extra_headers`.
  - El modelo es `step.model or step.provider.active_model`.
- Con stream: mismo patrón que A4 (`AsyncExitStack`, `client.stream`, `attempt_plan`, relay que reenvía líneas verbatim y captura `usage`).
- Sin stream: `open_attempt` hace `await client.post(...)`. Status < 400: `Opened(resp)`. Si no: `Failed(status, texto)`. El ganador se devuelve como `JSONResponse(status_code=resp.status_code, content=_safe_json(resp), headers=...)`.
- Body por paso sin mutar el original: `{**body, "model": model}` si hay modelo.
- Todos fallan: `JSONResponse(status, upstream.openai_error_body(status, message))`, con el mismo mensaje que A4.

- [ ] **Step 1: Escribir los tests**

Crear `backend/tests/test_openai_compat_failover.py` con el mismo harness de `test_messages_failover.py`: copiar `FakeResp`, `FakeClient` y el fixture `harness`, cambiando el monkeypatch a `"app.api.openai_compat.httpx.AsyncClient"`. Agregar a `FakeClient`:

```python
    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        return self.routes[url].pop(0)
```

Y a `FakeResp`:

```python
    def json(self):
        import json as _json
        return _json.loads(self._body or b"{}")

    @property
    def text(self):
        return (self._body or b"").decode()
```

Tests:

```python
BODY = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hola"}]}
P1 = "https://p1.example.com/v1/chat/completions"
P2 = "https://p2.example.com/v1/chat/completions"
OK_JSON = b'{"choices":[{"message":{"role":"assistant","content":"hola"}}],"usage":{"prompt_tokens":3,"completion_tokens":1}}'


def test_non_stream_falls_to_second_key(harness):
    client, install = harness
    fake = install({P1: [FakeResp(429, body=b"rate limit"), FakeResp(200, body=OK_JSON)]})
    resp = client.post("/v1/chat/completions", json=BODY)
    assert resp.status_code == 200 and resp.json()["choices"][0]["message"]["content"] == "hola"
    assert resp.headers["x-bipolar-target"] == "p1#1"
    assert [c["headers"]["Authorization"] for c in fake.calls] == ["Bearer key-one", "Bearer key-two"]


def test_stream_falls_to_next_provider(harness):
    client, install = harness
    install({P1: [FakeResp(503, body=b"unavailable")], P2: [FakeResp(200, OK_LINES)]})
    resp = client.post("/v1/chat/completions", json={**BODY, "stream": True})
    assert resp.status_code == 200
    assert resp.headers["x-bipolar-target"] == "p2#0"
    assert "hola" in resp.text


def test_all_failed_returns_openai_error_with_real_status(harness):
    client, install = harness
    install({P1: [FakeResp(429, body=b"rate limit"), FakeResp(429, body=b"rate limit")], P2: [FakeResp(529, body=b"Overloaded")]})
    resp = client.post("/v1/chat/completions", json=BODY)
    assert resp.status_code == 429
    assert resp.json()["error"]["type"] == "rate_limit_error"


def test_fatal_is_returned_without_retry(harness):
    client, install = harness
    fake = install({P1: [FakeResp(400, body=b'{"error":{"message":"bad"}}')], P2: [FakeResp(200, body=OK_JSON)]})
    resp = client.post("/v1/chat/completions", json=BODY)
    assert resp.status_code == 400
    assert len(fake.calls) == 1
```

(`OK_LINES` se copia de `test_messages_failover.py`.)

- [ ] **Step 2: Correr y verificar que fallan**

Run: `cd backend && python -m pytest tests/test_openai_compat_failover.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementar el diseño**

- [ ] **Step 4: Correr los tests nuevos y `tests/test_openai_compat.py`**

Run: `cd backend && python -m pytest tests/test_openai_compat_failover.py tests/test_openai_compat.py -q`
Expected: todos pasan. Si un test viejo afirmaba que un error se emite como chunk SSE con status 200, actualizarlo al comportamiento nuevo y cambiar su nombre.

- [ ] **Step 5: Suite completa**

Run: `cd backend && python -m pytest -q`
Expected: todo en verde.

---

### Task A6: API de llaves del pool

**Files:**
- Modify: `backend/app/api/providers.py`
- Test: `backend/tests/test_provider_credentials_api.py`

**Interfaces:**
- Consumes: `credentials.credential_slots(provider)`, `health_service.get(key)`, `health_service.seconds_left(key)`, `settings_service.is_valid_env_key(key) -> bool`, `env_value(name)`.
- Produces:
  - `UpdateProviderRequest.extra_auth_env_vars: Optional[list[str]] = None` y `AddProviderRequest.extra_auth_env_vars: list[str] = []`.
  - Validación: cada nombre debe pasar `is_valid_env_key`; máximo 10; sin repetir `auth_env_var` ni nombres duplicados. Si algo falla, 400.
  - `GET /api/providers/{provider_id}/credentials` → `{"provider_id", "slots": [{"slot", "env_var", "has_value", "health_key", "state", "seconds_left", "last_signal"}]}`. **Nunca incluye valores.** Declarar la ruta antes de cualquier ruta catch-all.

- [ ] **Step 1: Escribir los tests**

```python
"""API de llaves del pool: validación de nombres y estado por slot sin exponer valores."""
import pytest
from fastapi.testclient import TestClient

from app.models.provider import Provider, ProviderRegistry
from app.services import health_service, providers_service


@pytest.fixture
def api(tmp_path, monkeypatch):
    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(health_service, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    monkeypatch.setenv("P1_KEY", "secret-one")
    monkeypatch.setenv("P1_KEY_2", "secret-two")
    registry = ProviderRegistry(active_provider_id="p1", providers=[
        Provider(id="p1", name="p1", api_base="https://p1.example.com/v1", auth_env_var="P1_KEY", extra_auth_env_vars=["P1_KEY_2"]),
    ])
    monkeypatch.setattr(providers_service, "load_registry", lambda: registry)
    saved = {}

    def fake_update(pid, updates):
        saved.update(updates)
        return registry.providers[0].model_copy(update=updates)

    monkeypatch.setattr(providers_service, "update_provider", fake_update)
    from app.core.config import get_settings
    from app.main import app
    yield TestClient(app, headers={"x-api-key": get_settings().ui_api_key}), saved
    health_service.reload_for_tests()


def test_credentials_endpoint_lists_slots_without_values(api):
    client, _ = api
    resp = client.get("/api/providers/p1/credentials")
    assert resp.status_code == 200
    slots = resp.json()["slots"]
    assert [(s["slot"], s["env_var"], s["has_value"], s["state"]) for s in slots] == [
        (0, "P1_KEY", True, "available"), (1, "P1_KEY_2", True, "available"),
    ]
    assert "secret" not in resp.text


def test_patch_accepts_valid_extra_env_vars(api):
    client, saved = api
    resp = client.patch("/api/providers/p1", json={"extra_auth_env_vars": ["P1_KEY_2", "P1_KEY_3"]})
    assert resp.status_code == 200
    assert saved["extra_auth_env_vars"] == ["P1_KEY_2", "P1_KEY_3"]


@pytest.mark.parametrize("names", [["bad name"], ["P1_KEY"], ["X", "X"], [f"K_{i}" for i in range(11)]])
def test_patch_rejects_invalid_extra_env_vars(api, names):
    client, _ = api
    resp = client.patch("/api/providers/p1", json={"extra_auth_env_vars": names})
    assert resp.status_code == 400


def test_credentials_unknown_provider_404(api):
    client, _ = api
    assert client.get("/api/providers/nope/credentials").status_code == 404
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `cd backend && python -m pytest tests/test_provider_credentials_api.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementar**

En `api/providers.py`:
- Campo en los dos modelos.
- Función `_validate_extra_env_vars(names: list[str], auth_env_var: str) -> None` que lanza `HTTPException(400, ...)`.
- Llamarla en `add_provider` y en `update_provider`. En `update_provider`, la `auth_env_var` a comparar es la del body si viene, y si no, la del provider actual (`providers_service.get_provider(provider_id)`; si no existe, 404).
- Ruta nueva:

```python
@router.get("/{provider_id}/credentials")
def provider_credentials(provider_id: str):
    provider = providers_service.get_provider(provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' no encontrado")
    return {"provider_id": provider_id, "slots": [_slot_status(s) for s in credentials.credential_slots(provider)]}
```

`credential_slots` omite las extras sin valor. Para que la UI muestre también las que faltan, `_slot_status` va acompañado de una lista separada `missing: [nombres de extra_auth_env_vars sin valor]` en la misma respuesta. Agregar al test del Step 1:

```python
def test_credentials_endpoint_lists_missing_extras(api, monkeypatch):
    client, _ = api
    monkeypatch.delenv("P1_KEY_2")
    resp = client.get("/api/providers/p1/credentials")
    assert resp.json()["missing"] == ["P1_KEY_2"]
```

`_slot_status(slot)` devuelve `{"slot", "env_var", "has_value": bool(env_value(slot.env_var)) if slot.env_var else False, "health_key", "state", "seconds_left", "last_signal"}`, con el estado leído de `health_service.get(slot.health_key)`.

- [ ] **Step 4: Correr y verificar que pasan; luego la suite completa**

Run: `cd backend && python -m pytest tests/test_provider_credentials_api.py -q && python -m pytest -q`
Expected: todo en verde.
