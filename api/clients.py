"""Who is calling: the client address, the rate-limit key built from it, and the security log.

Behind a reverse proxy the peer address is the proxy's. Uvicorn rewrites it from
``X-Forwarded-For`` only for proxies in ``--forwarded-allow-ips`` (default 127.0.0.1, which
covers Caddy or nginx on the same machine). For a hosted proxy with changing addresses (Render
and similar) set ``TRUSTED_PROXY_HOPS`` instead: see :func:`client_ip`.

Refused requests are logged on ``airbrief.security``, one WARNING line each::

    <code> <status> client=<key> path=<path>

``key`` is the first 12 hex digits of SHA-256 over a random per-process salt and the client
address: lines from one client can be grouped, but the address itself is not written. Query
strings, headers and bodies are never logged.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import secrets
import threading
from collections import Counter

from starlette.types import Scope

security_log = logging.getLogger("airbrief.security")

_SALT = secrets.token_bytes(16)
_MAX_LOGGED_PATH = 200
_refusals: Counter[str] = Counter()
_refusals_lock = threading.Lock()


def forwarded_for(scope: Scope) -> list[str]:
    """The ``X-Forwarded-For`` entries of a request, leftmost first."""
    values = [v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"x-forwarded-for"]
    return [h.strip() for h in ",".join(values).split(",") if h.strip()]


def client_ip(scope: Scope, proxy_hops: int = 0) -> str:
    """The client address of a request.

    ``proxy_hops`` = the number of trusted proxies in front of the API that append to
    ``X-Forwarded-For``. The client address is then the entry the outermost of them appended:
    the ``proxy_hops``-th from the right. Entries further left come from the client and can be
    forged, so they are never used. With 0 (default) the peer address is used as is.
    Only set it when the API cannot be reached except through those proxies.
    """
    if proxy_hops > 0:
        hops = forwarded_for(scope)
        if hops:
            return _without_port(hops[-proxy_hops] if len(hops) >= proxy_hops else hops[0])
    client = scope.get("client")
    if client and client[0]:
        return str(client[0])
    return "unknown"


def _without_port(entry: str) -> str:
    # some proxies (Azure Application Gateway, App Service) append "address:port"
    if entry.startswith("[") and "]" in entry:
        return entry[1:entry.index("]")]
    if entry.count(":") == 1:
        return entry.split(":", 1)[0]
    return entry


def _ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def rate_key(address: str) -> str:
    """The rate-limit key of a client address: IPv4 as is, IPv6 by its /64.

    One subscriber usually gets a whole /64, so keying on the full IPv6 address would give
    them billions of separate allowances. IPv4-mapped addresses count as their IPv4 address.
    """
    ip = _ip(address)
    if ip is None:
        return address
    if ip.version == 6:
        return str(ipaddress.IPv6Network((int(ip) >> 64 << 64, 64)))
    return str(ip)


def is_public(address: str) -> bool:
    """True for a globally routable address.

    Loopback, private (LAN, Docker bridge), link-local and shared (CGNAT) ranges are not public,
    and neither is anything that is not an IP address (a Unix socket, the test client).
    """
    ip = _ip(address)
    return ip is not None and ip.is_global


def from_public_network(scope: Scope, proxy_hops: int = 0) -> bool:
    """True when the client, or the address the nearest proxy forwarded for, is public.

    Without ``TRUSTED_PROXY_HOPS`` the forwarded entry still counts here: on a hosted platform
    the peer is the platform's proxy with a private address, so the peer alone would make every
    request look local. A forged entry can only get its own request refused.
    """
    if is_public(client_ip(scope, proxy_hops)):
        return True
    if proxy_hops == 0:
        hops = forwarded_for(scope)
        return bool(hops) and is_public(hops[-1])
    return False


def client_key(address: str) -> str:
    """A short salted hash of a client address, for log lines."""
    return hashlib.sha256(_SALT + address.encode("utf-8", "replace")).hexdigest()[:12]


def log_refusal(scope: Scope, proxy_hops: int, status: int, code: str) -> None:
    """Write the security log line for a refused request."""
    path = str(scope.get("path", ""))[:_MAX_LOGGED_PATH]
    # the path is percent-decoded, so it can hold newlines: keep the line a single line
    path = path.encode("unicode_escape").decode("ascii")
    security_log.warning(
        "%s %d client=%s path=%s", code, status, client_key(client_ip(scope, proxy_hops)), path
    )
    with _refusals_lock:
        _refusals[code] += 1


def refusal_counts() -> dict[str, int]:
    """Refusals per code since this process started: the lines of the security log, counted."""
    with _refusals_lock:
        return dict(_refusals)


def on_this_machine(scope: Scope) -> bool:
    """The peer is a loopback address and no proxy forwarded the request."""
    if forwarded_for(scope):
        return False
    peer = scope.get("client")
    if not peer:
        return False
    try:
        return ipaddress.ip_address(peer[0]).is_loopback
    except ValueError:
        return False
