"""``/api/analyses/``: queue, poll, cancel, delete and export analyses."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_session
from ..deps import AppContext, get_ctx
from ..ownership import OWNER_TOKEN_HEADER, check_owner
from ..schemas import AnalysesPage, AnalysisInfo, AnalysisRequest, ErrorResponse
from ..services import analyses as service
from . import download_response, parse_ids, require_scope, route

router = APIRouter(tags=["analyses"])

_ERRORS = {404: {"model": ErrorResponse, "description": "Not found"}}
MAX_LIST_LIMIT = 100


@route(
    router,
    "POST",
    "/api/analyses/",
    response_model=AnalysisInfo,
    status_code=202,
    responses={
        **_ERRORS,
        409: {"model": ErrorResponse, "description": "dataset_not_ready"},
        422: {"model": ErrorResponse, "description": "invalid_params, no_features, report_invalid…"},
        429: {"model": ErrorResponse, "description": "rate_limited"},
        502: {"model": ErrorResponse, "description": "fetch_failed (report_url)"},
    },
    summary="Run an analysis",
)
def create_analysis(
    body: AnalysisRequest,
    owner_token: str | None = Header(
        None,
        alias=OWNER_TOKEN_HEADER,
        max_length=256,
        description="The dataset's owner token: marks the analysis as the owner's evidence (by_dataset_owner).",
    ),
    ctx: AppContext = Depends(get_ctx),
) -> AnalysisInfo:
    """Missing inputs are resolved from the dataset (SPEC §4); poll ``GET /api/analyses/{id}/``.

    Anyone with a dataset id may run an analysis on it. Only one sent with the dataset's owner
    token (``X-Owner-Token``) counts as the owner's: where owner tokens are enforced, the others
    stay out of the dataset's listing except for the clients that name their ids.
    """
    return service.create_analysis(ctx, body, dataset_token=owner_token)


@route(router, "GET", "/api/analyses/", response_model=AnalysesPage, summary="List analyses")
def list_analyses(
    dataset_id: str | None = Query(None, max_length=64),
    ids: str | None = Query(None, max_length=7000, description="Comma-separated analysis ids to list"),
    limit: int = Query(
        100,
        ge=1,
        le=MAX_LIST_LIMIT,
        description=f"At most {MAX_LIST_LIMIT}: every item carries its full result, which can be megabytes.",
    ),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    ctx: AppContext = Depends(get_ctx),
) -> AnalysesPage:
    """Newest first: one dataset's analyses, the given ``ids``, or (where allowed) everything.

    Where owner tokens are enforced (``REQUIRE_OWNER_TOKEN``, on in production), ``dataset_id``
    lists only the analyses run with the dataset's owner token; add ``ids`` to also get your own
    analyses on that dataset. Elsewhere ``dataset_id`` lists every analysis on the dataset.
    """
    id_list = parse_ids(ids)
    require_scope(bool(dataset_id) or id_list is not None, ctx.settings.global_listing_enabled, "analysis")
    return service.list_analyses(
        session, dataset_id or None, limit, offset, ids=id_list, owner_only=ctx.settings.owner_token_required
    )


@route(
    router,
    "GET",
    "/api/analyses/{analysis_id}/",
    response_model=AnalysisInfo,
    responses=_ERRORS,
    summary="Analysis status and result",
)
def get_analysis(
    analysis_id: str = Path(..., max_length=64),
    session: Session = Depends(get_session),
) -> AnalysisInfo:
    return service.to_analysis_info(service.get_analysis(session, analysis_id))


@route(
    router,
    "POST",
    "/api/analyses/{analysis_id}/cancel/",
    response_model=AnalysisInfo,
    status_code=202,
    responses=_ERRORS,
    summary="Cancel an analysis",
)
def cancel_analysis(
    analysis_id: str = Path(..., max_length=64),
    owner_token: str | None = Header(None, alias=OWNER_TOKEN_HEADER, max_length=256),
    session: Session = Depends(get_session),
    ctx: AppContext = Depends(get_ctx),
) -> AnalysisInfo:
    """A queued analysis is cancelled at once; a running one stops at its next checkpoint.

    Needs the analysis' owner token (``X-Owner-Token``) when ``REQUIRE_OWNER_TOKEN`` is on.
    """
    _require_owner(session, ctx, analysis_id, owner_token)
    return service.cancel_analysis(ctx, analysis_id)


def _require_owner(session: Session, ctx: AppContext, analysis_id: str, owner_token: str | None) -> None:
    row = service.get_analysis(session, analysis_id)
    check_owner(row.owner_token_hash, owner_token, required=ctx.settings.owner_token_required, what="analysis")


@route(
    router,
    "DELETE",
    "/api/analyses/{analysis_id}/",
    status_code=204,
    responses=_ERRORS,
    summary="Delete an analysis",
)
def delete_analysis(
    analysis_id: str = Path(..., max_length=64),
    owner_token: str | None = Header(None, alias=OWNER_TOKEN_HEADER, max_length=256),
    session: Session = Depends(get_session),
    ctx: AppContext = Depends(get_ctx),
) -> Response:
    """Needs the analysis' owner token (``X-Owner-Token``) when ``REQUIRE_OWNER_TOKEN`` is on."""
    _require_owner(session, ctx, analysis_id, owner_token)
    service.delete_analysis(ctx, analysis_id)
    return Response(status_code=204)


@route(
    router,
    "GET",
    "/api/analyses/{analysis_id}/export/",
    responses={**_ERRORS, 409: {"model": ErrorResponse, "description": "analysis_not_finished"}},
    summary="Download an analysis export",
)
def export_analysis(
    analysis_id: str = Path(..., max_length=64),
    fmt: Literal["json", "geojson", "kml", "csv", "ed318"] = Query("json", alias="format"),
    table: str | None = Query(
        None, max_length=40, pattern=r"^[a-z_]+$", description="CSV only: a table key from /export/tables/ (default: the kind's first)"
    ),
    ed318_limits: Literal["geometry", "properties"] | None = Query(
        None, description="ED-318 only: vertical limits in each geometry's layer (default, as ED-318) or in the feature properties"
    ),
    session: Session = Depends(get_session),
) -> Response:
    """``json`` = AnalysisEnvelope; ``geojson`` = all result layers merged (``_layer``, H3 cells as
    polygons, at most 50,000 features); ``kml`` = the evidence layers (2D placemarks, values in
    ExtendedData); ``csv`` = one table (``table``); ``ed318`` = the zones layer of an airspace
    check or flight audit as an ED-318 GeoJSON zone file, its vertical limits in each geometry's
    ``layer`` or, with ``ed318_limits=properties``, in the feature properties. Every format
    carries the evidence notice (ED-318 in its top-level ``airbrief`` member)."""
    content, media_type, filename = service.export_analysis(session, analysis_id, fmt, table, ed318_limits)
    return download_response(content, media_type, filename)


class ExportTable(BaseModel):
    key: str
    label: str
    rows: int
    multi: bool
    default: bool


@route(
    router,
    "GET",
    "/api/analyses/{analysis_id}/export/tables/",
    response_model=list[ExportTable],
    responses={**_ERRORS, 409: {"model": ErrorResponse, "description": "analysis_not_finished"}},
    summary="CSV tables of an analysis",
)
def export_tables(
    analysis_id: str = Path(..., max_length=64),
    session: Session = Depends(get_session),
) -> list[ExportTable]:
    """The ``?table=`` values of the CSV export for this analysis, with their row counts."""
    return [ExportTable.model_validate(t) for t in service.analysis_tables(session, analysis_id)]
