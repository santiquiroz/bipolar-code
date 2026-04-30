import time
import collections
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_WINDOW = 60.0


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

        bucket = self._buckets.setdefault(ip, collections.deque())

        while bucket and bucket[0] < now - _WINDOW:
            bucket.popleft()

        # Eliminar entradas de IPs sin actividad reciente para evitar crecimiento ilimitado
        if not bucket:
            del self._buckets[ip]
            bucket = collections.deque()
            self._buckets[ip] = bucket

        if len(bucket) >= self._rpm:
            retry_after = int(_WINDOW - (now - bucket[0])) + 1
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit excedido. Intenta en {retry_after}s."},
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)
        return await call_next(request)
