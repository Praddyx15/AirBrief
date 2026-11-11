"""``POST /api/flightmd/fetch/``: fetch a FlightMD report by URL, server-side and SSRF-guarded."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from .. import security
from ..deps import AppContext, get_ctx
from ..schemas import ErrorResponse, FlightmdFetchRequest
from . import route

router = APIRouter(tags=["flightmd"])


@route(
    router,
    "POST",
    "/api/flightmd/fetch/",
    responses={
        422: {"model": ErrorResponse, "description": "url_not_allowed, report_invalid, too_complex"},
        429: {"model": ErrorResponse, "description": "rate_limited"},
        502: {"model": ErrorResponse, "description": "fetch_failed"},
    },
    summary="Fetch a FlightMD report",
)
def fetch_report(body: FlightmdFetchRequest, ctx: AppContext = Depends(get_ctx)) -> JSONResponse:
    """Returns the report JSON (a ``{"report": ...}`` wrapper is removed).

    https on port 443 only, public addresses only, 5 MB cap, ``FLIGHTMD_FETCH_TIMEOUT_S`` timeout.
    Share links ``…/report/{id}`` go to ``{FLIGHTMD_API_URL}/report/{id}`` when an API URL is
    configured; that host serves nothing else. Errors say only which rule (``details.rule``) or
    stage (``details.stage``) failed.
    """
    report = security.fetch_flightmd_report(body.url, ctx.settings, context="POST /api/flightmd/fetch/")
    return JSONResponse(report)
