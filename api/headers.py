"""Security headers for every API response.

* ``X-Content-Type-Options: nosniff`` — browsers never guess content types (downloads stay data).
* ``X-Frame-Options: DENY`` and a ``frame-ancestors 'none'`` CSP on API responses — no clickjacking.
* ``Referrer-Policy: no-referrer`` — ids in URLs never leak to other sites.
* ``Cache-Control: no-store`` on ``/api/`` — results and owner tokens are not kept by shared caches.
* ``Strict-Transport-Security`` when ``APP_ENV=production`` — browsers stay on HTTPS.

``/docs`` and ``/redoc`` (:func:`add_docs_pages`, only when ``API_DOCS`` is on) load Swagger UI and
ReDoc from jsDelivr at pinned versions, with a CSP that allows exactly those files.
"""

from __future__ import annotations

import base64
import hashlib
import re

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_COMMON: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()"),
]
_API_ONLY: list[tuple[bytes, bytes]] = [
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"),
]
_NO_STORE = (b"cache-control", b"no-store")
_HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")

SWAGGER_UI_DIST = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.33.1"
REDOC_JS = "https://cdn.jsdelivr.net/npm/redoc@2.5.4/bundles/redoc.standalone.js"

_INLINE_SCRIPT = re.compile(rb"<script>(.*?)</script>", re.S)


class SecurityHeadersMiddleware:
    """Pure ASGI middleware adding the headers above (existing headers are not overwritten)."""

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        is_api = path.startswith("/api/") or path in ("/health", "/health/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                extra = _COMMON + (_API_ONLY if is_api else [])
                if path.startswith("/api/"):
                    extra = extra + [_NO_STORE]
                if self.hsts:
                    extra = extra + [_HSTS]
                for name, value in extra:
                    if name not in present:
                        headers.append((name, value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _docs_csp(page: HTMLResponse, script_url: str, styles: str) -> str:
    # the Swagger page boots from one inline script; allow exactly that script by its hash
    hashes = [
        "'sha256-" + base64.b64encode(hashlib.sha256(s).digest()).decode() + "'"
        for s in _INLINE_SCRIPT.findall(page.body)
    ]
    return "; ".join(
        [
            "default-src 'none'",
            " ".join(["script-src", script_url, *hashes]),
            f"style-src {styles}",
            # also keeps out ReDoc's footer logo, a request to cdn.redoc.ly
            "img-src 'self' data:",
            "connect-src 'self'",
            "worker-src blob:",  # ReDoc's search worker
            "frame-ancestors 'none'",
            "base-uri 'none'",
            "form-action 'none'",
        ]
    )


def add_docs_pages(app: FastAPI) -> None:
    """Serve ``/docs`` and ``/redoc`` like FastAPI's defaults, but from pinned asset versions, with
    no favicon or Google Fonts requests, and with a CSP that only allows those assets."""
    openapi_url = app.openapi_url or "/openapi.json"
    swagger_js = f"{SWAGGER_UI_DIST}/swagger-ui-bundle.js"
    swagger_css = f"{SWAGGER_UI_DIST}/swagger-ui.css"

    def spec_url(request: Request) -> str:
        return request.scope.get("root_path", "").rstrip("/") + openapi_url

    @app.get("/docs", include_in_schema=False)
    def swagger_ui(request: Request) -> HTMLResponse:
        page = get_swagger_ui_html(
            openapi_url=spec_url(request),
            title=f"{app.title} - Swagger UI",
            swagger_js_url=swagger_js,
            swagger_css_url=swagger_css,
            swagger_favicon_url="data:,",
        )
        page.headers["Content-Security-Policy"] = _docs_csp(page, swagger_js, f"{swagger_css} 'unsafe-inline'")
        return page

    @app.get("/redoc", include_in_schema=False)
    def redoc_ui(request: Request) -> HTMLResponse:
        page = get_redoc_html(
            openapi_url=spec_url(request),
            title=f"{app.title} - ReDoc",
            redoc_js_url=REDOC_JS,
            redoc_favicon_url="data:,",
            with_google_fonts=False,
        )
        # ReDoc injects <style> elements at runtime
        page.headers["Content-Security-Policy"] = _docs_csp(page, REDOC_JS, "'unsafe-inline'")
        return page
