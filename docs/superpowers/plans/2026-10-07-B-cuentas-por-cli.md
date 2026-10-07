# Plan B — Cuentas por CLI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que un mismo CLI (Claude Code, Codex, dsh, Cursor) tenga varias cuentas, cada una en su carpeta de config. El broker alterna entre ellas cuando una se agota y bipolar sabe el cupo real de las cuentas de Claude por el statusline.

**Architecture:** Una cuenta es una entrada más de `cli_agents` con `adapter` (qué CLI) y `account_dir` (su carpeta). Todo lo que hoy decide "qué CLI es" mira `agent.base` en vez de `agent.id`. El broker agrega al entorno del hijo la variable de cuenta del adaptador (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`…). Un servicio nuevo crea, borra, elige y recibe uso de cuentas, y un script stdlib reporta el `rate_limits` del statusline de Claude Code.

**Tech Stack:** Python 3.11, FastAPI, pydantic v2, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-07-orquestacion-transparente-design.md` (sección 5).

## Global Constraints

- Repo: `C:\personal\bipolar-code\bipolar-orq`, backend en `backend/`, tests con `cd backend && python -m pytest -q`.
- Sin dependencias nuevas. El script de statusline usa solo stdlib.
- Las entradas existentes de `cli_agents` (`id` sin sufijo y `adapter` vacío) cargan y se comportan igual.
- bipolar nunca lee el contenido de archivos de credenciales: solo `Path.exists()`.
- Las carpetas de cuenta viven en `<config_dir>/accounts/<id>`. Borrar una cuenta **no** borra su carpeta.
- Estilo: funciones cortas con nombres explícitos; sin docstrings largos; comentario de una línea solo para un porqué no obvio.
- No ejecutar `git add/commit/push/reset/checkout`: el orquestador commitea.
- Las rutas nuevas viven en `/api/accounts…`. Esto reemplaza el `/api/smart/accounts` de la spec, que se actualiza en la Task B4.

## Review Focus

1. **Registro viejo sin los campos nuevos**: carga sin errores y cada agente conserva su comportamiento. Test en B1.
2. **Id de cuenta inválido o adaptador incoherente** (`claude-2` con `adapter=codex`, `../evil`, mayúsculas): se rechaza en el modelo, porque el id termina en rutas de carpeta. Test en B1.
3. **Variable de cuenta pisada por el entorno del usuario**: si el proceso de bipolar ya tiene `CLAUDE_CONFIG_DIR` puesto, el hijo de una cuenta recibe la carpeta de la cuenta, y el de la entrada base no hereda la del usuario. Test en B2.
4. **Statusline sin `rate_limits`** (plan no Pro/Max, o antes de la primera respuesta): el reporter no falla ni bloquea, y bipolar no marca nada. Tests en B4 y B5.
5. **Mensaje de límite de Claude con hora de reloj ya pasada hoy** ("resets 3pm" a las 4pm): el reset se toma para mañana a las 3pm, no en el pasado. Test en B3.

---

### Task B1: Modelo de cuentas y `agent.base`

**Files:**
- Modify: `backend/app/models/smart.py` (`CliAgent`, `DelegationConfig`)
- Modify: `backend/app/services/cli_agents/registry.py` (`resolve_exe`, `probe`, `_with_runtime`)
- Modify: `backend/app/services/cli_agents/broker.py` (`_pool_key`, `_reject_agent`, `_run_attempt`, `_run_attempts`)
- Test: `backend/tests/test_cli_accounts_model.py`

**Interfaces:**
- Produces:
  - `CliAgent.id: str` validado con el patrón `^(claude|codex|copilot|antigravity|ollama|cursor|deepseek|muse)(-[a-z0-9]{1,24})?$`
  - `CliAgent.adapter: str = ""`, `CliAgent.account_dir: str = ""`, `CliAgent.account_label: str = ""`
  - `CliAgent.base -> str` (propiedad): `adapter` si no está vacío; si no, el prefijo del id antes del primer `-`.
  - `CliAgent.is_account -> bool` (propiedad): `bool(self.account_dir)`.
  - `DelegationConfig.thinkers: list[str] = ["claude", "codex"]` y `DelegationConfig.account_exhausted_pct: int = Field(default=98, ge=50, le=100)`.

**Reglas de validación** (con `model_validator(mode="after")`): si el id tiene sufijo (`claude-2`) y `adapter` está vacío, `adapter` se completa con el prefijo. Si `adapter` no está vacío, debe ser igual al prefijo del id; si no, `ValueError("adapter debe coincidir con el prefijo del id")`. `AgentId` y `AGENT_IDS` se mantienen como constantes; `id` deja de ser `Literal` y pasa a `str` con el patrón.

**Reemplazos de `agent.id` por `agent.base`** donde la pregunta es "¿qué CLI es?":
- `registry.resolve_exe`: `BINARIES[agent.base]`, `known_paths(agent.base)`.
- `registry.probe`: las comparaciones `agent.id == "ollama" / "cursor" / "deepseek" / "muse" / "codex" / "antigravity"` pasan a `agent.base`. **El cache y `AgentStatus.id` siguen usando `agent.id`.**
- `registry._with_runtime`: `agent.base == "antigravity"`.
- `broker._pool_key`: `agent.base == "antigravity"`.
- `broker._reject_agent`: las comparaciones con `"muse"`, `"antigravity"` y `"cursor"`.
- `broker._run_attempt`: `agent.base == "ollama"` y `adapter_for(agent.base)` (las dos llamadas).
- `broker._run_attempts`: `agent.base == "antigravity"` en el reintento de pool alternativo.
- **Quedan con `agent.id`** (es la identidad de la cuenta): semáforos, `adjust_running`, `tried`, `Attempt.agent_id`, `job.agent_id`, eventos e `invalidate`.

- [ ] **Step 1: Escribir los tests**

```python
"""Cuentas como entradas de cli_agents: validación del id, adaptador y base."""
import pytest
from pydantic import ValidationError

from app.models.provider import ProviderRegistry
from app.models.smart import CliAgent, DelegationConfig
from app.services.cli_agents import broker
from app.services.cli_agents.adapters import ClaudeAdapter, adapter_for


def test_base_agent_keeps_id_as_base():
    agent = CliAgent(id="claude")
    assert agent.base == "claude" and agent.adapter == "" and not agent.is_account


def test_account_id_fills_adapter_from_prefix():
    agent = CliAgent(id="claude-2", account_dir="C:/litellm/accounts/claude-2")
    assert agent.adapter == "claude" and agent.base == "claude" and agent.is_account
    assert agent.key == "cli:claude-2"


@pytest.mark.parametrize("bad", ["../evil", "Claude-2", "claude_2", "claude-", "gpt-2", "claude-" + "x" * 25])
def test_invalid_ids_are_rejected(bad):
    with pytest.raises(ValidationError):
        CliAgent(id=bad)


def test_adapter_must_match_prefix():
    with pytest.raises(ValidationError):
        CliAgent(id="claude-2", adapter="codex")


def test_legacy_registry_without_new_fields_loads():
    raw = {"cli_agents": [{"id": "claude", "enabled": True}, {"id": "muse"}], "delegation": {"enabled": True}}
    registry = ProviderRegistry(**raw)
    assert [a.base for a in registry.cli_agents] == ["claude", "muse"]
    assert registry.delegation.thinkers == ["claude", "codex"]
    assert registry.delegation.account_exhausted_pct == 98


def test_adapter_lookup_uses_base():
    assert isinstance(adapter_for(CliAgent(id="claude-2").base), ClaudeAdapter)


def test_pool_key_of_account_is_its_own():
    agent = CliAgent(id="claude-2")
    assert broker._pool_key(agent, "") == "cli:claude-2"
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `cd backend && python -m pytest tests/test_cli_accounts_model.py -q`
Expected: FAIL (`claude-2` no es un valor válido del `Literal`).

- [ ] **Step 3: Implementar el modelo**

En `models/smart.py`:

```python
import re
from pydantic import model_validator

AGENT_ID_PATTERN = r"^(claude|codex|copilot|antigravity|ollama|cursor|deepseek|muse)(-[a-z0-9]{1,24})?$"


class CliAgent(BaseModel):
    id: str = Field(pattern=AGENT_ID_PATTERN)
    adapter: str = ""
    account_dir: str = ""
    account_label: str = ""
    # …el resto de campos sin cambios…

    @model_validator(mode="after")
    def _fill_adapter(self) -> "CliAgent":
        prefix = self.id.split("-", 1)[0]
        if self.adapter and self.adapter != prefix:
            raise ValueError("adapter debe coincidir con el prefijo del id")
        if not self.adapter and "-" in self.id:
            self.adapter = prefix
        return self

    @property
    def base(self) -> str:
        return self.adapter or self.id.split("-", 1)[0]

    @property
    def is_account(self) -> bool:
        return bool(self.account_dir)
```

En `DelegationConfig`, agregar:

```python
    thinkers: list[str] = Field(default_factory=lambda: ["claude", "codex"])
    account_exhausted_pct: int = Field(default=98, ge=50, le=100)
```

- [ ] **Step 4: Aplicar los reemplazos de `agent.id` → `agent.base` listados arriba.**

- [ ] **Step 5: Correr los tests nuevos y los del broker, el registry y la migración**

Run: `cd backend && python -m pytest tests/test_cli_accounts_model.py tests/test_delegation_broker.py tests/test_cli_adapters.py tests/test_registry_migration_smart.py tests/test_smart_and_delegate_api.py tests/test_muse_probe.py tests/test_deepseek_probe.py -q`
Expected: todos pasan. `test_registry_migration_smart.py` sigue verde: los defaults no cambian el orden de tiers.

- [ ] **Step 6: Suite completa**

Run: `cd backend && python -m pytest -q`
Expected: verde.

---

### Task B2: Entorno de cuenta y aislamiento de los jobs de Claude

**Files:**
- Modify: `backend/app/services/cli_agents/adapters.py`
- Modify: `backend/app/services/cli_agents/broker.py` (`_run_attempt`)
- Modify: `backend/app/services/cli_agents/registry.py` (`probe`)
- Test: `backend/tests/test_cli_accounts_env.py`

**Interfaces:**
- Consumes: `CliAgent.base`, `CliAgent.account_dir`.
- Produces:
  - `adapters.ACCOUNT_ENV: dict[str, str] = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME", "deepseek": "DSH_HOME", "cursor": "CURSOR_CONFIG_DIR"}`
  - `adapters.CREDENTIAL_FILES: dict[str, str] = {"claude": ".credentials.json", "codex": "auth.json", "deepseek": ".credentials.yaml"}`
  - `adapters.supports_accounts(base: str) -> bool`
  - `adapters.account_env(agent: CliAgent) -> dict[str, str]`: `{ACCOUNT_ENV[base]: account_dir}` si hay `account_dir` y el adaptador lo admite; si no, `{}`.
  - `adapters.account_has_login(agent: CliAgent) -> Optional[bool]`: `None` si no hay archivo de referencia (cursor) o no es cuenta; si no, `True`/`False` según exista `<account_dir>/<archivo>`.
  - `ClaudeAdapter.build` agrega siempre `"--setting-sources", "project,local", "--strict-mcp-config"`.

**Comportamiento:**
- **Variables de cuenta del proceso padre**: se quitan del `child_env` base, así un hijo nunca hereda la carpeta del proceso de bipolar. Agregar las cuatro a `ENV_BLOCKLIST`: `child_env` borra las de la lista negra **antes** de aplicar `extra`; verificar el orden y ajustarlo si hace falta, de modo que `extra` gane.
- **Broker**: en `_run_attempt`, después de `spec = adapter_for(agent.base).build(...)`, hacer `spec.env.update(account_env(agent))`.
- **Registry**: en `probe`, si `agent.is_account`:
  - `account_has_login(agent)` es `False` → `status.auth = "auth_error"` y `status.error = "sin login en la carpeta de la cuenta"`.
  - Es `True` → `status.auth = "ok"`.
  - Es `None` → `auth` queda como lo deje el sondeo normal.
  - En cualquier caso, saltar los sondeos de auth que corren el CLI (`_probe_codex_auth`, `_probe_deepseek`): usarían la carpeta del usuario, no la de la cuenta. Sí se corre `_probe_version`.

- [ ] **Step 1: Escribir los tests**

```python
"""Entorno de cuenta: variable de carpeta por adaptador, login por archivo y flags de aislamiento."""
from pathlib import Path

from app.models.smart import CliAgent
from app.services.cli_agents import adapters
from app.services.cli_agents.adapters import ClaudeAdapter, account_env, account_has_login, child_env


def test_account_env_per_adapter(tmp_path):
    claude = CliAgent(id="claude-2", account_dir=str(tmp_path))
    codex = CliAgent(id="codex-2", account_dir=str(tmp_path))
    assert account_env(claude) == {"CLAUDE_CONFIG_DIR": str(tmp_path)}
    assert account_env(codex) == {"CODEX_HOME": str(tmp_path)}
    assert account_env(CliAgent(id="claude")) == {}
    assert account_env(CliAgent(id="muse-2", account_dir=str(tmp_path))) == {}


def test_parent_account_vars_are_not_inherited():
    env = child_env({"CLAUDE_CONFIG_DIR": "C:/user/.claude-work", "PATH": "x"})
    assert "CLAUDE_CONFIG_DIR" not in env


def test_extra_account_var_wins_over_blocklist():
    env = child_env({"CLAUDE_CONFIG_DIR": "C:/user/.claude-work", "PATH": "x"}, {"CLAUDE_CONFIG_DIR": "C:/acc"})
    assert env["CLAUDE_CONFIG_DIR"] == "C:/acc"


def test_account_has_login_checks_file_only(tmp_path):
    agent = CliAgent(id="claude-2", account_dir=str(tmp_path))
    assert account_has_login(agent) is False
    (tmp_path / ".credentials.json").write_text("{}", encoding="utf-8")
    assert account_has_login(agent) is True
    assert account_has_login(CliAgent(id="cursor-2", account_dir=str(tmp_path))) is None
    assert account_has_login(CliAgent(id="claude")) is None


def test_claude_jobs_ignore_user_settings_and_mcp(tmp_path):
    spec = ClaudeAdapter().build(CliAgent(id="claude"), "claude.exe", "job1", "tarea", "", tmp_path, "standard", 600)
    argv = spec.argv
    assert argv[argv.index("--setting-sources") + 1] == "project,local"
    assert "--strict-mcp-config" in argv


def test_supports_accounts():
    assert adapters.supports_accounts("claude") and adapters.supports_accounts("codex")
    assert not adapters.supports_accounts("muse") and not adapters.supports_accounts("copilot")
```

Agregar también, en `backend/tests/test_delegation_broker.py`, un test async que construya un registry con una cuenta `claude-2` (con `account_dir` a `tmp_path`) y verifique que el env de lanzamiento contiene `CLAUDE_CONFIG_DIR`. Seguir el patrón existente de ese archivo para fakear `_run_subprocess` y capturar el `spec`: buscar ahí cómo se captura el `LaunchSpec` en tests como `test_*_launch*` o similares y replicarlo.

- [ ] **Step 2: Correr y verificar que fallan**

Run: `cd backend && python -m pytest tests/test_cli_accounts_env.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementar**

En `adapters.py`, junto a las constantes de entorno:

```python
ACCOUNT_ENV = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME", "deepseek": "DSH_HOME", "cursor": "CURSOR_CONFIG_DIR"}
CREDENTIAL_FILES = {"claude": ".credentials.json", "codex": "auth.json", "deepseek": ".credentials.yaml"}


def supports_accounts(base: str) -> bool:
    return base in ACCOUNT_ENV


def account_env(agent: CliAgent) -> dict[str, str]:
    if not agent.account_dir or not supports_accounts(agent.base):
        return {}
    return {ACCOUNT_ENV[agent.base]: agent.account_dir}


def account_has_login(agent: CliAgent) -> Optional[bool]:
    marker = CREDENTIAL_FILES.get(agent.base)
    if not agent.account_dir or marker is None:
        return None
    return (Path(agent.account_dir) / marker).exists()
```

Agregar `*ACCOUNT_ENV.values()` a `ENV_BLOCKLIST` (es tupla: `ENV_BLOCKLIST = (..., "CLAUDE_CONFIG_DIR", "CODEX_HOME", "DSH_HOME", "CURSOR_CONFIG_DIR")`). Revisar `child_env`: hoy aplica `extra` y **después** quita la lista negra. Cambiarlo para que se borre la lista negra solo de lo que vino de `base`:

```python
def child_env(base: Mapping[str, str], extra: Optional[dict] = None) -> dict:
    env = {k: v for k, v in base.items() if k in ENV_ALLOWLIST or k.upper() in ENV_ALLOWLIST}
    env.update(ENV_FIXED)
    for key in list(env):
        upper = key.upper()
        if upper in ENV_BLOCKLIST or upper.endswith(SECRET_SUFFIXES):
            env.pop(key, None)
    env.update(extra or {})
    return env
```

Con esto sigue valiendo que un secreto pasado explícitamente por `extra` se respete y uno heredado no, que es la regla actual.

En `ClaudeAdapter.build`, agregar a `argv`, antes de `"--add-dir"`: `"--setting-sources", "project,local", "--strict-mcp-config",`.

Aplicar lo de broker y registry descrito arriba.

- [ ] **Step 4: Correr los tests nuevos, los de adaptadores y los del broker**

Run: `cd backend && python -m pytest tests/test_cli_accounts_env.py tests/test_cli_adapters.py tests/test_delegation_broker.py tests/test_hermetic_env.py -q`
Expected: verde. Si un test de `test_cli_adapters.py` afirmaba el argv exacto de Claude, actualizarlo agregando los flags nuevos.

- [ ] **Step 5: Suite completa**

Run: `cd backend && python -m pytest -q` → verde.

---

### Task B3: Reset de Claude en mensajes de límite

**Files:**
- Modify: `backend/app/core/quota_signals.py`
- Test: `backend/tests/test_quota_signals_claude.py`

**Interfaces:**
- Produces:
  - `parse_reset_at(text: str, now: datetime) -> Optional[datetime]` (UTC aware).
  - `detect_signal(text, status=None, now=None)`: con señal `quota_exhausted` y sin `retry_after_s`, lo calcula como los segundos hasta `parse_reset_at`.
  - `EXHAUSTED_RE` también detecta `hit your (?:\w+ )?limit` (cubre "hit your session limit" y "hit your weekly limit").

**Formatos que debe entender `parse_reset_at`:**
- `resets 3:45pm`, `resets 3pm`, `resets 15:45` → próxima ocurrencia de esa hora local; si ya pasó hoy, mañana.
- `resets Mon 9am` → próximo lunes a las 9:00 local (si hoy es lunes y ya pasó, el de la semana siguiente).
- `|1760000000` al final del texto (época Unix en segundos) → ese instante.
- Cualquier otra cosa → `None`.

La hora local se obtiene con `now.astimezone()`: los tests fijan `now` y una zona con `monkeypatch.setenv("TZ", ...)` no funciona en Windows. Por eso los tests construyen `now` como datetime aware en hora local (`datetime.now().astimezone()` reemplazado) y comparan en la misma zona.

- [ ] **Step 1: Escribir los tests**

```python
"""Mensajes de límite de Claude Code: detección y hora de reset."""
from datetime import datetime, timedelta, timezone

from app.core.quota_signals import detect_signal, parse_reset_at


def _local(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi).astimezone()


def test_clock_later_today():
    now = _local(2026, 10, 7, 14, 0)  # miércoles 14:00
    assert parse_reset_at("You've hit your session limit · resets 3:45pm", now) == _local(2026, 10, 7, 15, 45).astimezone(timezone.utc)


def test_clock_already_passed_goes_to_tomorrow():
    now = _local(2026, 10, 7, 16, 0)
    assert parse_reset_at("5-hour limit reached ∙ resets 3pm", now) == _local(2026, 10, 8, 15, 0).astimezone(timezone.utc)


def test_24h_clock():
    now = _local(2026, 10, 7, 9, 0)
    assert parse_reset_at("limit reached, resets 15:30", now) == _local(2026, 10, 7, 15, 30).astimezone(timezone.utc)


def test_weekday_clock():
    now = _local(2026, 10, 7, 9, 0)  # miércoles
    assert parse_reset_at("weekly limit reached ∙ resets Mon 9am", now) == _local(2026, 10, 12, 9, 0).astimezone(timezone.utc)


def test_epoch_suffix():
    now = _local(2026, 10, 7, 9, 0)
    assert parse_reset_at("Claude AI usage limit reached|1760000000", now) == datetime.fromtimestamp(1760000000, tz=timezone.utc)


def test_unknown_text_returns_none():
    assert parse_reset_at("something else", _local(2026, 10, 7, 9, 0)) is None


def test_detect_signal_uses_reset_for_retry_after():
    now = _local(2026, 10, 7, 14, 0)
    signal = detect_signal("You've hit your session limit · resets 3:45pm", None, now=now)
    assert signal.kind == "quota_exhausted"
    assert signal.retry_after_s == int(timedelta(hours=1, minutes=45).total_seconds())
```

- [ ] **Step 2: Correr y verificar que fallan**

Run: `cd backend && python -m pytest tests/test_quota_signals_claude.py -q` → FAIL.

- [ ] **Step 3: Implementar**

```python
from datetime import datetime, timedelta, timezone

RESET_CLOCK_RE = re.compile(r"resets?\s+(?:(mon|tue|wed|thu|fri|sat|sun)\w*\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)
RESET_EPOCH_RE = re.compile(r"\|(\d{9,11})\s*$")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _clock_hour(hour: int, meridiem: Optional[str]) -> int:
    if not meridiem:
        return hour
    hour = hour % 12
    return hour + 12 if meridiem.lower() == "pm" else hour


def _next_occurrence(local_now: datetime, hour: int, minute: int, weekday: Optional[int]) -> datetime:
    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if weekday is not None:
        candidate += timedelta(days=(weekday - local_now.weekday()) % 7)
    if candidate <= local_now:
        candidate += timedelta(days=7 if weekday is not None else 1)
    return candidate


def parse_reset_at(text: str, now: datetime) -> Optional[datetime]:
    epoch = RESET_EPOCH_RE.search(text or "")
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=timezone.utc)
    match = RESET_CLOCK_RE.search(text or "")
    if not match:
        return None
    day, hour, minute, meridiem = match.groups()
    weekday = _WEEKDAYS.index(day.lower()[:3]) if day else None
    local = _next_occurrence(now.astimezone(), _clock_hour(int(hour), meridiem), int(minute or 0), weekday)
    return local.astimezone(timezone.utc)
```

Cambiar `detect_signal`:

```python
def detect_signal(text: str, status: Optional[int] = None, now: Optional[datetime] = None) -> Optional[QuotaSignal]:
    text = text or ""
    kind = _kind_for(text, status)
    if kind is None:
        return None
    retry_after = parse_retry_after(text)
    if retry_after is None and kind == "quota_exhausted":
        retry_after = _seconds_until_reset(text, now or datetime.now(timezone.utc))
    return QuotaSignal(kind=kind, retry_after_s=retry_after, excerpt=make_excerpt(text))


def _seconds_until_reset(text: str, now: datetime) -> Optional[int]:
    reset = parse_reset_at(text, now)
    if reset is None:
        return None
    return max(60, int((reset - now).total_seconds()))
```

Ampliar `EXHAUSTED_RE` agregando `|hit your (?:\w+ )?limit` (dejar el `hit your limit` actual, que queda cubierto).

**Cuidado**: `RETRY_AFTER_RE` ya captura `resets? in N unidad` ("resets in 5 hours"). El nuevo `RESET_CLOCK_RE` exige un número seguido de `am|pm|:mm` o un día de la semana para no confundirse con "resets in 5 hours". Verificar con el test de `test_quota_signals.py` existente que "resets in" sigue resolviéndose por `parse_retry_after`: como `parse_retry_after` corre primero, ya gana.

- [ ] **Step 4: Correr los tests nuevos y los existentes de señales**

Run: `cd backend && python -m pytest tests/test_quota_signals_claude.py tests/test_quota_signals.py -q` → verde.

- [ ] **Step 5: Suite completa** → verde.

---

### Task B4: Servicio y API de cuentas

**Files:**
- Create: `backend/app/services/accounts_service.py`
- Create: `backend/app/api/accounts.py`
- Modify: `backend/app/main.py` (registrar el router con `prefix="/api"`)
- Modify: `docs/superpowers/specs/2026-10-07-orquestacion-transparente-design.md` §5.3 (las rutas pasan a `/api/accounts…`)
- Test: `backend/tests/test_accounts_service.py`, `backend/tests/test_accounts_api.py`

**Interfaces:**
- Consumes: `providers_service.load_registry/save_registry/_registry_lock`, `health_service.get/set_exhausted_until/reset/seconds_left`, `adapters.supports_accounts/account_has_login`, `CliAgent`, `DEFAULT_CLI_AGENTS`, `get_settings().litellm_config_dir`.
- Produces (servicio):
  - `accounts_root() -> Path`
  - `next_account_id(base: str, existing: set[str]) -> str`: el primer `f"{base}-{n}"` con n ≥ 2 que no exista.
  - `create_account(base: str, label: str) -> tuple[CliAgent, dict[str, str]]`: lanza `AccountError` si el adaptador no admite cuentas.
  - `delete_account(agent_id: str) -> None`: lanza `AccountError` si no es cuenta o no existe.
  - `pick_account(adapter: str) -> dict`
  - `record_usage(agent_id: str, rate_limits: dict, now: datetime | None = None) -> dict`
  - `list_accounts() -> list[dict]`
  - `login_commands(base: str, account_dir: str) -> dict[str, str]`
  - `class AccountError(ValueError)`
- Produces (API, todas bajo `/api`):
  - `GET /accounts` → `{"accounts": list_accounts()}`
  - `POST /accounts` con `{base, label}` → `{"agent": ..., "login": {...}}` (400 si `AccountError`)
  - `DELETE /accounts/{agent_id}` → `{"deleted": agent_id}` (400 si `AccountError`)
  - `GET /accounts/pick?adapter=claude` → la respuesta de `pick_account` y, en modo proxy, `base_url = str(request.base_url).rstrip("/")`
  - `POST /accounts/{agent_id}/usage` con `{"rate_limits": {...}}` → la respuesta de `record_usage` (404 si el id no existe)

**Reglas:**
- `create_account`:
  - Bajo `_registry_lock`, copia la entrada base (`registry.cli_agents` con ese id o, si no está, `DEFAULT_CLI_AGENTS`) con `id` nuevo, `adapter=base`, `account_dir=str(accounts_root()/id)`, `account_label=label or id` y `enabled=False`.
  - Crea la carpeta.
  - Inserta el id justo después de la base en cada lista de `tier_order` que contenga la base, y lo mismo en `thinkers`.
  - Guarda.
  - Si `base == "claude"` y la carpeta no tiene `settings.json`, escribe `{"statusLine": {"type": "command", "command": <cmd>}}` con `<cmd> = f'python "{scripts_dir}/bipolar-statusline.py" --agent-id {id}'`. `scripts_dir` = carpeta `scripts` del repo, resuelta como `Path(__file__).resolve().parents[3] / "scripts"`; en el binario de PyInstaller esa ruta no existe, y en ese caso no se escribe nada.
- `login_commands`:

| base | PowerShell | bash |
|---|---|---|
| claude | `$env:CLAUDE_CONFIG_DIR='<dir>'; claude` (y luego `/login`) | `CLAUDE_CONFIG_DIR='<dir>' claude` |
| codex | `$env:CODEX_HOME='<dir>'; codex login` | `CODEX_HOME='<dir>' codex login` |
| deepseek | `$env:DSH_HOME='<dir>'; dsh` | `DSH_HOME='<dir>' dsh` |

  El dict es `{"powershell": ..., "bash": ..., "note": ...}`. La nota de claude es "Dentro de Claude Code ejecuta /login con la cuenta que quieras asociar."
- `delete_account`: solo para entradas con `account_dir`. Las quita de `cli_agents`, de cada lista de `tier_order` y de `thinkers`, y llama `health_service.reset(f"cli:{id}")`. No toca la carpeta.
- `pick_account(adapter)`:
  - Orden de candidatos: primero los ids de `thinkers` en su orden, después el resto de `cli_agents` en su orden.
  - Se filtra a `base == adapter`, con `account_dir` y `account_has_login(agent) is not False` y `health_service.get(agent.key).state == "available"`.
  - Respuesta: `{"mode": "account", "agent_id", "account_dir", "label"}` con el primero, o `{"mode": "proxy"}` si no hay ninguno.
- `record_usage`:
  - Guarda en memoria `_usage[agent_id] = {"received_at": iso, "five_hour": {...}, "seven_day": {...}}` (solo las ventanas presentes).
  - Por cada ventana con `used_percentage >= registry.delegation.account_exhausted_pct` y `resets_at` numérico, llama `health_service.set_exhausted_until(f"cli:{agent_id}", datetime.fromtimestamp(resets_at, tz=timezone.utc), excerpt=f"{window} {pct:.0f}%")`.
  - Sin `rate_limits` o vacío: guarda el `received_at` y no marca nada.
  - Devuelve `{"agent_id", "state": health_service.get(...).state, "usage": _usage[agent_id]}`.
- `list_accounts()`: por cada entrada con `account_dir` → `{"agent_id", "base", "label", "account_dir", "enabled", "has_login": account_has_login(a), "state", "seconds_left", "usage": _usage.get(id)}`.

- [ ] **Step 1: Escribir los tests del servicio**

```python
"""Servicio de cuentas: crear, borrar, elegir y registrar uso."""
from datetime import datetime, timezone

import pytest

from app.models.provider import ProviderRegistry
from app.models.smart import CliAgent, DelegationConfig
from app.services import accounts_service, health_service, providers_service


@pytest.fixture
def env(tmp_path, monkeypatch):
    class FakeSettings:
        litellm_config_dir = str(tmp_path)

    monkeypatch.setattr(accounts_service, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(health_service, "get_settings", lambda: FakeSettings())
    health_service.reload_for_tests()
    accounts_service._usage.clear()
    state = {"registry": ProviderRegistry(
        cli_agents=[CliAgent(id="claude"), CliAgent(id="codex"), CliAgent(id="muse")],
        delegation=DelegationConfig(tier_order={"standard": ["muse", "codex", "claude"], "complex": ["codex", "claude"]},
                                    thinkers=["claude", "codex"]),
    )}
    monkeypatch.setattr(providers_service, "load_registry", lambda: state["registry"].model_copy(deep=True))
    monkeypatch.setattr(providers_service, "save_registry", lambda r: state.__setitem__("registry", r))
    monkeypatch.setattr(accounts_service, "_scripts_dir", lambda: tmp_path / "scripts")
    (tmp_path / "scripts").mkdir()
    yield state, tmp_path
    health_service.reload_for_tests()


def test_create_account_inserts_after_base_everywhere(env):
    state, tmp = env
    agent, login = accounts_service.create_account("claude", "Personal 2")
    assert agent.id == "claude-2" and agent.adapter == "claude" and not agent.enabled
    assert agent.account_dir == str(tmp / "accounts" / "claude-2")
    assert (tmp / "accounts" / "claude-2").is_dir()
    reg = state["registry"]
    assert reg.delegation.tier_order["standard"] == ["muse", "codex", "claude", "claude-2"]
    assert reg.delegation.tier_order["complex"] == ["codex", "claude", "claude-2"]
    assert reg.delegation.thinkers == ["claude", "claude-2", "codex"]
    assert "CLAUDE_CONFIG_DIR" in login["powershell"] and "claude-2" in login["bash"]
    settings = (tmp / "accounts" / "claude-2" / "settings.json").read_text(encoding="utf-8")
    assert "bipolar-statusline.py" in settings and "--agent-id claude-2" in settings


def test_second_account_gets_next_id(env):
    accounts_service.create_account("claude", "")
    agent, _ = accounts_service.create_account("claude", "")
    assert agent.id == "claude-3"


def test_unsupported_adapter_is_rejected(env):
    with pytest.raises(accounts_service.AccountError):
        accounts_service.create_account("muse", "x")


def test_delete_account_removes_everywhere_but_keeps_dir(env):
    state, tmp = env
    accounts_service.create_account("claude", "")
    accounts_service.delete_account("claude-2")
    reg = state["registry"]
    assert all(a.id != "claude-2" for a in reg.cli_agents)
    assert "claude-2" not in reg.delegation.thinkers and "claude-2" not in reg.delegation.tier_order["standard"]
    assert (tmp / "accounts" / "claude-2").is_dir()


def test_delete_base_agent_is_rejected(env):
    with pytest.raises(accounts_service.AccountError):
        accounts_service.delete_account("claude")


def test_pick_skips_accounts_without_login_and_exhausted(env):
    state, tmp = env
    accounts_service.create_account("claude", "")
    accounts_service.create_account("claude", "")
    assert accounts_service.pick_account("claude") == {"mode": "proxy"}
    (tmp / "accounts" / "claude-2" / ".credentials.json").write_text("{}", encoding="utf-8")
    (tmp / "accounts" / "claude-3" / ".credentials.json").write_text("{}", encoding="utf-8")
    assert accounts_service.pick_account("claude")["agent_id"] == "claude-2"
    health_service.set_exhausted_until("cli:claude-2", datetime(2099, 1, 1, tzinfo=timezone.utc), "5h 100%")
    assert accounts_service.pick_account("claude")["agent_id"] == "claude-3"


def test_record_usage_marks_exhausted_at_threshold(env):
    accounts_service.create_account("claude", "")
    resets = int(datetime(2099, 1, 1, tzinfo=timezone.utc).timestamp())
    out = accounts_service.record_usage("claude-2", {"five_hour": {"used_percentage": 99, "resets_at": resets},
                                                     "seven_day": {"used_percentage": 40, "resets_at": resets}})
    assert out["state"] == "exhausted"
    assert out["usage"]["five_hour"]["used_percentage"] == 99


def test_record_usage_without_rate_limits_marks_nothing(env):
    accounts_service.create_account("claude", "")
    out = accounts_service.record_usage("claude-2", {})
    assert out["state"] == "available" and "received_at" in out["usage"]
```

- [ ] **Step 2: Escribir los tests de la API**

```python
"""API de cuentas: rutas, códigos y base_url en modo proxy."""
import pytest
from fastapi.testclient import TestClient

from app.services import accounts_service


@pytest.fixture
def client(monkeypatch):
    from app.core.config import get_settings
    from app.main import app
    return TestClient(app, headers={"x-api-key": get_settings().ui_api_key})


def test_pick_proxy_includes_base_url(client, monkeypatch):
    monkeypatch.setattr(accounts_service, "pick_account", lambda adapter: {"mode": "proxy"})
    resp = client.get("/api/accounts/pick?adapter=claude")
    assert resp.status_code == 200
    assert resp.json()["mode"] == "proxy" and resp.json()["base_url"].startswith("http://testserver")


def test_create_rejects_unsupported(client, monkeypatch):
    def boom(base, label):
        raise accounts_service.AccountError("no admite cuentas")
    monkeypatch.setattr(accounts_service, "create_account", boom)
    resp = client.post("/api/accounts", json={"base": "muse", "label": "x"})
    assert resp.status_code == 400


def test_usage_unknown_agent_404(client, monkeypatch):
    def missing(agent_id, rate_limits, now=None):
        raise KeyError(agent_id)
    monkeypatch.setattr(accounts_service, "record_usage", missing)
    resp = client.post("/api/accounts/claude-9/usage", json={"rate_limits": {}})
    assert resp.status_code == 404


def test_accounts_require_api_key():
    from app.main import app
    assert TestClient(app).get("/api/accounts").status_code == 401
```

- [ ] **Step 3: Correr y verificar que fallan**

Run: `cd backend && python -m pytest tests/test_accounts_service.py tests/test_accounts_api.py -q` → FAIL.

- [ ] **Step 4: Implementar el servicio y la API**

- `accounts_service.py` importa `from app.core.config import get_settings` (así los tests lo pueden reemplazar) y define `_usage: dict[str, dict] = {}` y `_scripts_dir()` como función.
- `record_usage` lanza `KeyError` si el id no existe en `cli_agents`.
- `api/accounts.py`:
  - `router = APIRouter(prefix="/accounts", tags=["accounts"])`.
  - Los modelos de request son `CreateAccountRequest(base: str, label: str = "")` y `UsageReport(rate_limits: dict = {})`.
  - Declarar `GET /pick` **antes** de `DELETE /{agent_id}` y `POST /{agent_id}/usage`.
- `main.py`: `app.include_router(accounts_router.router, prefix="/api")`, junto a los demás.
- En la spec §5.3, reemplazar `POST /api/smart/accounts` por `POST /api/accounts` y `DELETE /api/smart/accounts/{id}` por `DELETE /api/accounts/{id}`, y agregar `GET /api/accounts`.

- [ ] **Step 5: Correr los tests nuevos y la suite completa** → verde.

---

### Task B5: Reporter de statusline

**Files:**
- Create: `scripts/bipolar-statusline.py`
- Test: `backend/tests/test_statusline_reporter.py`

**Interfaces:**
- Consumes: el JSON que Claude Code manda por stdin al statusline. Campos usados: `model.display_name` y `rate_limits.five_hour` / `rate_limits.seven_day`, cada uno con `used_percentage` y `resets_at`.
- Produces (funciones importables):
  - `build_report(data: dict) -> dict`: `{"rate_limits": {...}}`, solo con las ventanas presentes.
  - `status_text(data: dict) -> str`: por ejemplo `"Opus 4.6 · 5h 42% · 7d 13%"`, o solo el modelo si no hay `rate_limits`.
  - `api_key(config_dir: Path, environ: Mapping) -> str`: `BIPOLAR_API_KEY` del entorno o `UI_API_KEY=` del `.env` del config dir.
  - `main(argv: list[str], stdin: TextIO, stdout: TextIO, post: Callable[[str, dict, str], None]) -> int`.

**Comportamiento:**
- Argumentos:
  - `--agent-id` (obligatorio).
  - `--url`, default `http://127.0.0.1:8000`.
  - `--config-dir`: default `LITELLM_CONFIG_DIR` del entorno; si no está, `C:/litellm` en Windows y `~/.litellm` en otros.
  - `--then "<comando>"`: statusline original del usuario. Si viene, se ejecuta con el mismo stdin vía `subprocess.run(cmd, shell=True, input=raw, capture_output=True, text=True, timeout=2)` y su stdout se imprime **en vez de** `status_text`. Es el comando que el propio usuario configuró, igual que haría Claude Code.
- El POST va a `{url}/api/accounts/{agent_id}/usage` con header `x-api-key` y timeout 1 s, usando `urllib.request`. Cualquier excepción se ignora.
- Siempre devuelve 0 e imprime una línea. Con un JSON inválido en stdin imprime `""` y devuelve 0.

- [ ] **Step 1: Escribir los tests**

```python
"""Reporter de statusline: arma el reporte, no bloquea y no falla nunca."""
import importlib.util
import io
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "bipolar-statusline.py"


def _load():
    spec = importlib.util.spec_from_file_location("bipolar_statusline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DATA = {"model": {"display_name": "Opus 4.6"},
        "rate_limits": {"five_hour": {"used_percentage": 42.4, "resets_at": 1760000000},
                        "seven_day": {"used_percentage": 13, "resets_at": 1760500000}}}


def test_build_report_and_text():
    sl = _load()
    assert sl.build_report(DATA) == {"rate_limits": DATA["rate_limits"]}
    assert sl.status_text(DATA) == "Opus 4.6 · 5h 42% · 7d 13%"
    assert sl.status_text({"model": {"display_name": "Opus 4.6"}}) == "Opus 4.6"


def test_main_posts_and_prints(tmp_path):
    sl = _load()
    (tmp_path / ".env").write_text("UI_API_KEY=bc-test\n", encoding="utf-8")
    calls, out = [], io.StringIO()
    rc = sl.main(["--agent-id", "claude-2", "--config-dir", str(tmp_path)], io.StringIO(json.dumps(DATA)), out,
                 lambda url, payload, key: calls.append((url, payload, key)))
    assert rc == 0
    assert calls == [("http://127.0.0.1:8000/api/accounts/claude-2/usage", {"rate_limits": DATA["rate_limits"]}, "bc-test")]
    assert out.getvalue().strip() == "Opus 4.6 · 5h 42% · 7d 13%"


def test_main_survives_post_failure_and_bad_json(tmp_path):
    sl = _load()

    def boom(url, payload, key):
        raise OSError("down")

    out = io.StringIO()
    assert sl.main(["--agent-id", "claude-2", "--config-dir", str(tmp_path)], io.StringIO(json.dumps(DATA)), out, boom) == 0
    assert "Opus 4.6" in out.getvalue()
    out2 = io.StringIO()
    assert sl.main(["--agent-id", "claude-2", "--config-dir", str(tmp_path)], io.StringIO("{not json"), out2, boom) == 0


def test_without_rate_limits_does_not_post(tmp_path):
    sl = _load()
    calls = []
    sl.main(["--agent-id", "claude-2", "--config-dir", str(tmp_path)], io.StringIO(json.dumps({"model": {"display_name": "X"}})),
            io.StringIO(), lambda *a: calls.append(a))
    assert calls == []
```

- [ ] **Step 2: Correr y verificar que fallan** (el script no existe).

- [ ] **Step 3: Implementar `scripts/bipolar-statusline.py`** según el contrato. Debe correr con `python scripts/bipolar-statusline.py --agent-id x < data.json`; `main()` real usa `sys.argv[1:]`, `sys.stdin`, `sys.stdout` y un `_post` con `urllib.request`. Usar `if __name__ == "__main__": sys.exit(main(...))`. Sin dependencias.

- [ ] **Step 4: Correr los tests** → verde.

- [ ] **Step 5: Probar el script de verdad** (lección del proyecto: un script que no se ejecuta llega roto):

```powershell
'{"model":{"display_name":"Opus 4.6"},"rate_limits":{"five_hour":{"used_percentage":10,"resets_at":1760000000}}}' | python scripts/bipolar-statusline.py --agent-id claude-2 --url http://127.0.0.1:9
```

Expected: imprime `Opus 4.6 · 5h 10%` y sale con 0 aunque no haya nada escuchando en el puerto 9.
