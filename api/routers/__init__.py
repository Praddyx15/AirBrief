"""HTTP routers. Paths follow docs/SPEC.md §4 exactly (with trailing slashes); every route is also
registered without (or, for ``/health``, with) the trailing slash as a hidden alias, because the
application runs with ``redirect_slashes=False`` (redirects break CORS preflights and POSTs).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter
from fastapi.responses import Response

F = TypeVar("F", bound=Callable[..., Any])


def route(router: APIRouter, method: str, path: str, **kwargs: Any) -> Callable[[F], F]:
    """Register ``path`` and its slash alias (hidden from the OpenAPI schema) for ``method``."""

    def decorator(func: F) -> F:
        router.add_api_route(path, func, methods=[method], **kwargs)
        alias = path[:-1] if path.endswith("/") else f"{path}/"
        if alias and alias != path:
            alias_kwargs = dict(kwargs)
            alias_kwargs["include_in_schema"] = False
            alias_kwargs.pop("operation_id", None)
            router.add_api_route(alias, func, methods=[method], **alias_kwargs)
        return func

    return decorator


MAX_LIST_IDS = 100


def parse_ids(text: str | None) -> list[str] | None:
    """Parse ``?ids=a,b,c`` (comma separated, at most :data:`MAX_LIST_IDS`), or None when absent."""
    from ..errors import ApiError

    if text is None:
        return None
    ids = list(dict.fromkeys(part.strip() for part in text.split(",") if part.strip()))
    if len(ids) > MAX_LIST_IDS or any(len(i) > 64 for i in ids):
        raise ApiError(
            "invalid_params",
            f"ids must be at most {MAX_LIST_IDS} comma-separated ids of up to 64 characters.",
            details={"count": len(ids)},
        )
    return ids


def require_scope(scoped: bool, allowed: bool, what: str) -> None:
    """Refuse an unscoped listing when global listing is disabled (public instances)."""
    from ..errors import ApiError

    if not scoped and not allowed:
        raise ApiError(
            "listing_disabled",
            f"Listing every {what} is disabled on this server; pass ?ids= with the ids you created.",
            details={"setting": "ALLOW_GLOBAL_LISTING"},
        )


def attachment_headers(filename: str) -> dict[str, str]:
    """``Content-Disposition: attachment`` with a safe ASCII file name, and no caching."""
    safe = "".join(ch for ch in filename if ch.isascii() and (ch.isalnum() or ch in "._-")) or "download"
    return {"Content-Disposition": f'attachment; filename="{safe}"', "Cache-Control": "no-store"}


def download_response(content: bytes, media_type: str, filename: str) -> Response:
    """A file download with ``Content-Disposition: attachment``."""
    return Response(content=content, media_type=media_type, headers=attachment_headers(filename))
