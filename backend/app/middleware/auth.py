import secrets
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


def _is_public(path: str) -> bool:
    if path in {"/api/health"}:
        return True
    # archivos estáticos: cualquier cosa sin prefijo /api o /v1
    if not path.startswith("/api") and not path.startswith("/v1"):
        return True
    return False


def _extract_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "")


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str):
        super().__init__(app)
        self._key = api_key

    async def dispatch(self, request: Request, call_next):
        if _is_public(request.url.path):
            return await call_next(request)

        if not self._key:
            return JSONResponse(
                status_code=503,
                content={"detail": "Autenticación no configurada en el servidor"},
            )

        provided = _extract_key(request)
        if secrets.compare_digest(provided, self._key):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "API key inválida o faltante"},
            headers={"WWW-Authenticate": "Bearer"},
        )
