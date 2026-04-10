# Bipolar Code

Web UI para gestionar un proxy [LiteLLM](https://github.com/BerriAI/litellm) — cambia backends, explora modelos disponibles, monitorea uso y enruta Claude Code a través del proxy con un clic.

## Quick Start (ejecutable)

1. Descarga el ejecutable de la [página de Releases](../../releases) para tu plataforma:
   - `bipolar-code-windows.exe` — Windows
   - `bipolar-code-linux` — Linux
   - `bipolar-code-macos` — macOS

2. Crea el directorio de configuración:
   - Windows: `C:\litellm\`
   - Linux/macOS: `~/.litellm/`

3. Copia [`backend/.env.example`](backend/.env.example) a ese directorio como `.env` y rellena tus API keys.

4. Instala `litellm` y verifica que está en el PATH:
   ```bash
   pip install litellm
   litellm --version
   ```

5. Ejecuta el binario:
   - Windows: doble clic en `bipolar-code-windows.exe`
   - Linux/macOS: `chmod +x bipolar-code-linux && ./bipolar-code-linux`

6. Abre [http://localhost:8000](http://localhost:8000)

---

## Dev Setup (código fuente)

### Requisitos

- Python 3.11+
- Node 20+
- `litellm` instalado y en PATH (`pip install litellm`)

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend (dev)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm test           # vitest
```

### Tests backend

```bash
cd backend
pytest
```

---

## Configuración

El backend lee el archivo `.env` desde el directorio de configuración:

| Plataforma | Ruta por defecto |
|---|---|
| Windows | `C:\litellm\.env` |
| Linux/macOS | `~/.litellm\.env` |

Puede sobreridarse con la variable de entorno `LITELLM_CONFIG_DIR`.

Ver [`backend/.env.example`](backend/.env.example) para todas las variables disponibles.

---

## Stack

- **Frontend**: React 18 + Vite + TypeScript + Tailwind CSS + TanStack Query
- **Backend**: Python FastAPI + structlog + httpx + pydantic-settings

## Features

- Estado del proxy en tiempo real con indicador de backend activo
- Cambio de proveedor con un clic (Copilot / Claude / LM Studio / cualquier OpenAI-compatible)
- Chat con streaming y soporte de visión (imágenes)
- Auto-refresco del token de GitHub Copilot en background
- Enrutamiento de Claude Code: cambia entre Anthropic directo y LiteLLM proxy
- Logs de uso y costo estimado

## License

MIT
