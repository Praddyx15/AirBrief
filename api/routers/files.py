"""``/api/files/``: upload, list, inspect, measure, export and delete datasets."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, Path, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from airbrief_core.models.features import FeatureRecord, MeasurementStatus

from ..db import get_session
from ..deps import AppContext, get_ctx
from ..ownership import OWNER_TOKEN_HEADER, check_owner
from ..schemas import ErrorResponse, FileInfo, FilesPage, MeasurementsPage
from ..services import datasets as service
from . import download_response, parse_ids, require_scope, route

router = APIRouter(tags=["files"])

_ERRORS = {404: {"model": ErrorResponse, "description": "Not found"}}


@route(
    router,
    "POST",
    "/api/files/",
    response_model=FileInfo,
    status_code=201,
    summary="Upload a file",
    responses={
        202: {"model": FileInfo, "description": "Queued (file above INLINE_PROCESS_MAX_MB)"},
        413: {"model": ErrorResponse, "description": "file_too_large"},
        415: {"model": ErrorResponse, "description": "unsupported_format"},
        422: {"model": ErrorResponse, "description": "unsafe_archive, archive_too_large, invalid_crs…"},
        429: {"model": ErrorResponse, "description": "rate_limited"},
        507: {"model": ErrorResponse, "description": "storage_full"},
    },
)
async def upload_file(
    response: Response,
    file: UploadFile = File(..., description=".zip (shapefile, or KML/GeoJSON/GPX/CSV files), .kml, .kmz, .geojson/.json, .gpx or .csv"),
    crs_override: str | None = Form(None, description="CRS for layers without one, e.g. EPSG:32643"),
    origin: str | None = Form(None, description="'lon,lat,alt' origin for local x/y/z CSV tracks"),
    ctx: AppContext = Depends(get_ctx),
) -> FileInfo:
    """Files up to ``INLINE_PROCESS_MAX_MB`` are processed in the request (201 COMPLETED/FAILED),
    larger ones are queued (202). In production that limit defaults to 0: every upload is queued."""
    info, status_code = await service.create_from_upload(ctx, file, crs_override, origin)
    response.status_code = status_code
    return info


@route(router, "GET", "/api/files/", response_model=FilesPage, summary="List datasets")
def list_files(
    ids: str | None = Query(None, max_length=7000, description="Comma-separated dataset ids to list"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
    ctx: AppContext = Depends(get_ctx),
) -> FilesPage:
    """Newest first. Without ``ids`` this lists every dataset, which public instances refuse."""
    id_list = parse_ids(ids)
    require_scope(id_list is not None, ctx.settings.global_listing_enabled, "dataset")
    return service.list_files(session, limit, offset, ids=id_list)


@route(router, "GET", "/api/files/{file_id}/", response_model=FileInfo, responses=_ERRORS, summary="Dataset info")
def get_file(
    file_id: str = Path(..., max_length=64),
    session: Session = Depends(get_session),
) -> FileInfo:
    return service.to_file_info(service.get_dataset(session, file_id))


@route(
    router,
    "GET",
    "/api/files/{file_id}/measurements/",
    response_model=MeasurementsPage,
    responses=_ERRORS,
    summary="Per-feature measurements",
)
def get_measurements(
    file_id: str = Path(..., max_length=64),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    geometry_type: str | None = Query(None, max_length=64),
    status: MeasurementStatus | None = Query(None),
    session: Session = Depends(get_session),
) -> MeasurementsPage:
    return service.measurements_page(
        session,
        file_id,
        limit=limit,
        offset=offset,
        geometry_type=geometry_type or None,
        status=status.value if status is not None else None,
    )


@route(router, "GET", "/api/files/{file_id}/features/", responses=_ERRORS, summary="Features as GeoJSON")
def get_features(
    file_id: str = Path(..., max_length=64),
    session: Session = Depends(get_session),
) -> JSONResponse:
    """A WGS84 FeatureCollection; features with an unknown CRS have ``geometry: null``."""
    return JSONResponse(service.features_geojson(session, file_id), media_type="application/geo+json")


@route(
    router,
    "GET",
    "/api/files/{file_id}/features/{index}/",
    response_model=FeatureRecord,
    responses=_ERRORS,
    summary="One feature",
)
def get_feature(
    file_id: str = Path(..., max_length=64),
    index: int = Path(..., ge=0),
    session: Session = Depends(get_session),
) -> FeatureRecord:
    return service.feature_record(session, file_id, index)


@route(router, "GET", "/api/files/{file_id}/export/", responses=_ERRORS, summary="Download an export")
def export_file(
    file_id: str = Path(..., max_length=64),
    fmt: Literal["csv", "geojson", "kml", "ed318"] = Query("geojson", alias="format"),
    ed318_limits: Literal["geometry", "properties"] | None = Query(
        None, description="ED-318 only: vertical limits in each geometry's layer (default, as ED-318) or in the feature properties"
    ),
    session: Session = Depends(get_session),
) -> Response:
    """``csv`` (measurements), ``geojson`` (WGS84 features), ``kml`` (with ExtendedData) or
    ``ed318`` (the polygons as an ED-318 zone file, read like a zone dataset, so a polygon without
    a zone column is red; 422 ``no_zone_polygons`` without polygons; ``ed318_limits`` says where
    the vertical limits go)."""
    content, media_type, filename = service.export_dataset(session, file_id, fmt, ed318_limits)
    return download_response(content, media_type, filename)


@route(router, "DELETE", "/api/files/{file_id}/", status_code=204, responses=_ERRORS, summary="Delete a dataset")
def delete_file(
    file_id: str = Path(..., max_length=64),
    owner_token: str | None = Header(None, alias=OWNER_TOKEN_HEADER, max_length=256),
    session: Session = Depends(get_session),
    ctx: AppContext = Depends(get_ctx),
) -> Response:
    """Deletes the dataset, its features, its analyses and its files.

    Needs the dataset's owner token (``X-Owner-Token``) when ``REQUIRE_OWNER_TOKEN`` is on.
    """
    row = service.get_dataset(session, file_id)
    check_owner(row.owner_token_hash, owner_token, required=ctx.settings.owner_token_required, what="dataset")
    service.delete_dataset(session, ctx.storage, file_id)
    return Response(status_code=204)
