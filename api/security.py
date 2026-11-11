"""Server-side URL fetching with an SSRF guard (used for FlightMD reports).

Rules applied to every URL, including each redirect target:

* only ``https`` on port 443. The host of ``FLIGHTMD_API_URL`` is the one exception (it may be a
  self-hosted instance on a private address, plain http, its own port), and on it only
  ``{FLIGHTMD_API_URL}/report/{id}`` is fetched;
* no credentials in the URL;
* the host name is resolved and **every** address must be public: no private, loopback,
  link-local, multicast, reserved, unspecified or shared addresses, no NAT64 or IPv4-compatible
  IPv6 addresses, and no IPv6 form that embeds a non-public IPv4 address;
* the connection goes to the address that was checked (no second DNS lookup, which defeats
  DNS rebinding), with TLS certificate and host-name verification;
* responses are capped at 5 MB; each request (the first and every redirect) gets
  ``FLIGHTMD_FETCH_TIMEOUT_S``;
* at most three redirects.

Clients learn only which rule (``details.rule``) or stage (``details.stage``) failed. Host
names, addresses, exception text and HTTP statuses go to the ``airbrief.api.fetch`` log (INFO).

FlightMD share links of the form ``https://<site>/report/<id>`` are rewritten to
``{FLIGHTMD_API_URL}/report/<id>`` when an API URL is configured, and a ``{"report": ...}``
wrapper is removed.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote, urljoin, urlsplit

from airbrief_core.errors import AirBriefError
from airbrief_core.geo.parse import check_json_limits

if TYPE_CHECKING:
    from .config import Settings

log = logging.getLogger("airbrief.api.fetch")

MAX_REDIRECTS = 3
_READ_CHUNK = 64 * 1024
_REPORT_PATH_RE = re.compile(r"/report/([^/?#]+)/?$")
_TRUSTED_PATH = r"/report/[A-Za-z0-9_-]{1,128}/?"
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_PRIVATE = "The link points to a private or reserved network address."
_NO_DOWNLOAD = "The link could not be downloaded"

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _for_log(url: str) -> str:
    # share links can carry a token in the query string
    return url.split("?", 1)[0].split("#", 1)[0][:500]


def _log(what: str, code: str, url: str, context: str, detail: dict[str, Any]) -> None:
    extra = " ".join(f"{k}={v}" for k, v in detail.items() if v is not None)
    log.info("FlightMD fetch %s (%s): %s url=%s %s", what, context or "-", code, _for_log(url), extra)


def _not_allowed(rule: str, message: str, url: str, context: str, **detail: Any) -> AirBriefError:
    _log("refused", f"rule={rule}", url, context, detail)
    return AirBriefError("url_not_allowed", message, details={"rule": rule})


def _fetch_failed(stage: str, message: str, url: str, context: str, **detail: Any) -> AirBriefError:
    _log("failed", f"stage={stage}", url, context, detail)
    return AirBriefError("fetch_failed", message, details={"stage": stage})


def resolve_host(host: str, port: int) -> list[str]:
    """All IP addresses of ``host`` (tests monkeypatch this function)."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def embedded_ipv4(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 address stands for: IPv4-mapped, 6to4, well-known NAT64
    (``64:ff9b::/96``) or IPv4-compatible (``::a.b.c.d``). None for other addresses."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in _NAT64[0] or address in _IPV4_COMPATIBLE:
        return ipaddress.IPv4Address(int(address) & 0xFFFF_FFFF)
    return None


def is_public_address(address: IPAddress) -> bool:
    """True only for globally routable unicast addresses."""
    if isinstance(address, ipaddress.IPv6Address):
        if address.teredo is not None:
            return False
        inner = embedded_ipv4(address)
        if inner is not None and not is_public_address(inner):
            return False
        # A NAT64 gateway (or an old IPv4-compatible route) reaches IPv4 hosts on its own side,
        # whatever the embedded address looks like from here.
        if address in _IPV4_COMPATIBLE or any(address in net for net in _NAT64):
            return False
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or not address.is_global
    )


@dataclass(frozen=True)
class CheckedUrl:
    """A URL that passed the guard, with the addresses that may be connected to."""

    url: str
    scheme: str
    host: str
    port: int
    target: str
    addresses: list[str] = field(default_factory=list)
    trusted: bool = False


def check_url(
    url: str,
    *,
    trusted_hosts: frozenset[tuple[str, int]] = frozenset(),
    trusted_prefix: str = "",
    context: str = "",
) -> CheckedUrl:
    """Validate ``url`` against the SSRF rules and resolve it.

    ``trusted_hosts`` holds ``(hostname, port)`` pairs (the configured FlightMD API) for which
    plain http and non-public addresses are allowed, but only the path
    ``{trusted_prefix}/report/{id}`` without a query. ``context`` names the caller in the log.

    Raises:
        AirBriefError: ``url_not_allowed`` (422) when a rule is broken, ``fetch_failed`` when
            the host name cannot be resolved.
    """
    text = (url or "").strip()
    if not text or len(text) > 2000:
        raise _not_allowed("length", "The link is empty or longer than 2,000 characters.", text, context)
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise _not_allowed("characters", "The link contains spaces or control characters.", text, context)
    try:
        parts = urlsplit(text)
        port_value = parts.port
    except ValueError as exc:
        raise _not_allowed("syntax", "The link is not a valid URL.", text, context, reason=exc) from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise _not_allowed("scheme", "Only https links can be fetched.", text, context, scheme=scheme)
    if parts.username is not None or parts.password is not None:
        raise _not_allowed("credentials", "Links with a user name or password are not allowed.", text, context)
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise _not_allowed("host", "The link has no host name.", text, context)
    port = port_value or (443 if scheme == "https" else 80)
    trusted = (host, port) in trusted_hosts
    if trusted:
        if parts.query or not re.fullmatch(re.escape(trusted_prefix) + _TRUSTED_PATH, parts.path):
            raise _not_allowed(
                "path",
                "Only report links (…/report/<id>) can be fetched from the configured FlightMD server.",
                text,
                context,
                host=host,
                path=parts.path[:200],
            )
    elif scheme != "https":
        raise _not_allowed("scheme", "Only https links can be fetched.", text, context, scheme=scheme)
    elif port != 443:
        raise _not_allowed("port", "Only the standard https port (443) can be used.", text, context, port=port)

    try:
        literal: IPAddress | None = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None

    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise _not_allowed("host", "The link's host name is not valid.", text, context, host=host) from exc
        try:
            addresses = resolve_host(ascii_host, port)
        except (OSError, UnicodeError) as exc:
            raise _fetch_failed(
                "resolve", f"{_NO_DOWNLOAD}: its host name was not found.", text, context, host=host, reason=exc
            ) from exc
        host = ascii_host
    if not addresses:
        raise _fetch_failed("resolve", f"{_NO_DOWNLOAD}: its host name was not found.", text, context, host=host)

    if not trusted:
        for value in addresses:
            try:
                address = ipaddress.ip_address(value.split("%", 1)[0])
            except ValueError as exc:
                raise _not_allowed("private_address", _PRIVATE, text, context, host=host, address=value) from exc
            if not is_public_address(address):
                inner = embedded_ipv4(address) if isinstance(address, ipaddress.IPv6Address) else None
                raise _not_allowed(
                    "private_address", _PRIVATE, text, context, host=host, address=address, embeds=inner
                )

    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    return CheckedUrl(
        url=text,
        scheme=scheme,
        host=host,
        port=port,
        target=target,
        addresses=addresses,
        trusted=trusted,
    )


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection to a pre-resolved address (Host header keeps the name)."""

    def __init__(self, host: str, port: int, *, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection to a pre-resolved address with SNI and certificate checks on the name."""

    def __init__(self, host: str, port: int, *, address: str, timeout: float) -> None:
        context = ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=context)
        self._address = address
        self._tls_context = context

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._tls_context.wrap_socket(sock, server_hostname=self.host)


@dataclass
class _Response:
    status: int
    headers: dict[str, str]
    body: bytes


def _http_get(
    checked: CheckedUrl, *, timeout_s: float, max_bytes: int, user_agent: str, context: str
) -> _Response:
    """GET ``checked`` from its first reachable address, reading at most ``max_bytes``."""
    deadline = time.monotonic() + timeout_s
    too_large = f"The report is larger than {max_bytes // (1024 * 1024)} MB."
    no_answer = f"{_NO_DOWNLOAD}: no answer in time."
    last_error: Exception | None = None
    for address in checked.addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        conn_cls = _PinnedHTTPSConnection if checked.scheme == "https" else _PinnedHTTPConnection
        conn = conn_cls(checked.host, checked.port, address=address, timeout=remaining)
        try:
            conn.request(
                "GET",
                checked.target,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "User-Agent": user_agent,
                },
            )
            resp = conn.getresponse()
            headers = {k.lower(): v for k, v in resp.getheaders()}
            declared = headers.get("content-length")
            if declared is not None and declared.isdigit() and int(declared) > max_bytes:
                raise _fetch_failed("too_large", too_large, checked.url, context, declared=declared)
            chunks: list[bytes] = []
            size = 0
            if resp.status not in _REDIRECT_STATUSES:
                while True:
                    if time.monotonic() > deadline:
                        raise _fetch_failed("connect", no_answer, checked.url, context, address=address, reason="read timeout")
                    chunk = resp.read(_READ_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise _fetch_failed("too_large", too_large, checked.url, context, read=size)
                    chunks.append(chunk)
            return _Response(status=resp.status, headers=headers, body=b"".join(chunks))
        except AirBriefError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            last_error = exc
        finally:
            conn.close()
    reason = f"{type(last_error).__name__}: {last_error}" if last_error else "timeout"
    raise _fetch_failed(
        "connect",
        no_answer,
        checked.url,
        context,
        host=checked.host,
        addresses=",".join(checked.addresses),
        reason=reason,
    )


def trusted_hosts_for(settings: Settings) -> frozenset[tuple[str, int]]:
    """The hosts exempt from the https/public-address rules (the configured FlightMD API)."""
    host = settings.flightmd_host
    return frozenset({host}) if host else frozenset()


def fetch_json(
    url: str,
    *,
    timeout_s: float,
    max_bytes: int,
    trusted_hosts: frozenset[tuple[str, int]] = frozenset(),
    trusted_prefix: str = "",
    context: str = "",
) -> Any:
    """Fetch and parse JSON from ``url`` under the SSRF rules (redirects re-checked)."""
    from airbrief_core import __version__

    user_agent = f"AirBrief/{__version__} (+https://github.com/Praddyx15/AirBrief)"
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        checked = check_url(current, trusted_hosts=trusted_hosts, trusted_prefix=trusted_prefix, context=context)
        response = _http_get(
            checked, timeout_s=timeout_s, max_bytes=max_bytes, user_agent=user_agent, context=context
        )
        if response.status in _REDIRECT_STATUSES:
            location = response.headers.get("location")
            if not location:
                message = f"{_NO_DOWNLOAD}: it redirected without a target."
                raise _fetch_failed("redirects", message, current, context, status=response.status)
            current = urljoin(checked.url, location)
            continue
        if response.status != 200:
            message = f"{_NO_DOWNLOAD}: the server did not return the report."
            raise _fetch_failed("http_status", message, current, context, status=response.status)
        try:
            return json.loads(response.body.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise _fetch_failed("not_json", "The link did not return JSON.", current, context, reason=exc) from exc
    raise _fetch_failed("redirects", f"{_NO_DOWNLOAD}: it redirected too many times.", url, context)


def resolve_flightmd_url(url: str, api_url: str | None) -> str:
    """Map a FlightMD share link ``…/report/{id}`` to ``{api_url}/report/{id}``.

    URLs already on the API host, URLs without a ``/report/{id}`` path, and every URL when no
    API is configured are returned unchanged.
    """
    text = url.strip()
    if not api_url:
        return text
    try:
        parts = urlsplit(text)
        api = urlsplit(api_url)
        same_host = (parts.hostname or "").lower() == (api.hostname or "").lower() and (
            parts.port or (443 if parts.scheme == "https" else 80)
        ) == (api.port or (443 if api.scheme == "https" else 80))
    except ValueError:
        return text
    if same_host:
        return text
    match = _REPORT_PATH_RE.search(parts.path)
    if not match:
        return text
    report_id = quote(unquote(match.group(1)), safe="")
    return f"{api_url.rstrip('/')}/report/{report_id}"


def unwrap_report(data: Any) -> Any:
    """Remove a ``{"report": {...}}`` wrapper."""
    if isinstance(data, dict) and isinstance(data.get("report"), dict):
        return data["report"]
    return data


def link_allowed(url: str, settings: Settings) -> bool:
    """Whether ``url`` may become the "Open in FlightMD" link of an audit: https, or outside
    production also plain http to a loopback host (a FlightMD running on the same computer)."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname or ""
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    if not host or parts.username is not None or parts.password is not None:
        return False
    if scheme == "https":
        return True
    if scheme != "http" or settings.is_production:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def fetch_flightmd_report(
    url: str, settings: Settings, *, validate: bool = True, context: str = ""
) -> dict[str, Any]:
    """Fetch a FlightMD report JSON by URL (share link or API URL) under the SSRF guard.

    ``validate=False`` skips the full parse for callers that parse the report themselves right
    after (analysis creation does, in ``input_bbox``), so a large track is not parsed twice.
    ``context`` says who asked, for the log.
    """
    api_url = settings.flightmd_api_url
    resolved = resolve_flightmd_url(url, api_url)
    data = fetch_json(
        resolved,
        timeout_s=settings.flightmd_fetch_timeout_s,
        max_bytes=settings.flightmd_max_bytes,
        trusted_hosts=trusted_hosts_for(settings),
        trusted_prefix=urlsplit(api_url).path if api_url else "",
        context=context,
    )
    report = unwrap_report(data)
    if not isinstance(report, dict):
        raise AirBriefError("report_invalid", "The link did not return a FlightMD report.")
    # a report goes on into the analysis params: same nesting cap as anything sent there
    check_json_limits(report)
    if validate:
        # Only real FlightMD reports are passed on: the endpoint must not work as a general JSON proxy.
        from airbrief_core.integrations.flightmd import parse_flightmd_report

        parse_flightmd_report(report)  # raises AnalysisError("report_invalid") for anything else
    return report
