import time
import collections
import ipaddress
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_WINDOW = 60.0
_DEFAULT_MAX_KEYS = 10_000

TrustedProxies = tuple[frozenset[str], tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]]


def _as_network(entry: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    try:
        return ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return None


def _as_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def parse_trusted_proxies(raw: str) -> TrustedProxies:
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    networks = tuple(net for net in map(_as_network, entries) if net is not None)
    names = frozenset(entry for entry in entries if _as_network(entry) is None)
    return names, networks


def is_trusted_proxy(host: str, trusted: TrustedProxies) -> bool:
    names, networks = trusted
    if host in names:
        return True
    address = _as_address(host)
    return address is not None and any(address in net for net in networks)


def resolve_client_ip(peer: str, forwarded_for: str, trusted: TrustedProxies) -> str:
    if not forwarded_for or not is_trusted_proxy(peer, trusted):
        return peer
    hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    # Cada proxy agrega a la derecha: el primer salto no confiable desde ahí es el cliente real
    for hop in reversed(hops):
        if not is_trusted_proxy(hop, trusted):
            return hop
    return hops[0] if hops else peer


def _drop_expired(bucket: collections.deque, cutoff: float) -> None:
    while bucket and bucket[0] < cutoff:
        bucket.popleft()


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, rpm: int = 120, trusted_proxies: str = "", max_keys: int = _DEFAULT_MAX_KEYS):
        super().__init__(app)
        self._rpm = rpm
        self._trusted = parse_trusted_proxies(trusted_proxies)
        self._max_keys = max_keys
        self._buckets: collections.OrderedDict[str, collections.deque] = collections.OrderedDict()

    def _client_ip(self, request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        return resolve_client_ip(peer, request.headers.get("x-forwarded-for", ""), self._trusted)

    def _sweep_idle(self, cutoff: float) -> None:
        while self._buckets:
            oldest = next(iter(self._buckets.values()))
            if oldest and oldest[-1] >= cutoff:
                return
            self._buckets.popitem(last=False)

    def _touch_bucket(self, ip: str) -> collections.deque:
        if ip in self._buckets:
            self._buckets.move_to_end(ip)
            return self._buckets[ip]
        bucket = self._buckets[ip] = collections.deque()
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        return bucket

    def admit(self, ip: str, now: float) -> int | None:
        cutoff = now - _WINDOW
        self._sweep_idle(cutoff)
        bucket = self._touch_bucket(ip)
        _drop_expired(bucket, cutoff)
        if len(bucket) >= self._rpm:
            return int(_WINDOW - (now - bucket[0])) + 1
        bucket.append(now)
        return None

    async def dispatch(self, request: Request, call_next):
        if self._rpm <= 0:
            return await call_next(request)

        retry_after = self.admit(self._client_ip(request), time.monotonic())
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit excedido. Intenta en {retry_after}s."},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
