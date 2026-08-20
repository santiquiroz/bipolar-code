# Bipolar Code

Tu gateway personal de LLMs, self-hosted. Una sola app que:

- **Sirve modelos locales grandes** con llama.cpp gestionado (multi-GPU AMD/NVIDIA vía Vulkan, tensor-split automático, descarga de modelos desde Hugging Face en la UI).
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

## Caso de uso 5 — Routing por escenario + failover

Providers → **Routing por escenario**. Reglas primer-match-gana sobre el nombre de modelo que pide el cliente:

| Patrón | Min tokens | Provider destino | Para qué |
|---|---|---|---|
| `haiku` | 0 | llamacpp → GGUF chico | Background de Claude Code (gratis y rápido) |
| `opus` | 0 | Anthropic real | Solo razonamiento pesado |
| *(vacío)* | 60000 | provider con contexto largo | Prompts gigantes |
| *(sin match)* | — | provider activo | Todo lo demás |

**Failover** (mismo panel): lista de providers de respaldo — si el destino local no responde, el request cae al primero alcanzable. Ej: `copilot, anthropic` = si apagaste llama-server, todo sigue funcionando solo.

## Caso de uso 6 — Bot de Telegram

Chatea con tu stack desde el teléfono. En Settings (o el `.env`):

```
TELEGRAM_BOT_TOKEN=<token de @BotFather>
TELEGRAM_ALLOWED_CHAT_IDS=123456789
```

Allowlist vacía = bot inerte (default seguro). El bot responde con el provider activo/ruteado.

## Caso de uso 7 — Delegar tareas desde Claude Code (plugin)

[bipolar-plugin-cc](https://github.com/santiquiroz/bipolar-plugin-cc): subagente `bipolar-rescue` que lanza un Claude Code headless contra tu modelo local — delega tareas mecánicas-medias (renames, specs, boilerplate) gratis y desde cualquier PC de la LAN:

```
/plugin marketplace add santiquiroz/bipolar-plugin-cc
/plugin install bipolar@bipolar-plugin-cc
/bipolar:setup http://<ip>:8000 <api-key>
```

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

## Seguridad

- Todo (`/api/*` y `/v1/*`) exige tu API key — nada queda anónimo en la LAN.
- Rate limiting por IP configurable (`RATE_LIMIT_RPM`).
- Fuera de la LAN: VPN. No abras el puerto al internet público.

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

Ver [`backend/.env.example`](backend/.env.example) para la lista completa.

## Stack

- **Frontend**: React 18 + Vite + TypeScript + Tailwind CSS + TanStack Query
- **Backend**: Python FastAPI + structlog + httpx + pydantic-settings
- **Inferencia local**: llama.cpp (`llama-server`, backend Vulkan) gestionado por la app
- **Providers cloud vía proxy**: LiteLLM

## License

MIT
