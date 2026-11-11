"""Per-client rate limiting and the request guard in front of every route.

:class:`RateLimiter` is an in-memory sliding window (one deque of timestamps per client key).
:class:`RequestGuardMiddleware` applies it to every ``POST`` and ``DELETE`` under ``/api/`` (and
a second one, ``CONTEXT_RATE_LIMIT_PER_HOUR``, to ``GET /api/context/`` and ``/api/places/``, and
a third, ``READ_RATE_LIMIT_PER_HOUR``, to every other ``GET`` under ``/api/``) and answers
``429 rate_limited`` with a ``Retry-After`` header. :class:`ByteBudget` caps the upload bytes of
one client in 24 hours (``UPLOAD_MB_PER_DAY``). It also rejects uploads whose
``Content-Length`` already exceeds the upload limit or the free storage before the body is read
(413, 507), state-changing requests from web pages on other origins (403), and, in development
mode, clients on public networks (403).
The limiter is per process: behind several API processes each one counts separately.

Client addresses (proxy handling, IPv6 /64 keys) come from :mod:`api.clients`.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .clients import client_ip, from_public_network, log_refusal, rate_key
from .errors import ApiError, CodedHTTPException, error_response
from .storage import Storage

MULTIPART_SLACK_BYTES = 1024 * 1024
"""Allowance for multipart framing and form fields on top of the file size limit."""

STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

DEV_LOCAL_ONLY_MESSAGE = (
    "This AirBrief server runs in development mode, which only answers clients on this machine "
    "or a private network. Its operator must set APP_ENV=production before serving it publicly."
)


class RateLimiter:
    """Sliding-window limiter: at most ``limit`` hits per ``window_s`` per key (0 = unlimited).

    At most ``max_keys`` keys are kept. A new key beyond that evicts the least recently used
    one, even if its window is still open, so memory stays bounded however many addresses call.
    """

    def __init__(
        self,
        limit: int,
        window_s: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 100_000,
    ) -> None:
        self.limit = int(limit)
        self.window_s = float(window_s)
        self.clock = clock
        self.max_keys = max(1, max_keys)
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def hit(self, key: str) -> tuple[bool, int]:
        """Record a hit for ``key``. Returns ``(allowed, retry_after_seconds)``."""
        if not self.enabled:
            return True, 0
        now = self.clock()
        start = now - self.window_s
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                while len(self._hits) >= self.max_keys:
                    self._hits.popitem(last=False)
                hits = deque()
                self._hits[key] = hits
            else:
                self._hits.move_to_end(key)
            while hits and hits[0] <= start:
                hits.popleft()
            if len(hits) >= self.limit:
                retry = max(1, math.ceil(hits[0] + self.window_s - now))
                return False, retry
            hits.append(now)
            return True, 0

    def remaining(self, key: str) -> int:
        """Hits left in the current window for ``key``."""
        if not self.enabled:
            return -1
        start = self.clock() - self.window_s
        with self._lock:
            hits = self._hits.get(key)
            used = sum(1 for t in hits if t > start) if hits else 0
        return max(0, self.limit - used)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class ByteBudget:
    """At most ``limit`` bytes per key over ``hours`` hours, counted in hourly buckets (0 = unlimited).

    Keys are kept like :class:`RateLimiter` keeps them; each holds at most ``hours`` numbers.
    """

    def __init__(
        self,
        limit: int,
        hours: int = 24,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 100_000,
    ) -> None:
        self.limit = int(limit)
        self.hours = hours
        self.clock = clock
        self.max_keys = max(1, max_keys)
        self._used: OrderedDict[str, dict[int, int]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def take(self, key: str, size: int) -> tuple[bool, int]:
        """Count ``size`` bytes for ``key`` if they fit. Returns ``(allowed, retry_after_seconds)``."""
        if not self.enabled:
            return True, 0
        now = self.clock()
        hour = int(now // 3600)
        with self._lock:
            buckets = self._used.get(key)
            if buckets is None:
                while len(self._used) >= self.max_keys:
                    self._used.popitem(last=False)
                buckets = self._used[key] = {}
            else:
                self._used.move_to_end(key)
            for old in [h for h in buckets if h <= hour - self.hours]:
                del buckets[old]
            if sum(buckets.values()) + size > self.limit:
                first = min(buckets, default=hour)
                return False, max(1, math.ceil((first + self.hours) * 3600 - now))
            buckets[hour] = buckets.get(hour, 0) + size
            return True, 0

    def refund(self, key: str, size: int) -> None:
        """Give back ``size`` bytes taken for ``key``, newest hour first (an upload that failed)."""
        if not self.enabled:
            return
        with self._lock:
            buckets = self._used.get(key) or {}
            for hour in sorted(buckets, reverse=True):
                back = min(size, buckets[hour])
                buckets[hour] -= back
                size -= back
                if not size:
                    break


class RequestGuardMiddleware:
    """ASGI middleware run before any route. In order, it refuses:

    * with ``local_only`` (development mode), every request from a public address
      (403 ``development_mode_local_only``);
    * with ``allowed_origins``, any ``POST``/``PUT``/``PATCH``/``DELETE`` under ``/api/`` whose
      ``Origin`` header is present and not in the list (403 ``cross_site_request``). Requests
      without ``Origin`` (curl, scripts) pass: only browsers send it;
    * request bodies over the limit (413), enforced twice: from ``Content-Length`` before anything
      is read, and by counting the bytes actually received (chunked requests have no length).
      Uploads may be ``max_upload_bytes`` plus multipart framing; every other body ``max_body_bytes``;
    * uploads whose ``Content-Length`` does not fit in ``storage`` (507 ``storage_full``);
    * ``POST`` and ``DELETE`` under ``/api/`` over the ``limiter`` budget (429);
    * with ``upload_budget``, uploads past the client's bytes for the day (429
      ``upload_budget_exceeded``), and uploads without a ``Content-Length`` (411), which it could
      not count in advance.

    ``context_limiter`` (optional) limits ``GET`` requests to ``context_paths`` the same way, with its
    own budget: the context layers and place search, which anyone can call and which can return
    megabytes of data-pack data per request.
    ``read_limiter`` (optional) gives every other ``GET`` under ``/api/`` a third budget: results,
    exports and listings can be large too, and nobody needs to sign in to read them.

    Every refusal is written to the security log (:func:`api.clients.log_refusal`).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter,
        max_upload_bytes: int,
        max_body_bytes: int = 10 * 1024 * 1024,
        proxy_hops: int = 0,
        context_limiter: RateLimiter | None = None,
        context_paths: tuple[str, ...] = (),
        read_limiter: RateLimiter | None = None,
        upload_budget: ByteBudget | None = None,
        allowed_origins: list[str] | None = None,
        local_only: bool = False,
        storage: Storage | None = None,
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.max_upload_bytes = max_upload_bytes
        self.max_body_bytes = max_body_bytes
        self.proxy_hops = proxy_hops
        self.context_limiter = context_limiter
        self.context_paths = frozenset(p.rstrip("/") for p in context_paths)
        self.read_limiter = read_limiter
        self.upload_budget = upload_budget
        self.allowed_origins = (
            None if allowed_origins is None else frozenset(o.rstrip("/") for o in allowed_origins)
        )
        self.local_only = local_only
        self.storage = storage

    async def _refuse(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        log_refusal(scope, self.proxy_hops, status, code)
        await error_response(status, code, message, details, headers)(scope, receive, send)

    async def _rate_limited(self, limiter: RateLimiter, scope: Scope, receive: Receive, send: Send) -> bool:
        """Count the request against ``limiter``; answer 429 and return True when over it."""
        allowed, retry_after = limiter.hit(rate_key(client_ip(scope, self.proxy_hops)))
        if allowed:
            return False
        await self._refuse(
            scope,
            receive,
            send,
            429,
            "rate_limited",
            f"Too many requests: the limit is {limiter.limit} per hour. Retry in {retry_after} s.",
            {"retry_after_s": retry_after, "limit_per_hour": limiter.limit},
            {"Retry-After": str(retry_after)},
        )
        return True

    def _foreign_origin(self, scope: Scope) -> bool:
        if self.allowed_origins is None or "*" in self.allowed_origins:
            return False
        origin = _header(scope, b"origin")
        return origin is not None and origin.rstrip("/") not in self.allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        method = scope.get("method")
        if self.local_only and from_public_network(scope, self.proxy_hops):
            await self._refuse(scope, receive, send, 403, "development_mode_local_only", DEV_LOCAL_ONLY_MESSAGE)
            return
        if method in STATE_CHANGING and path.startswith("/api/") and self._foreign_origin(scope):
            await self._refuse(
                scope,
                receive,
                send,
                403,
                "cross_site_request",
                "This request came from a web page on another site. The API only accepts changes "
                "from the AirBrief web app's own origin (CORS_ORIGINS).",
            )
            return
        if method == "GET" and path.startswith("/api/"):
            limiter = self.context_limiter if path.rstrip("/") in self.context_paths else self.read_limiter
            if limiter is not None and limiter.enabled and await self._rate_limited(limiter, scope, receive, send):
                return
            await self.app(scope, receive, send)
            return
        if method not in ("POST", "DELETE") or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        is_upload = method == "POST" and path.rstrip("/") == "/api/files"
        limit = self.max_upload_bytes + MULTIPART_SLACK_BYTES if is_upload else self.max_body_bytes
        code = "file_too_large" if is_upload else "request_too_large"
        limit_mb = (self.max_upload_bytes if is_upload else self.max_body_bytes) / (1024 * 1024)
        message = (
            f"The file is larger than the {limit_mb:g} MB upload limit."
            if is_upload
            else f"The request body is larger than {limit_mb:g} MB."
        )
        length = _content_length(scope)
        if length is not None and length > limit:
            await self._refuse(
                scope, receive, send, 413, code, message,
                {"limit_bytes": limit, "content_length": length}, {"Connection": "close"},
            )
            return

        if is_upload and length is not None and self.storage is not None:
            try:
                self.storage.check_space(length)
            except ApiError as exc:
                await self._refuse(
                    scope, receive, send, exc.http_status, exc.code, exc.message, exc.details,
                    {"Connection": "close"},
                )
                return

        if await self._rate_limited(self.limiter, scope, receive, send):
            return

        charged: tuple[str, int] | None = None
        if is_upload and self.upload_budget is not None and self.upload_budget.enabled:
            if length is None:
                await self._refuse(
                    scope, receive, send, 411, "length_required",
                    "Uploads need a Content-Length header on this server.", None, {"Connection": "close"},
                )
                return
            day_mb = round(self.upload_budget.limit / (1024 * 1024), 4)
            if length > self.upload_budget.limit:
                await self._refuse(
                    scope, receive, send, 413, "file_too_large",
                    f"The file is larger than the {day_mb:g} MB one address may upload here in 24 hours.",
                    {"limit_bytes_per_day": self.upload_budget.limit, "content_length": length}, {"Connection": "close"},
                )
                return
            key = rate_key(client_ip(scope, self.proxy_hops))
            allowed, retry = self.upload_budget.take(key, length)
            if not allowed:
                await self._refuse(
                    scope, receive, send, 429, "upload_budget_exceeded",
                    f"This address has uploaded its {day_mb:g} MB for the last 24 hours. Retry in {retry} s.",
                    {"retry_after_s": retry, "limit_bytes_per_day": self.upload_budget.limit},
                    {"Retry-After": str(retry), "Connection": "close"},
                )
                return
            charged = (key, length)

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > limit:
                    # FastAPI re-raises HTTPException from body parsing, so this becomes a 413.
                    raise CodedHTTPException(413, code, message, {"limit_bytes": limit})
            return msg

        if charged is None:
            await self.app(scope, limited_receive, send)
            return
        status = 500

        async def recording_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, limited_receive, recording_send)
        finally:
            if not 200 <= status < 300 and self.upload_budget is not None:
                # nothing was stored, so it does not count against the day
                self.upload_budget.refund(*charged)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def _content_length(scope: Scope) -> int | None:
    value = _header(scope, b"content-length")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
