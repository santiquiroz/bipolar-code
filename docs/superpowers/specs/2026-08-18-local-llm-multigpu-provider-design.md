# Diseño: Proveedor LLM local multi-GPU (llama.cpp gestionado) + passthrough Anthropic nativo

Fecha: 2026-08-18
Estado: aprobado por autonomía delegada ("tienes toda la libertad") — pendiente de revisión posterior del usuario.

## Contexto

El usuario quiere que bipolar-code reemplace correctamente el backend de Claude Code en VS Code, sirviendo un modelo local grande sobre **dos GPUs AMD simultáneas** (Radeon AI PRO R9700 32GB + RX 7800 XT 16GB, ~48GB VRAM combinados) en este PC, y consumible desde PCs externos en la LAN.

Investigación (last30days, 2026-08-18, guardada en `~/Documents/Last30Days/running-large-local-llms-on-dual-amd-gpus-r9700-and-rx-7800-xt-for-agentic-coding-raw-v3.md`):

- **llama.cpp `llama-server` ya habla la Anthropic Messages API (`/v1/messages`) de forma nativa** (igual que Ollama desde enero 2026 y LM Studio ≥0.4.1). Claude Code funciona contra estos servidores con solo `ANTHROPIC_BASE_URL`, sin proxy de traducción.
- **Vulkan es la ruta multi-GPU heterogénea** (RDNA4 gfx1201 + RDNA3 gfx1101 mezcladas): `--split-mode layer` + `--tensor-split` manual proporcional. No hay P2P entre GPUs distintas; cada handoff paga GPU→PCIe→RAM→PCIe (por eso layer-split, no row-split).
- En R9700, RADV/Vulkan gana a ROCm en la mayoría de cargas llama.cpp (Phoronix); en Windows el backend Vulkan es la opción estable para ambas GPUs.
- **Modelos objetivo para el tier 48GB**: Qwen3-Coder-Next 80B-A3B (~35-40GB Q4, #1 SWE-bench local), GLM-series, Qwen3-Coder-30B (cabe en la R9700 sola). GLM-4.5-Air 4bit (~59GB) no cabe.
- Comunidad activa haciendo exactamente esto: r/LocalLLaMA "2× Radeon R9700 for Local AI" (52 comentarios), r/ROCm Qwen3.8-27B en R9700 (77 pts).

## Decisiones (tomadas en autonomía; revisar si algo no cuadra)

| # | Decisión | Justificación |
|---|---|---|
| D1 | Runtime local = **llama.cpp `llama-server` gestionado por bipolar-code** (spawn/kill/status, como ya se hace con litellm) | Único runtime con Vulkan hetero multi-GPU en Windows + `/v1/messages` nativo |
| D2 | Nuevo flag `anthropic_native` en `Provider`: si está activo, `/v1/messages` reenvía el body Anthropic **verbatim** al provider (tercer camino: ni litellm, ni traducción OAI) | Fidelidad total de tool-use/imágenes/streaming; menos código en el hot path |
| D3 | `--tensor-split` **auto proporcional a VRAM libre detectada** por GPU (parse de `llama-server --list-devices`), con override manual | Aprendizaje Upflow: admisión por capacidad real medida; ratios relativos, nunca constantes absolutas sobre magnitudes variables |
| D4 | Chequeo de ajuste (tamaño GGUF + estimación KV vs VRAM libre) = **warning, no bloqueo** | Aprendizaje Upflow: un clasificador con fallback nunca sirve como gate |
| D5 | Antes de matar `llama-server`, consultar in-flight (`/health`, slots); si hay trabajo activo, requerir `force=true` | Aprendizaje: "check active job before killing server" (corté un job RIFE real por no hacerlo) |
| D6 | Alcance = **single-box multi-GPU**. Distribuido entre PCs (llama.cpp RPC) → backlog v3. Descarga de modelos HF → backlog v3 (el usuario apunta a un GGUF local) | YAGNI; el caso externo-PC se cubre consumiendo bipolar-code por LAN |
| D7 | PCs externos: ya cubierto por `host=0.0.0.0` + auth por API key existentes; se añade panel "Conexión externa" en Settings con snippet copy-paste (`ANTHROPIC_BASE_URL` + key) | Todo el mecanismo ya existe en la rama v2; solo falta descubribilidad |

## Arquitectura

### Backend

1. **`models/provider.py`** — dos campos nuevos:
   - `anthropic_native: bool = False` — el provider expone `/v1/messages` nativo.
   - `local_launch: dict = {}` — config de lanzamiento para providers locales gestionados: `{exe_path, model_path, ctx_size, split_mode, tensor_split ("auto" | [float]), ngl, extra_args}`.

2. **`services/llamacpp_service.py`** (nuevo, ~250 líneas) — espejo del patrón litellm:
   - `list_devices()` → parsea `llama-server --list-devices`: `[{index, backend, name, vram_total_mib, vram_free_mib}]`.
   - `compute_tensor_split(devices)` → ratios proporcionales a VRAM libre.
   - `estimate_fit(model_path, ctx_size, devices)` → `{fits: bool, needed_mib, available_mib}` (tamaño archivo × 1.15 + KV aprox; solo warning).
   - `start_llamacpp(provider)` / `stop_llamacpp(force)` / `get_status()` — PID file `llamacpp.pid`, logs `llamacpp-{out,err}.log`, kill por PID + fallback cmdline, chequeo in-flight antes de matar.
   - Puerto derivado del `api_base` del provider (default `http://127.0.0.1:4002`).
   - Flags generados: `--model`, `--ctx-size`, `--n-gpu-layers`, `--split-mode layer`, `--tensor-split`, `--jinja`, `--host`, `--port`.

3. **`api/llamacpp.py`** (nuevo router, thin): `GET /api/llamacpp/devices`, `GET /api/llamacpp/status`, `POST /api/llamacpp/start`, `POST /api/llamacpp/stop?force=`.

4. **`api/messages.py`** — rama nueva al inicio de `messages_passthrough`: si `active.anthropic_native` → reescribir `body["model"] = active.active_model` y reenviar streaming/non-streaming a `{api_base}/v1/messages` sin transformar. Registro de usage igual que hoy (el response Anthropic trae `usage`).

5. **`providers_service._DEFAULTS`** — nueva entrada `llamacpp` (`api_base=http://127.0.0.1:4002`, `anthropic_native=True`, `litellm_prefix="openai"` como fallback si algún flujo pasa por litellm). Switch a este provider **no** reinicia litellm si el server local ya está arriba; el arranque de llama-server es explícito vía UI (los arranques cargan 30-40GB a VRAM, no deben ser efecto colateral).

### Frontend

- `types/provider.ts`: `anthropic_native`, `local_launch`, tipos de devices/status.
- `services/llamacpp.ts` + `hooks/useLlamaCpp.ts` (TanStack Query).
- `components/LlamaCppPanel.tsx`: visible en Providers cuando `provider.id === "llamacpp"` — lista de GPUs con VRAM, path del GGUF, ctx, tensor-split auto/manual, botones Start/Stop, estado y warning de ajuste.
- `pages/Settings.tsx`: sección "Conexión externa" — muestra IP LAN + puerto + `ui_api_key` como snippet `ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY` copy-paste para otros PCs.

### Flujo de request (provider llamacpp activo)

```
Claude Code (este PC u otro) → FastAPI :8000 /v1/messages (auth API key)
  → passthrough verbatim → llama-server :4002 /v1/messages (Vulkan, 2 GPUs tensor-split)
  → SSE de vuelta sin transformar
```

## Manejo de errores

- llama-server caído + provider llamacpp activo → 503 con mensaje accionable ("Inicia el servidor local desde Providers").
- `--list-devices` falla o exe no encontrado → devices=[], UI muestra cómo instalar llama.cpp (release Vulkan de GitHub) y campo exe_path.
- Kill con in-flight sin `force` → 409 con detalle de slots ocupados.

## Testing

- `test_llamacpp_service.py`: parse de `--list-devices` (fixtures de output real), ratios de tensor-split (proporcionalidad, suma=1, redondeo), estimate_fit en límites, generación de cmdline.
- `test_messages_native_passthrough.py`: rama nativa reenvía body verbatim con model reescrito, streaming SSE se retransmite, usage se registra, 503 si server caído.
- Existentes de bypass no deben romperse.

## Fuera de alcance (backlog v3)

- llama.cpp RPC multi-host (juntar GPUs de PCs distintos).
- Descarga/gestión de modelos GGUF desde HF.
- Auto-arranque de llama-server al boot.
