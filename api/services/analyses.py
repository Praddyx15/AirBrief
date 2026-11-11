"""Analyses: input resolution, data-pack selection, job rows, results and exports.

Input resolution (docs/SPEC.md §4), applied only to parameters the request leaves out:

* ``airspace``, ``ground_risk``, ``survey``, ``evtol_siting``, ``environment`` — ``geometry`` is the union of the
  selected features' WGS84 geometries (all features when none are selected); ``survey`` and
  ``evtol_siting`` keep the polygonal part only (a ``survey`` with ``coverage="corridor"`` keeps
  the union of the line parts only: its centreline; 422 when the selection has no line or its
  lines do not join into one).
* ``evtol_corridor`` — ``origin``/``destination`` are the first two selected point features (or
  the first two points of the dataset), named after the features; ``study_area`` is the first
  polygon, if any.
* ``show`` — ``tracks`` are the features with ``times`` (``{id, times, coordinates}``; a timed
  track without heights is refused, 422 ``no_tracks``); ``show_box`` is the polygon named like ``/show\\s*box/i`` and ``audience`` the one named like
  ``/audience/i``.
* ``flight_audit`` — ``report``, or ``report_url`` fetched server-side through the SSRF guard
  (FlightMD share links are mapped to ``FLIGHTMD_API_URL``) and then removed from the params;
  it is also the default ``flightmd_url`` (the "Open in FlightMD" link) when it may be one: https,
  or outside production http to localhost. A ``flightmd_url`` sent that breaks this is a 422.
  Without either (nor a ``track``), a dataset's timed track and its training area become
  ``track`` / ``training_box`` (:mod:`api.services.tracks`).
* ``line_of_sight`` — ``observers`` from point features named like a pilot, observer or ground
  station, and ``geometry`` from the selection without those points (:mod:`.line_of_sight`).
* ``batch`` (one result per feature) — no union: ``batch.features`` holds the selected features
  with their properties and ``geometry`` is their extent (:mod:`.batch`).

The data pack is ``DataPackRegistry.select(bbox_of_inputs, preferred=DEFAULT_DATAPACK)``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

import shapely
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ValidationError
from shapely.geometry.base import BaseGeometry
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from airbrief_core import __version__
from airbrief_core.analyses import REGISTRY
from airbrief_core.errors import AirBriefError, AnalysisError
from airbrief_core.geo.parse import check_json_limits
from airbrief_core.geo.transform import geojson_to_geometry, geometry_to_geojson
from airbrief_core.geo.validity import lineal_part, polygonal_part
from airbrief_core.integrations.flightmd import parse_flightmd_report
from airbrief_core.io.geozones import Ed318Limits
from airbrief_core.models.common import SCHEMA_VERSION

from .. import security
from ..config import Settings
from ..deps import AppContext
from ..errors import ApiError, invalid_params_error, not_found
from ..models import Analysis, Dataset, Feature, as_utc, utcnow
from ..ownership import new_owner_token, owner_token_matches
from ..schemas import AnalysesPage, AnalysisInfo, AnalysisRequest, ErrorInfo
from .batch import resolve_batch
from .datasets import export_basename, get_dataset, load_features
from .exports import analysis_kml, evidence_labels, geojson_document, table_catalog, table_csv
from .exports import h3_layers as h3_layers  # re-exported: the export layers live in .exports
from .exports import merged_layers as merged_layers
from .line_of_sight import line_of_sight_bounds, resolve_line_of_sight
from .tracks import flight_audit_track_bounds, resolve_flight_audit_track
from .zones import ZONE_PROFILE_KINDS, analysis_ed318, record_zone_profile, resolve_zone_inputs

log = logging.getLogger("airbrief.api.analyses")

GEOMETRY_KINDS = frozenset({"airspace", "ground_risk", "survey", "evtol_siting", "environment"})
POLYGON_KINDS = frozenset({"survey", "evtol_siting"})
SHOW_BOX_RE = re.compile(r"show\s*box", re.IGNORECASE)
AUDIENCE_RE = re.compile(r"audience", re.IGNORECASE)
ACTIVE_STATUSES = ("PENDING", "RUNNING")
# The UI copies dataset polygons into params (training area, show box), so this is well above
# the 10k of typed or pasted text; the JSON body limit still bounds the request.
MAX_PARAM_VERTICES = 100_000

_EXPORT_MEDIA_TYPES = {
    "json": "application/json",
    "geojson": "application/geo+json",
    "kml": "application/vnd.google-earth.kml+xml",
    "csv": "text/csv; charset=utf-8",
}

FeatureLoader = Callable[[], list[Feature]]


def _feature_shape(feature: Feature) -> BaseGeometry | None:
    if not feature.geometry_wgs84:
        return None
    try:
        geom = geojson_to_geometry(feature.geometry_wgs84)
    except AirBriefError:
        return None
    return None if geom.is_empty else geom


def _union_geometry(kind: str, features: list[Feature], polygonal: bool) -> dict[str, Any]:
    geoms = [g for g in (_feature_shape(f) for f in features) if g is not None]
    if not geoms:
        raise AnalysisError(
            "no_features",
            "The selection has no features with a known position (unknown CRS or empty geometry).",
            details={"kind": kind},
        )
    if len({g.has_z for g in geoms}) > 1:
        # 2D and 3D features together (e.g. a GPX route and its track): every geometry module
        # works in 2D, and a union of mixed dimensions does not survive the GeoJSON round trip.
        geoms = [shapely.force_2d(g) for g in geoms]
    if polygonal:
        parts = [p for p in (polygonal_part(g) for g in geoms) if p is not None and not p.is_empty]
        if not parts:
            raise AnalysisError(
                "geometry_type_not_supported",
                f"The {kind.replace('_', ' ')} module needs a polygon. Select a polygon feature.",
                details={"kind": kind, "expected": "Polygon"},
            )
        geoms = list(parts)
    merged = geoms[0] if len(geoms) == 1 else shapely.unary_union(geoms)
    if polygonal:
        part = polygonal_part(merged)
        if part is None or part.is_empty:
            raise AnalysisError(
                "geometry_type_not_supported",
                f"The {kind.replace('_', ' ')} module needs a polygon. Select a polygon feature.",
                details={"kind": kind, "expected": "Polygon"},
            )
        merged = part
    return geometry_to_geojson(merged, decimals=8)


def _corridor_centreline(features: list[Feature]) -> dict[str, Any]:
    """The line parts of the selection, merged, for a corridor survey (422 without any)."""
    lines = [
        shapely.force_2d(part)
        for part in (lineal_part(g) for g in (_feature_shape(f) for f in features) if g is not None)
        if part is not None and not part.is_empty
    ]
    if not lines:
        raise AnalysisError(
            "geometry_type_not_supported",
            "Corridor mapping needs a centreline. Select a line feature.",
            details={"kind": "survey", "coverage": "corridor", "expected": "LineString"},
        )
    merged = lines[0] if len(lines) == 1 else shapely.line_merge(shapely.unary_union(lines))
    if merged.geom_type != "LineString":
        # The module merges the lines the same way and refuses what does not join: say so now.
        raise AnalysisError(
            "geometry_type_not_supported",
            "Corridor mapping needs one connected centreline: the selected lines do not join into one. Select a single line.",
            details={"kind": "survey", "coverage": "corridor", "lines": len(lines)},
        )
    return geometry_to_geojson(merged, decimals=8)


def _points(features: list[Feature]) -> list[tuple[float, float, str | None]]:
    found: list[tuple[float, float, str | None]] = []
    for f in features:
        geom = f.geometry_wgs84
        if not geom or geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates")
        if isinstance(coords, list) and len(coords) >= 2:
            found.append((float(coords[0]), float(coords[1]), f.name or f.fid))
    return found


def _first_polygon(features: list[Feature], pattern: re.Pattern[str] | None = None) -> dict[str, Any] | None:
    for f in features:
        if pattern is not None and not (f.name and pattern.search(f.name)):
            continue
        geom = _feature_shape(f)
        part = polygonal_part(geom) if geom is not None else None
        if part is not None and not part.is_empty:
            return geometry_to_geojson(part, decimals=8)
    return None


def _track_coordinates(geometry: dict[str, Any]) -> list[list[float]] | None:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not isinstance(coords, list):
        return None
    if gtype in ("LineString", "MultiPoint"):
        flat = coords
    elif gtype == "MultiLineString":
        flat = [c for part in coords for c in part]
    else:
        return None
    out: list[list[float]] = []
    for c in flat:
        if not isinstance(c, list) or len(c) < 2:
            return None
        out.append([float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 and c[2] is not None else 0.0])
    return out


def _has_heights(geometry: dict[str, Any]) -> bool:
    """True when every position of a line track carries a height (``[lon, lat, alt]``)."""
    coords = geometry.get("coordinates")
    if not isinstance(coords, list):
        return False
    flat = [c for part in coords if isinstance(part, list) for c in part] if geometry.get("type") == "MultiLineString" else coords
    return all(isinstance(c, list) and len(c) > 2 and c[2] is not None for c in flat)


def _tracks(features: list[Feature]) -> tuple[list[dict[str, Any]], set[str]]:
    """The timed tracks among ``features`` and the set of their altitude modes.

    A timed track without heights (a GPX without ``<ele>``, a CSV without a height column) is
    refused (422 ``no_tracks``): read as 0 m, every drone would stand on the ground and the
    separation, speed and ceiling checks would silently find nothing.
    """
    tracks: list[dict[str, Any]] = []
    modes: set[str] = set()
    used: set[str] = set()
    flat: list[str] = []
    for f in features:
        if not f.times or len(f.times) < 2 or not f.geometry_wgs84:
            continue
        coords = _track_coordinates(f.geometry_wgs84)
        if coords is None or len(coords) != len(f.times):
            continue
        track_id = (f.name or f.fid or f"track-{f.feature_index}").strip()[:72] or f"track-{f.feature_index}"
        if track_id in used:
            track_id = f"{track_id}#{f.feature_index}"
        used.add(track_id)
        if not _has_heights(f.geometry_wgs84):
            flat.append(track_id)
            continue
        tracks.append({"id": track_id, "times": [float(t) for t in f.times], "coordinates": coords})
        modes.add(f.altitude_mode or "relativeToGround")
    if flat:
        names = ", ".join(f"'{name}'" for name in flat[:5]) + (f" and {len(flat) - 5} more" if len(flat) > 5 else "")
        subject = f"Track {names} has" if len(flat) == 1 else f"Tracks {names} have"
        raise AnalysisError(
            "no_tracks",
            f"{subject} no heights (a GPX without <ele>, a CSV without a height column): the show "
            "checks need a height for every position. Upload the tracks with their heights.",
            details={"tracks_without_heights": flat[:50]},
        )
    return tracks, modes


def _show_altitude_reference(tracks: list[dict[str, Any]], modes: set[str]) -> dict[str, Any]:
    """Altitude handling for show tracks read from a dataset.

    Tracks stored with ``absolute`` (above sea level) altitudes are measured from the take-off
    pad: the origin is the mean first position at the lowest first altitude. Mixed altitude modes
    cannot be compared and are refused.
    """
    if "absolute" not in modes:
        return {}
    if len(modes) > 1:
        raise AnalysisError(
            "mixed_altitude_modes",
            "Some tracks have altitudes above sea level and others above ground. Use one altitude mode for every drone.",
        )
    firsts = [t["coordinates"][0] for t in tracks]
    lon = sum(c[0] for c in firsts) / len(firsts)
    lat = sum(c[1] for c in firsts) / len(firsts)
    ground = min(c[2] for c in firsts)
    return {"altitude_reference": "absolute", "origin": [lon, lat, ground]}


def resolve_inputs(
    kind: str,
    params: dict[str, Any],
    selected: list[Feature],
    all_features: FeatureLoader,
    settings: Settings,
    has_dataset: bool,
) -> dict[str, Any]:
    """Fill the parameters the request left out from the dataset (see the module docstring).

    What the request sent is first held to the JSON caps (422 ``too_complex``): geometry from the
    dataset already passed the upload limits.
    """
    check_json_limits(params, max_vertices=MAX_PARAM_VERTICES)
    resolved = dict(params)
    if "batch" in resolved:
        # One result per feature: no union, the features go into batch.features (.batch).
        resolve_batch(kind, resolved, selected, has_dataset)

    if kind in GEOMETRY_KINDS and "geometry" not in resolved and has_dataset:
        if kind == "survey" and resolved.get("coverage") == "corridor":
            # A corridor survey maps along a centreline: it keeps the lines of the selection only
            # (a union with a polygon would swallow the part of the line inside it).
            resolved["geometry"] = _corridor_centreline(selected)
        else:
            resolved["geometry"] = _union_geometry(kind, selected, kind in POLYGON_KINDS)

    elif kind == "evtol_corridor" and has_dataset:
        if "origin" not in resolved or "destination" not in resolved:
            points = _points(selected)
            if len(points) < 2:
                points = _points(all_features())
            origin_given = "origin" in resolved
            if not origin_given:
                if not points:
                    raise AnalysisError(
                        "no_features",
                        "The corridor needs two point features (origin and destination) or explicit coordinates.",
                    )
                lon, lat, name = points.pop(0)
                resolved["origin"] = [lon, lat]
                if name and "origin_name" not in resolved:
                    resolved["origin_name"] = name
            if "destination" not in resolved:
                origin = resolved["origin"]
                candidates = [
                    p for p in points
                    if not (isinstance(origin, list | tuple) and len(origin) >= 2
                            and abs(p[0] - float(origin[0])) < 1e-12 and abs(p[1] - float(origin[1])) < 1e-12)
                ]
                if not candidates:
                    raise AnalysisError(
                        "no_features",
                        "The corridor needs a second point feature as destination, or explicit coordinates.",
                    )
                lon, lat, name = candidates[0]
                resolved["destination"] = [lon, lat]
                if name and "destination_name" not in resolved:
                    resolved["destination_name"] = name
        if "study_area" not in resolved:
            area = _first_polygon(selected) or _first_polygon(all_features())
            if area is not None:
                resolved["study_area"] = area

    elif kind == "show" and has_dataset:
        if "tracks" not in resolved:
            tracks, modes = _tracks(selected)
            if not tracks:
                tracks, modes = _tracks(all_features())
            if not tracks:
                raise AnalysisError(
                    "no_tracks",
                    "No timed tracks found. Upload a KML with gx:Track elements or a CSV track file.",
                )
            resolved["tracks"] = tracks
            if "altitude_reference" not in resolved:
                for key, value in _show_altitude_reference(tracks, modes).items():
                    resolved.setdefault(key, value)
        if "show_box" not in resolved:
            box = _first_polygon(all_features(), SHOW_BOX_RE)
            if box is not None:
                resolved["show_box"] = box
        if "audience" not in resolved:
            audience = _first_polygon(all_features(), AUDIENCE_RE)
            if audience is not None:
                resolved["audience"] = audience

    if kind == "line_of_sight" and has_dataset:
        resolve_line_of_sight(resolved, selected, all_features)

    if kind == "flight_audit":
        # Every viewer of the audit sees this as the "Open in FlightMD" button.
        link = resolved.get("flightmd_url")
        if isinstance(link, str) and link.strip() and not security.link_allowed(link, settings):
            allowed = "an https link" if settings.is_production else "an https link (or http to localhost)"
            raise ApiError("invalid_params", f"flightmd_url must be {allowed}.", details={"field": "flightmd_url"})
        report_url = resolved.pop("report_url", None)
        if "report" not in resolved and report_url is not None:
            if not isinstance(report_url, str) or not report_url.strip():
                raise ApiError("invalid_params", "report_url must be a non-empty URL string.")
            # Not validated here: input_bbox parses the report next and refuses anything else.
            resolved["report"] = security.fetch_flightmd_report(
                report_url.strip(), settings, validate=False, context="flight_audit analysis"
            )
            # an http link of the configured FlightMD API can be fetched but is no link for viewers
            if security.link_allowed(report_url, settings):
                resolved.setdefault("flightmd_url", report_url.strip())
        if has_dataset and report_url is None:
            # A plain position track from the dataset (api/services/tracks.py).
            resolve_flight_audit_track(resolved, selected, all_features)
    return resolved


def validate_params(kind: str, params: dict[str, Any]) -> BaseModel:
    """Validate ``params`` with the module's params model (422 ``invalid_params``)."""
    spec = REGISTRY[kind]
    try:
        return spec.params_model.model_validate(params)
    except ValidationError as exc:
        raise invalid_params_error(exc, prefix="params") from exc


def _bounds_of(points: Sequence[Sequence[float]]) -> tuple[float, float, float, float] | None:
    lons = [float(p[0]) for p in points if len(p) >= 2]
    lats = [float(p[1]) for p in points if len(p) >= 2]
    if not lons:
        return None
    return min(lons), min(lats), max(lons), max(lats)


def _merge_bounds(
    a: tuple[float, float, float, float] | None, b: tuple[float, float, float, float] | None
) -> tuple[float, float, float, float] | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _geometry_bounds(geometry: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not geometry:
        return None
    geom = geojson_to_geometry(geometry)
    if geom.is_empty:
        return None
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    return float(min_lon), float(min_lat), float(max_lon), float(max_lat)


def input_bbox(kind: str, model: BaseModel) -> tuple[float, float, float, float] | None:
    """WGS84 bounds of an analysis' inputs (used to pick the data pack)."""
    data = model.model_dump()
    if kind in GEOMETRY_KINDS:
        return _geometry_bounds(data.get("geometry"))
    if kind == "evtol_corridor":
        bounds = _bounds_of([data["origin"], data["destination"]])
        return _merge_bounds(bounds, _geometry_bounds(data.get("study_area")))
    if kind == "line_of_sight":
        return line_of_sight_bounds(data)
    if kind == "show":
        coords = [c for track in data.get("tracks", []) for c in track.get("coordinates", [])]
        bounds = _bounds_of(coords)
        origin = data.get("origin")
        if origin:
            bounds = _merge_bounds(bounds, _bounds_of([origin]))
        return bounds
    if kind == "flight_audit":
        if data.get("track"):
            return flight_audit_track_bounds(data)
        try:
            track = parse_flightmd_report(data["report"])
        except AirBriefError:
            raise
        except Exception as exc:  # any parser failure means the report is not usable
            log.warning("FlightMD report could not be parsed", exc_info=True)
            raise AnalysisError("report_invalid", "The FlightMD report could not be read.") from exc
        return _bounds_of([list(p) for p in track.points])
    return None


class _FeatureLoader:
    """Lazy access to the selected features and to all features of a dataset."""

    def __init__(self, session: Session, dataset_id: str | None, indices: list[int] | None) -> None:
        self.session = session
        self.dataset_id = dataset_id
        self.indices = indices
        self._selected: list[Feature] | None = None
        self._all: list[Feature] | None = None

    def selected(self) -> list[Feature]:
        """The selected features (all features when nothing is selected)."""
        if self.dataset_id is None:
            return []
        if self._selected is None:
            self._selected = load_features(self.session, self.dataset_id, self.indices)
        return self._selected

    def all(self) -> list[Feature]:
        if self.dataset_id is None:
            return []
        if self.indices is None:
            return self.selected()
        if self._all is None:
            self._all = load_features(self.session, self.dataset_id)
        return self._all


def create_analysis(ctx: AppContext, request: AnalysisRequest, dataset_token: str | None = None) -> AnalysisInfo:
    """Resolve inputs, validate, choose the data pack and queue the analysis (status PENDING).

    ``dataset_token`` is the caller's ``X-Owner-Token``: the analysis counts as the dataset
    owner's (``by_dataset_owner``) only when it matches the dataset's token.
    """
    kind = request.kind
    if kind not in REGISTRY:
        raise ApiError(
            "invalid_params",
            f"Unknown analysis kind '{kind}'.",
            details={"kind": kind, "known": sorted(REGISTRY)},
        )
    indices: list[int] | None = sorted(set(request.feature_indices)) if request.feature_indices else None

    with ctx.db.session() as session:
        loader = _FeatureLoader(session, request.dataset_id, indices)
        by_owner = False
        if request.dataset_id:
            dataset = get_dataset(session, request.dataset_id)
            by_owner = owner_token_matches(dataset.owner_token_hash, dataset_token)
            if dataset.status != "COMPLETED":
                raise ApiError(
                    "dataset_not_ready",
                    f"Dataset '{request.dataset_id}' is {dataset.status}; analyses need a COMPLETED dataset.",
                    details={"dataset_id": request.dataset_id, "status": dataset.status},
                )
            if indices is not None:
                bad = [i for i in indices if i < 0 or i >= (dataset.feature_count or 0)]
                if bad:
                    raise ApiError(
                        "invalid_params",
                        f"feature_indices out of range (the dataset has {dataset.feature_count} features).",
                        details={"invalid": bad[:20], "feature_count": dataset.feature_count},
                    )
        elif indices:
            raise ApiError("invalid_params", "feature_indices requires a dataset_id.")
        params = resolve_inputs(
            kind,
            request.params,
            loader.selected(),
            loader.all,
            ctx.settings,
            bool(request.dataset_id),
        )
        if kind == "airspace":
            resolve_zone_inputs(session, params)
        elif kind in ZONE_PROFILE_KINDS:
            record_zone_profile(params, kind)

    model = validate_params(kind, params)
    bbox = input_bbox(kind, model)
    pack = ctx.registry.select(bbox, preferred=ctx.settings.default_datapack)

    owner_token, owner_token_hash = new_owner_token()
    row = Analysis(
        id=str(uuid.uuid4()),
        kind=kind,
        status="PENDING",
        progress=0.0,
        stage="queued",
        dataset_id=request.dataset_id,
        feature_indices=indices,
        params=jsonable_encoder(params),
        result=None,
        datapack=pack.name if pack is not None else None,
        datapack_synthetic=bool(pack.synthetic) if pack is not None else False,
        backend=None,
        cancel_requested=False,
        owner_token_hash=owner_token_hash,
        by_dataset_owner=by_owner,
        created_at=utcnow(),
    )
    with ctx.db.session() as session:
        session.add(row)
        session.commit()
        # The owner token is shown exactly once, in this creation response.
        info = to_analysis_info(row).model_copy(update={"owner_token": owner_token})
    ctx.notify_jobs()
    return info


def _shown_params(params: dict[str, Any]) -> dict[str, Any]:
    """The params as AnalysisInfo shows them: a per-feature run's ``batch.features`` (up to 1,000
    features, in every listing) becomes ``batch.feature_count``. The JSON export keeps them."""
    batch = params.get("batch")
    if not isinstance(batch, dict) or "features" not in batch:
        return params
    collection = batch["features"]
    items = collection.get("features") if isinstance(collection, dict) else None
    shown = {k: v for k, v in batch.items() if k != "features"}
    shown["feature_count"] = len(items) if isinstance(items, list) else None
    return {**params, "batch": shown}


def to_analysis_info(row: Analysis) -> AnalysisInfo:
    error = None
    if row.error_code:
        error = ErrorInfo(code=row.error_code, message=row.error_message or row.error_code)
    created = as_utc(row.created_at)
    assert created is not None
    return AnalysisInfo.model_validate(
        {
            "id": row.id,
            "kind": row.kind,
            "status": row.status,
            "progress": float(row.progress or 0.0),
            "stage": row.stage,
            "dataset_id": row.dataset_id,
            "by_dataset_owner": bool(row.by_dataset_owner),
            "feature_indices": row.feature_indices,
            "params": _shown_params(row.params or {}),
            "result": row.result,
            "error": error,
            "datapack": row.datapack,
            "datapack_synthetic": bool(row.datapack_synthetic),
            "backend": row.backend,
            "created_at": created,
            "started_at": as_utc(row.started_at),
            "finished_at": as_utc(row.finished_at),
            "duration_ms": row.duration_ms,
        }
    )


def get_analysis(session: Session, analysis_id: str) -> Analysis:
    row = session.get(Analysis, analysis_id)
    if row is None:
        raise not_found("Analysis", analysis_id)
    return row


def list_analyses(
    session: Session,
    dataset_id: str | None,
    limit: int,
    offset: int,
    *,
    ids: list[str] | None = None,
    owner_only: bool = False,
) -> AnalysesPage:
    """Analyses newest first: one dataset's, or the given ``ids``.

    With ``owner_only`` (owner tokens enforced) a dataset lists only its owner's analyses, plus
    those of ``ids`` on that dataset. Without it a dataset lists everything run on it.
    """
    query = select(Analysis)
    count = select(func.count()).select_from(Analysis)
    if dataset_id:
        scope = Analysis.dataset_id == dataset_id
        if owner_only:
            owner = Analysis.by_dataset_owner.is_(True)
            scope = scope & (or_(owner, Analysis.id.in_(ids)) if ids else owner)
        query = query.where(scope)
        count = count.where(scope)
    elif ids is not None:
        query = query.where(Analysis.id.in_(ids))
        count = count.where(Analysis.id.in_(ids))
    total = int(session.scalar(count) or 0)
    rows = session.scalars(
        query.order_by(Analysis.created_at.desc(), Analysis.id).limit(limit).offset(offset)
    )
    return AnalysesPage(items=[to_analysis_info(r) for r in rows], total=total)


def cancel_analysis(ctx: AppContext, analysis_id: str) -> AnalysisInfo:
    """Cancel a queued analysis at once, or ask a running one to stop."""
    with ctx.db.session() as session:
        row = get_analysis(session, analysis_id)
        if row.status == "PENDING":
            now = utcnow()
            result = session.execute(
                update(Analysis)
                .where(Analysis.id == analysis_id, Analysis.status == "PENDING")
                .values(
                    status="CANCELLED",
                    cancel_requested=True,
                    stage="cancelled",
                    error_code="cancelled",
                    error_message="Cancelled before it started.",
                    finished_at=now,
                )
            )
            session.commit()
            if getattr(result, "rowcount", 0) == 1:
                session.refresh(row)
                return to_analysis_info(row)
            session.refresh(row)
        if row.status == "RUNNING":
            session.execute(
                update(Analysis).where(Analysis.id == analysis_id).values(cancel_requested=True)
            )
            session.commit()
            if ctx.runner is not None:
                ctx.runner.cancel(analysis_id)
            session.refresh(row)
        return to_analysis_info(row)


def delete_analysis(ctx: AppContext, analysis_id: str) -> None:
    """Delete an analysis (a running one is asked to stop first)."""
    with ctx.db.session() as session:
        get_analysis(session, analysis_id)
        if ctx.runner is not None:
            ctx.runner.cancel(analysis_id)
        session.execute(delete(Analysis).where(Analysis.id == analysis_id))
        session.commit()


def _iso(value: datetime | None) -> str:
    stamp = as_utc(value) or utcnow()
    return stamp.isoformat().replace("+00:00", "Z")


def envelope_for(row: Analysis) -> dict[str, Any]:
    """The :class:`~airbrief_core.models.AnalysisEnvelope` of a completed analysis."""
    from airbrief_core.models.analyses import AnalysisEnvelope

    notice, watermark = evidence_labels(row.kind, bool(row.datapack_synthetic))
    envelope = AnalysisEnvelope.model_validate(
        {
            "notice": notice,
            "watermark": watermark,
            "schema_version": SCHEMA_VERSION,
            "airbrief_version": __version__,
            "kind": row.kind,
            "params": row.params or {},
            "result": row.result or {},
            "datapack": row.datapack,
            "datapack_synthetic": bool(row.datapack_synthetic),
            "datapack_sha256": row.datapack_sha256,
            "backend": row.backend if row.backend in ("cpp", "python") else "python",
            "started_utc": _iso(row.started_at),
            "finished_utc": _iso(row.finished_at),
            "duration_ms": int(row.duration_ms or 0),
        }
    )
    return envelope.model_dump(mode="json")


def main_table_csv(
    kind: str, result: dict[str, Any], params: dict[str, Any], table: str | None = None, *, datapack_synthetic: bool = False
) -> bytes:
    """One CSV table of an analysis: ``table`` from :func:`~api.services.exports.table_catalog`
    (default: the kind's first, e.g. airspace's zones, obstacles, grid cells and profile in one
    file with a leading ``table`` column). Every row ends with the evidence ``notice``."""
    return table_csv(kind, result, params, table, datapack_synthetic=datapack_synthetic)


def _completed(session: Session, analysis_id: str) -> Analysis:
    row = get_analysis(session, analysis_id)
    if row.status != "COMPLETED" or row.result is None:
        raise ApiError(
            "analysis_not_finished",
            f"Analysis '{analysis_id}' is {row.status}; only completed analyses can be exported.",
            details={"status": row.status},
        )
    return row


def export_base(session: Session, row: Analysis) -> str:
    """The download name of an analysis export, without extension (safe ASCII)."""
    base = f"airbrief_{row.kind}_{row.id[:8]}"
    if row.dataset_id:
        dataset = session.get(Dataset, row.dataset_id)
        if dataset is not None:
            base = f"{export_basename(dataset.filename)}_{row.kind}_{row.id[:8]}"
    return base


def analysis_tables(session: Session, analysis_id: str) -> list[dict[str, Any]]:
    """The CSV tables of a completed analysis: ``{key, label, rows, multi, default}`` each."""
    row = _completed(session, analysis_id)
    out: list[dict[str, Any]] = []
    for i, spec in enumerate(table_catalog(row.kind, row.result or {})):
        rows, _columns = spec.build(row.result or {}, row.params or {})
        out.append({"key": spec.key, "label": spec.label, "rows": len(rows), "multi": spec.multi, "default": i == 0})
    return out


def export_analysis(
    session: Session, analysis_id: str, fmt: str, table: str | None = None, ed318_limits: Ed318Limits | None = None
) -> tuple[bytes, str, str]:
    """Export a completed analysis. Returns ``(content, media_type, filename)``.

    ``json`` = the :class:`~airbrief_core.models.analyses.AnalysisEnvelope`; ``geojson`` = every
    result layer (H3 cells as polygons, capped); ``kml`` = the evidence layers; ``csv`` = one table
    (``table``, see :func:`analysis_tables`); ``ed318`` = the zones and bands as an ED-318 collection,
    with the notice and watermark in its ``airbrief`` member and the vertical limits where
    ``ed318_limits`` says (``geometry`` by default).
    """
    row = _completed(session, analysis_id)
    base = export_base(session, row)
    result = row.result or {}
    synthetic = bool(row.datapack_synthetic)
    if table is not None and fmt != "csv":
        raise ApiError("invalid_params", "table applies to the CSV export only.", details={"format": fmt, "table": table})
    if ed318_limits is not None and fmt != "ed318":
        raise ApiError("invalid_params", "ed318_limits applies to the ED-318 export only.",
                       details={"format": fmt, "ed318_limits": ed318_limits})
    if fmt == "json":
        content = json.dumps(envelope_for(row), ensure_ascii=False).encode("utf-8")
    elif fmt == "geojson":
        doc = geojson_document(row.kind, result, analysis_id=row.id, datapack=row.datapack, datapack_synthetic=synthetic, name=base)
        content = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    elif fmt == "kml":
        content = analysis_kml(row.kind, result, analysis_id=row.id, datapack=row.datapack, datapack_synthetic=synthetic, name=base)
    elif fmt == "csv":
        known = [spec.key for spec in table_catalog(row.kind, result)]
        if not known:
            # A kind without a table (one added later): answer in the error envelope, not with a 500.
            raise ApiError(
                "invalid_params",
                f"A {row.kind.replace('_', ' ')} analysis has no CSV table; export JSON, GeoJSON or KML instead.",
                details={"format": fmt, "known": known},
            )
        if table is not None and table not in known:
            raise ApiError(
                "invalid_params",
                f"This {row.kind.replace('_', ' ')} analysis has no table '{table}'.",
                details={"table": table, "known": known},
            )
        content = main_table_csv(row.kind, result, row.params or {}, table, datapack_synthetic=synthetic)
        if table is not None and known and table != known[0]:
            base = f"{base}_{table}"
    elif fmt == "ed318":
        doc = analysis_ed318(row.kind, result, name=base, limits=ed318_limits or "geometry")
        notice, watermark = evidence_labels(row.kind, synthetic)
        doc["airbrief"] = {"notice": notice, "watermark": watermark, "analysis_id": row.id}
        return json.dumps(doc, ensure_ascii=False).encode("utf-8"), "application/geo+json", f"{base}_zones.ed318.geojson"
    else:
        raise ApiError("invalid_params", "format must be json, geojson, kml, csv or ed318.", details={"format": fmt})
    return content, _EXPORT_MEDIA_TYPES[fmt], f"{base}.{fmt}"
