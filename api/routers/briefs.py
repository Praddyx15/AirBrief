"""``/api/briefs/``: the operation evidence brief (JSON) and its self-contained bundle (zip).

Stateless: nothing is stored. A brief holds the completed analyses whose ids the request names
(at most 12), whatever datasets they come from; the caller can read those analyses already, so
no listing or ownership is involved. See :mod:`api.services.briefs`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from airbrief_core.models.brief import Brief

from ..db import get_session
from ..deps import AppContext, get_ctx
from ..schemas import ErrorResponse
from ..services import briefs as service
from . import attachment_headers, route

router = APIRouter(tags=["briefs"])

_ERRORS = {
    404: {"model": ErrorResponse, "description": "not_found (an analysis id)"},
    409: {"model": ErrorResponse, "description": "analysis_not_finished"},
    422: {"model": ErrorResponse, "description": "invalid_params"},
    429: {"model": ErrorResponse, "description": "rate_limited"},
}


@route(router, "POST", "/api/briefs/", response_model=Brief, responses=_ERRORS, summary="Build an operation evidence brief")
def create_brief(
    body: service.BriefRequest,
    session: Session = Depends(get_session),
    ctx: AppContext = Depends(get_ctx),
) -> Brief:
    """Every evidence section (``not_assessed`` when no analysis covers it), cross-checks and the
    provenance (versions, data packs, SHA-256 of inputs, canonical parameters and the brief).
    Evidence, not authorisation."""
    brief, _sources = service.create_brief(ctx, session, body)
    return brief


@route(
    router,
    "POST",
    "/api/briefs/bundle/",
    responses={**_ERRORS, 413: {"model": ErrorResponse, "description": "bundle_too_large"}},
    response_class=StreamingResponse,
    summary="Download the brief bundle (zip)",
)
def create_bundle(
    body: service.BriefRequest,
    session: Session = Depends(get_session),
    ctx: AppContext = Depends(get_ctx),
) -> StreamingResponse:
    """A zip with ``brief.html``, ``brief.json``, ``provenance.json``, ``evidence.geojson``,
    ``evidence.kml``, ``analyses/NN_<kind>.json`` and ``tables/NN_<kind>_<table>.csv``; on an
    instance with a signing key also ``signature.json``, an Ed25519 signature of
    ``provenance.json`` (public key in ``GET /api/meta/`` → ``signing``)."""
    bundle = service.build_bundle(ctx, session, body)
    headers = {**attachment_headers(bundle.filename), "Content-Length": str(bundle.size)}
    return StreamingResponse(bundle.chunks(), media_type="application/zip", headers=headers)
