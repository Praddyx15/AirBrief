"""``GET /health``: liveness plus database, data-pack and native-kernel status."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from airbrief_core import __version__
from airbrief_core._native import BACKEND
from airbrief_core.models.common import SCHEMA_VERSION

from ..clients import on_this_machine, refusal_counts
from ..deps import AppContext, get_ctx
from ..schemas import HealthResponse
from . import route

router = APIRouter(tags=["health"])


@route(router, "GET", "/health", response_model=HealthResponse, summary="Service health")
async def health(request: Request, ctx: AppContext = Depends(get_ctx)) -> HealthResponse:
    """``status`` is ``ok`` when the database answers, otherwise ``degraded``."""
    db_ok = await run_in_threadpool(ctx.db.ping)
    try:
        packs = await run_in_threadpool(ctx.registry.names)
    except Exception:  # a broken data-pack folder must not break the health check
        packs = []
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        schema_version=SCHEMA_VERSION,
        db="ok" if db_ok else "error",
        datapacks=packs,
        native_backend=BACKEND,
        refusals=refusal_counts() if on_this_machine(request.scope) else None,
    )
