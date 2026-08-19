import secrets
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


def _is_public(path: str) -> bool:
    if path in {"/api/health"}:
        return True
    # archivos estáticos y SPA
    if not path.startswith("/api") and not path.startswith("/v1"):
        return True
    return False


def _extract_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "")


def _matches(provided: str, expected: str) -> bool:
    return bool(expected) and secrets.compare_digest(provided, expected)


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, ui_key: str, proxy_key: str):
        super().__init__(app)
        self._ui_key = ui_key
        # /v1/* también acepta proxy_key: clientes Claude Code configurados antes
        # del cierre de /v1 tienen ANTHROPIC_API_KEY=proxy_key escrito
        self._proxy_key = proxy_key

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_public(path):
            return await call_next(request)

        if not self._ui_key:
            return JSONResponse(
                status_code=503,
                content={"detail": "Autenticación no configurada en el servidor"},
            )

        provided = _extract_key(request)
        if provided and _matches(provided, self._ui_key):
            return await call_next(request)
        if path.startswith("/v1") and provided and _matches(provided, self._proxy_key):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "API key inválida o faltante"},
            headers={"WWW-Authenticate": "Bearer"},
        )
