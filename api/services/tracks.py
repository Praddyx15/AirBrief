"""Flight audit from a dataset's position track (called from :mod:`api.services.analyses`).

Input resolution (docs/features/tracks.md), applied only when the request gives no FlightMD
``report``, ``report_url`` or ``track``:

* ``track`` — the first **timed** track (a feature with ``times``: KML ``gx:Track``, GPX ``trk``
  with ``<time>`` on every point, CSV track with a time column) among the selected features, else
  the first one in the dataset: ``{name, coordinates, times, time_origin_utc, altitude_mode,
  source_format, feature_index}``. Coordinates keep their dimension (2D tracks stay 2D, the audit
  then reads heights above take-off).
* ``training_box`` — only when the request leaves it out (send ``null`` to switch the check
  off): the first polygon, or closed line such as a GPX route, named like :data:`TRAINING_BOX_RE`
  ("Training box", "Training area", "Flying area", "Boundary"…) but not like an area to stay out
  of (:data:`NOT_TRAINING_BOX_RE`: "No-fly area", "Restricted area", "Audience area");
  ``training_box_name`` is the feature name.

This audits recorded positions; it parses no flight logs (FlightMD's scope).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from airbrief_core.errors import AirBriefError, AnalysisError
from airbrief_core.geo.transform import geojson_to_geometry, geometry_to_geojson
from airbrief_core.geo.validity import polygonal_part

__all__ = [
    "NOT_TRAINING_BOX_RE",
    "TRAINING_BOX_RE",
    "flight_audit_track_bounds",
    "is_training_area_name",
    "resolve_flight_audit_track",
    "track_source_format",
]

TRAINING_BOX_RE = re.compile(r"training|\b(area|boundary|geofence)\b", re.IGNORECASE)
"""Feature names taken as the training area of a dataset track (unless :data:`NOT_TRAINING_BOX_RE`)."""

NOT_TRAINING_BOX_RE = re.compile(
    r"\bno[\s_-]*fly|\bnfz\b|restrict|prohibit|danger|exclu|keep[\s_-]*out|forbidden|audience|spectator|\bred[\s_-]*zone",
    re.IGNORECASE,
)
"""Names of areas to stay *out of* ("No-fly area", "Restricted area", "Audience area"): never a
training area, whatever else the name says."""


def is_training_area_name(name: str | None) -> bool:
    """True for a feature name that reads as the area a flight should stay inside."""
    return bool(name and TRAINING_BOX_RE.search(name) and not NOT_TRAINING_BOX_RE.search(name))

_SOURCE_KEYS = ("report", "report_url", "track")
_CSV_FORMATS = frozenset({"lon_lat", "local_xyz"})


def track_source_format(properties: dict[str, Any] | None) -> str:
    """``gpx`` / ``csv`` / ``kml`` for a timed track feature (only these readers keep times)."""
    props = properties or {}
    if props.get("gpx_element") is not None:
        return "gpx"
    if props.get("source_format") in _CSV_FORMATS:
        return "csv"
    return "kml"


def _line_coordinates(geometry: dict[str, Any] | None) -> list[list[float]] | None:
    """The positions of a LineString (or a MultiLineString's parts in order), dimension kept."""
    if not geometry:
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not isinstance(coords, list):
        return None
    if gtype == "LineString":
        flat = coords
    elif gtype == "MultiLineString":
        flat = [c for part in coords if isinstance(part, list) for c in part]
    else:
        return None
    out: list[list[float]] = []
    for c in flat:
        if not isinstance(c, list) or len(c) < 2:
            return None
        values = [float(v) for v in c[:3] if v is not None]
        if len(values) < 2 or not all(math.isfinite(v) for v in values):
            return None
        out.append(values)
    return out


def _timed_track(features: list[Any]) -> tuple[Any, list[list[float]]] | None:
    """The first feature whose times match its line positions."""
    for f in features:
        if not f.times or len(f.times) < 2:
            continue
        coords = _line_coordinates(f.geometry_wgs84)
        if coords is not None and len(coords) == len(f.times):
            return f, coords
    return None


def _area(geometry: dict[str, Any] | None) -> BaseGeometry | None:
    """A polygon, or a closed line read as its outline."""
    if not geometry:
        return None
    try:
        geom = geojson_to_geometry(geometry)
    except AirBriefError:
        return None
    if geom.is_empty:
        return None
    if isinstance(geom, LineString) and geom.is_closed and len(geom.coords) >= 4:
        geom = Polygon([(x, y) for x, y, *_ in geom.coords])
    part = polygonal_part(geom)
    return part if part is not None and not part.is_empty else None


def _training_box(features: list[Any]) -> tuple[dict[str, Any], str] | None:
    for f in features:
        # A timed track is a flight, never a training area (a closed track could look like one).
        if f.times and len(f.times) >= 2:
            continue
        if not is_training_area_name(f.name):
            continue
        area = _area(f.geometry_wgs84)
        if area is not None:
            return geometry_to_geojson(area, decimals=8), f.name.strip()[:200]
    return None


def resolve_flight_audit_track(
    resolved: dict[str, Any], selected: list[Any], all_features: Callable[[], list[Any]]
) -> None:
    """Fill ``track`` and ``training_box`` in ``resolved`` from the dataset (see the module docstring)."""
    if any(key in resolved for key in _SOURCE_KEYS):
        return
    found = _timed_track(selected) or _timed_track(all_features())
    if found is None:
        raise AnalysisError(
            "no_tracks",
            "No timed track found in the dataset. Upload a GPX track with times, a KML gx:Track or a CSV track "
            "with a time column, or audit a FlightMD report.",
            details={"kind": "flight_audit"},
        )
    feature, coords = found
    name = (feature.name or feature.fid or f"Track {int(feature.feature_index) + 1}").strip()[:200]
    resolved["track"] = {
        "name": name or None,
        "coordinates": coords,
        "times": [float(t) for t in feature.times],
        "time_origin_utc": feature.time_origin_utc,
        "altitude_mode": feature.altitude_mode,
        "source_format": track_source_format(feature.properties),
        "feature_index": int(feature.feature_index),
    }
    if "training_box" not in resolved:
        box = _training_box(all_features())
        if box is not None:
            resolved["training_box"] = box[0]
            resolved.setdefault("training_box_name", box[1])


def flight_audit_track_bounds(data: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """WGS84 bounds of an audited position track and its training area (pack selection)."""
    lons: list[float] = []
    lats: list[float] = []
    track = data.get("track") or {}
    for c in track.get("coordinates") or []:
        lons.append(float(c[0]))
        lats.append(float(c[1]))
    box = data.get("training_box")
    if box:
        geom = geojson_to_geometry(box)
        if not geom.is_empty:
            min_lon, min_lat, max_lon, max_lat = geom.bounds
            lons += [float(min_lon), float(max_lon)]
            lats += [float(min_lat), float(max_lat)]
    if not lons:
        return None
    return min(lons), min(lats), max(lons), max(lats)
