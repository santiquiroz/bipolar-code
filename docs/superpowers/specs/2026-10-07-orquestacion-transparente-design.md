# Orquestación transparente: bipolar-code como "OpenRouter local"

Fecha: 2026-10-07 · Versión objetivo: bipolar-code 2.18.0 · Rama: `feature/orquestacion-transparente`
Estado: aprobado para implementar. El usuario aprobó el enfoque y las secciones 1-2 en conversación y delegó el resto ("continúa con todo, impleméntalo con buenas prácticas").

## 1. Objetivo

Que Claude Code (y cualquier cliente Anthropic u OpenAI) apunte **una sola vez** a bipolar-code (`ANTHROPIC_BASE_URL=http://<host>:8000`) y que bipolar resuelva, sin que el usuario lo note:

1. **Qué backend y qué credencial atiende cada request**: si una llave o un provider se queda sin cuota, falla o no responde, el mismo request se reintenta en la siguiente llave o el siguiente provider antes de que el cliente vea un error.
2. **La delegación a agentes CLI** (Muse, Codex, cuentas de Claude Code headless, DeepSeek…) **desde bipolar**, sin los plugins `*-plugin-cc`: Claude Code llama herramientas MCP que expone bipolar.
3. **Calidad de lo delegado**: cada job pasa por verificación (comandos), revisión de un "pensador" en solo lectura y escalado al siguiente carril si algo falla.
4. **Varias cuentas por CLI** (por ejemplo, dos suscripciones de Claude Code), con alternancia automática cuando una llega a su límite, tanto para el broker como para el uso interactivo vía un lanzador.

### Criterios de éxito

- Un request a `/v1/messages` cuyo primer destino responde 429, 402, 401/403, 5xx, 529 o no conecta se atiende con el siguiente destino del plan, y el cliente recibe una respuesta normal. Las cabeceras dicen qué destino respondió y cuántos intentos hubo.
- Si todos los destinos fallan, el cliente recibe un **status HTTP real** (429, 529 o 502) con cuerpo de error en el formato de su API, para que aplique su propio backoff, en vez de un 200 con un evento SSE de error.
- Desde Claude Code, `delegate` (MCP) crea un job en bipolar; `job_status` lo sigue; ningún plugin interviene.
- Un job cuya verificación falla nunca termina como `succeeded`.
- Cuando una cuenta de Claude se agota, el siguiente job del broker usa la otra cuenta sin intervención, y `bipolar-claude` abre la sesión interactiva con la cuenta que tenga cupo.

## 2. Fuera de alcance (y por qué)

| Excluido | Motivo |
|---|---|
| Servir las **suscripciones** de Claude (OAuth de claude.ai) detrás de `/v1` | Los términos de Anthropic prohíben usar esas credenciales fuera de Claude Code, y técnicamente no encaja: un `claude -p` ejecuta sus propias herramientas y no puede devolverle bloques `tool_use` al cliente de afuera. Las suscripciones se usan solo como **CLIs** (broker headless y lanzador interactivo). |
| Pool de tokens para el provider `copilot` | Mismo problema de términos con cuentas personales o de empleador. El pool aplica a providers con API key. |
| Autenticación por usuario, llaves por miembro, OIDC | Se hará con la infraestructura de SURU (Authentik, ver `SURU-roadmap/docs/auth/group-sso.md`). Mientras tanto se mantiene `ui_api_key`. |
| Planificador (Claude descompone tareas y reparte subtareas) | Spec propia después de esta; depende de B, D y E. |
| Cambio de cuenta **dentro** de una sesión interactiva | No hay mecanismo soportado: Claude Code no expone hook de límite y cada `CLAUDE_CONFIG_DIR` es una isla de sesiones. El lanzador elige la cuenta al abrir y ofrece retomar de forma experimental. |

## 3. Arquitectura

```
                    ┌──────────────────────────── bipolar-code (:8000) ─────────────────────────────┐
Claude Code ──/v1──▶│ smart_router.decide → RoutePlan [destino#llave, …]                              │
 (ANTHROPIC_        │   └─ upstream.attempt_plan: abre el stream del 1º destino; si falla antes del   │
  BASE_URL)         │      primer byte → marca salud de esa llave/provider → siguiente destino        │──▶ llama-server, Anthropic API,
                    │                                                                                  │    OpenRouter, DeepSeek API…
Claude Code ──/mcp─▶│ MCP (JSON-RPC): delegate · job_status · job_output · cancel_job · list_agents    │
                    │   └─ broker: elige trabajador (Muse primero) → corre → verify (comandos) →       │──▶ muse, codex, claude
                    │      review (pensador en solo lectura) → aprobar / revisar / escalar             │    (cuentas por CONFIG_DIR), dsh…
                    │                                                                                  │
bipolar-claude ────▶│ /api/accounts/pick → cuenta Claude con cupo (o modo proxy)                       │
 (lanzador)         │ statusline de cada cuenta ──▶ /api/accounts/{id}/usage (rate_limits reales)      │
                    └──────────────────────────────────────────────────────────────────────────────────┘
```

Componentes y orden de entrega: **A** failover con pools de llaves → **B** cuentas por CLI → **D** verificación, revisión y escalado → **E** servidor MCP → **C** lanzador → **F** UI → **G** documentación. Cada componente se entrega con sus pruebas y deja la suite en verde.

## 4. Componente A — Pools de llaves y failover dentro del request

### 4.1 Modelo

`Provider` (en `models/provider.py`) gana:

```python
extra_auth_env_vars: list[str] = []   # llaves adicionales del pool, en orden; los valores viven en .env
```

- **Slots de credencial**: slot 0 = `auth_env_var`; slots 1..n = `extra_auth_env_vars`. Solo cuentan los slots con valor no vacío en `.env`. Un provider sin `auth_env_var` (local) tiene un único slot sin llave.
- **Clave de salud por slot**: `credential_key(provider, slot)` = `provider:<id>` si el provider tiene un solo slot (compatibilidad total con el estado actual), y `provider:<id>#<slot>` si tiene dos o más.
- **Clave de salud del provider**: `provider:<id>` sigue representando al provider entero (errores de conexión, 5xx, sobrecarga).
- Las señales **de credencial** (`rate_limit`, `quota_exhausted`, `auth`) marcan el slot. Las señales **de provider** (`overloaded`, 5xx genérico, conexión) marcan `provider:<id>`.
- Un provider se descarta de un plan si `provider:<id>` no está disponible o si ninguno de sus slots lo está (motivo `all_credentials_unavailable`).
- Orden dentro del pool: el primero disponible (se llena una llave antes de pasar a la siguiente). Un 429 mueve a la siguiente llave en el mismo request.

### 4.2 Plan de ruta

`smart_router.decide` sigue devolviendo `RouteDecision`, que ahora incluye `plan: list[PlanStep]`:

```python
@dataclass
class PlanStep:
    provider: Provider
    model: Optional[str]      # override de modelo (None = active_model del provider)
    slot: int                 # índice de credencial
    is_active: bool           # provider activo (decide si anthropic va vía litellm)
```

Construcción, sin duplicados `(provider, slot)` y con tope `smart.max_failover_attempts` (nuevo, default 4):

1. El destino elegido hoy (`chosen_provider`), con cada slot disponible.
2. Smart activo: los demás candidatos rankeados que sobrevivieron el filtro, en orden. Smart apagado o en shadow: `registry.fallback_provider_ids` (comportamiento actual), filtrando cooling y alcanzabilidad de bases locales.
3. Regla explícita (`routing_rules`) presente: el destino de la regla va primero y los fallbacks se agregan después; no se cambia la semántica de "la regla gana".
4. Sticky de conversación: si el destino sticky falla y otro responde, el sticky se actualiza al que respondió.

### 4.3 Ejecución (módulo nuevo `services/upstream.py`)

La lógica de "abrir upstream, mirar status, decidir" sale de `api/messages.py` y `api/openai_compat.py` a un módulo con funciones chicas y probables:

- `classify_failure(status, body_text, exc) -> FailureKind` con valores `retry_credential` (401, 403, 429, 402, texto de cuota/auth), `retry_provider` (conexión, timeout, 404, 408, 5xx, 529, sobrecarga), `fatal` (400, 413, 422 y cualquier otro 4xx).
- `async attempt_plan(plan, open_attempt) -> AttemptResult`: recorre el plan; por cada paso llama `open_attempt(step)`, que devuelve un upstream abierto (status < 400) o una falla con su clasificación. `retry_credential` marca el slot y sigue con el siguiente paso; `retry_provider` marca el provider y salta los pasos restantes de ese provider; `fatal` corta y se devuelve tal cual.
- El stream abierto se entrega al `StreamingResponse` junto con un `AsyncExitStack` que lo cierra al terminar (también si el cliente se desconecta). Se usa `client.stream(...)` como context manager para mantener el mismo punto de mock que ya usan los tests.

**Clave del diseño: el failover ocurre antes del primer byte.** `api/messages.py` resuelve el intento ganador **antes** de construir la `StreamingResponse`. Así:

- Las cabeceras pueden decir el destino real: `X-Bipolar-Target: <provider>#<slot>` y `X-Bipolar-Attempts: <n>`. `X-Bipolar-Route` se mantiene.
- Si todo falla, la respuesta es JSON con status real:
  - `/v1/messages`: `{"type":"error","error":{"type":"rate_limit_error"|"overloaded_error"|"api_error","message":...}}` con 429 si alguna falla fue de cuota o rate limit, 529 si fue sobrecarga, y 502 en otro caso.
  - `/v1/chat/completions`: `{"error":{"message":...,"type":...}}` con el mismo mapeo de status.
- Un error `fatal` del primer destino se devuelve con su status y mensaje sanitizado (hoy sale como evento SSE con status 200).

Se conservan sin cambios: la conversión Anthropic → OpenAI, el reintento por límite de contexto (dentro del intento OAI), la compresión, el registro de uso y `report_outcome`.

## 5. Componente B — Cuentas por CLI

### 5.1 Modelo

`CliAgent` (en `models/smart.py`) gana:

```python
adapter: str = ""        # adaptador a usar; "" = el id (las entradas existentes no cambian)
account_dir: str = ""    # carpeta de config de la cuenta ("" = la del sistema)
account_label: str = ""  # nombre visible ("Personal", "Personal 2")
```

Una cuenta es una entrada más de `cli_agents`, por ejemplo `{"id": "claude-2", "adapter": "claude", "account_dir": "C:/litellm/accounts/claude-2"}`. `adapter_for(agent)` resuelve por `agent.adapter or agent.id`; también lo hacen `resolve_exe` y los sondeos.

### 5.2 Aislamiento por adaptador

Cada adaptador declara `account_env`: la variable que apunta su CLI a otra carpeta de config.

| Adaptador | `account_env` | Archivo que prueba el login |
|---|---|---|
| claude | `CLAUDE_CONFIG_DIR` | `.credentials.json` |
| codex | `CODEX_HOME` | `auth.json` |
| deepseek (dsh) | `DSH_HOME` | `.credentials.yaml` |
| cursor | `CURSOR_CONFIG_DIR` | (sin verificación: `auth = unknown`) |
| muse, copilot, antigravity, ollama | — | no admiten cuentas extra hasta verificar su mecanismo |

Con `account_dir` definido, el hijo recibe esa variable vía `child_env(..., extra=...)`. bipolar **nunca lee** los archivos de credenciales; solo comprueba que existen.

**Aislamiento de los jobs de Claude, con o sin carpeta propia**: `ClaudeAdapter` siempre agrega `--setting-sources project,local` y `--strict-mcp-config`.

- El primero ignora el `settings.json` de usuario de esa carpeta, y con él su bloque `env`. Si el usuario apunta su Claude Code a bipolar con `ANTHROPIC_BASE_URL` en `~/.claude/settings.json`, un job del broker igual usa la suscripción de la cuenta y no da la vuelta por el proxy. También ignora sus hooks y plugins.
- El segundo desactiva los servidores MCP, incluido el `/mcp` de bipolar, lo que corta la recursión (job → MCP → job).

`--bare` no sirve para esto: nunca lee OAuth. Verificado con `claude --help` 2.1.293.

### 5.3 API

- `POST /api/smart/accounts` `{base_agent, label}`: valida que el adaptador admita cuentas; crea el id `<base>-<n>` y la carpeta `<config_dir>/accounts/<id>`; copia los campos de la entrada base (tiers, modelos, timeouts) con `enabled=false`; la inserta justo después de la base en cada tier de `tier_order` y en `delegation.thinkers`; para `claude`, escribe en la carpeta un `settings.json` mínimo con `statusLine` apuntando al reporter (5.4) si no existe. Responde la entrada y los comandos de login para PowerShell y bash.
- `DELETE /api/smart/accounts/{id}`: quita la entrada, sus menciones en `tier_order` y `thinkers`, y su salud. **No borra la carpeta** (puede tener credenciales; la borra el usuario).
- `GET /api/accounts/pick?adapter=claude`: primera entrada de ese adaptador **con `account_dir`** en el orden de `delegation.thinkers` (y después en `cli_agents`) cuya salud esté disponible. Considera también las entradas deshabilitadas para el broker: habilitar sirve para el broker, mientras que el lanzador usa toda cuenta que tenga login. Responde `{"mode": "account", "agent_id", "account_dir", "label"}` o `{"mode": "proxy", "base_url"}`.
- `POST /api/accounts/{id}/usage`: recibe `rate_limits` del statusline (`five_hour` y `seven_day` con `used_percentage` y `resets_at`), guarda el último reporte y, si alguno llega a `used_percentage >= smart.account_exhausted_pct` (default 98), marca la cuenta `exhausted` hasta su `resets_at` con `set_exhausted_until`.

### 5.4 Reporter de statusline

`scripts/bipolar-statusline.py`, solo stdlib: lee de stdin el JSON que Claude Code pasa al statusline, toma `rate_limits` y lo envía a `/api/accounts/<id>/usage` (id y URL por argumentos; llave desde `BIPOLAR_API_KEY` o el `.env` de la carpeta de config). Nunca bloquea el statusline: timeout de 1 s y cualquier error se ignora. Imprime una línea corta: modelo y porcentajes de 5 h y 7 días.

### 5.5 Reset de Claude en jobs headless

`quota_signals` aprende los formatos de Claude Code: `You've hit your session limit · resets 3:45pm`, `5-hour limit reached ∙ resets 3pm`, `resets Mon 9am` y el sufijo `|<epoch>`. `parse_reset_at(text, now) -> datetime | None` convierte la hora de reloj a la próxima ocurrencia en hora local. Si no se puede leer, se usa el `quota_reset` del agente (5 h o semanal), como hoy.

## 6. Componente D — Verificación, revisión y escalado

### 6.1 Contrato

`JobRequest` gana:

```python
verify: list[str] = []          # comandos; cada uno se parte con shlex (sin shell)
review: Optional[bool] = None   # None = delegation.review_default
max_revisions: int = Field(default=1, ge=0, le=3)
```

`DelegationConfig` gana:

```python
allow_request_verify: bool = False   # instalaciones nuevas: apagado. La del usuario: encendido.
review_default: bool = True
thinkers: list[str] = ["claude", "codex"]   # cadena de pensadores (las cuentas nuevas se insertan tras su base)
verify_timeout_s: int = 600
review_timeout_s: int = 900
```

Un `verify` no vacío con `allow_request_verify=false` se rechaza con 400 (`verify_disabled`).

### 6.2 Ciclo

Por cada trabajador W que el broker elige (el mismo que hoy, Muse primero):

1. **Trabajo**: W corre la tarea. Las señales de cuota siguen produciendo el failover actual.
2. **Verificación**: si W terminó bien y hay comandos, se corren en orden dentro del workspace validado: argumentos sin shell (ejecutable resuelto con `shutil.which` y envuelto con `exe_argv` si es `.cmd`/`.bat`), `child_env` mínimo, `verify_timeout_s` y los últimos 8 KB de salida. El primer código de salida distinto de 0 cuenta como fallo.
3. **Revisión**: si la verificación pasó (o no había comandos) y la revisión está activa, revisa el primer pensador de `thinkers` que esté disponible, admita solo lectura y **no sea W**. Recibe la tarea, el diff de los archivos que tocó el job (`git diff` más el contenido de archivos nuevos sin seguimiento, con un tope de 60 KB) y la salida de los checks. Debe terminar con un JSON `{"verdict": "approve"|"revise"|"reject", "issues": [...]}`.
   - Si el JSON no se puede leer, el pensador falló y se prueba el siguiente.
   - Si no queda ninguno, `review_status = skipped`: tener revisor no es requisito para aprobar.
4. **Decisión**:
   - `approve` (o revisión skipped): `succeeded`.
   - `revise` con revisiones restantes: W vuelve a correr con la tarea y los issues ("el revisor pidió: …"), y se regresa al paso 2.
   - `reject`, revisiones agotadas o verificación que sigue fallando: se **escala**.
5. **Escalado**: el siguiente trabajador es el primer pensador disponible que no haya trabajado en el job; si no hay, el siguiente carril del tier. Recibe la tarea original, un resumen de los intentos anteriores ("el intento de <W> dejó cambios en el árbol; falló porque: …") y el pedido de partir de ese estado. **Nunca se revierte nada**: el árbol puede tener trabajo sin commit del usuario.
6. **Tope**: `max_attempts` (existente) cuenta trabajos más escalados; las revisiones de un mismo trabajador no cuentan.

### 6.3 Solo lectura por adaptador

`build(...)` gana el parámetro `read_only: bool = False`; cada adaptador declara `supports_read_only`:

| Adaptador | Solo lectura |
|---|---|
| claude | `--permission-mode plan` y `--disallowedTools Edit,Write,MultiEdit,NotebookEdit,Bash,Task,Agent,WebFetch,WebSearch` |
| codex | `--sandbox read-only` |
| deepseek | `DSH_PERMISSION_MODE=read-only` |
| muse | flags de solo lectura del CLI (verificar en la implementación; si no hay garantía, `supports_read_only = False`) |
| cursor | ask mode |
| antigravity, copilot, ollama | no admiten (`agy` edita aunque se le pida no hacerlo) |

### 6.4 Registro y eventos

- `Attempt` gana `kind: Literal["work", "verify", "review"]` y `detail: dict` (comando y código de salida, o veredicto e issues).
- `Job` gana `verification_status` y `review_status` (`passed`, `failed`, `skipped` o `n/a`) y `escalations: int`.
- Eventos SSE nuevos: `verify_result`, `review_result`, `revision` y `escalated`.
- Errores finales: `verify_failed` y `review_rejected`.

## 7. Componente E — Servidor MCP de delegación

`POST /mcp`: JSON-RPC 2.0 sobre Streamable HTTP, respondiendo con `application/json` (sin SSE). `GET /mcp` devuelve 405. Métodos:

- `initialize`: responde la versión de protocolo del cliente si es conocida (`2025-06-18`, `2025-03-26`, `2024-11-05`) o la más reciente, con `capabilities: {tools: {}}` y `serverInfo` = bipolar-code con su versión. Entrega `Mcp-Session-Id`.
- `notifications/initialized` (y cualquier notificación): 202 sin cuerpo.
- `ping`: `{}`.
- `tools/list` y `tools/call`.

Herramientas:

| Herramienta | Entrada | Salida |
|---|---|---|
| `delegate` | `task`, `workspace`, `tier?`, `agent?`, `verify?`, `review?`, `wait_s?` (0-540, default 0) | job resumido; si `wait_s > 0`, espera hasta que termine o venza |
| `job_status` | `job_id`, `wait_s?` | estado, intentos, verificación, revisión, archivos tocados |
| `job_output` | `job_id`, `tail_chars?` (default 4000) | últimas líneas del log |
| `cancel_job` | `job_id` | estado final |
| `list_agents` | — | agentes habilitados con estado de cuota |

Los errores de herramienta van como `result.isError = true` con texto explicativo; los errores de protocolo, como errores JSON-RPC (`-32601` método desconocido, `-32602` parámetros inválidos).

**Seguridad**: `/mcp` ejecuta jobs en el host, así que es plano de control: requiere `ui_api_key` (`_is_public` deja de tratarlo como estático) y respeta `control_plane_allowed_cidrs` igual que `/api`.

**Uso desde Claude Code**: `claude mcp add --transport http --scope user bipolar http://localhost:8000/mcp --header "x-api-key: <ui_api_key>"`. Las reglas globales de delegación del usuario pasan a usar `delegate` de bipolar en lugar de los plugins `*-rescue`.

## 8. Componente C — Lanzador `bipolar-claude`

`scripts/bipolar-claude.ps1` (más un shim `.cmd`) y `scripts/bipolar-claude.sh`:

**Configuración recomendada**: cada suscripción en su propia carpeta de cuenta (`claude-1`, `claude-2`, creadas con la API de 5.3) y `~/.claude` apuntando a bipolar con `ANTHROPIC_BASE_URL`. Así, `claude` a secas es el modo proxy transparente (modelos locales y APIs con failover), y `bipolar-claude` usa primero las suscripciones.

1. Consulta `GET /api/accounts/pick?adapter=claude` (llave desde el `.env` del config dir). Solo cuentan las cuentas con `account_dir`: la carpeta del sistema es el perfil proxy.
2. **Modo cuenta**: sincroniza el perfil espejo (abajo) y lanza `claude` con `CLAUDE_CONFIG_DIR=<account_dir>`, pasando todos los argumentos.
3. **Modo proxy** (todas las cuentas agotadas o ninguna configurada): lanza `claude` con `ANTHROPIC_BASE_URL=http://localhost:8000` y `ANTHROPIC_API_KEY=<ui_api_key>` solo para ese proceso. Claude Code sigue funcionando con lo que tenga bipolar.
4. `-Continue` (experimental): copia el transcript más reciente del proyecto actual desde la cuenta usada la última vez y ejecuta `claude --resume <ruta>`. Si falla, abre una sesión nueva y avisa. El formato del transcript es interno de Claude Code y puede cambiar.
5. Recuerda la última cuenta usada en `<config_dir>/accounts/.last`.
6. Si bipolar no responde, abre `claude` normal, sin variables.

**Perfil espejo**: una carpeta de cuenta vacía no tendría los plugins, skills, CLAUDE.md ni servidores MCP del usuario. En cada lanzamiento en modo cuenta, el lanzador sincroniza desde `~/.claude`, salvo con `-NoMirror`:

- Junctions (`mklink /J`, sin admin) para `skills`, `agents`, `commands`, `rules`, `hooks` y `plugins`.
- Copia de `CLAUDE.md`.
- Copia de `settings.json`, quitando del bloque `env` las variables `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` y `ANTHROPIC_API_KEY`, y con `statusLine` reemplazado por el reporter de 5.4. Si el usuario tenía un `statusLine` propio, el reporter lo ejecuta con `--then` y muestra su salida.
- Fusión de `mcpServers` del `.claude.json` del usuario en el `.claude.json` de la cuenta. La ubicación exacta de `.claude.json` con `CLAUDE_CONFIG_DIR` se verifica en la implementación.

Nunca se copian `.credentials.json`, `projects/` ni el historial.

## 9. Componente F — UI

- **Providers**: en cada provider con `auth_env_var`, una sección "Llaves del pool" para agregar llaves (escribe `.env` vía `POST /api/settings/env` con el nombre `<AUTH_VAR>_<n>` y actualiza `extra_auth_env_vars`), ver el estado de cada slot y quitarlas.
- **Agentes**: botón "Agregar cuenta" por adaptador compatible, que muestra los comandos de login; listado de cuentas con uso de 5 h/7 días cuando hay reporte; ajustes de `thinkers`, `review_default`, `allow_request_verify` y timeouts.
- **Jobs**: intentos con su tipo (trabajo, verificación, revisión), veredicto e issues, y estado de verificación y revisión.

## 10. Errores y seguridad

- El failover nunca reintenta errores `fatal`: un 400 por un request mal formado no se pasea por todos los providers.
- Los mensajes de error que llegan al cliente pasan por `sanitize_error` (hoy ya existe) y nunca incluyen llaves.
- Los comandos de verificación corren sin shell, en el workspace validado, con entorno mínimo y timeout. Riesgo aceptado por el usuario: corren fuera de cualquier sandbox con su usuario de Windows. Mitigaciones: el endpoint exige `ui_api_key`, el workspace debe estar en la lista permitida y `allow_request_verify` viene apagado en instalaciones nuevas.
- `/mcp` es plano de control (llave y CIDR como `/api`).
- Las carpetas de cuenta viven bajo el config dir, nunca dentro de un workspace; bipolar no lee ni registra credenciales.
- El revisor corre en solo lectura y con guardia de recursión (`x-bipolar-depth`).

## 11. Migración y compatibilidad

- Todos los campos nuevos tienen defaults neutros: un registro existente carga igual y se comporta igual, salvo dos cambios intencionales:
  - En `/v1`, el failover dentro del request se activa con `fallback_provider_ids` o con smart activo.
  - Los errores totales salen con status HTTP real.
- `_seed_smart_defaults` agrega `thinkers` solo si está vacío.
- La instalación del usuario se configura después por API: `allow_request_verify=true` y las cuentas de Claude que cree.

## 12. Pruebas

- **A**: `classify_failure` (tabla de casos); `attempt_plan` con pasos falsos (falla de credencial → siguiente slot, falla de provider → salta provider, fatal → corta); construcción del plan (sin duplicados, tope, regla explícita primero, sticky); `/v1/messages` y `/v1/chat/completions` con upstream simulado (429 en llave 1 → responde llave 2; todos 429 → HTTP 429 con cuerpo Anthropic); cabeceras `X-Bipolar-Target` y `X-Bipolar-Attempts`.
- **B**: resolución por `adapter`; `account_env` en el entorno del hijo; sondeo por archivo de credenciales; crear y borrar cuentas (orden de tiers y thinkers); `pick`; `usage` marca agotada; `parse_reset_at` con los cuatro formatos.
- **D**: verificación (ok, fallo, timeout, `.cmd`); revisión (approve, revise → revisión → approve, reject → escalado, JSON inválido → siguiente pensador, sin pensadores → skipped); el escalado no reutiliza trabajadores; el tope; `verify_disabled`.
- **E**: `initialize`, `tools/list` y `tools/call` de cada herramienta; notificación → 202; método desconocido → -32601; `/mcp` sin llave → 401; fuera de CIDR → 403.
- **C**: el `.ps1` se ejecuta de verdad en modo `-WhatIf`/dry-run contra un bipolar de prueba (lección registrada: un script que no se corre llega roto).
- **F**: vitest de los paneles nuevos.
- Suite completa del backend y del frontend en verde al cerrar cada componente.
