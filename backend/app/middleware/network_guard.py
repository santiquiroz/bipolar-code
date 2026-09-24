import ipaddress
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address

DEFAULT_ALLOWED_CIDRS = (
    "127.0.0.0/8", "::1/128",
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
    "169.254.0.0/16", "fe80::/10",
    "100.64.0.0/10",
)


def _parse_network(entry: str) -> Network:
    try:
        return ipaddress.ip_network(entry, strict=False)
    except ValueError as exc:
        raise ValueError(f"CONTROL_PLANE_ALLOWED_CIDRS: '{entry}' no es una red CIDR válida") from exc


def parse_allowed_networks(raw: str) -> tuple[Network, ...]:
    entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    return tuple(_parse_network(entry) for entry in entries or DEFAULT_ALLOWED_CIDRS)


def _parse_address(host: str) -> Address | None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def is_allowed_client(host: str, networks: tuple[Network, ...]) -> bool:
    address = _parse_address(host)
    return address is not None and any(address in net for net in networks)


def _is_control_plane(path: str) -> bool:
    return path.startswith("/api")


class ControlPlaneGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_cidrs: str = ""):
        super().__init__(app)
        self._networks = parse_allowed_networks(allowed_cidrs)

    async def dispatch(self, request: Request, call_next):
        if not _is_control_plane(request.url.path):
            return await call_next(request)
        # Solo el peer TCP: X-Forwarded-For lo controla el cliente
        peer = request.client.host if request.client else ""
        if is_allowed_client(peer, self._networks):
            return await call_next(request)
        return JSONResponse(
            status_code=403,
            content={"detail": "El plano de control solo acepta conexiones locales, de la LAN o de Tailscale"},
        )
