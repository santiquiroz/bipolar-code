# Bipolar Code

Web UI for managing a [LiteLLM](https://github.com/BerriAI/litellm) proxy — switch backends, browse available models, and monitor usage from a single dashboard.

## Stack

- **Frontend**: React 18 + Vite + TypeScript + Tailwind CSS + TanStack Query
- **Backend**: Python FastAPI + structlog + httpx + pydantic-settings

## Features

- Live proxy health status and active backend indicator
- One-click backend switching (Copilot / Claude / Gemma)
- Browse all models available on GitHub Copilot and GitHub Models
- Token usage and estimated cost from Anthropic API and local proxy logs
- Structured JSON logging throughout the backend for easy debugging

## Project structure

```
bipolar-code/
├── backend/
│   ├── app/
│   │   ├── api/          # FastAPI routers
│   │   ├── services/     # Business logic (proxy, copilot, models, usage)
│   │   ├── models/       # Pydantic schemas
│   │   └── core/         # Config, logging
│   └── tests/
└── frontend/
    └── src/
        ├── components/   # Atomic UI (Card, Badge, Button, Layout)
        ├── hooks/        # useProxy, useModels, useUsage
        ├── pages/        # Dashboard, Models, Usage
        ├── services/     # Axios API client
        └── types/        # Shared TypeScript types
```

## Setup

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
npm test           # vitest
```

### Backend tests

```bash
cd backend
pytest
```

## Environment

The backend reads `C:/litellm/.env` automatically. No extra configuration needed if the proxy is already set up.

## License

MIT
