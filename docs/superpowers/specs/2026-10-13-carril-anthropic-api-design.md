# Carril «anthropic»: API de Anthropic con los créditos mensuales del plan Max

Fecha: 2026-10-13 · Estado: aprobado en conversación (Santiago pidió implementar directo)

## Objetivo

Agregar al broker de delegación un carril que use la API de Anthropic pagada con los créditos mensuales que el plan Max deposita en la organización de Console «SURU» (USD 200/mes en Max 20x; USD 100/mes desde el cambio a Max 5x del 30-oct-2026). El carril se usa **primero, hasta agotar el crédito**, y luego el broker sigue con los carriles de siempre. Nunca debe generar cobros por encima del crédito.

## Restricciones (reglas de Anthropic)

- Los créditos cubren Claude API, Claude Agent SDK, Managed Agents y Playground. **No cubren Claude Code** (interactivo ni sesiones Claude Code) ni el uso extra de las apps.
- Al agotarse el saldo (sin créditos comprados ni recarga automática) la API responde «Your credit balance is too low to access the Anthropic API…» y no cobra nada.
- Por eso el carril usa el **Agent SDK de Python** (`claude-agent-sdk`), no el CLI `claude`, y la key de SURU **nunca** se entrega al proxy de litellm (tráfico de Claude Code).

## Decisiones

| Tema | Decisión |
|---|---|
| Usos | Delegación agéntica (tareas de código) y revisión en solo lectura (entra a `delegation.thinkers`) |
| Prioridad | La más alta del broker hasta que Anthropic responda «credit balance too low» |
| Modelos | trivial/simple → `claude-haiku-5-5`, standard → `claude-sonnet-5-5`, complex → `claude-opus-5-5`; revisión con `claude-sonnet-5-5` |
| Enfoque | Adapter nuevo que lanza un runner Python con `claude-agent-sdk` (enfoque A). Si la prueba real muestra que el SDK no descuenta de los créditos, el modo agéntico pasa a un loop propio sobre la Messages API (enfoque B) |
| Credencial | Variable `ANTHROPIC_CREDITS_API_KEY` en el `.env` de bipolar (leída con `env_value`). Distinta de `ANTHROPIC_API_KEY`, que usa el proxy |

## Componentes

1. **Agente `anthropic`** en `DEFAULT_CLI_AGENTS` (`backend/app/models/smart.py`): nombre «Anthropic API (créditos Max)», `supported_tiers` trivial/simple/standard/complex, `model_by_tier` según la tabla, `default_model` `claude-sonnet-5-5`, `priority` mayor que todos los demás agentes por defecto, `cost_weight` 0.1, `agentic` true, `quota_reset` "none", `cooldown_s` 21600 (6 h), `timeout_s` 900, `max_monthly_usd` 200.
2. **Campo nuevo `max_monthly_usd: float = 0`** en `CliAgent` (0 = sin tope). Editable como los demás campos del agente.
3. **`AnthropicAdapter`** en `backend/app/services/cli_agents/adapters.py`, registrado en `ADAPTERS` con `supports_read_only = True`:
   - `build()` lanza `[sys.executable, <ruta absoluta de anthropic_runner.py>, "--model", m, "--cwd", ws, "--max-turns", N, "--max-budget-usd", B, ("--read-only")]` con la tarea por stdin (o por el mecanismo de puntero que ya usa ClaudeAdapter, el que sea más simple y seguro).
   - Entorno del hijo: el de siempre (`child_env`) **más** `ANTHROPIC_API_KEY=<ANTHROPIC_CREDITS_API_KEY>` y `CLAUDE_CONFIG_DIR=~/.bipolar/anthropic-sdk`, y **sin** `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` ni `CLAUDE_CODE_OAUTH_TOKEN`. La key nunca va en argv ni en logs (`redacted`).
   - `max_turns` por nivel: trivial 15, simple 30, standard 60, complex 120. `max_budget_usd` por job: trivial 0.5, simple 1, standard 3, complex 8.
   - `parse()` lee la última línea JSON del runner: `{text, is_error, total_cost_usd, usage:{input_tokens, output_tokens}, session_id, error}` y devuelve `AdapterResult` con `usage={"input_tokens","output_tokens","cost_usd"}`; si hay `error`, el texto del error queda en `text` para que `quota_signals` lo detecte.
4. **`backend/app/services/cli_agents/anthropic_runner.py`**: script autónomo que usa `claude_agent_sdk.query` con `ClaudeAgentOptions(model, cwd, permission_mode="acceptEdits" (o "plan" en solo lectura), allowed_tools, disallowed_tools, max_turns, max_budget_usd, setting_sources=[], system_prompt preset claude_code)`.
   - Herramientas en modo agéntico: Read, Glob, Grep, Edit, Write, Bash. Bloqueadas siempre: Task, Agent, WebFetch, WebSearch y las reglas `Bash(git push:*)`, `Bash(git commit:*)`, `Bash(git reset:*)`, `Bash(git checkout:*)`, `Bash(git clean:*)`, `Bash(git stash:*)`, `Bash(rm:*)`, `Bash(rmdir:*)`, `Bash(del:*)`, `Bash(Remove-Item:*)`, `Bash(powershell:*)`, `Bash(pwsh:*)`, `Bash(cmd:*)`, `Bash(curl:*)`, `Bash(wget:*)`.
   - Modo `--read-only`: solo Read, Glob, Grep; sin Edit, Write ni Bash.
   - Siempre imprime UNA línea JSON final (también ante excepciones del SDK: `CLINotFoundError`, `ProcessError`, `ResultError`), con `error` = mensaje y, si existe, `api_error_status`.
5. **Señal de cuota**: `backend/app/core/quota_signals.py` reconoce `credit balance (is )?too low` y `credit_balance_too_low` como señal de cuota (no de fallo genérico).
6. **Sonda** en `backend/app/services/cli_agents/registry.py` (`_probe_anthropic`):
   - Sin `ANTHROPIC_CREDITS_API_KEY` → `installed=True`, `auth="auth_error"`, `error="falta ANTHROPIC_CREDITS_API_KEY"`.
   - Con key: llamada mínima con el SDK `anthropic` (`messages.create`, `claude-haiku-5-5`, `max_tokens=1`, prompt "ok", timeout corto). 200 → `auth="ok"`; 401 → `auth_error`; respuesta «credit balance too low» → `state` de cuota con `last_signal` de crédito agotado (el carril queda fuera hasta la próxima sonda, cada `cooldown_s`). La sonda nunca usa la key del proxy.
   - `quota` del status incluye `month_spend_usd` y `max_monthly_usd` para la UI y `list_agents`.
7. **Tope mensual de bipolar** en el broker: `_reject_agent` rechaza el agente con razón `monthly_budget` cuando `max_monthly_usd > 0` y el gasto del mes calendario de `cli:<id>` (vía `budget_service.spend_since` + `window_start("month")`) es ≥ `max_monthly_usd`. Como `_reject_agent` es síncrona, el gasto se precalcula donde se arman los `statuses` (o se cachea), sin I/O por candidato.
8. **Revisión**: el agente `anthropic` se agrega a la lista por defecto de `delegation.thinkers` (primero), usando el modo solo lectura del adapter con `claude-sonnet-5-5`.

## Fuera de alcance

- El provider `anthropic` del proxy de litellm no cambia ni recibe la key de SURU.
- Ritmo de gasto (pacing) por días: no; la prioridad es «primero hasta agotar».

## Configuración que hace Santiago (una vez)

1. Console → organización SURU → API Keys: crear una key; guardarla en el `.env` de bipolar como `ANTHROPIC_CREDITS_API_KEY=...`.
2. Console → Settings → Billing: **Auto-reload apagado** y saldo comprado en 0. Opcional: límite de gasto mensual de la organización igual al crédito.
3. Bajar `max_monthly_usd` a 100 cuando pase a Max 5x (30-oct).

## Errores

| Señal | Estado del job | Efecto |
|---|---|---|
| «credit balance too low» | `quota` | failover al siguiente carril; sonda cada 6 h |
| 401 / key inválida o ausente | `auth_error` | carril deshabilitado hasta corregir la key |
| 429 | `quota` con retry-after | cooldown corto y failover |
| 5xx / overloaded | `failed` | failover |
| `ResultError` por `error_max_budget_usd` / `error_max_turns` | `failed` | failover; el costo igual se registra |
| runner sin JSON | `failed` (`structured_error`) | failover |

## Pruebas

- Unitarias (pytest, estilo de `tests/test_cli_adapters.py`): `build()` (key en env, sin BASE_URL/AUTH_TOKEN, `CLAUDE_CONFIG_DIR`, flags de solo lectura, key ausente de argv y `redacted`); `parse()` (éxito, crédito agotado, 401, basura); patrón nuevo de `quota_signals`; rechazo por `monthly_budget`; sonda con cliente `anthropic` simulado; runner con `query` simulado (éxito, excepción del SDK → línea JSON con `error`).
- Paso 0 manual (Santiago, con la key real): `python anthropic_runner.py --model claude-haiku-5-5 --cwd <repo de prueba> --max-turns 2 --max-budget-usd 0.05` con la tarea «responde ok»; confirmar que responde y que en Console baja «Promotional credits». Si responde «Credit balance too low», no aplica: se pasa al enfoque B.
- Final: una delegación real por MCP sobre un repo de prueba, con `verify`.
