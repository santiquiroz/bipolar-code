# Bipolar Code

Tu gateway personal de LLMs, self-hosted. Una sola app que:

- **Sirve modelos locales grandes** con llama.cpp gestionado (multi-GPU AMD/NVIDIA vía Vulkan, tensor-split automático, descarga de modelos desde Hugging Face en la UI).
- **Gestión inteligente** (2.13): clasifica cada request por complejidad y lo enruta al destino con cuota y capacidad (modo shadow para probar sin riesgo), y delega tareas de código a los CLIs instalados (Claude Code, Codex, Copilot, Antigravity, Ollama) según tier y cuota.
- **Habla los dos idiomas**: Anthropic Messages API (`/v1/messages`) y OpenAI (`/v1/chat/completions`) — cualquier herramienta que use Claude o un endpoint OpenAI-compatible puede apuntar aquí.
- **Enruta por escenario**: tareas background → modelo chico, código → modelo grande local, razonamiento pesado → Anthropic real. Con failover automático si un provider local está caído.
- Gestiona providers cloud (Copilot, Anthropic, NVIDIA NIM, OpenRouter, DeepSeek…) con cambio de un clic, chat con streaming, tracking de uso/costos.

```
Claude Code / OpenClaw / VS Code / Cursor / bot Telegram
        │  (Anthropic API u OpenAI API + tu API key)
        ▼
  bipolar-code :8000  ──routing──►  llama-server local (tus GPUs)
                      ──routing──►  GitHub Copilot / Anthropic / NIM / …
```

---

## Quick Start (desde cero, sin experiencia)

1. **Descarga el ejecutable** de la [página de Releases](../../releases):
   - `bipolar-code-windows.exe` — Windows
   - `bipolar-code-linux` — Linux
   - `bipolar-code-macos` — macOS

2. **Ejecútalo**:
   - Windows: doble clic en `bipolar-code-windows.exe`
   - Linux/macOS: `chmod +x bipolar-code-linux && ./bipolar-code-linux`

   Al primer arranque crea solo su directorio de configuración (Windows: `C:\litellm\`, Linux/macOS: `~/.litellm/`) y genera una **API key** propia — no necesitas crear nada a mano.

3. **Abre [http://localhost:8000](http://localhost:8000)**. La UI te pedirá la API key la primera vez: está en `Settings → Acceso y Seguridad → Copiar API Key completa` (o en el archivo `.env` del directorio de configuración, variable `UI_API_KEY`).

4. **Elige un provider** en la pestaña **Providers** y pulsa *Activar*. Para probar sin GPU ni cuentas de pago: `NVIDIA NIM` (gratis con registro) u `OpenRouter` (tiene modelos free). Las keys de cada provider se pegan en **Settings**.

5. *(Solo si vas a usar providers cloud vía proxy — Copilot/Anthropic)*: instala litellm una vez:
   ```bash
   pip install litellm
   ```
   Para modelos locales (llama.cpp, LM Studio, Ollama) **no hace falta**: bipolar-code les habla directo.

Listo. Todo lo demás son casos de uso.

---

## Caso de uso 1 — Claude Code con el backend que tú elijas

**En este PC**: Dashboard → *Routing de Claude Code* → `Proxy`. Eso escribe `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` por ti (settings.json + registro en Windows). Reinicia Claude Code y ya está pasando por bipolar-code. Volver a Anthropic directo = un clic (`Direct`).

**En otros PCs de tu red**: Settings → *Conectar PCs remotas* muestra el snippet exacto con tu IP LAN:

```jsonc
// ~/.claude/settings.json → "env"
"ANTHROPIC_BASE_URL": "http://192.168.x.x:8000",
"ANTHROPIC_API_KEY": "<tu API key de bipolar-code>"
```

Un portátil sin GPU usa los modelos del PC grande. Fuera de tu LAN: VPN (Tailscale/WireGuard), nunca expongas el puerto a internet.

## Caso de uso 2 — Modelo local grande (llama.cpp gestionado)

1. Descarga un release **Vulkan** de [llama.cpp](https://github.com/ggml-org/llama.cpp/releases) y descomprímelo (o pon `llama-server` en el PATH).
2. Providers → tarjeta **llama.cpp (Local multi-GPU)**:
   - Pega la ruta de `llama-server` (si no está en PATH).
   - **Modelos (buscar / descargar)**: busca un GGUF en Hugging Face (ej. `Qwen3-Coder-Next Q4_K_M`), descárgalo con barra de progreso y pulsa *Usar*. Modelos gated: variable `HF_TOKEN` en Settings.
   - Pulsa **Iniciar** y luego **Activar** el provider.
3. El panel detecta tus GPUs y reparte el modelo proporcionalmente a la VRAM libre (`--tensor-split` automático — dos AMD distintas mezcladas funcionan, ej. R9700 32GB + RX 7800 XT 16GB = pool de 48GB).

Extras del panel:
- **Auto-arranque**: levanta llama-server al abrir bipolar-code.
- **Router mode**: sirve TODOS los GGUF descargados a la vez con carga/descarga dinámica — cada request elige modelo por nombre (combínalo con el routing por escenario).
- **Logs del servidor** en la propia UI para diagnosticar cargas/OOM.
- **Granja multi-PC** (avanzado): `rpc_servers` en la config del provider suma GPUs de otros PCs corriendo `ggml-rpc-server` (`--rpc`). Solo vale la pena para modelos que no caben en un solo PC; usa cable 2.5GbE+ y el mismo build de llama.cpp en todos los nodos.

## Caso de uso 3 — OpenClaw (o cualquier agente que hable Anthropic)

OpenClaw quema tokens con ganas (system prompt grande, heartbeats). Apuntado a bipolar-code, eso sale gratis en tu GPU:

- Provider Anthropic de OpenClaw → `ANTHROPIC_BASE_URL=http://<ip-de-bipolar>:8000` + `ANTHROPIC_API_KEY=<tu API key>`.
- Alternativa OpenAI-compat: `baseUrl http://<ip>:8000/v1` (mismo camino que OpenClaw usa para Ollama/LM Studio).
- Recomendado: routing por escenario (abajo) para que sus heartbeats vayan al modelo chico y el trabajo real al grande. Nota: los agentes exigen tool-calling sólido — modelos clase Qwen3-Coder, no 7B genéricos.

## Caso de uso 4 — VS Code Copilot Chat, Cursor, Cline, Continue…

Cualquier cliente BYOK con proveedor "OpenAI compatible":

- **URL**: `http://<ip>:8000/v1`
- **API key**: la de bipolar-code
- En VS Code: Copilot Chat → *Manage models* → OpenAI compatible.

El request llega al provider activo (o al que diga el routing).

## Caso de uso 5 — Routing por escenario + failover transparente

Providers → **Routing por escenario**. Reglas primer-match-gana sobre el nombre de modelo que pide el cliente:

| Patrón | Min tokens | Provider destino | Para qué |
|---|---|---|---|
| `haiku` | 0 | llamacpp → GGUF chico | Background de Claude Code (gratis y rápido) |
| `opus` | 0 | Anthropic real | Solo razonamiento pesado |
| *(vacío)* | 60000 | provider con contexto largo | Prompts gigantes |
| *(sin match)* | — | provider activo | Todo lo demás |

**Failover** (mismo panel): lista de providers de respaldo — si el destino local no responde, el request cae al primero alcanzable. Ej: `copilot, anthropic` = si apagaste llama-server, todo sigue funcionando solo.

**Pools de llaves**: cada provider con `auth_env_var` puede tener llaves extra (`extra_auth_env_vars`, se agregan en la tarjeta del provider en Providers → **Llaves del pool**). El slot 0 es la llave principal y los slots 1..n las extra; solo cuentan los slots con valor en el `.env`.

**Failover dentro del mismo request**: si el destino elegido falla, el request se reintenta en la siguiente llave o el siguiente provider **antes del primer byte**, sin que el cliente vea un error. El orden es llave → provider: un 429 mueve a la siguiente llave del mismo provider; una caída del provider salta a los pasos del siguiente destino.

**Qué se reintenta y qué no**: se reintenta ante 401/403/429/402 (cuota, rate limit, auth), 5xx/529 (sobrecarga, error del upstream) y fallas de conexión o timeout. No se reintenta ante 400/413/422 ni otros 4xx: un request mal formado se devuelve tal cual, no se pasea por todos los providers.

Cada respuesta dice qué pasó: `X-Bipolar-Target: <provider>#<slot>` (quién respondió) y `X-Bipolar-Attempts: <n>` (cuántos intentos hubo). Si ningún destino responde, el cliente recibe un **status HTTP real** — 429 si hubo cuota o rate limit, 529 si hubo sobrecarga, 502 en otro caso (o el status del error fatal) — con el cuerpo de error en el formato de su API, para que aplique su propio backoff.

## Caso de uso 6 — Bot de Telegram

Chatea con tu stack desde el teléfono. En Settings (o el `.env`):

```
TELEGRAM_BOT_TOKEN=<token de @BotFather>
TELEGRAM_ALLOWED_CHAT_IDS=123456789
```

Allowlist vacía = bot inerte (default seguro). El bot responde con el provider activo/ruteado.

## Caso de uso 7 — Delegar tareas desde Claude Code (MCP de bipolar)

bipolar-code expone en `/mcp` un servidor MCP (JSON-RPC sobre HTTP) para delegar tareas de código a los agentes CLI del host, sin plugins. Desde Claude Code, una sola vez:

```
claude mcp add --transport http --scope user bipolar http://localhost:8000/mcp --header "x-api-key: <ui_api_key>"
```

(`<ui_api_key>` es tu API key: Settings → Acceso y Seguridad, o `UI_API_KEY` en el `.env`. En otros PCs de la LAN, cambia `localhost` por la IP del gateway.)

Cinco herramientas:

| Herramienta | Para qué |
|---|---|
| `delegate` | Crea un job: `task`, `workspace` (ruta absoluta del repo) y opcionales `tier`, `agent`, `verify` (comandos), `review`, `wait_s` (0–540, default 0). Con `wait_s > 0`, espera a que termine o venza. |
| `job_status` | Estado del job (`job_id`, `wait_s` opcional): intentos, verificación, revisión, archivos tocados. |
| `job_output` | Últimas líneas del log (`job_id`, `tail_chars`, default 4000). |
| `cancel_job` | Cancela un job en curso. |
| `list_agents` | Agentes habilitados con su estado de cuota. |

Los plugins `*-plugin-cc` siguen funcionando, pero ya no son necesarios: las reglas de delegación pueden llamar a `delegate` directamente.

---

## Caso de uso 8 — Gestión inteligente: routing por complejidad y delegación a agentes CLI

Providers → **Routing inteligente**. Cada request a `/v1/messages` o `/v1/chat/completions` se clasifica en un tier (`trivial`, `simple`, `standard`, `complex`) con señales deterministas (tokens, tools, `tool_result` en curso, intención del último mensaje, hint del modelo pedido, thinking) y va al primer destino elegible de la tabla tier → providers: se descartan los que estén en cooldown por 429/cuota, sin capacidad (tools, visión, contexto), fuera de presupuesto o inalcanzables. Las reglas explícitas del caso 5 siguen ganando.

- **Shadow primero**: en modo shadow no cambia ningún destino, solo registra qué habría elegido. Cada respuesta lleva la cabecera `X-Bipolar-Route` y la decisión queda en Usage → *Decisiones de routing* (acuerdo legacy↔smart, motivos, rechazos). Cuando te convenza, pásalo a *Activo*.
- **Explicable**: `POST /api/smart/explain` con un body de ejemplo devuelve tier, puntaje, motivos y destino sin llamar a nadie; `X-Bipolar-Tier: complex` fuerza el tier desde el cliente.
- **Presupuestos** por destino y ventana (día, semana, mes) sobre `usage.db`.

Pestaña **Agentes**: bipolar-code detecta los CLIs instalados en el host (`claude`, `codex`, `copilot`, `agy` de Antigravity, `ollama`, `cursor-agent` de Cursor, `dsh` de DeepSeek Harness, `muse` de Meta), muestra versión, auth y estado de cuota (Antigravity expone sus dos pools con `agy -p "/usage"`, gratis; Cursor informa con `cursor-agent about`, sin gastar cuota) y delega tareas de código al mejor disponible:

DeepSeek Harness (`dsh`, la app de escritorio de DeepSeek) es el último carril de todos los tiers (último recurso: cobra por token contra saldo prepago): `deepseek-flash` en trivial y simple, `deepseek-v4-pro` en standard y complex. Corre `dsh --profile headless --json -` con la tarea por stdin y `DSH_PERMISSION_MODE=workspace-write`: el sandbox del propio dsh (token restringido en Windows) confina las escrituras al workspace y, como el modo headless no tiene a quién pedir aprobación, toda escalada se rechaza. Usa el login de la app (`~/.dsh/.credentials.yaml`, provider `deepseek-account`) o, sin login, `DEEPSEEK_API_KEY`; cobra contra el saldo de platform.deepseek.com (`Insufficient Balance` marca al agente como agotado). Al actualizar desde una versión anterior, `deepseek` se agrega una sola vez al final de tu `tier_order` (y `muse` al principio); después el orden es tuyo. En Windows, el sandbox de dsh necesita que tu usuario tenga control total explícito sobre el workspace: con solo derechos de dueño (típico en carpetas fuera del perfil, como `C:\proyectos`) su herramienta de shell falla con `SetNamedSecurityInfoW failed (Win32 5)` aunque las ediciones de archivos funcionen. Arreglo, una vez por árbol: `icacls C:\proyectos /grant <usuario>:(OI)(CI)F`.

Muse Code de Meta es el primer carril de todos los tiers del broker y usa el modelo predeterminado de tu cuenta. Se ejecuta en modo headless con `--no-foreign-personal-context` para no cargar reglas personales de Claude Code; en Windows, comprueba el sandbox con `muse sandbox windows check` y configúralo con `muse sandbox windows setup` si no está listo. Las ejecuciones headless pueden ser lentas; evita workspaces privados bajo `%USERPROFILE%\AppData`, donde el shell aislado de Muse puede quedarse colgado.

Cursor requiere `~/.cursor-rescue/cli-config.json`, generado por `/cursor:setup` del plugin `cursor-plugin-cc`; sus reglas `permissions.deny` prevalecen sobre `--force`. En el plan Free de Cursor solo funciona el modelo `auto`.

```bash
curl -sS -H "x-api-key: $KEY" -H "content-type: application/json" -d '{"task":"Genera tests para src/pagos.py (firmas abajo) ...","workspace":"C:/repos/miapp"}' http://localhost:8000/api/delegate/jobs
```

- El broker clasifica la tarea, recorre el orden de agentes de ese tier y salta los agotados, no instalados u ocupados. Si un intento muere por cuota (`429`, `out of credits`, `RESOURCE_EXHAUSTED`, el `stream was interrupted` de agy) marca al agente como `exhausted` hasta su reset (5 h, diario o semanal) y reintenta en el siguiente; Antigravity prueba primero su otro pool.
- Cada job corre como subproceso acotado (timeout, kill del árbol de procesos) dentro de un workspace de la lista blanca, vacía por defecto, así que nada corre hasta que la configures. Los flags de seguridad de cada CLI son fijos: claude `acceptEdits` sin `Task/Agent`, codex `workspace-write`, copilot con deny list de `rm`, `git push`, `reset`, `clean` y `checkout`, agy solo si existe tu deny list global. El log se sigue por SSE en `GET /api/delegate/jobs/{id}/stream`; al terminar, el job trae `files_touched`.
- Los CLIs autentican con sus propias sesiones: el hijo recibe un entorno mínimo sin claves del gateway ni `ANTHROPIC_BASE_URL`, para que un `claude` hijo no vuelva a entrar por bipolar.

`/v1` enruta solo entre providers HTTP; los agentes CLI reciben tareas por la API de jobs (un CLI trae su propio loop de herramientas y no puede devolver `tool_use` a Claude Code a mitad de turno).

**Puerta de calidad**: cada job pasa por verificación y revisión antes de darse por bueno:

- `verify`: lista de comandos (hasta 10) que corren sin shell dentro del workspace, con entorno mínimo y timeout. El primero que devuelva distinto de 0 cuenta como fallo, y un job cuya verificación falla nunca termina como `succeeded`.
- `review`: un "pensador" en solo lectura (de la cadena `thinkers`, por defecto `claude, codex`) revisa el diff del job más la salida de los checks y devuelve `approve`, `revise` o `reject`. El revisor nunca es el mismo trabajador.
- `revise` con revisiones restantes (`max_revisions`, por defecto 1): el trabajador vuelve a correr con los issues del revisor. `reject`, revisiones agotadas o verificación que sigue fallando: se **escala** al siguiente pensador disponible (o al siguiente carril del tier), que parte del estado que dejó el intento anterior — nunca se revierte nada.
- Sin pensadores disponibles, la revisión se salta (`skipped`): tener revisor no es requisito para aprobar.
- `allow_request_verify` (Agentes → ajustes, **apagado por defecto**): con él apagado, pedir `verify` por API o MCP se rechaza con 400.

**Cuentas por CLI**: `claude`, `codex` y `deepseek` admiten varias cuentas, una entrada más por cuenta con su propia carpeta de config bajo `<config_dir>/accounts/`. Agentes → **Agregar cuenta** crea la entrada (`<base>-<n>`, deshabilitada hasta que la habilites) y muestra los comandos de login; para Claude:

```
$env:CLAUDE_CONFIG_DIR='<carpeta-de-la-cuenta>'; claude
```

y dentro de Claude Code, `/login` con la cuenta que quieras asociar. Cuando una cuenta llega a su límite, el siguiente job usa la otra automáticamente; el reporter de statusline de cada cuenta informa a bipolar del uso real de 5 h y 7 días. **Cursor no admite cuentas**: su carpeta de config lleva la deny list del plugin y una cuenta la pisaría.

## Varias suscripciones de Claude Code: `bipolar-claude`

Si tienes dos (o más) suscripciones de Claude Code, `bipolar-claude` (en `scripts/`) las usa por turno y cae al proxy cuando todas están agotadas. Agrega la carpeta `scripts` del repo a tu PATH, o invócalo directo: `scripts\bipolar-claude.cmd` en Windows, `scripts/bipolar-claude.sh` en Linux/macOS. Necesita Python 3 en el PATH.

1. **Configuración recomendada**: deja tu `~/.claude` apuntando a bipolar (`ANTHROPIC_BASE_URL=http://localhost:8000`, caso de uso 1) y pon cada suscripción en su propia cuenta (`claude-2`, `claude-3`…). Así, `claude` a secas es el modo proxy transparente, y `bipolar-claude` usa primero las suscripciones.
2. **Crear las cuentas**: Agentes → **Agregar cuenta** (base `claude`), una por suscripción; luego haz login en cada una con `$env:CLAUDE_CONFIG_DIR='<carpeta>'; claude` y `/login` dentro. Habilítalas para que el broker también las use.
3. **Uso**: `bipolar-claude` consulta a bipolar qué cuenta tiene cupo y abre `claude` con `CLAUDE_CONFIG_DIR` apuntando a ella, pasando todos tus argumentos. Si todas están agotadas (o no hay cuentas), abre en modo proxy con `ANTHROPIC_BASE_URL` y tu API key solo para ese proceso. Si bipolar no responde, abre `claude` normal.

**Perfil espejo**: una cuenta recién creada estaría vacía (sin tus plugins, skills ni servidores MCP). En cada lanzamiento, `bipolar-claude` sincroniza desde tu `~/.claude`: junctions (`mklink /J`) para `skills`, `agents`, `commands`, `rules`, `hooks` y `plugins`, copia de `CLAUDE.md`, `settings.json` sin las variables del proxy (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`) y con el statusline apuntando al reporter de uso, y fusión de tus `mcpServers`. Nunca copia credenciales, proyectos ni historial. `--bc-no-mirror` lo salta.

**Flags propios** (llevan el prefijo `--bc-`; el resto pasa intacto a `claude`):

- `--bc-dry-run`: muestra qué haría (modo, variables y argumentos) sin lanzar nada. Para verificar: `bipolar-claude --bc-dry-run`.
- `--bc-continue` (experimental): retoma en la cuenta actual la sesión más reciente de este directorio, copiando su transcript desde la cuenta usada la última vez (`claude --resume <id>`). Si no hay sesión previa, abre una nueva y avisa. Cambiar de cuenta dentro de una sesión no es posible: cada carpeta de config es una isla.

**Por qué las suscripciones no van detrás del proxy**: los términos de Anthropic prohíben usar las credenciales OAuth de la suscripción fuera de Claude Code, así que bipolar nunca las toca: solo comprueba que el login existe. Y técnicamente no encajaría: un `claude -p` ejecuta sus propias herramientas y responde texto, no puede devolverle bloques `tool_use` al cliente de afuera. Por eso las suscripciones solo se usan como CLIs (broker headless y lanzador).

---

## Extras

- **Compresión semántica** (Settings, opt-in): al acercarse al límite de contexto, resume el historial viejo con el provider activo en vez de truncarlo.
- **Uso y costos**: pestaña Usage — tokens, costo estimado por provider/modelo.
- **Chat integrado** con streaming y visión.
- **Token de Copilot** se auto-refresca en background.

## Deploy con Docker (servidor / headless)

```bash
docker compose up -d --build
```

Gateway + UI en `:8000`, config en el volumen `bipolar-data`. `llama-server` NO va dentro (necesita las GPUs del host): córrelo en el host y apunta el provider llamacpp a `http://host.docker.internal:4002`.

## Arranque con Windows (sin iniciar sesión)

Para que bipolar arranque al encender el PC, sin esperar a que alguien inicie sesión, y para que lo alcance un servicio dentro de WSL:

```powershell
# PowerShell como administrador; pide tu contraseña de Windows una vez
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
```

El script:
- Registra la tarea `bipolar-code backend` con dos disparadores, al encender y al iniciar sesión. La tarea corre con tu cuenta y tu contraseña, así las credenciales del llavero de Windows (gh, Copilot) funcionan. Se reinicia hasta 3 veces si falla.
- Crea una regla de firewall que **bloquea el puerto 8000 salvo** desde loopback y la red de WSL (`172.16.0.0/12`).
- Reinicia bipolar con la tarea nueva.

Para que WSL lo alcance, el lanzador (`C:\litellm\start-bipolar.ps1`) debe usar `--host 0.0.0.0`. Desde WSL, Windows es la puerta de enlace: `curl http://$(ip route show default | awk '{print $3}'):8000/api/health`.

## Seguridad

- Todo (`/api/*`, `/v1/*` y `/mcp`) exige tu API key — nada queda anónimo en la LAN.
- Rate limiting por IP configurable (`RATE_LIMIT_RPM`). `X-Forwarded-For` se ignora salvo que la conexión venga de un proxy listado en `TRUSTED_PROXIES`.
- El plano de control (`/api/*`) responde 403 a cualquier IP que no sea loopback, LAN privada (RFC1918/ULA), link-local o Tailscale (`100.64.0.0/10`), aunque traiga la key. Se mira solo la IP de la conexión, nunca `X-Forwarded-For`. `/v1/*` no se filtra. En Docker, si el reenvío de puertos no conserva la IP de origen (Docker Desktop, `userland-proxy`), las conexiones llegan con la IP privada del gateway y el filtro no distingue el tráfico público.
- Fuera de la LAN: VPN. No abras el puerto al internet público.
- `/mcp` es plano de control, igual que `/api`: exige la API key y respeta el mismo filtro de redes (`CONTROL_PLANE_ALLOWED_CIDRS`).
- Los comandos de `verify` corren en tu host sin shell y con entorno mínimo, pero **fuera de cualquier sandbox** y con tu usuario. Como mitigaciones, el workspace debe estar en la lista permitida y `allow_request_verify` viene **apagado** en instalaciones nuevas.
- Ojo con lo que implica: verificar es **ejecutar código que el agente delegado pudo modificar**. `pytest` carga el `conftest.py` del repo, `npm test` corre los scripts de `package.json`, y un script del propio repo puede haber sido reescrito por el agente. Los agentes trabajan dentro de su sandbox, pero la verificación corre afuera. Delega con `verify` solo en repos y agentes en los que confíes, y revisa el diff antes de ejecutar cualquier cosa por tu cuenta.

---

## Dev Setup (código fuente)

Requisitos: Python 3.11+, Node 20+.

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend (dev, proxy a :8000)
cd frontend
npm install
npm run dev        # http://localhost:5173

# Tests
cd backend && pytest
cd frontend && npm test
```

## Configuración (.env)

El backend lee `.env` del directorio de configuración (`C:\litellm\` / `~/.litellm/`, override con `LITELLM_CONFIG_DIR`). Variables principales:

| Variable | Para qué |
|---|---|
| `UI_API_KEY` | API key del gateway (se autogenera si falta) |
| `ANTHROPIC_API_KEY` | Provider Anthropic real |
| `GITHUB_OAUTH_TOKEN` | Auto-refresh del token de Copilot |
| `NVIDIA_NIM_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY` | Providers cloud |
| `HF_TOKEN` | Descargas de modelos gated en Hugging Face |
| `SEMANTIC_COMPRESSION` | `true` activa la compresión de contexto |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS` | Bot de Telegram |
| `RATE_LIMIT_RPM`, `ALLOWED_ORIGINS` | Endurecimiento de red |
| `TRUSTED_PROXIES` | Proxies inversos (IPs o CIDR, separados por coma) cuyo `X-Forwarded-For` se respeta en el rate limit; vacío = ninguno |
| `CONTROL_PLANE_ALLOWED_CIDRS` | Redes CIDR (separadas por coma) que pueden usar `/api/*`; reemplaza al default (loopback, LAN privada, link-local y Tailscale) |

Ver [`backend/.env.example`](backend/.env.example) para la lista completa.

## Stack

- **Frontend**: React 18 + Vite + TypeScript + Tailwind CSS + TanStack Query
- **Backend**: Python FastAPI + structlog + httpx + pydantic-settings
- **Inferencia local**: llama.cpp (`llama-server`, backend Vulkan) gestionado por la app
- **Providers cloud vía proxy**: LiteLLM

## License

MIT
