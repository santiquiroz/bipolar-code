# CLAUDE.md
## Approach
- Think before acting. Read existing files before writing code.
- Be concise in output but thorough in reasoning.
- Prefer editing over rewriting whole files.
- Do not re-read files you have already read unless the file may have changed.
- Test your code before declaring done.
- No sycophantic openers or closing fluff.
- Keep solutions simple and direct.
- User instructions always override this file.

## Commands

### Backend
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

pytest                          # all tests
pytest tests/test_proxy_service.py  # single test file
```

### Frontend
```bash
cd frontend
npm install
npm run dev     # http://localhost:5173 (proxies /api → localhost:8000)
npm test        # vitest
npm run build   # outputs to frontend/dist/
```

### Build release binary
```bash
cd frontend && npm ci && npm run build
cd backend && pip install pyinstaller
pyinstaller bipolar-code.spec   # run from repo root
```

## Architecture

**bipolar-code** is a web UI that manages a locally-running [LiteLLM](https://github.com/BerriAI/litellm) proxy. The backend controls which AI provider (Copilot, Anthropic, LM Studio…) is active by generating a YAML config and restarting the litellm process.

### Request flow

- **Dev**: Browser → Vite (5173) → `/api/*` proxied → FastAPI (8000) → litellm (4001) → provider
- **Prod/binary**: Browser → FastAPI (8000) serves the built `frontend/dist/` as an SPA, `/api/*` handled by routers

### Backend layout (`backend/app/`)

| Layer | Path | Responsibility |
|---|---|---|
| Config | `core/config.py` | Reads `.env` from config dir via pydantic-settings; config dir is `C:\litellm` (Win) / `~/.litellm` (other) |
| Services | `services/providers_service.py` | Provider CRUD, litellm config YAML generation, kill/start litellm subprocess |
| Services | `services/proxy_service.py` | Proxy health checks; Claude Code routing (writes `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY` to `~/.claude/settings.json` and Windows registry) |
| Services | `services/llamacpp_service.py` | llama-server local gestionado: detección de GPUs, tensor-split auto por VRAM libre, start/stop con PID file |
| API | `api/` | Thin FastAPI routers — each delegates to the matching `*_service.py` |
| Models | `models/` | Pydantic schemas (`schemas.py`) and provider entity (`provider.py`) |
| Startup | `main.py` | Mounts `frontend/dist/` as SPA fallback; spawns Copilot token auto-refresh background loop |
| Logging | `core/logging.py` | structlog setup; use `get_logger(__name__)` throughout |

**Provider registry** persists in `{config_dir}/providers.json`. Built-in providers include `copilot`, `anthropic`, `lmstudio`, `nvidia_nim`, `openrouter`, `deepseek`, `ollama` and `llamacpp`. litellm always exposes the aliases `claude-sonnet-4-6`, `claude-opus-4-6`, `gpt-4o` regardless of the active backend. Providers with `anthropic_native: true` (llama-server, LM Studio ≥0.4.1, Ollama 2026+) receive `/v1/messages` verbatim — no litellm, no OAI translation. The `llamacpp` provider spawns a managed local `llama-server` (Vulkan multi-GPU, port 4002) via `/api/llamacpp/*`.

**Platform guards**: `providers_service._start_litellm` uses PowerShell on Windows and `subprocess.Popen(start_new_session=True)` on Linux/macOS. `proxy_service._set_user_env` writes the Windows registry only on `sys.platform == "win32"`; it always writes `~/.claude/settings.json`.

### Frontend layout (`frontend/src/`)

- `App.tsx` — router root; six routes wrapped in `Layout`
- `pages/` — one component per route (Dashboard, Chat, Models, Usage, Providers, Settings)
- `services/` — axios wrappers for `/api/*` endpoints
- `hooks/` — TanStack Query hooks consumed by pages
- `components/` — shared UI primitives (Button, Card, Badge, Spinner, ModelPicker, etc.)

### Scripts (`scripts/`)

- `get-copilot-token.ps1` — obtiene manualmente un token OAuth de GitHub Copilot (útil para diagnóstico)

### Settings / env file

The backend reads `.env` from the config dir at startup. `get_settings()` is `@lru_cache`'d; call `get_settings.cache_clear()` after writing a new token to pick up the change in the same process.

# Repository Instructions for Commit Messages
All commits in this repository must follow a structured format that groups technical changes by architecture layer.
The work item title and URL are variables and must be provided for each commit.
---
## Commit Structure
Historia técnica <WORK_ITEM_ID>: <WORK_ITEM_TITLE>
<WORK_ITEM_URL>
Dominio:
- Describe changes related to domain logic such as entities, value objects, constants, domain services, validations or domain gateways.
Aplicación:
- Describe use cases, application services, configuration of use cases, orchestration logic or application-level configuration.
Infraestructura:
- Describe adapters, repositories, external integrations, framework configurations or infrastructure components.
Configuración:
- Describe configuration updates such as application.yml, build.gradle, environment variables, actuator configuration, security settings or deployment configuration.
Pruebas:
- Describe unit tests, integration tests, coverage improvements or test fixes.
Cobertura global del proyecto: <percentage if available>
---
## Rules
- Always group changes by architecture layer.
- Only include sections where changes occurred.
- Use bullet points.
- Be explicit about technical implementation.
- Avoid generic descriptions like "fix stuff".
- Mention new classes, adapters, configurations or modules created.
- Mention when tests are added or modified.
- Commit messages must be written in Spanish.
---
## Layer Mapping (Project Structure)
Infer the section based on the modified directories:
- `backend/app/services/` → Dominio
- `backend/app/api/` → Aplicación
- `backend/app/models/` → Dominio
- `backend/app/core/` → Infraestructura
- `frontend/src/` → Infraestructura
- `requirements.txt`, `vite.config.ts`, `bipolar-code.spec`, `.env`, config files → Configuración
- `scripts/` → Configuración
- `backend/tests/`, `frontend/src/**/*.test.*` → Pruebas