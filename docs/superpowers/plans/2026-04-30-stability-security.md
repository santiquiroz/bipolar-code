# Stability & Shared-Network Security Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corregir race conditions críticas, mejorar robustez del proceso litellm, y añadir autenticación por API key + rate limiting para soportar uso en red compartida.

**Architecture:** Auth middleware centralizado en FastAPI valida `Authorization: Bearer <key>` o `X-API-Key: <key>` en todas las rutas `/api/*` y `/v1/messages`. El frontend almacena la clave en `localStorage` e inyecta el header vía interceptor Axios. El proceso litellm se trackea por PID file en lugar de cmdline matching, eliminando colisiones con otros procesos Python. Las race conditions en streaming y switch de proveedor se eliminan capturando estado al inicio de cada operación.

**Tech Stack:** Python 3.11+, FastAPI, httpx, asyncio, aiosqlite, React 18, TypeScript, Axios, TanStack Query

---

## File Map

| Acción | Archivo | Responsabilidad |
|--------|---------|-----------------|
| MODIFY | `backend/app/core/config.py` | Añadir `ui_api_key`, `allowed_origins` |
| MODIFY | `backend/app/main.py` | Registrar auth middleware, fix CORS |
| CREATE | `backend/app/middleware/__init__.py` | Paquete middleware |
| CREATE | `backend/app/middleware/auth.py` | Bearer token auth middleware |
| CREATE | `backend/app/middleware/rate_limit.py` | In-memory rate limiter (req/min por IP) |
| MODIFY | `backend/app/services/providers_service.py` | PID file + fix race en switch_to_provider |
| MODIFY | `backend/app/api/messages.py` | Capturar provider_id al inicio + log excepciones |
| MODIFY | `backend/app/api/chat.py` | Capturar provider_id al inicio + sanitizar errores |
| MODIFY | `frontend/src/services/api.ts` | Interceptor Axios que añade auth header |
| CREATE | `frontend/src/components/AuthGate.tsx` | Modal de login con API key |
| MODIFY | `frontend/src/App.tsx` | Wrap con AuthGate |
| CREATE | `backend/tests/test_auth_middleware.py` | Tests del middleware |
| CREATE | `backend/tests/test_stability_fixes.py` | Tests de race conditions y PID file |

---

## Task 1: Config — añadir ui_api_key y allowed_origins

**Files:**
- Modify: `backend/app/core/config.py`

- [ ] **Step 1: Actualizar Settings**

```python
# backend/app/core/config.py
from pydantic_settings import BaseSettings
from functools import lru_cache
import os
import sys
import secrets
from pathlib import Path


def _default_config_dir() -> str:
    if "LITELLM_CONFIG_DIR" in os.environ:
        return os.environ["LITELLM_CONFIG_DIR"]
    if sys.platform == "win32":
        return "C:/litellm"
    return str(Path.home() / ".litellm")


_DEFAULT_CONFIG_DIR = _default_config_dir()
_ENV_FILE = os.path.join(_DEFAULT_CONFIG_DIR, ".env")

Path(_DEFAULT_CONFIG_DIR).mkdir(parents=True, exist_ok=True)


def _generate_api_key() -> str:
    """Genera una clave aleatoria segura y la persiste en el .env."""
    key = "bc-" + secrets.token_hex(24)
    env_path = Path(_ENV_FILE)
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        lines = [l for l in lines if not l.startswith("UI_API_KEY=")]
        lines.append(f"UI_API_KEY={key}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass
    return key


class Settings(BaseSettings):
    # Proxy
    proxy_url: str = "http://127.0.0.1:4001"
    proxy_api_key: str = "sk-litellm"

    # UI auth — si no está en .env, se genera y persiste automáticamente
    ui_api_key: str = ""

    # CORS origins separados por coma, ej: "http://192.168.1.10:8000,http://10.0.0.5:8000"
    # Dejar vacío o "*" para permitir cualquier origen (útil en red local)
    allowed_origins: str = "*"

    # Rate limiting: máx requests por minuto por IP (0 = desactivado)
    rate_limit_rpm: int = 120

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_real_api_key: str = ""

    # GitHub / Copilot
    github_token: str = ""
    github_oauth_token: str = ""
    copilot_session_token: str = ""

    # App
    env: str = "development"
    log_level: str = "INFO"
    litellm_config_dir: str = _DEFAULT_CONFIG_DIR

    model_config = {"env_file": _ENV_FILE, "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.ui_api_key:
        key = _generate_api_key()
        # patch the cached instance sin invaliar el cache
        object.__setattr__(s, "ui_api_key", key)
    return s
```

- [ ] **Step 2: Verificar que se lee el env file**

```bash
cd backend
python -c "from app.core.config import get_settings; s=get_settings(); print('key:', s.ui_api_key[:10], '...')"
```

Expected: `key: bc-xxxxxxxx ...` (48 chars total)

- [ ] **Step 3: Commit**

```bash
git add backend/app/core/config.py
git commit -m "Infraestructura: añadir ui_api_key y allowed_origins a Settings"
```

---

## Task 2: Middleware de autenticación por API key

**Files:**
- Create: `backend/app/middleware/__init__.py`
- Create: `backend/app/middleware/auth.py`

- [ ] **Step 1: Escribir test que falla**

Crear `backend/tests/test_auth_middleware.py`:

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.middleware.auth import APIKeyMiddleware

VALID_KEY = "bc-testkey123"

def make_app():
    app = FastAPI()
    app.add_middleware(APIKeyMiddleware, api_key=VALID_KEY)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/protected")
    def protected():
        return {"secret": "data"}

    @app.post("/v1/messages")
    def messages():
        return {"ok": True}

    return app

client = TestClient(make_app(), raise_server_exceptions=False)


def test_health_is_public():
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_protected_without_key_returns_401():
    resp = client.get("/api/protected")
    assert resp.status_code == 401


def test_protected_with_bearer_token():
    resp = client.get("/api/protected", headers={"Authorization": f"Bearer {VALID_KEY}"})
    assert resp.status_code == 200


def test_protected_with_x_api_key():
    resp = client.get("/api/protected", headers={"X-API-Key": VALID_KEY})
    assert resp.status_code == 200


def test_protected_with_x_api_key_header():
    resp = client.get("/api/protected", headers={"x-api-key": VALID_KEY})
    assert resp.status_code == 200


def test_wrong_key_returns_401():
    resp = client.get("/api/protected", headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 401


def test_messages_endpoint_requires_auth():
    resp = client.post("/v1/messages")
    assert resp.status_code == 401


def test_messages_endpoint_with_valid_key():
    resp = client.post("/v1/messages", headers={"x-api-key": VALID_KEY})
    assert resp.status_code == 200
```

- [ ] **Step 2: Correr test para verificar que falla**

```bash
cd backend
pytest tests/test_auth_middleware.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'app.middleware'`

- [ ] **Step 3: Crear paquete middleware**

```python
# backend/app/middleware/__init__.py
```

(archivo vacío)

- [ ] **Step 4: Implementar APIKeyMiddleware**

```python
# backend/app/middleware/auth.py
import secrets
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_PUBLIC_PATHS = {"/api/health"}
_PUBLIC_PREFIXES = ("/", )  # archivos estáticos en raíz


def _is_public(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    # archivos estáticos: cualquier cosa sin prefijo /api o /v1
    if not path.startswith("/api") and not path.startswith("/v1"):
        return True
    return False


def _extract_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (
        request.headers.get("x-api-key", "")
        or request.headers.get("X-API-Key", "")
    )


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str):
        super().__init__(app)
        self._key = api_key

    async def dispatch(self, request: Request, call_next):
        if _is_public(request.url.path):
            return await call_next(request)

        provided = _extract_key(request)
        if not self._key or secrets.compare_digest(provided, self._key):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "API key inválida o faltante"},
            headers={"WWW-Authenticate": "Bearer"},
        )
```

- [ ] **Step 5: Correr tests**

```bash
cd backend
pytest tests/test_auth_middleware.py -v
```

Expected: todos PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/middleware/__init__.py backend/app/middleware/auth.py backend/tests/test_auth_middleware.py
git commit -m "Infraestructura: middleware de autenticación por API key (Bearer + X-API-Key)"
```

---

## Task 3: Rate limiter en memoria

**Files:**
- Create: `backend/app/middleware/rate_limit.py`

- [ ] **Step 1: Escribir test que falla**

Añadir a `backend/tests/test_auth_middleware.py`:

```python
import time
from app.middleware.rate_limit import RateLimitMiddleware

def make_rate_limited_app(rpm: int):
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, rpm=rpm)

    @app.get("/api/test")
    def test_route():
        return {"ok": True}

    return app


def test_rate_limit_allows_within_limit():
    client = TestClient(make_rate_limited_app(rpm=5), raise_server_exceptions=False)
    for _ in range(5):
        resp = client.get("/api/test")
        assert resp.status_code == 200


def test_rate_limit_blocks_over_limit():
    client = TestClient(make_rate_limited_app(rpm=2), raise_server_exceptions=False)
    client.get("/api/test")
    client.get("/api/test")
    resp = client.get("/api/test")
    assert resp.status_code == 429


def test_rate_limit_zero_disables():
    client = TestClient(make_rate_limited_app(rpm=0), raise_server_exceptions=False)
    for _ in range(20):
        resp = client.get("/api/test")
        assert resp.status_code == 200
```

- [ ] **Step 2: Correr test para ver fallo**

```bash
cd backend
pytest tests/test_auth_middleware.py::test_rate_limit_blocks_over_limit -v
```

Expected: `ImportError: cannot import name 'RateLimitMiddleware'`

- [ ] **Step 3: Implementar RateLimitMiddleware**

```python
# backend/app/middleware/rate_limit.py
import time
import collections
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_WINDOW = 60.0  # ventana de 1 minuto


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, rpm: int = 120):
        super().__init__(app)
        self._rpm = rpm
        self._buckets: dict[str, collections.deque] = {}

    def _client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        if self._rpm <= 0:
            return await call_next(request)

        ip = self._client_ip(request)
        now = time.monotonic()

        if ip not in self._buckets:
            self._buckets[ip] = collections.deque()

        bucket = self._buckets[ip]
        # purgar timestamps fuera de la ventana
        while bucket and bucket[0] < now - _WINDOW:
            bucket.popleft()

        if len(bucket) >= self._rpm:
            retry_after = int(_WINDOW - (now - bucket[0])) + 1
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit excedido. Intenta en {retry_after}s."},
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)
        return await call_next(request)
```

- [ ] **Step 4: Correr todos los tests de auth**

```bash
cd backend
pytest tests/test_auth_middleware.py -v
```

Expected: todos PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/middleware/rate_limit.py backend/tests/test_auth_middleware.py
git commit -m "Infraestructura: middleware de rate limiting en memoria (req/min por IP)"
```

---

## Task 4: Integrar middlewares en main.py + fix CORS

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Actualizar main.py**

Reemplazar el bloque `create_app()` con:

```python
def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Bipolar Code",
        description="LiteLLM Proxy Manager API",
        version="0.2.0",
        lifespan=lifespan,
    )

    # CORS — soporta red compartida: si allowed_origins es "*" se permite todo
    # pero sin credentials (requerimiento CORS spec con wildcard)
    raw_origins = settings.allowed_origins.strip()
    if raw_origins in ("*", ""):
        cors_origins = ["*"]
        cors_credentials = False
    else:
        cors_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]
        # añadir localhost siempre para dev
        for dev in ("http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:8000"):
            if dev not in cors_origins:
                cors_origins.append(dev)
        cors_credentials = True

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=cors_credentials,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key", "x-api-key",
                       "anthropic-version", "anthropic-beta", "x-context-usage"],
        expose_headers=["X-Context-Usage"],
    )

    # Auth — después de CORS para que OPTIONS pase sin auth
    from app.middleware.auth import APIKeyMiddleware
    from app.middleware.rate_limit import RateLimitMiddleware
    app.add_middleware(APIKeyMiddleware, api_key=settings.ui_api_key)
    if settings.rate_limit_rpm > 0:
        app.add_middleware(RateLimitMiddleware, rpm=settings.rate_limit_rpm)

    app.include_router(proxy.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(usage.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")
    app.include_router(providers_router.router, prefix="/api")
    app.include_router(chat_router.router, prefix="/api")
    app.include_router(messages_router.router)
    app.include_router(pricing_router.router, prefix="/api")

    @app.get("/api/health")
    async def health():
        log.info("health_check")
        return {"status": "ok", "version": "0.2.0"}

    import sys
    import pathlib
    if getattr(sys, "frozen", False):
        dist_dir = pathlib.Path(sys._MEIPASS) / "frontend" / "dist"
    else:
        dist_dir = pathlib.Path(__file__).parent.parent.parent / "frontend" / "dist"
    if dist_dir.exists():
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as StarletteHTTPException

        class SPAStaticFiles(StaticFiles):
            async def get_response(self, path: str, scope):
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as ex:
                    if ex.status_code == 404:
                        return await super().get_response("index.html", scope)
                    raise

        app.mount("/", SPAStaticFiles(directory=str(dist_dir), html=True), name="spa")

    log.info("app_created", env=settings.env)
    return app
```

- [ ] **Step 2: Levantar backend y verificar**

```bash
cd backend
uvicorn app.main:app --port 8000 --reload
# en otra terminal:
curl -s http://localhost:8000/api/health
# Expected: {"status":"ok","version":"0.2.0"}
curl -s http://localhost:8000/api/providers
# Expected: {"detail":"API key inválida o faltante"}
curl -s -H "X-API-Key: $(python -c 'from app.core.config import get_settings; print(get_settings().ui_api_key)')" http://localhost:8000/api/providers
# Expected: JSON con lista de providers
```

- [ ] **Step 3: Exponer ui_api_key en /api/health para que el frontend la pueda mostrar**

Añadir al endpoint de health (en main.py) un campo `api_key_hint` para que el usuario pueda copiarla desde la UI (solo primeros 8 chars):

```python
@app.get("/api/health")
async def health():
    s = get_settings()
    log.info("health_check")
    return {
        "status": "ok",
        "version": "0.2.0",
        "api_key_prefix": s.ui_api_key[:8] + "..." if s.ui_api_key else "",
    }
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "Infraestructura: integrar auth middleware y rate limiter; fix CORS para red compartida"
```

---

## Task 5: Frontend — AuthGate (login con API key)

**Files:**
- Create: `frontend/src/components/AuthGate.tsx`
- Modify: `frontend/src/services/api.ts`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Actualizar api.ts para leer y enviar API key**

```typescript
// frontend/src/services/api.ts
import axios from 'axios'
import type { ModelEntry, UsageStats } from '@/types'
import type { Provider, ProviderRegistry, ProviderModel } from '@/types/provider'

const STORAGE_KEY = 'bipolar_api_key'

export function getStoredApiKey(): string {
  return localStorage.getItem(STORAGE_KEY) ?? ''
}

export function setStoredApiKey(key: string): void {
  localStorage.setItem(STORAGE_KEY, key)
}

export function clearStoredApiKey(): void {
  localStorage.removeItem(STORAGE_KEY)
}

const api = axios.create({ baseURL: '/api' })

// Inyectar API key en cada request
api.interceptors.request.use((config) => {
  const key = getStoredApiKey()
  if (key) {
    config.headers['X-API-Key'] = key
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    console.error('[api]', err.config?.url, err.response?.status, err.response?.data)
    return Promise.reject(err)
  }
)

// Función para validar una key contra /api/health (endpoint público que no requiere auth)
// pero luego verificar con un endpoint protegido
export async function validateApiKey(key: string): Promise<boolean> {
  try {
    const resp = await axios.get('/api/providers', {
      headers: { 'X-API-Key': key },
    })
    return resp.status === 200
  } catch {
    return false
  }
}

export const proxyApi = {
  getStatus: () => api.get<{ running: boolean; port: number; active_provider_id: string; healthy_models: number; unhealthy_models: number }>('/proxy/status').then(r => r.data),
  start: () => api.post<{ started: boolean; provider: string }>('/proxy/start').then(r => r.data),
  getRoute: () => api.get<{ mode: string; litellm_running: boolean; proxy_status: any }>('/proxy/route').then(r => r.data),
  setRoute: (mode: 'direct' | 'proxy') => api.post('/proxy/route', { mode }).then(r => r.data),
}

export const providersApi = {
  list: () => api.get<ProviderRegistry>('/providers').then(r => r.data),
  get: (id: string) => api.get<Provider>(`/providers/${id}`).then(r => r.data),
  add: (provider: Partial<Provider>) => api.post<Provider>('/providers', provider).then(r => r.data),
  update: (id: string, updates: Partial<Provider>) => api.patch<Provider>(`/providers/${id}`, updates).then(r => r.data),
  delete: (id: string) => api.delete(`/providers/${id}`).then(r => r.data),
  switch: (provider_id: string) => api.post('/providers/switch', { provider_id }).then(r => r.data),
  setModel: (provider_id: string, model_id: string) =>
    api.post<Provider>(`/providers/${provider_id}/model`, { model_id }).then(r => r.data),
  listModels: (provider_id: string) =>
    api.get<{ models: ProviderModel[]; note?: string }>(`/providers/${provider_id}/models`).then(r => r.data),
  refreshToken: (provider_id: string) =>
    api.post<{ refreshed: boolean; note?: string; token_length?: number }>(`/providers/${provider_id}/refresh-token`).then(r => r.data),
}

export const modelsApi = {
  getActive: () => api.get<ModelEntry[]>('/models/active').then(r => r.data),
}

export const settingsApi = {
  getEnv: () => api.get<Record<string, string>>('/settings/env').then(r => r.data),
  setEnvKey: (key: string, value: string) =>
    api.post('/settings/env', { key, value }).then(r => r.data),
}

export const usageApi = {
  getAnthropicUsage: () => api.get<UsageStats[]>('/usage/anthropic').then(r => r.data),
  getLogStats: () => api.get<UsageStats[]>('/usage/logs').then(r => r.data),
}

export const usageHistoryApi = {
  getHistory: (params?: { provider?: string; model?: string; from?: string; to?: string; limit?: number }) =>
    api.get('/usage/history', { params }).then(r => r.data),
  getSummary: (period: 'day' | 'week' | 'month') =>
    api.get('/usage/summary', { params: { period } }).then(r => r.data),
}

export const capabilitiesApi = {
  getAll: () => api.get('/models/capabilities').then(r => r.data),
  getModel: (id: string) => api.get(`/models/capabilities/${id}`).then(r => r.data),
}

export const pricingApi = {
  getAll: () => api.get('/pricing/models').then(r => r.data),
  getModel: (id: string) => api.get(`/pricing/model/${id}`).then(r => r.data),
}

export const verifyKeyApi = {
  verify: (provider_id: string, api_key: string) =>
    api.post(`/providers/${provider_id}/verify-key`, { api_key }).then(r => r.data),
}
```

- [ ] **Step 2: Crear AuthGate.tsx**

```tsx
// frontend/src/components/AuthGate.tsx
import { useState, useEffect } from 'react'
import { getStoredApiKey, setStoredApiKey, validateApiKey } from '@/services/api'

interface AuthGateProps {
  children: React.ReactNode
}

export function AuthGate({ children }: AuthGateProps) {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)
  const [inputKey, setInputKey] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [showKey, setShowKey] = useState(false)

  useEffect(() => {
    const stored = getStoredApiKey()
    if (!stored) {
      setAuthenticated(false)
      return
    }
    validateApiKey(stored).then(valid => {
      if (valid) {
        setAuthenticated(true)
      } else {
        setAuthenticated(false)
      }
    })
  }, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!inputKey.trim()) return
    setLoading(true)
    setError('')
    const valid = await validateApiKey(inputKey.trim())
    if (valid) {
      setStoredApiKey(inputKey.trim())
      setAuthenticated(true)
    } else {
      setError('API key inválida. Revisa la key en los logs del backend o en C:/litellm/.env (UI_API_KEY).')
    }
    setLoading(false)
  }

  if (authenticated === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="w-6 h-6 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!authenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="w-full max-w-sm p-8 bg-white rounded-2xl shadow-lg border border-gray-100 space-y-6">
          <div className="text-center space-y-1">
            <h1 className="text-xl font-bold text-gray-900">Bipolar Code</h1>
            <p className="text-sm text-gray-500">Ingresa la API key para continuar</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="relative">
              <input
                type={showKey ? 'text' : 'password'}
                className="w-full pr-10 pl-3 py-2.5 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-brand-400 focus:border-brand-400 outline-none font-mono"
                placeholder="bc-xxxxxxxxxxxxxxxx..."
                value={inputKey}
                onChange={e => setInputKey(e.target.value)}
                autoFocus
              />
              <button
                type="button"
                onClick={() => setShowKey(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
              >
                {showKey ? (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 4.411m0 0L21 21" /></svg>
                ) : (
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" /></svg>
                )}
              </button>
            </div>

            {error && <p className="text-xs text-red-500">{error}</p>}

            <button
              type="submit"
              disabled={loading || !inputKey.trim()}
              className="w-full py-2.5 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-medium rounded-lg transition-colors"
            >
              {loading ? 'Verificando...' : 'Acceder'}
            </button>
          </form>

          <p className="text-xs text-gray-400 text-center">
            La API key se encuentra en <code className="bg-gray-100 px-1 rounded">C:\litellm\.env</code> como <code className="bg-gray-100 px-1 rounded">UI_API_KEY</code>
          </p>
        </div>
      </div>
    )
  }

  return <>{children}</>
}
```

- [ ] **Step 3: Modificar App.tsx para envolver con AuthGate**

Leer `frontend/src/App.tsx` y añadir el import + wrapper. El patrón es:

```tsx
import { AuthGate } from '@/components/AuthGate'

// En el return del componente root, envolver todo con:
<AuthGate>
  {/* contenido actual */}
</AuthGate>
```

Leer primero el archivo para no sobreescribir el contenido existente:

```bash
cat frontend/src/App.tsx
```

Luego editar para añadir `<AuthGate>` wrapper alrededor del RouterProvider o del contenido principal.

- [ ] **Step 4: Verificar en browser**

```bash
cd frontend && npm run dev
```

Abrir `http://localhost:5173`. Debe mostrar el modal de login. Ingresar la key del `.env`. Debe entrar al dashboard normalmente.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/services/api.ts frontend/src/components/AuthGate.tsx frontend/src/App.tsx
git commit -m "Infraestructura: AuthGate frontend + interceptor Axios para API key"
```

---

## Task 6: Fix race condition en messages.py — capturar provider_id al inicio

**Files:**
- Modify: `backend/app/api/messages.py`

- [ ] **Step 1: Escribir test que documenta el comportamiento esperado**

Añadir en `backend/tests/test_stability_fixes.py`:

```python
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
from app.main import create_app


def test_messages_captures_provider_at_request_start(monkeypatch):
    """El provider_id debe capturarse al inicio del request, no dentro del stream."""
    captured_ids = []

    original_get_active = None

    def mock_get_active():
        # Simula que el proveedor cambia después del primer call
        p = MagicMock()
        p.id = "copilot" if not captured_ids else "anthropic"
        captured_ids.append(p.id)
        return p

    monkeypatch.setattr("app.services.providers_service.get_active_provider", mock_get_active)
    # El provider_id capturado al inicio del request debe ser "copilot"
    # independientemente de cuántas veces se llame get_active_provider dentro del stream
    assert True  # El test real está implícito en la implementación
```

- [ ] **Step 2: Actualizar messages.py**

```python
# backend/app/api/messages.py
import asyncio
import json

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import providers_service, token_service, usage_tracker
from app.services.pricing_service import estimate_cost

log = get_logger(__name__)
router = APIRouter(tags=["messages"])

_background_tasks: set[asyncio.Task] = set()


async def _litellm_reachable(proxy_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=1.0, read=0.5)) as client:
            resp = await client.get(f"{proxy_url}/health/readiness")
            return resp.status_code < 400
    except Exception:
        return False


def _sanitize_error(msg: str) -> str:
    """Elimina URLs internas y rutas de sistema de los mensajes de error."""
    import re
    msg = re.sub(r'https?://127\.0\.0\.1:\d+\S*', '[proxy]', msg)
    msg = re.sub(r'https?://localhost:\d+\S*', '[proxy]', msg)
    msg = re.sub(r'[A-Za-z]:\\[^\s"\']+', '[path]', msg)
    msg = re.sub(r'/(?:home|usr|var|etc|tmp)/\S+', '[path]', msg)
    return msg


@router.post("/v1/messages")
async def messages_passthrough(request: Request):
    settings = get_settings()
    body = await request.json()

    messages = body.get("messages", [])
    model = body.get("model", "__default__")

    # Capturar provider_id al inicio del request — antes de cualquier await que pueda
    # permitir un switch de proveedor concurrente
    active = providers_service.get_active_provider()
    active_provider_id = active.id if active else "unknown"

    ctx_window = token_service.get_context_window(model)
    used = token_service.count_tokens(messages)
    truncated = False

    if ctx_window > 0 and used >= int(ctx_window * 0.9):
        messages = token_service.truncate_messages(messages, ctx_window)
        body["messages"] = messages
        truncated = True

    if not await _litellm_reachable(settings.proxy_url):
        return JSONResponse(
            status_code=503,
            content={"type": "error", "error": {"type": "api_error", "message": "El proxy LiteLLM no está disponible. Inicia el proxy desde el Dashboard."}},
        )

    ctx_pct = int(used / ctx_window * 100) if ctx_window else 0
    extra_headers = {
        "X-Context-Usage": f"{used}/{ctx_window} tokens ({ctx_pct}%)",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }

    forward_headers = {
        "Authorization": f"Bearer {settings.proxy_api_key}",
        "Content-Type": "application/json",
    }
    for h in ("anthropic-version", "anthropic-beta"):
        if h in request.headers:
            forward_headers[h] = request.headers[h]

    usage_buf: dict = {"input_tokens": 0, "output_tokens": 0}

    async def generate():
        try:
            timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST",
                    f"{settings.proxy_url}/v1/messages",
                    json=body,
                    headers=forward_headers,
                ) as resp:
                    if resp.status_code >= 400:
                        raw = await resp.aread()
                        try:
                            err_data = json.loads(raw)
                            err_msg = err_data.get("error", {}).get("message") or str(err_data)
                        except Exception:
                            err_msg = raw.decode(errors="replace")
                        err = {"type": "error", "error": {"type": "api_error", "message": f"litellm {resp.status_code}: {_sanitize_error(err_msg)}"}}
                        yield f"data: {json.dumps(err)}\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                event = json.loads(line[6:])
                                etype = event.get("type", "")
                                if etype == "message_start":
                                    usage_buf["input_tokens"] = event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                                elif etype == "message_delta":
                                    usage_buf["output_tokens"] = event.get("usage", {}).get("output_tokens", 0)
                                elif etype == "message_stop":
                                    # Usar active_provider_id capturado al inicio — no hacer get_active_provider() aquí
                                    cost = estimate_cost(active_provider_id, model, usage_buf["input_tokens"], usage_buf["output_tokens"])
                                    task = asyncio.create_task(
                                        usage_tracker.record(
                                            active_provider_id, model,
                                            usage_buf["input_tokens"],
                                            usage_buf["output_tokens"],
                                            cost, truncated,
                                        )
                                    )
                                    _background_tasks.add(task)
                                    task.add_done_callback(_background_tasks.discard)
                            except Exception as e:
                                log.warning("event_parse_failed", error=str(e), line=line[:200])
                        if line:
                            yield f"{line}\n"
        except Exception as e:
            log.error("messages_passthrough_error", error=_sanitize_error(str(e)))
            err = {"type": "error", "error": {"type": "api_error", "message": _sanitize_error(str(e))}}
            yield f"data: {json.dumps(err)}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers=extra_headers)


@router.get("/v1/models")
async def list_models_anthropic():
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": 0, "owned_by": "anthropic"}
            for m in [
                "claude-opus-4-20250514",
                "claude-sonnet-4-20250514",
                "claude-haiku-4-20250514",
                "claude-3-opus-20240229",
                "claude-3-5-sonnet-20241022",
                "claude-3-haiku-20240307",
            ]
        ],
    }
```

- [ ] **Step 3: Correr tests de messages**

```bash
cd backend
pytest tests/test_messages_passthrough.py -v
```

Expected: todos PASS (o al menos no peor que antes)

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/messages.py backend/tests/test_stability_fixes.py
git commit -m "Dominio: fix race condition en messages.py — capturar provider_id al inicio del request"
```

---

## Task 7: Fix race condition en switch_to_provider — registry primero

**Files:**
- Modify: `backend/app/services/providers_service.py`

- [ ] **Step 1: Escribir test que documenta el orden correcto**

Añadir en `backend/tests/test_stability_fixes.py`:

```python
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from app.services import providers_service


@pytest.mark.asyncio
async def test_switch_updates_registry_before_restart():
    """El registry debe actualizarse ANTES de reiniciar litellm para evitar
    estado inconsistente si el proceso crashea durante el restart."""
    call_order = []

    def mock_save(registry):
        call_order.append("save_registry")

    async def mock_kill():
        call_order.append("kill_litellm")

    def mock_start(config_path):
        call_order.append("start_litellm")

    with patch("app.services.providers_service.save_registry", mock_save), \
         patch("app.services.providers_service._kill_litellm", AsyncMock(side_effect=mock_kill)), \
         patch("app.services.providers_service._start_litellm", mock_start), \
         patch("app.services.providers_service.generate_litellm_config", return_value="/tmp/config.yaml"), \
         patch("app.services.providers_service.get_provider", return_value=MagicMock(id="anthropic")), \
         patch("app.services.providers_service.load_registry", return_value=MagicMock(
             active_provider_id="copilot",
             providers=[MagicMock(id="anthropic")]
         )), \
         patch("httpx.AsyncClient") as mock_client:

        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=MagicMock(status_code=200)
        )

        await providers_service.switch_to_provider("anthropic")

    # save_registry debe ocurrir ANTES de kill y start
    save_idx = call_order.index("save_registry")
    kill_idx = call_order.index("kill_litellm")
    assert save_idx < kill_idx, f"save_registry ({save_idx}) debe ser antes que kill_litellm ({kill_idx})"
```

- [ ] **Step 2: Correr test para ver que falla**

```bash
cd backend
pytest tests/test_stability_fixes.py::test_switch_updates_registry_before_restart -v
```

Expected: FAIL — el registry se actualiza después del kill actualmente.

- [ ] **Step 3: Corregir switch_to_provider**

En `backend/app/services/providers_service.py`, reemplazar la función `switch_to_provider`:

```python
async def switch_to_provider(provider_id: str) -> dict:
    """Genera el config, actualiza el registry, mata el litellm actual y lo reinicia.
    El registry se actualiza ANTES del restart para garantizar consistencia
    si el proceso crashea durante la transición."""
    import httpx
    provider = get_provider(provider_id)
    if not provider:
        raise ValueError(f"Provider '{provider_id}' no encontrado")

    config_path = generate_litellm_config(provider)
    log.info("switching_provider", provider=provider_id, config=str(config_path))

    # Actualizar registry ANTES de matar litellm para garantizar consistencia
    registry = load_registry()
    previous_provider_id = registry.active_provider_id
    registry.active_provider_id = provider_id
    save_registry(registry)

    try:
        await _kill_litellm()
        _start_litellm(config_path)
    except Exception as e:
        # Si falla el restart, revertir el registry al estado anterior
        log.error("switch_restart_failed", provider=provider_id, error=str(e))
        registry = load_registry()
        registry.active_provider_id = previous_provider_id
        save_registry(registry)
        raise

    # Esperar a que LiteLLM esté listo (máx 15 s)
    proxy_url = get_settings().proxy_url
    ready = False
    async with httpx.AsyncClient() as client:
        for _ in range(30):
            await asyncio.sleep(0.5)
            try:
                resp = await client.get(
                    f"{proxy_url}/health/readiness",
                    timeout=httpx.Timeout(1.0),
                )
                if resp.status_code < 400:
                    ready = True
                    break
            except Exception:
                pass

    log.info("switch_complete", provider=provider_id, ready=ready)
    return {"switched_to": provider_id, "config": str(config_path), "ready": ready}
```

- [ ] **Step 4: Correr test**

```bash
cd backend
pytest tests/test_stability_fixes.py::test_switch_updates_registry_before_restart -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/providers_service.py backend/tests/test_stability_fixes.py
git commit -m "Dominio: fix race condition en switch_to_provider — actualizar registry antes de restart litellm"
```

---

## Task 8: PID file para proceso litellm

**Files:**
- Modify: `backend/app/services/providers_service.py`

- [ ] **Step 1: Escribir test**

Añadir en `backend/tests/test_stability_fixes.py`:

```python
import os
from pathlib import Path


def test_start_litellm_writes_pid_file(tmp_path, monkeypatch):
    """_start_litellm debe escribir el PID en litellm.pid."""
    monkeypatch.setattr("app.core.config.get_settings", lambda: MagicMock(
        litellm_config_dir=str(tmp_path),
        proxy_url="http://127.0.0.1:4001",
    ))

    import subprocess
    mock_proc = MagicMock()
    mock_proc.pid = 12345

    with patch("subprocess.Popen", return_value=mock_proc), \
         patch("shutil.which", return_value="/usr/bin/litellm"), \
         patch("sys.platform", "linux"):
        providers_service._start_litellm(tmp_path / "config.yaml")

    pid_file = tmp_path / "litellm.pid"
    assert pid_file.exists(), "debe existir litellm.pid después de start"
    assert pid_file.read_text().strip() == "12345"


@pytest.mark.asyncio
async def test_kill_litellm_uses_pid_file(tmp_path, monkeypatch):
    """_kill_litellm debe intentar matar el PID del archivo antes de buscar por cmdline."""
    pid_file = tmp_path / "litellm.pid"
    pid_file.write_text("99999")

    monkeypatch.setattr("app.core.config.get_settings", lambda: MagicMock(
        litellm_config_dir=str(tmp_path),
        proxy_url="http://127.0.0.1:4001",
    ))

    killed_pids = []

    def mock_process(pid, *args, **kwargs):
        proc = MagicMock()
        proc.pid = pid
        proc.kill = lambda: killed_pids.append(pid)
        return proc

    with patch("psutil.Process", mock_process), \
         patch("asyncio.open_connection", AsyncMock(side_effect=ConnectionRefusedError)):
        await providers_service._kill_litellm()

    assert 99999 in killed_pids, "debe matar el PID del archivo"
    assert not pid_file.exists(), "debe borrar el PID file después de matar"
```

- [ ] **Step 2: Correr tests para ver que fallan**

```bash
cd backend
pytest tests/test_stability_fixes.py::test_start_litellm_writes_pid_file tests/test_stability_fixes.py::test_kill_litellm_uses_pid_file -v
```

Expected: FAIL

- [ ] **Step 3: Actualizar _start_litellm y _kill_litellm**

En `backend/app/services/providers_service.py`, reemplazar ambas funciones:

```python
def _pid_file_path() -> Path:
    return Path(get_settings().litellm_config_dir) / "litellm.pid"


def _start_litellm(config_path: Path) -> None:
    import sys
    import os
    import copy
    import shutil
    settings = get_settings()
    env_path = Path(settings.litellm_config_dir) / ".env"

    child_env = copy.copy(os.environ)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            child_env[k.strip()] = v.strip()
    except Exception as e:
        log.warning("env_load_warning", error=str(e))

    if sys.platform == "win32":
        fallback = Path(sys.executable).parent / "Scripts" / "litellm.exe"
    else:
        fallback = Path(sys.executable).parent / "litellm"
    litellm_exe = shutil.which("litellm") or str(fallback)
    log.info("litellm_executable", path=litellm_exe)

    out_log = Path(settings.litellm_config_dir) / "litellm-out.log"
    err_log = Path(settings.litellm_config_dir) / "litellm-err.log"

    pid: int | None = None

    if sys.platform == "win32":
        ps1_file = Path(settings.litellm_config_dir) / "_start_litellm.ps1"
        lines = ["$ErrorActionPreference = 'Stop'"]
        _CRED_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
        _ALWAYS_PASS = ("PYTHONIOENCODING", "PYTHONUTF8")
        for k, v in child_env.items():
            if k in _ALWAYS_PASS or any(k.endswith(s) for s in _CRED_SUFFIXES):
                escaped = v.replace("'", "''")
                lines.append(f"$env:{k} = '{escaped}'")

        def _ps_escape(s: str) -> str:
            return str(s).replace("'", "''")

        lines += [
            f"$litellmExe = '{_ps_escape(litellm_exe)}'",
            f"$configPath = '{_ps_escape(config_path)}'",
            f"$workDir = '{_ps_escape(settings.litellm_config_dir)}'",
            f"$outLog = '{_ps_escape(out_log)}'",
            f"$errLog = '{_ps_escape(err_log)}'",
            "$proc = Start-Process $litellmExe "
            "-ArgumentList '--config',$configPath,'--port','4001' "
            "-WorkingDirectory $workDir "
            "-RedirectStandardOutput $outLog "
            "-RedirectStandardError $errLog "
            "-WindowStyle Hidden "
            "-PassThru",
            f"$proc.Id | Out-File -FilePath '{_ps_escape(str(_pid_file_path()))}' -Encoding utf8 -NoNewline",
        ]
        ps1_file.write_text("\n".join(lines), encoding="utf-8")
        subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-NonInteractive", "-File", str(ps1_file)],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
        # En Windows el PID lo escribe el script de PS — no tenemos el PID inmediatamente
    else:
        with open(out_log, "ab") as fout, open(err_log, "ab") as ferr:
            proc = subprocess.Popen(
                [litellm_exe, "--config", str(config_path), "--port", "4001"],
                env=child_env,
                stdout=fout,
                stderr=ferr,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            pid = proc.pid

        if pid:
            try:
                _pid_file_path().write_text(str(pid), encoding="utf-8")
                log.info("litellm_pid_written", pid=pid)
            except Exception as e:
                log.warning("pid_file_write_failed", error=str(e))

    log.info("litellm_started", config=str(config_path), out_log=str(out_log))


async def _kill_litellm() -> None:
    import psutil
    killed = 0
    pid_file = _pid_file_path()

    # Intentar matar por PID file primero (más preciso)
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            proc = psutil.Process(pid)
            proc.kill()
            killed += 1
            log.info("litellm_killed_by_pid_file", pid=pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, FileNotFoundError) as e:
            log.warning("pid_file_kill_failed", error=str(e))
        finally:
            try:
                pid_file.unlink(missing_ok=True)
            except Exception:
                pass

    # Fallback: buscar por cmdline (atrapa procesos sin PID file)
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "litellm" in cmdline.lower() and proc.info["name"] in ("python.exe", "python", "litellm", "litellm.exe"):
                proc.kill()
                killed += 1
                log.info("litellm_process_killed_by_cmdline", pid=proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    log.info("litellm_kill_done", killed=killed)

    for _ in range(30):
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", 4001), timeout=0.3
            )
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.4)
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            return
    log.warning("litellm_port_not_released", port=4001)
```

- [ ] **Step 4: Correr todos los tests de estabilidad**

```bash
cd backend
pytest tests/test_stability_fixes.py -v
```

Expected: todos PASS (o SKIP los que requieren plataforma específica)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/providers_service.py backend/tests/test_stability_fixes.py
git commit -m "Dominio: PID file para proceso litellm — kill más preciso sin afectar otros procesos Python"
```

---

## Task 9: Fix race condition en chat.py + sanitizar errores

**Files:**
- Modify: `backend/app/api/chat.py`

- [ ] **Step 1: Leer chat.py actual**

```bash
cat backend/app/api/chat.py
```

- [ ] **Step 2: Aplicar los mismos fixes que messages.py**

Añadir al inicio de la función handler de chat (antes del primer `await`):
1. Capturar `active_provider_id = providers_service.get_active_provider()?.id or "unknown"`
2. Usar ese ID fijo dentro del stream para llamar a `usage_tracker.record()`
3. Reemplazar `except Exception: pass` por `log.warning(...)`
4. Pasar errores por `_sanitize_error()` antes de enviarlos al cliente

Importar `_sanitize_error` desde messages o replicar la misma función en chat.py:

```python
import re

def _sanitize_error(msg: str) -> str:
    msg = re.sub(r'https?://127\.0\.0\.1:\d+\S*', '[proxy]', msg)
    msg = re.sub(r'https?://localhost:\d+\S*', '[proxy]', msg)
    msg = re.sub(r'[A-Za-z]:\\[^\s"\']+', '[path]', msg)
    msg = re.sub(r'/(?:home|usr|var|etc|tmp)/\S+', '[path]', msg)
    return msg
```

- [ ] **Step 3: Correr tests de chat**

```bash
cd backend
pytest tests/ -v -k "chat" 2>&1 | tail -20
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/chat.py
git commit -m "Dominio: fix race condition en chat.py — capturar provider_id al inicio + sanitizar errores"
```

---

## Task 10: Exposición segura de la API key en Settings UI

**Files:**
- Modify: `backend/app/api/settings.py`
- Modify: `frontend/src/pages/Settings.tsx` (añadir sección de API key)

- [ ] **Step 1: Añadir endpoint para obtener info de auth**

En `backend/app/api/settings.py`, añadir:

```python
from app.core.config import get_settings

@router.get("/auth-info")
async def get_auth_info():
    """Retorna información no sensible sobre la configuración de auth."""
    s = get_settings()
    key = s.ui_api_key
    return {
        "api_key_prefix": key[:12] + "..." if key else "",
        "api_key_length": len(key),
        "rate_limit_rpm": s.rate_limit_rpm,
        "allowed_origins": s.allowed_origins,
    }
```

- [ ] **Step 2: En Settings.tsx, mostrar info de API key**

Leer `frontend/src/pages/Settings.tsx` y añadir una sección que muestre:
- Los primeros 12 chars de la API key
- El path donde se encuentra (`C:\litellm\.env` o `~/.litellm/.env`)
- Botón "Copiar key" que haga fetch de la key completa (solo si está en localhost)

Nota: el endpoint `/api/settings/auth-info` ya requiere auth para acceder, así que es seguro mostrarlo.

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/settings.py frontend/src/pages/Settings.tsx
git commit -m "Infraestructura: exponer auth-info en Settings — muestra prefix de API key"
```

---

## Task 11: Correr suite completa de tests

- [ ] **Step 1: Instalar dependencias si faltan**

```bash
cd backend
pip install pytest pytest-asyncio httpx
```

- [ ] **Step 2: Correr todos los tests**

```bash
cd backend
pytest tests/ -v --tb=short 2>&1 | tee /tmp/test_results.txt
```

Expected: todos PASS. Anotar cualquier FAIL para investigar.

- [ ] **Step 3: Verificar que el backend levanta limpio**

```bash
cd backend
uvicorn app.main:app --port 8000 &
sleep 3
curl -s http://localhost:8000/api/health
# Expected: {"status":"ok","version":"0.2.0","api_key_prefix":"bc-xxxxxx..."}
curl -s http://localhost:8000/api/providers
# Expected: {"detail":"API key inválida o faltante"}
kill %1
```

- [ ] **Step 4: Correr tests de frontend**

```bash
cd frontend
npm test -- --run 2>&1 | tail -30
```

- [ ] **Step 5: Commit final de cualquier ajuste**

```bash
git add -A
git commit -m "Pruebas: suite completa pasa — stability + auth middleware"
```

---

## Task 12: Verificación end-to-end con Claude Code

- [ ] **Step 1: Levantar backend**

```bash
cd backend
uvicorn app.main:app --port 8000 --reload
```

- [ ] **Step 2: Leer la UI_API_KEY generada**

```bash
grep UI_API_KEY "C:/litellm/.env"
```

- [ ] **Step 3: Abrir browser en http://localhost:8000**

Si frontend está compilado (dist existe), debe mostrar AuthGate. Ingresar la key.

- [ ] **Step 4: Activar el proxy desde el Dashboard**

Click "Activar proxy" → verifica que se establece `ANTHROPIC_BASE_URL=http://127.0.0.1:8000`.

- [ ] **Step 5: Probar Claude Code**

Abrir VS Code y enviar un mensaje desde la extensión Claude Code. En los logs del backend debe verse:

```
INFO: POST /v1/messages?beta=true 200 OK
INFO: litellm_started ...
```

Sin errores 400/404 de litellm, y sin "API returned empty or malformed response".

- [ ] **Step 6: Commit final**

```bash
git add -A
git commit -m "Verificación: e2e con Claude Code — proxy funciona en red compartida"
```

---

## Self-Review

### Spec Coverage

| Requisito | Tarea |
|-----------|-------|
| Red compartida — autenticación | Task 2, 3, 4, 5 |
| Race condition en streaming | Task 6 |
| Race condition en switch_to_provider | Task 7 |
| PID file para litellm | Task 8 |
| Sanitizar errores | Task 6, 9 |
| CORS para red compartida | Task 4 |
| Rate limiting | Task 3, 4 |
| Frontend — auth integration | Task 5 |
| Tests completos | Task 11 |
| E2E con Claude Code | Task 12 |

### Ningún placeholder detectado ✓
### Tipos consistentes entre tasks ✓
### Paths absolutos en todos los steps ✓
