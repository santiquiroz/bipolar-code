# bipolar-code v2 — Design Spec

**Fecha:** 2026-04-29  
**Rama:** `feat/v2-providers-context-pricing-images`  
**Enfoque:** Extender wrapper LiteLLM (Enfoque A)  
**Inspiración:** Análisis comparativo con [free-claude-code](https://github.com/Alishahryar1/free-claude-code)

---

## Resumen ejecutivo

Esta actualización agrega 4 nuevos providers (NVIDIA NIM, OpenRouter, DeepSeek, Ollama), implementa context management automático para evitar errores de ventana de contexto, introduce un sistema completo de pricing y tracking de costos, y mejora el soporte de imágenes en el chat (fix de bug + drag & drop + Ctrl+V + URL).

---

## 1. Arquitectura

### Flujo de request actualizado

```
ANTES:
Browser → FastAPI (8000) → LiteLLM (4001) → [Copilot | Anthropic | LM Studio]

DESPUÉS:
Browser → FastAPI (8000)
              ├─ ContextGuard middleware   ← NUEVO
              ├─ TokenCounter service      ← NUEVO
              ├─ PricingRegistry           ← NUEVO
              ├─ UsageTracker (SQLite)     ← NUEVO
              └─ LiteLLM (4001) → [Copilot | Anthropic | LM Studio | NVIDIA NIM | OpenRouter | DeepSeek | Ollama]
```

### Módulos nuevos en `backend/app/`

| Módulo | Responsabilidad |
|---|---|
| `services/token_service.py` | Estima tokens con tiktoken `cl100k_base`. Determina si un request supera el context window del modelo activo. |
| `middleware/context_guard.py` | Intercepta `POST /v1/chat/completions`. Trunca mensajes antiguos si tokens >= 90% del límite. Agrega header `X-Context-Usage`. |
| `services/pricing_service.py` | Lee `data/pricing.json`. Calcula costo estimado por request (USD). Soporta precios dinámicos de OpenRouter. |
| `services/usage_tracker.py` | Persiste requests en SQLite (`usage.db`). Lee tokens del evento `message_delta` del stream de LiteLLM. |
| `data/pricing.json` | Base de datos de precios estática versionada. Cubre Anthropic, NVIDIA NIM, OpenRouter, DeepSeek. Actualizable sin tocar código. |
| `data/model_capabilities.json` | Mapa `model_id → { context_window, supports_vision, supports_tools }`. Reemplaza toda lista hardcodeada en frontend y backend. |

### Nuevos endpoints

```
GET  /api/usage/history?provider=&model=&from=&to=&limit=
GET  /api/usage/summary?period=day|week|month
GET  /api/pricing/models
GET  /api/pricing/model/{id}
GET  /api/models/capabilities
POST /api/providers/nvidia_nim/verify-key
```

---

## 2. Providers

### Providers nuevos (presets en `providers.json`)

| Provider | LiteLLM prefix | Auth env var | Modelo por defecto | Context window |
|---|---|---|---|---|
| NVIDIA NIM | `openai/` | `NVIDIA_NIM_API_KEY` | `meta/llama-3.1-70b-instruct` | 128k |
| OpenRouter | `openrouter/` | `OPENROUTER_API_KEY` | `meta-llama/llama-3.1-8b-instruct:free` | Dinámico |
| DeepSeek | `deepseek/` | `DEEPSEEK_API_KEY` | `deepseek-chat` | 64k |
| Ollama | `openai/` | ninguna | `llama3.2` | Dinámico |

Configuración JSON completa de cada provider:

```json
[
  {
    "id": "nvidia_nim",
    "name": "NVIDIA NIM",
    "api_base": "https://integrate.api.nvidia.com/v1",
    "litellm_prefix": "openai",
    "auth_env_var": "NVIDIA_NIM_API_KEY",
    "models_endpoint": "https://integrate.api.nvidia.com/v1/models",
    "active_model": "meta/llama-3.1-70b-instruct",
    "drop_params": true,
    "model_info": { "supports_vision": false }
  },
  {
    "id": "openrouter",
    "name": "OpenRouter",
    "api_base": "https://openrouter.ai/api/v1",
    "litellm_prefix": "openrouter",
    "auth_env_var": "OPENROUTER_API_KEY",
    "models_endpoint": "https://openrouter.ai/api/v1/models",
    "active_model": "meta-llama/llama-3.1-8b-instruct:free",
    "drop_params": true
  },
  {
    "id": "deepseek",
    "name": "DeepSeek",
    "api_base": "https://api.deepseek.com/v1",
    "litellm_prefix": "deepseek",
    "auth_env_var": "DEEPSEEK_API_KEY",
    "active_model": "deepseek-chat",
    "drop_params": true
  },
  {
    "id": "ollama",
    "name": "Ollama (Local)",
    "api_base": "http://localhost:11434",
    "litellm_prefix": "openai",
    "auth_env_var": "",
    "models_endpoint": "http://localhost:11434/api/tags",
    "active_model": "llama3.2",
    "drop_params": true
  }
]
```

### Wizard de onboarding NVIDIA NIM

Se activa automáticamente cuando el usuario intenta activar NVIDIA NIM sin `NVIDIA_NIM_API_KEY` configurada. Modal de 3 pasos:

**Paso 1 — Instrucciones de cuenta:**
- Texto explicativo sobre créditos gratuitos ($200 USD para cuentas nuevas)
- Botón "Abrir build.nvidia.com" (abre en el navegador del sistema)
- Pasos: crear cuenta → ir a "API Keys" → crear nueva key

**Paso 2 — Ingresar API key:**
- Input con toggle de visibilidad (mostrar/ocultar)
- Botón "Verificar y guardar" → llama a `POST /api/providers/nvidia_nim/verify-key`
- El endpoint hace un request de prueba a NIM antes de guardar la key

**Paso 3 — Confirmación:**
- Muestra cantidad de modelos disponibles
- Recordatorio de créditos gratuitos
- Botón "Activar provider"

### Guía manual de creación de cuenta NVIDIA NIM

1. Ir a [build.nvidia.com](https://build.nvidia.com)
2. Clic en "Sign Up" (esquina superior derecha)
3. Registrarse con email o cuenta Google/GitHub
4. Verificar email
5. En el dashboard, navegar a **API Keys** en el menú lateral
6. Clic en **"+ Generate API Key"**
7. Nombrar la key (ej: "bipolar-code") y copiarla
8. Las cuentas nuevas reciben **$200 USD en créditos** para usar con cualquier modelo NIM
9. Pegar la key en el wizard de bipolar-code (comienza con `nvapi-`)

### Fetch dinámico de modelos por provider

| Provider | Endpoint | Respuesta |
|---|---|---|
| NVIDIA NIM | `GET /v1/models` | Array de model objects |
| OpenRouter | `GET /v1/models` | Array con `id`, `pricing.prompt`, `pricing.completion`, `context_length` |
| DeepSeek | Lista estática en backend | No tiene endpoint público |
| Ollama | `GET /api/tags` | Array de `{name, size}` |

Los precios de OpenRouter se cachean en memoria en `pricing_service.py` (no se escriben a disco). Al arrancar, `pricing_service` carga `pricing.json`; cuando se hace fetch de modelos de OpenRouter, los precios recibidos sobreescriben el caché en memoria solo para esa sesión.

---

## 3. Context Management

### Problema

Claude Code envía conversaciones completas en cada request. Si el historial supera la ventana del modelo backend, LiteLLM retorna 400/413 y Claude Code falla silenciosamente.

### `ContextGuard` middleware

Intercepta todo `POST /v1/chat/completions` antes de llegar a LiteLLM:

```
1. Leer model_capabilities.json → context_window del modelo activo
2. Contar tokens del request completo (system + messages + tools)
3. Si tokens < 90% del límite → pasar sin cambios
4. Si tokens >= 90% → truncar (lógica abajo)
5. Agregar header X-Context-Usage: "{used}/{total} tokens ({pct}%)" a la respuesta
```

### Lógica de truncación

```python
def truncate_messages(messages, max_tokens, model):
    system = [m for m in messages if m["role"] == "system"]
    last_user = messages[-1]

    reserved = count_tokens(system + [last_user]) + 1000  # margen para respuesta
    budget = max_tokens - reserved

    history = [m for m in messages[:-1] if m["role"] != "system"]
    kept = []
    for msg in reversed(history):
        t = count_tokens([msg])
        if budget - t < 0:
            break
        kept.insert(0, msg)
        budget -= t

    if len(kept) < len(history):
        notice = {
            "role": "system",
            "content": "[Nota: historial anterior omitido por límite de contexto]"
        }
        return system + [notice] + kept + [last_user]

    return system + kept + [last_user]
```

**Invariantes garantizados:**
- El system prompt siempre se preserva completo
- El último mensaje del usuario siempre se preserva completo
- Se descarta desde los mensajes más antiguos hacia los más recientes
- Si se descartó algo, se inserta aviso explícito al modelo

### `data/model_capabilities.json`

Fuente única de verdad para capacidades de modelo (reemplaza listas hardcodeadas):

```json
{
  "claude-sonnet-4-6":                        { "context_window": 200000, "supports_vision": true,  "supports_tools": true },
  "claude-opus-4-6":                          { "context_window": 200000, "supports_vision": true,  "supports_tools": true },
  "claude-haiku-4-5":                         { "context_window": 200000, "supports_vision": true,  "supports_tools": true },
  "gpt-4o":                                   { "context_window": 128000, "supports_vision": true,  "supports_tools": true },
  "meta/llama-3.1-70b-instruct":              { "context_window": 128000, "supports_vision": false, "supports_tools": true },
  "meta/llama-3.2-90b-vision-instruct":       { "context_window": 128000, "supports_vision": true,  "supports_tools": true },
  "meta/llama-3.1-8b-instruct":               { "context_window": 128000, "supports_vision": false, "supports_tools": true },
  "mistralai/mistral-large-2-instruct":        { "context_window": 128000, "supports_vision": false, "supports_tools": true },
  "nvidia/llama-3.1-nemotron-70b-instruct":   { "context_window": 128000, "supports_vision": false, "supports_tools": true },
  "deepseek-chat":                            { "context_window": 64000,  "supports_vision": false, "supports_tools": true },
  "deepseek-reasoner":                        { "context_window": 64000,  "supports_vision": false, "supports_tools": false },
  "meta-llama/llama-3.1-8b-instruct:free":    { "context_window": 32768,  "supports_vision": false, "supports_tools": true },
  "llama3.2":                                 { "context_window": 128000, "supports_vision": false, "supports_tools": true },
  "openrouter/*":                             { "context_window": 32000,  "supports_vision": false, "supports_tools": true },
  "__default__":                              { "context_window": 8192,   "supports_vision": false, "supports_tools": true }
}
```

### Indicador de contexto en la UI

Dashboard y Chat muestran una barra de progreso de uso de contexto:

```
Modelo activo: claude-sonnet-4-6
[████████████░░░░░░░░] 45k / 200k tokens (22%)
```

- **Verde:** < 70% usado
- **Amarillo:** 70–90% usado
- **Rojo + badge "Truncando automáticamente":** > 90%

Fuente de datos: header `X-Context-Usage` leído desde los headers de la respuesta de chat en `Chat.tsx`. El componente actualiza un estado local `contextUsage` después de cada respuesta completada y lo muestra en el header del chat. El Dashboard muestra el último valor conocido (persistido en memoria en el frontend).

---

## 4. Pricing & Cost Tracking

### `data/pricing.json`

```json
{
  "version": "2026-04-29",
  "providers": {
    "anthropic": {
      "claude-sonnet-4-6":  { "input": 3.00,  "output": 15.00, "cache_read": 0.30,  "cache_write": 3.75 },
      "claude-opus-4-6":    { "input": 15.00, "output": 75.00, "cache_read": 1.50,  "cache_write": 18.75 },
      "claude-haiku-4-5":   { "input": 0.80,  "output": 4.00,  "cache_read": 0.08,  "cache_write": 1.00 }
    },
    "nvidia_nim": {
      "meta/llama-3.1-70b-instruct":           { "input": 0.35, "output": 0.40 },
      "meta/llama-3.2-90b-vision-instruct":    { "input": 0.35, "output": 0.40 },
      "mistralai/mistral-large-2-instruct":     { "input": 2.00, "output": 6.00 },
      "nvidia/llama-3.1-nemotron-70b-instruct": { "input": 0.35, "output": 0.40 }
    },
    "deepseek": {
      "deepseek-chat":     { "input": 0.27, "output": 1.10 },
      "deepseek-reasoner": { "input": 0.55, "output": 2.19 }
    },
    "openrouter": {
      "__dynamic__": true,
      "__note__": "Precios cargados dinámicamente desde /v1/models"
    },
    "copilot": {
      "__flat_rate__": true,
      "__note__": "Incluido en suscripción GitHub Copilot — sin costo por token"
    },
    "lmstudio": {
      "__free__": true,
      "__note__": "Local — sin costo"
    },
    "ollama": {
      "__free__": true,
      "__note__": "Local — sin costo"
    }
  }
}
```

Precios en USD por millón de tokens (input/output). `cache_read` y `cache_write` solo aplican donde el provider soporte prompt caching.

### `services/usage_tracker.py` — SQLite

Schema de la tabla `requests`:

```sql
CREATE TABLE requests (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp     TEXT    NOT NULL,  -- ISO 8601
  provider_id   TEXT    NOT NULL,
  model         TEXT    NOT NULL,
  input_tokens  INTEGER,
  output_tokens INTEGER,
  cost_usd      REAL,
  truncated     BOOLEAN DEFAULT 0  -- 1 si ContextGuard truncó el request
);
```

El tracker es invocado desde `chat.py` al finalizar cada stream. `chat.py` acumula los tokens del evento `message_delta` (campo `usage`) y al detectar el evento `message_stop` llama a `usage_tracker.record(provider_id, model, input_tokens, output_tokens)`. El tracker calcula el costo con `pricing_service` y persiste en SQLite.

### UI: 3 puntos de precio

**1. ModelPicker — precio al seleccionar modelo:**

```
Modelo activo
┌──────────────────────────────────────┐
│ claude-sonnet-4-6               ▼   │
└──────────────────────────────────────┘
  Input: $3.00 / 1M   Output: $15.00 / 1M

Dropdown:
  ● claude-sonnet-4-6       $3 / $15 per 1M
  ○ claude-opus-4-6         $15 / $75 per 1M
  ○ gpt-4o                  $5 / $15 per 1M
  ○ llama-3.1-70b [NIM]     $0.35 / $0.40 per 1M
  ○ deepseek-chat            $0.27 / $1.10 per 1M
  ○ llama3.2 [Local]        GRATIS
```

Providers gratuitos (Copilot, Ollama, LM Studio) muestran badge `GRATIS`.

**2. Chat header — contador de costo de sesión:**

```
Chat                    Sesión: $0.023 · 8.2k tokens ▓▓▓░░
```

Actualizado en tiempo real después de cada respuesta completada.

**3. Usage Dashboard — rediseñado:**

```
Uso y Costos                          [Día] [Semana] [Mes]

┌──────────────┬──────────────┬──────────────┬──────────────┐
│ Total gastado│   Requests   │  Tokens in   │  Tokens out  │
│    $2.47     │     143      │    1.2M      │    287k      │
└──────────────┴──────────────┴──────────────┴──────────────┘

Gasto por día (Recharts — barras apiladas por provider)
[gráfica]

Por provider          Requests    Tokens        Costo
● Anthropic             89         890k         $2.31
● NVIDIA NIM            31         198k         $0.12
● OpenRouter            23         145k         $0.04
● DeepSeek               0           0k         $0.00
● Copilot                0           0k         GRATIS
● Ollama                 0           0k         GRATIS

Últimos requests
14:32  claude-sonnet-4-6        2.1k / 487 out    $0.014
14:28  deepseek-chat              890 / 203 out   $0.001
14:15  llama-3.1-70b-instruct   1.2k / 891 out    $0.000
```

Implementado con **Recharts** (compatible con React + Tailwind, sin dependencias extra pesadas).

---

## 5. Image Support

### Bug fix: falso negativo de validación

**Causa:** El frontend tiene una lista hardcodeada de modelos con soporte de visión. Si el modelo activo no está exactamente en esa lista, bloquea el upload aunque el modelo sí soporte imágenes.

**Fix:** Eliminar lista hardcodeada. El frontend llama a `GET /api/models/capabilities` (lee `model_capabilities.json`) y determina `supports_vision` dinámicamente.

Si `supports_vision === false`, el botón de imagen aparece deshabilitado con tooltip `"Este modelo no soporta imágenes"` — feedback preventivo en lugar de error post-envío.

### Nuevas modalidades de input de imágenes

**Drag & Drop:**
- El área completa del chat es una drop zone
- Al arrastrar una imagen encima: overlay semitransparente con borde dashed y texto "Suelta la imagen aquí"
- Al soltar: convierte a base64 y agrega thumbnail al input

**Paste desde portapapeles (Ctrl+V / Cmd+V):**
- Event listener `paste` en el textarea
- Detecta `item.type.startsWith('image/')` en `clipboardData.items`
- Funciona con screenshots (Win+Shift+S, Cmd+Shift+4) y copiar imagen de cualquier fuente

**URL de imagen:**
- Botón `[🔗 URL]` en la toolbar del chat
- Popover con input de URL
- La URL se envía como `source.type: "url"` (sin convertir a base64 — más eficiente para imágenes grandes)
- Providers soportados para URL: Anthropic, OpenRouter, NVIDIA NIM con modelos vision

### Preview de imágenes adjuntas

```
┌──────────────────────────────────────────────────────────┐
│  ┌──────┐ ┌──────┐                                       │
│  │ img1 │×│ img2 │×   ← thumbnails removibles (48x48)   │
│  └──────┘ └──────┘                                       │
│                                                          │
│  Escribe tu mensaje...                                   │
│                                                          │
│  [📎 Archivo]  [🔗 URL]                    [Enviar ↵]   │
└──────────────────────────────────────────────────────────┘
```

### Validación

| Criterio | Límite |
|---|---|
| Tipos aceptados | `image/jpeg`, `image/png`, `image/gif`, `image/webp` |
| Tamaño máximo | 5 MB por imagen |
| Máximo imágenes por mensaje | 5 (límite Anthropic) |
| Validación | Inmediata en el frontend, toast de error sin enviar al backend |

---

## 6. Rama Git

```
git checkout -b feat/v2-providers-context-pricing-images
```

Secuencia de implementación recomendada (por dependencias):

1. `data/model_capabilities.json` y `data/pricing.json` — base de datos estática
2. `services/token_service.py` — cimientos del context management
3. `middleware/context_guard.py` — usa token_service
4. `services/pricing_service.py` — usa pricing.json
5. `services/usage_tracker.py` — usa pricing_service
6. Providers nuevos en `providers.json` — NVIDIA NIM, OpenRouter, DeepSeek, Ollama
7. Endpoints nuevos en `api/` — capabilities, pricing, usage/history
8. Frontend: fix imagen + ModelPicker con precios + Usage dashboard + Context bar
9. Frontend: Wizard NVIDIA NIM
10. Frontend: Drag & drop + Ctrl+V + URL para imágenes
11. Tests

---

## 7. Dependencias nuevas

### Backend

```
tiktoken>=0.7.0      # token counting
aiosqlite>=0.20.0    # async SQLite para usage_tracker
```

### Frontend

```
recharts             # gráficas Usage dashboard
```

---

## 8. Fuera de alcance (esta versión)

- Compresión semántica de conversaciones (resumir con LLM)
- Autenticación/login para la UI
- Deploy en servidor remoto (sigue siendo localhost)
- Modo Discord/Telegram bot (como free-claude-code)
- Streaming de logs de litellm en tiempo real en la UI
