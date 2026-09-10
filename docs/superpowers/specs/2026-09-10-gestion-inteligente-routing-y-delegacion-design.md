# Gestión inteligente: routing por complejidad y broker de delegación a agentes CLI

Fecha: 2026-09-10 · Versión objetivo: bipolar-code 2.13.0 · Estado: aprobado para implementar

## 1. Objetivo

Que bipolar-code decida, según **complejidad** del pedido y **uso/cuota** disponible,
a dónde va cada request y a qué agente se delega cada tarea de código:

- **Routing inteligente en `/v1/messages` y `/v1/chat/completions`**: clasifica cada
  request en un tier (`trivial`, `simple`, `standard`, `complex`), consulta una tabla
  tier → destinos ordenados y elige el primer destino elegible (capacidad, salud,
  cuota, presupuesto). Las reglas explícitas existentes siguen ganando.
- **Broker de delegación**: registra los agentes CLI instalados en el host
  (`claude`, `codex`, `copilot`, `agy` de Antigravity, `ollama`), conoce su estado de
  cuota y despacha tareas de código a la mejor opción disponible, con failover
  automático cuando una cuota se agota. Cada job corre como subproceso acotado en un
  workspace permitido y transmite su log por SSE.
- **Explicabilidad**: toda decisión lleva motivos (`reasons`), se registra en SQLite y
  sale en cabeceras `X-Bipolar-Route` / `X-Bipolar-Decision-Id`. El **modo shadow**
  registra qué elegiría el routing inteligente sin cambiar el destino real.

Fuera de alcance esta versión: clasificadores ML, tocar `litellm`, enviar turnos con
`tool_use`/`tool_result` a un CLI desde `/v1` (los CLIs traen su propio loop de
herramientas: en `/v1` solo se enruta entre providers HTTP), brokering multi-host,
lectura de cuotas de vendors que no la exponen (solo Antigravity la expone gratis vía
`agy -p "/usage"`), cambios en los cuatro plugins de Claude Code (se publica el
contrato `POST /api/delegate/jobs` para que lo consuman después).

## 2. Modelo de datos (todo aditivo, defaults neutros)

`backend/app/models/smart.py` (nuevo):

```python
Tier = Literal["trivial", "simple", "standard", "complex"]
TIER_ORDER = ("trivial", "simple", "standard", "complex")

class RouteTarget(BaseModel):
    provider_id: str
    model: str = ""                    # "" = active_model del provider

class TierPolicy(BaseModel):
    tier: Tier
    targets: list[RouteTarget] = []    # orden = preferencia

class BudgetWindow(BaseModel):
    target_key: str                    # "provider:<id>" | "cli:<id>"
    window: Literal["day", "week", "month"] = "day"
    max_requests: int = 0              # 0 = sin límite
    max_cost_usd: float = 0.0

class SmartRoutingConfig(BaseModel):
    enabled: bool = False
    mode: Literal["shadow", "active"] = "shadow"
    thresholds: dict[str, int] = {"simple": 25, "standard": 50, "complex": 75}
    tiers: list[TierPolicy] = []
    budgets: list[BudgetWindow] = []
    honor_tier_header: bool = True     # X-Bipolar-Tier del cliente
    sticky_tool_loops: bool = True     # mismo destino mientras haya tool_result en curso
    sticky_ttl_seconds: int = 1800
    skip_cooling_providers: bool = True
    respect_capabilities: bool = True  # supports_tools / supports_vision / context_window

class CliAgent(BaseModel):
    id: Literal["claude", "codex", "copilot", "antigravity", "ollama"]
    enabled: bool = False              # nunca se habilita solo
    exe_path: str = ""                 # "" = shutil.which(binario por defecto)
    default_model: str = ""
    model_by_tier: dict[str, str] = {} # antigravity: {"trivial":"gemini-3.8-flash-low", ...}
    alt_model_on_quota: str = ""       # antigravity: "claude-sonnet-4-6" (otro pool)
    supported_tiers: list[str] = ["simple", "standard", "complex"]
    agentic: bool = True               # ollama = False (solo respuesta de texto)
    priority: int = 50                 # menor gana empates
    cost_weight: float = 1.0           # 0 = gratis (ollama), 0.5 copilot, 1 claude/codex/agy
    max_concurrency: int = 1
    timeout_s: int = 600               # 60..3600
    cooldown_s: int = 900              # tras rate_limit sin retry-after
    quota_reset: Literal["none", "5h", "daily", "weekly"] = "none"
    max_credits: int = 0               # copilot --max-ai-credits (0 = omitir)
    extra_args: list[str] = []         # validados contra DANGEROUS_ARG_RE

class DelegationConfig(BaseModel):
    enabled: bool = False              # el interruptor "Gestión inteligente" del broker
    workspace_allowlist: list[str] = []# vacío = el broker rechaza todo job (fail closed)
    max_parallel_jobs: int = 3
    max_attempts: int = 3
    job_retention: int = 200
```

`backend/app/models/provider.py`: `RoutingRule` gana `tier: str = ""`,
`max_tokens: int = 0`, `label: str = ""` (regla vieja = comportamiento idéntico).
`ProviderRegistry` gana `smart: SmartRoutingConfig`, `cli_agents: list[CliAgent]`,
`delegation: DelegationConfig`. `load_registry` siembra los cinco agentes deshabilitados
y una tabla de tiers por defecto que solo referencia providers registrados; nunca
cambia `enabled`. Un `providers.json` viejo carga sin cambios.

Estado runtime persistido en `<config_dir>/routing_state.json` (no en providers.json):
salud/cuota por `target_key` con `state ∈ {available, cooling, exhausted, unavailable}`,
`until`, `last_signal`, `last_excerpt` (≤200 chars), `consecutive_failures`.

SQLite (`usage.db`, `init_db`, todo `IF NOT EXISTS`): tabla `route_decisions`
(`id, timestamp, surface, tier, score, intent, requested_model, prompt_tokens,
chosen_key, chosen_model, would_key, would_model, source, mode, reasons(json),
rejected(json), decision_ms, outcome, outcome_latency_ms, outcome_error`) y tabla
`delegate_jobs` (`id, created_at, finished_at, status, tier, agent_id, model,
workspace, attempts, duration_s, tokens_in, tokens_out, files_touched, signal`).

## 3. Clasificador (`services/route_classifier.py`, puro, sin I/O)

Señales (`RequestSignals`): `prompt_tokens`, `n_messages`, `n_tools`,
`has_tool_results` (último mensaje con `tool_result` o `role == "tool"`), `has_images`,
`last_user_tokens`, `code_fence_count`, `file_path_refs`, `intent`,
`model_hint ∈ {haiku, sonnet, opus, unknown}`, `max_tokens`, `thinking_requested`.
Acepta forma Anthropic y OpenAI. `classify_task(text)` reutiliza el mismo scoring para
el broker.

Intención (regex ES/EN, primer match): `agentic_loop`, `debug`, `plan`, `review`,
`test`, `code_edit`, `summarize`, `translate`, `explain`, `chat`.

Puntaje (base 10, cada aporte genera un `reason` como `"tokens:+10"`; clamp 0..100):
model hint haiku −15 / sonnet +5 / opus +25 · prompt_tokens <2k 0, <8k +5, <32k +10,
<100k +20, ≥100k +30 · n_tools 0/1–10 +5/>10 +10 · has_tool_results +15 · intent
chat/translate/summarize −10, explain 0, test/review +5, code_edit +10, debug +15,
plan +15 · code_fence_count ≥2 +5 · file_path_refs ≥3 +5 · has_images +5 ·
thinking_requested +20 · max_tokens ≥8000 +5. Tier por `thresholds` (validados
crecientes). Pisos: thinking → ≥ complex; opus → ≥ standard.

Ejemplos: "hola" con haiku → trivial (score ≈0). Turno de Claude Code con 15 tools y
12k tokens → standard (10+10+10+15=45 si trae tool_result). "Diagnostica la causa
raíz del deadlock y refactoriza el servicio" con opus y 40k tokens → complex.

## 4. Política de routing (`services/smart_router.py`)

`decide(body, model, prompt_tokens, headers, surface, dry_run=False) -> RouteDecision`:

1. `legacy = pick_provider(model, prompt_tokens)` (semántica actual: regla explícita →
   provider activo → `fallback_provider_ids` con chequeo TCP), ahora saltando también
   candidatos en `cooling`/`exhausted` cuando `skip_cooling_providers`.
2. `smart.enabled == False` → `mode="off"`, se devuelve `legacy`; solo se agregan las
   cabeceras. Idéntico a hoy.
3. Clasificar (cabecera `X-Bipolar-Tier` válida y `honor_tier_header` → `source="header"`).
4. Regla explícita que matchea (con las nuevas puertas `tier`/`max_tokens`) → gana;
   igual se calcula `would_choose` para el log.
5. Sticky: `has_tool_results` y LRU por `conversation_key` → mismo destino si sigue
   elegible.
6. Tabla de tiers: `TierPolicy` del tier; si no tiene destinos, sube al tier definido
   más cercano (`tier_fallback:<de>-><a>`), nunca baja.
7. Filtro con motivo por destino, primero que falla: `target_unknown`,
   `capability_tools`, `capability_vision`, `context_too_small`, `circuit_open`
   (3 fallos consecutivos → 120 s), `quota_cooldown`, `budget_exceeded:<window>`,
   `oai_surface_anthropic_not_active`, `unreachable` (base local, 0.4 s).
8. Ranking determinista: posición en la lista, luego menos fallos recientes.
9. Sin sobreviviente → `legacy` con `smart_no_candidate`.
10. `shadow` → `chosen=legacy`, `would_choose=smart`; `active` → `chosen=smart`,
    `would_choose=legacy`. Se persiste la decisión (fire-and-forget) salvo `dry_run`.

`report_outcome(decision_id, target_key, ok, latency_ms, status, error)`: éxito
→ `available`; 429 o texto de cuota → `cooling` por `Retry-After` o 900 s (Anthropic
API) ; 5xx/conexión → contador de fallos; se actualiza `outcome` en la fila.

Cabeceras: `X-Bipolar-Route: tier=complex;score=64;intent=debug;target=provider:copilot;model=claude-sonnet-4.6;src=smart;mode=active;id=<12 hex>[;shadow_target=...][;rejected=key:reason,...]`
y `X-Bipolar-Decision-Id`. Ambas en `expose_headers` de CORS.

## 5. Broker de delegación (`services/cli_agents/`)

**Registro y sondeo** (`registry.py`): `resolve_exe(agent)` = `exe_path` o
`shutil.which(BINARIES[id])` (Windows: `.exe`/`.cmd` vía `PATHEXT`; rutas conocidas
extra: `~/.local/bin/claude.exe`, `~/.gemini/bin/agy.exe`, `%LOCALAPPDATA%\agy\bin\agy.exe`).
`probe(agent)` (cache 10 min, sin gastar cuota): versión con `--version` (10 s);
auth/cuota barata: `codex login status`; `agy -p "/usage"` y `agy -p "/model"` con
`MSYS_NO_PATHCONV=1` (print mode responde sin turno de agente; devuelve % restante y
reset por pool) más existencia del bloque `deny` en
`~/.gemini/antigravity-cli/settings.json`; `ollama` → `GET /api/tags`; claude/copilot →
`unknown` hasta el primer job.

**Señales de cuota** (`core/quota_signals.py`, compartido con `/v1`):
`RATE_LIMIT_RE = rate.?limit|\b429\b|too many requests|slow down`,
`EXHAUSTED_RE = quota|usage limit|insufficient_quota|resource_exhausted|out of (ai )?credits|credits? (limit|exhausted)|weekly limit|hit your limit|limit reached`,
`AUTH_RE = not (logged in|authenticated)|authentication required|invalid api key|\b401\b|login required|unauthori[sz]ed`,
`OVERLOADED_RE = overloaded|\b529\b|\b503\b|service unavailable`,
`RETRY_AFTER_RE = (retry.?after|try again in|resets? in)[:= ]*(\d+)\s*(s|sec|m|min|h)?`.
`signal_from_attempt(returncode, stdout, stderr, structured_error)` solo mira las
últimas 40 líneas y solo cuenta si `returncode != 0`, hay error estructurado, o la
salida total es corta (<400 chars): evita falsos positivos cuando la tarea habla de
cuotas. Caso Antigravity verificado hoy: con el pool en 0 % agy no falla rápido,
reintenta con backoff hasta `--print-timeout` y devuelve `status: ERROR` /
`The stream was interrupted`; por eso el adaptador consulta `/usage` **antes** de
lanzar y trata esa firma como agotamiento.

**Máquina de estados** (`health_service.py`): `rate_limit` → `cooling` hasta
`retry_after` o `cooldown_s`; `overloaded` → `cooling` 120 s; `quota_exhausted` →
`exhausted` hasta `reset_at(quota_reset)` (`5h` +5 h, `daily` 00:00 local siguiente,
`weekly` lunes 00:00, `none` +6 h); `auth` → `unavailable` 1 h; 3 fallos genéricos →
`unavailable` 30 min; éxito → `available`. Antigravity usa dos claves
(`cli:antigravity#gemini`, `cli:antigravity#claude`) porque son dos pools.

**Adaptadores** (`adapters.py`, constructores puros): argv en lista, nunca `shell=True`,
el texto de la tarea nunca va en argv (stdin o archivo puntero dentro del workspace),
cwd = workspace validado, env mínimo (PATH, HOME/USERPROFILE, APPDATA, LOCALAPPDATA,
TEMP/TMP, SystemRoot, ComSpec, PATHEXT, LANG, `PYTHONUTF8=1`, `GIT_TERMINAL_PROMPT=0`,
`GIT_SSH_COMMAND=ssh -o BatchMode=yes`, `BIPOLAR_DELEGATION_DEPTH=1`) sin ningún
`*_API_KEY/*_TOKEN/*_SECRET` ni `ANTHROPIC_BASE_URL` (guardia anti-recursión). En
Windows un shim `.cmd/.bat` se ejecuta como `[ComSpec, "/d", "/s", "/c", shim, ...]`
con argv que solo contiene flags fijos y el path del workspace, validado contra
`^[A-Za-z]:[\\/][^&|<>^%!"'\r\n]*$`. `extra_args` rechazados si matchean
`dangerously|bypass|--yolo|--allow-all|full-auto|danger-full-access|--permission-mode|--sandbox|--approve|--add-dir|-C`.

Sufijo fijo al final de toda tarea (`TASK_CONSTRAINTS`): "Trabaja solo dentro de este
directorio con estas instrucciones. No delegues a otros agentes ni CLIs de IA. No hagas
git commit/push/reset/checkout ni borres archivos; deja los cambios en el working tree
y termina con la lista de archivos tocados."

Líneas de comando (`M = ["--model", model] si hay modelo`):

- **claude**: `[exe, "-p", "--output-format", "json", "--permission-mode", "acceptEdits", "--disallowedTools", "Task,Agent,WebFetch,WebSearch", "--max-turns", "50", "--add-dir", ws, *M]`, tarea por stdin. Parse: JSON → `result`, `is_error`, `usage`, `total_cost_usd`, `session_id`.
- **codex**: `[exe, "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "--color", "never", "--json", "-C", ws, "-o", out_file, *(["-m", model]), "-"]`, tarea por stdin (`-`). Parse: texto de `out_file` (fallback último `agent_message` del JSONL), `turn.completed` → usage.
- **copilot**: archivo puntero `ws/.bipolar/jobs/<id>/task.md` (con `.gitignore` `*`); `[exe, "-p", "Read the file .bipolar/jobs/<id>/task.md and do exactly what it says. Do not modify or delete that file.", "-s", "--no-ask-user", "--add-dir", ws, "--allow-tool", "write", "--allow-tool", "shell(git:*)", "--deny-tool", "shell(rm)", "--deny-tool", "shell(rmdir)", "--deny-tool", "shell(del)", "--deny-tool", "shell(Remove-Item)", "--deny-tool", "shell(git push)", "--deny-tool", "shell(git reset)", "--deny-tool", "shell(git clean)", "--deny-tool", "shell(git checkout)", *(["--max-ai-credits", n]), *(["--effort", "low"] si tier ≤ simple), *M]`. Deny gana sobre allow. Tras un match de rate limit con 10 s de silencio se mata el proceso (bug documentado de resume).
- **antigravity**: precondición bloque `deny` en settings.json (si falta → `unavailable` 1 h, motivo `agy_deny_list_missing`); preflight `/usage`: pool del modelo ≤2 % → usar `alt_model_on_quota` si el otro pool tiene margen, si no `exhausted`; archivo puntero como copilot; `[exe, "-p", POINTER_PROMPT, "--add-dir", ws, "--dangerously-skip-permissions", "--disable-slash-commands", "--output-format", "json", "--print-timeout", f"{max(60, timeout_s-30)}s", *M, *(["--effort", e] solo si el modelo empieza por "gemini-")]`. Parse: JSON `status`/`response`/`error`; stderr conserva líneas `jetski:`, `[agy]`, `error:`.
- **ollama** (solo `agentic=False`, modo texto): sin subproceso, `POST {api_base}/api/generate` con `stream=true`, `options.num_predict=4096`; el modelo debe terminar en `-32k` o `-mechanical` (regla del dueño: nunca un tag crudo).

**Broker** (`broker.py`): `validate_workspace` (absoluto, `resolve(strict=True)`,
`is_dir`, dentro de un allowlist también resuelto, ni raíz de disco, ni home, ni
config_dir, ni componente `.git`; allowlist vacío → `workspace_allowlist_empty`);
`submit(req)` → clasificar (`tier_hint` del cliente > clasificador) → `choose_agent`
(preferido si elegible; si no, orden por tier del `DelegationConfig`/`priority`,
filtrando `enabled`, instalado, tier soportado, `agentic` si el modo lo exige,
estado `available`, cupo de concurrencia) → `dry_run` devuelve la elección sin lanzar
→ `asyncio.create_task(_run_job)`; `_run_job`: semáforo global + por agente, hasta
`max_attempts` intentos solo ante señal de cuota (nunca reintento ciego ante fallo
genérico), timeout con kill del árbol (psutil), `files_touched` con
`git -C ws status --porcelain`, registro en `usage_tracker` con `provider_id =
"cli:<agent>"`, log en `<config_dir>/delegate/jobs/<id>.log` (5 MB) y ring de 2000
líneas para SSE; `cancel`, `list_jobs`, `get_job`, `subscribe` (replay + tail, evento
final `done`). Rechaza jobs si la request trae `X-Bipolar-Depth ≥ 1` o el proceso
propio nació con `BIPOLAR_DELEGATION_DEPTH` (anti-recursión).

## 6. API (todo detrás del `APIKeyMiddleware` existente)

`api/smart.py` (prefijo `/api/smart`):
`GET /config` → `{smart, cli_agents, delegation, agents: AgentStatus[], recommendations}`
(recomendación: Ollama ≥ 0.33 detectado → activar `anthropic_native` con un clic) ·
`PUT /config` (parcial: `smart?`, `cli_agents?`, `delegation?`; 400 por ids
desconocidos, thresholds no crecientes, rutas no absolutas/inexistentes, `extra_args`
peligrosos) · `POST /presets/default?save=` · `POST /classify {body|task}` ·
`POST /explain {body, surface}` (dry-run) · `GET /health`, `POST /health/reset?target=` ·
`GET /decisions?limit&tier&target&source&since`, `GET /decisions/summary?period=` ·
`GET /agents?refresh=`, `POST /agents/{id}/probe`.

`api/delegate.py` (prefijo `/api/delegate`): `POST /jobs` (202 `Job`; `dry_run` →
200; 400 `workspace_not_allowed`/`no_agent_available` con `rejected`; 409
`delegation_disabled`; 429 backlog) · `GET /jobs?limit&status` · `GET /jobs/{id}` ·
`GET /jobs/{id}/stream` (SSE `status|line|attempt|done`) · `GET /jobs/{id}/output`
(texto) · `DELETE /jobs/{id}`.

`/v1/messages` y `/v1/chat/completions`: sin cambios de cuerpo; cabecera de entrada
`X-Bipolar-Tier`; cabeceras de salida `X-Bipolar-Route`, `X-Bipolar-Decision-Id`.
`GET /api/providers/routing` devuelve las reglas con los campos nuevos.

## 7. UI mínima (habilitar, configurar, observar)

- **Providers**: `SmartRoutingPanel` debajo de `RoutingPanel`: interruptor, modo
  Shadow/Activo con texto de ayuda, umbrales, tabla de 4 tiers (CSV ordenado de
  `provider_id[:model]` como el campo de fallback), botón "Sugerir", presupuestos,
  probador "¿A dónde iría?" (POST `/explain`). Las filas de `RoutingPanel` ganan
  selector de tier, `max tokens` y etiqueta.
- **Agentes** (ruta nueva `/agents`, entrada "Agentes" en `Layout`): tabla de agentes
  (instalado/versión, auth/cuota con cuenta regresiva y % de pools de Antigravity,
  enabled, modelo, timeout, `max_credits`, Probar, Reiniciar cuota), textarea de
  workspaces permitidos, interruptor de delegación, formulario de job (tarea,
  workspace, modo, agente preferido, dry-run) y lista de jobs con log en vivo (fetch +
  ReadableStream con `X-API-Key`), Cancelar.
- **Usage**: sección "Decisiones de routing" (chips: decisiones, acuerdo legacy↔smart,
  ms promedio; tabla de últimas 100) y "Delegaciones por agente".
- Vitest: `SmartRoutingPanel.test.tsx`, `pages/Agents.test.tsx`.

## 8. Seguridad

Todo bajo API key; argv en lista y prompt por stdin/archivo; workspace allow-list
cerrado por defecto y resuelto contra symlinks; env mínimo sin secretos del gateway
ni `ANTHROPIC_BASE_URL`; flags de seguridad de cada CLI como constantes del adaptador
no configurables (claude sin bypass y sin `Task/Agent`; codex `workspace-write`;
copilot deny list que gana; agy solo con deny list global presente); timeouts con kill
de árbol; límites de salida/log/cola; `sanitize_error` en todo lo que sale; el juez
LLM queda para 2.13.1. Jamás se loguea la tarea ni el env del hijo.

## 9. Pruebas (pytest, sin subprocesos ni red reales)

`test_route_classifier.py`, `test_quota_signals.py`, `test_health_service.py`,
`test_budget_service.py`, `test_smart_router.py`, `test_cli_adapters.py`,
`test_delegation_broker.py`, `test_smart_api.py`, `test_delegate_api.py`,
`test_messages_smart.py`, `test_registry_migration_smart.py`. Los 147 existentes deben
seguir verdes sin tocarlos (`pick_provider`, `resolve_route`, `set_routing` y
`usage_tracker.record` conservan firma).

## 10. Orden de implementación

1. Modelos + migración de registro + tests de compat.
2. `quota_signals` + `health_service` + `budget_service` + tests.
3. `route_classifier` + tests.
4. `decisions_log` + `smart_router` + integración en `messages.py`/`openai_compat.py`
   (cabeceras, `report_outcome`) + tests.
5. `cli_agents` (registry, adapters, broker) + `api/delegate` + tests.
6. `api/smart` + tests.
7. Frontend (SmartRoutingPanel, página Agentes, sección en Usage) + vitest.
8. README, CHANGELOG, bump 2.13.0, PR, tag `v2.13.0` (Actions publica binarios).

## 11. Riesgos y decisiones

- Deriva de flags de los CLIs (claude 2.1.x, codex 0.153, copilot 1.0.83, agy 1.2.0,
  verificados hoy): centralizados por adaptador; el sondeo y `last_error` los exponen.
- Falsos positivos de cuota → cooldowns visibles y reseteables.
- Bucle de delegación (un `claude` hijo apuntado a bipolar) → env limpio,
  `BIPOLAR_DELEGATION_DEPTH`, `--disallowedTools Task,Agent`, sufijo de tarea.
- Calibración del clasificador → `shadow` por defecto, reglas explícitas mandan,
  `X-Bipolar-Tier` y `/explain` para ajustar antes de activar.
- Alcance nocturno: si el tiempo no alcanza, el orden de implementación garantiza que
  lo publicado (routing shadow/activo con explicabilidad + broker con claude/codex/
  copilot/agy/ollama) sea coherente aunque la UI de Usage quede para 2.13.1.
