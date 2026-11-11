"""Error envelope and exception handlers.

Every error response has the body ``{"error": {"code", "message", "details"}}``. Core errors
(:class:`~airbrief_core.errors.AirBriefError`) carry their HTTP status through
``HTTP_STATUS_BY_CODE``; the API adds a few codes of its own here.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from airbrief_core.errors import HTTP_STATUS_BY_CODE, AirBriefError
from airbrief_core.geo.parse import MAX_NESTING

from .clients import log_refusal

log = logging.getLogger("airbrief.api")

API_STATUS_BY_CODE: dict[str, int] = {
    "listing_disabled": 403,
    "url_not_allowed": 422,
    "dataset_not_ready": 409,
    "analysis_not_finished": 409,
    "sample_not_dataset": 422,
    "method_not_allowed": 405,
    "service_unavailable": 503,
    "storage_full": 507,
}

# Refusals worth an operator's attention go to the security log (api/clients.py).
_LOGGED_STATUSES = frozenset({403, 413, 429, 507})
_LOGGED_CODES = frozenset({"url_not_allowed"})

_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "file_too_large",
    415: "unsupported_format",
    422: "invalid_params",
    429: "rate_limited",
    503: "service_unavailable",
    507: "storage_full",
}


class ApiError(AirBriefError):
    """An API-level error with an optional explicit HTTP status."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, details=details)
        self._status = status

    @property
    def http_status(self) -> int:
        if self._status is not None:
            return self._status
        if self.code in API_STATUS_BY_CODE:
            return API_STATUS_BY_CODE[self.code]
        return HTTP_STATUS_BY_CODE.get(self.code, 422)


class CodedHTTPException(StarletteHTTPException):
    """A Starlette ``HTTPException`` that carries an API error code.

    For code that must raise Starlette's class: FastAPI re-raises only ``HTTPException`` from
    request-body parsing and turns anything else (including :class:`ApiError`) into a 400.
    """

    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.details = details or {}


# Datasets ("File" in the service layer), analyses and features are addressed by opaque ids: their
# message uses the words the web app uses and leaves the id to ``details.id``.
_NOT_FOUND_MESSAGES: dict[str, str] = {
    "File": "This dataset does not exist. It may have been deleted, or it expired.",
    "Analysis": "This analysis does not exist. It may have been deleted (with its dataset), or it expired.",
    "Feature": "This feature is not in the dataset.",
}


def not_found(what: str, identifier: object) -> ApiError:
    """A ``not_found`` error for ``what`` (e.g. "File") with id ``identifier`` (in ``details.id``)."""
    message = _NOT_FOUND_MESSAGES.get(what, f"{what} '{identifier}' was not found.")
    return ApiError("not_found", message, details={"id": str(identifier)})


def status_for(exc: AirBriefError) -> int:
    """HTTP status of any AirBrief error, including API-only codes."""
    if isinstance(exc, ApiError):
        return exc.http_status
    if exc.code in API_STATUS_BY_CODE:
        return API_STATUS_BY_CODE[exc.code]
    return exc.http_status


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """The JSON error envelope."""
    return {"error": {"code": code, "message": message, "details": jsonable_encoder(details or {})}}


def error_response(
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=status, content=error_body(code, message, details), headers=headers)


def _validation_message(errors: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for err in errors[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
        msg = str(err.get("msg", "invalid value"))
        parts.append(f"{loc}: {msg}" if loc else msg)
    if len(errors) > 5:
        parts.append(f"(+{len(errors) - 5} more)")
    return "; ".join(parts) or "Invalid request."


def _clean_errors(errors: list[Any]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for err in errors:
        if not isinstance(err, dict):
            continue
        item = {k: v for k, v in err.items() if k in ("loc", "msg", "type")}
        if "ctx" in err and isinstance(err["ctx"], dict):
            item["ctx"] = {k: str(v) for k, v in err["ctx"].items()}
        cleaned.append(item)
    return jsonable_encoder(cleaned)


def invalid_params_error(exc: ValidationError, *, prefix: str | None = None) -> ApiError:
    """Turn a pydantic ValidationError (e.g. analysis params) into a 422 ``invalid_params``."""
    errors = _clean_errors(list(exc.errors()))
    if prefix:
        for err in errors:
            err["loc"] = [prefix, *list(err.get("loc", []))]
    return ApiError("invalid_params", _validation_message(errors), details={"errors": errors})


def install_exception_handlers(app: FastAPI, *, development: bool = False, proxy_hops: int = 0) -> None:
    """Register handlers that turn every failure into the error envelope.

    ``proxy_hops`` (``TRUSTED_PROXY_HOPS``) tells the security log which address is the client's.
    """

    def log_if_refusal(request: Request, status: int, code: str) -> None:
        if status in _LOGGED_STATUSES or code in _LOGGED_CODES:
            log_refusal(request.scope, proxy_hops, status, code)

    @app.exception_handler(AirBriefError)
    async def _airbrief_error(request: Request, exc: AirBriefError) -> JSONResponse:
        status = status_for(exc)
        log_if_refusal(request, status, exc.code)
        headers: dict[str, str] | None = None
        if exc.code == "rate_limited":
            retry = exc.details.get("retry_after_s")
            if retry is not None:
                headers = {"Retry-After": str(int(retry))}
        if status >= 500:
            log.error("Request failed with %s: %s", exc.code, exc.message)
        return error_response(status, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = _clean_errors(list(exc.errors()))
        return error_response(422, "invalid_params", _validation_message(errors), {"errors": errors})

    @app.exception_handler(ValidationError)
    async def _pydantic_validation(_request: Request, exc: ValidationError) -> JSONResponse:
        errors = _clean_errors(list(exc.errors()))
        return error_response(422, "invalid_params", _validation_message(errors), {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        headers = dict(exc.headers) if exc.headers else None
        if isinstance(exc, CodedHTTPException):
            log_if_refusal(request, exc.status_code, exc.code)
            return error_response(exc.status_code, exc.code, str(exc.detail), exc.details, headers)
        if exc.status_code == 400 and isinstance(exc.__cause__, RecursionError):
            # Python 3.11's json gives up on deep nesting before check_json_limits sees the body,
            # and FastAPI reports that as a body it could not parse; 3.12 decodes it and says this
            log_if_refusal(request, 422, "too_complex")
            message = f"The JSON is nested more than {MAX_NESTING} levels deep; real inputs need far fewer."
            return error_response(422, "too_complex", message, {}, headers)
        code = _STATUS_CODES.get(exc.status_code, "http_error")
        log_if_refusal(request, exc.status_code, code)
        if exc.status_code == 404:
            message = f"No such endpoint: {request.method} {request.url.path}"
        elif isinstance(exc.detail, str):
            message = exc.detail
        else:
            message = "Request failed."
        return error_response(exc.status_code, code, message, {}, headers)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        details: dict[str, Any] = {}
        if development:
            details = {"type": type(exc).__name__, "reason": str(exc)}
        return error_response(500, "internal_error", "Internal server error.", details)
