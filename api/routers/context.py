"""Plan without a file: context layers for a map view, place search and geometry parsing.

* ``GET /api/context/?bbox=&layers=&limit=`` — context layers (aerodromes, aerodrome zone rings,
  pack zones, obstacles, water, pack coverage) of the data pack covering a map view of at most
  2° × 2°, at most 5,000 features and 100,000 vertices per layer, with an ETag per pack version
  and request.
* ``GET /api/places/?q=&limit=`` — coordinates typed in ``q`` first, then the aerodromes of every
  installed pack that match it (ident, IATA code, name, municipality).
* ``POST /api/geometry/parse/`` — pasted GeoJSON, WKT or typed coordinates → one WGS84 geometry
  plus notes on how it was read.

All three are read-only: nothing is stored and nothing is sent anywhere. A defined operation is
saved by uploading it as a GeoJSON dataset (``POST /api/files/``), like any other file.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

import shapely
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from shapely.geometry import shape

from airbrief_core import __version__
from airbrief_core.data.context import CONTEXT_FEATURE_LIMIT, CONTEXT_LAYERS, SEARCH_LIMIT_MAX, check_context_bbox
from airbrief_core.data.datapack import MANIFEST_NAME, DataPack, DataPackRegistry
from airbrief_core.errors import AnalysisError
from airbrief_core.geo.parse import detect_text_format, format_lat_lon, parse_coordinates, parse_geometry_text
from airbrief_core.models.common import BBox

from ..deps import AppContext, get_ctx
from ..errors import ApiError
from ..schemas import ContextResponse, ErrorResponse, GeometryParseRequest, GeometryParseResponse, PlaceResult
from . import route

router = APIRouter(tags=["context"])

CONTEXT_MAX_AGE_S = 300
"""Browsers may reuse a context response this long; the ETag revalidates it afterwards."""


def _parse_bbox(text: str) -> BBox:
    parts = [p.strip() for p in text.split(",")]
    try:
        values = [float(p) for p in parts]
    except ValueError:
        values = []
    if len(values) != 4 or not all(math.isfinite(v) for v in values):
        raise ApiError(
            "invalid_params",
            "bbox must be four numbers: min_lon,min_lat,max_lon,max_lat (degrees).",
            details={"bbox": text},
        )
    bbox = BBox.from_bounds(values)
    check_context_bbox(bbox)
    return bbox


def _parse_layers(text: str | None) -> list[str]:
    if text is None or not text.strip():
        return list(CONTEXT_LAYERS)
    names = list(dict.fromkeys(p.strip().lower() for p in text.split(",") if p.strip()))
    unknown = [n for n in names if n not in CONTEXT_LAYERS]
    if unknown:
        raise ApiError(
            "invalid_params",
            f"Unknown context layer(s): {', '.join(unknown)}. Choose from {', '.join(CONTEXT_LAYERS)}.",
            details={"unknown": unknown, "allowed": list(CONTEXT_LAYERS)},
        )
    return names


def _overlap(a: BBox, b: BBox) -> float:
    w = min(a.max_lon, b.max_lon) - max(a.min_lon, b.min_lon)
    h = min(a.max_lat, b.max_lat) - max(a.min_lat, b.min_lat)
    return w * h if w > 0 and h > 0 else 0.0


def pack_for_view(registry: DataPackRegistry, bbox: BBox) -> DataPack | None:
    """The pack an analysis of this view would use, else the one overlapping it most (a view at
    the edge of a pack still shows the part it covers), else None."""
    chosen = registry.select(bbox)
    if chosen is not None:
        return chosen
    overlapping = [(p, _overlap(p.bbox, bbox)) for p in registry.list()]
    overlapping = [(p, a) for p, a in overlapping if a > 0]
    if not overlapping:
        return None
    return max(overlapping, key=lambda t: (t[1], not t[0].synthetic, t[0].name))[0]


def _etag(pack: DataPack, bbox: BBox, layers: list[str], limit: int, profile: str) -> str:
    try:
        mtime = (pack.root / MANIFEST_NAME).stat().st_mtime_ns
    except OSError:
        mtime = 0
    # The app version too: a new release may build the layers differently from the same pack.
    basis = json.dumps(
        [__version__, pack.name, pack.manifest.version, pack.manifest.created_utc, mtime, bbox.as_list(), layers, limit, profile],
        separators=(",", ":"),
    )
    return f'W/"{hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]}"'


@route(
    router,
    "GET",
    "/api/context/",
    response_model=ContextResponse,
    responses={422: {"model": ErrorResponse, "description": "invalid_params, area_too_large (view over 2° × 2°)"}},
    summary="Context layers for a map view",
)
def get_context(
    request: Request,
    bbox: str = Query(..., max_length=200, description="min_lon,min_lat,max_lon,max_lat (WGS84 degrees, at most 2° × 2°)"),
    layers: str | None = Query(
        None, max_length=200, description=f"Comma-separated subset of {', '.join(CONTEXT_LAYERS)} (default: all)"
    ),
    limit: int = Query(CONTEXT_FEATURE_LIMIT, ge=1, le=CONTEXT_FEATURE_LIMIT, description="Most features per layer"),
    profile: Literal["dgca_2021", "conservative_illustrative"] = Query(
        "dgca_2021", description="Airspace rule set of the aerodrome rings (the airspace check's zone_profile)"
    ),
    ctx: AppContext = Depends(get_ctx),
) -> Response:
    """Aerodromes, aerodrome zone rings (of the rule set ``profile``), the pack's zones, obstacles
    (tallest first when cut), water and the pack's coverage box. Features whose geometry touches
    the view are returned whole. With no pack covering the view, ``datapack`` is null and every
    layer is empty."""
    view = _parse_bbox(bbox)
    names = _parse_layers(layers)
    pack = pack_for_view(ctx.registry, view)
    if pack is None:
        empty = ContextResponse(
            datapack=None,
            bbox=view.as_list(),
            layers={n: {"type": "FeatureCollection", "features": []} for n in names},
            truncated=dict.fromkeys(names, False),
            counts=dict.fromkeys(names, 0),
            limit=limit,
        )
        return JSONResponse(empty.model_dump(), headers={"Cache-Control": f"public, max-age={CONTEXT_MAX_AGE_S}"})

    tag = _etag(pack, view, names, limit, profile)
    headers = {"ETag": tag, "Cache-Control": f"public, max-age={CONTEXT_MAX_AGE_S}"}
    if tag in {t.strip() for t in request.headers.get("if-none-match", "").split(",")}:
        return Response(status_code=304, headers=headers)
    result = pack.context(view, names, limit, profile)
    body = ContextResponse(
        datapack=pack.name,
        synthetic=pack.synthetic,
        bbox=view.as_list(),
        layers=result.layers,
        truncated=result.truncated,
        counts=result.counts,
        limit=limit,
        attribution=pack.attribution,
    )
    return JSONResponse(body.model_dump(), headers=headers)


def _coordinate_detail(codes: set[str]) -> str:
    if "ambiguous_order" in codes:
        return "Coordinates, read as latitude, longitude"
    if "lonlat_order" in codes:
        return "Coordinates, read as longitude, latitude"
    return "Coordinates"


@route(router, "GET", "/api/places/", response_model=list[PlaceResult], summary="Find coordinates or an aerodrome")
def get_places(
    q: str = Query(..., min_length=1, max_length=200, description="Coordinates, or an ICAO/IATA code or name"),
    limit: int = Query(10, ge=1, le=SEARCH_LIMIT_MAX),
    ctx: AppContext = Depends(get_ctx),
) -> list[PlaceResult]:
    """Coordinates typed in ``q`` (decimal degrees, hemispheres or DMS; see
    ``airbrief_core.geo.parse``) come first, then matching aerodromes of every installed pack:
    exact ident or IATA code, then prefixes, then name and municipality matches."""
    out: list[PlaceResult] = []
    try:
        points, issues = parse_coordinates(q)
    except AnalysisError:
        points, issues = [], []
    if len(points) == 1:
        lon, lat = points[0]
        out.append(
            PlaceResult(
                label=format_lat_lon(lon, lat),
                kind="coordinates",
                lon=lon,
                lat=lat,
                detail=_coordinate_detail({i.code for i in issues}),
            )
        )
    # Every pack's best matches, merged by match quality, real packs before synthetic ones (a
    # stable sort keeps each pack's own size/name order); an aerodrome in several packs is listed
    # once, from the first. A pack that failed its hash check is skipped, so one bad pack doesn't
    # take search down for the others.
    packs = [p for p in ctx.registry.list() if p.integrity != "mismatch"]
    found = [(match, pack.name, pack.synthetic) for pack in packs for match in pack.search(q, limit)]
    found.sort(key=lambda t: (t[0].rank, t[2]))
    seen: set[str] = set()
    for match, pack_name, _ in found:
        key = match.ident or f"{match.lon:.6f},{match.lat:.6f}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            PlaceResult(
                label=match.label,
                kind=match.kind,
                ident=match.ident,
                lon=match.lon,
                lat=match.lat,
                detail=match.detail,
                datapack=pack_name,
            )
        )
    return out[:limit]


@route(
    router,
    "POST",
    "/api/geometry/parse/",
    response_model=GeometryParseResponse,
    responses={
        422: {"model": ErrorResponse, "description": "invalid_geometry, invalid_coordinates, too_complex"},
        429: {"model": ErrorResponse, "description": "rate_limited"},
    },
    summary="Read pasted GeoJSON, WKT or coordinates",
)
def parse_geometry(body: GeometryParseRequest) -> GeometryParseResponse:
    """One WGS84 geometry from the text (100 kB, 10,000 vertices at most) and notes on how it was
    read: coordinate order, decimal commas, dropped heights, repaired polygons."""
    geometry, issues = parse_geometry_text(body.text, body.shape)
    return GeometryParseResponse(
        geometry=geometry,
        kind=str(geometry.get("type")),
        source=detect_text_format(body.text),
        vertices=int(shapely.get_num_coordinates(shape(geometry))),
        issues=issues,
    )
