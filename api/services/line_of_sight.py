"""Line-of-sight inputs and exports (called from :mod:`api.services.analyses`).

Input resolution (docs/features/los.md), applied only to parameters the request leaves out:

* ``observers`` — point features named like a pilot (``pilot``, ``remote pilot``, ``RPIC``), an
  observer (``observer``, ``visual observer``, ``spotter``, ``VO``) or a ground station (``GCS``,
  ``ground station``, ``ground control station``, ``base station``), or with a ``role``
  property ``pilot`` / ``observer`` / ``gcs``; ``height_agl_m`` is taken from the feature's
  property when it is a number in 0–500 m. At most :data:`MAX_OBSERVERS`, in dataset order. An
  explicit ``observers: []`` is kept (the module then assumes the pilot position).
* ``geometry`` — the union of the selected features (all features when none are selected)
  without those observer points; when nothing else is selected, every other feature.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from typing import Any, Protocol

import shapely
from shapely.geometry.base import BaseGeometry

from airbrief_core.errors import AirBriefError, AnalysisError
from airbrief_core.geo.transform import geojson_to_geometry, geometry_to_geojson

__all__ = [
    "LOS_CSV_COLUMNS",
    "MAX_OBSERVERS",
    "line_of_sight_bounds",
    "line_of_sight_csv",
    "observer_role",
    "resolve_line_of_sight",
]

MAX_OBSERVERS = 10

GCS_RE = re.compile(r"\b(gcs|ground\s*(control\s*)?station|base\s*station)\b", re.IGNORECASE)
OBSERVER_RE = re.compile(r"\b(observer|spotter|vo)\b", re.IGNORECASE)
PILOT_RE = re.compile(r"\b(pilot|rpic)\b", re.IGNORECASE)
_ROLES = ("pilot", "observer", "gcs")


class _FeatureLike(Protocol):
    feature_index: int
    name: str | None
    fid: str | None
    geometry_wgs84: dict[str, Any] | None
    properties: dict[str, Any] | None


def observer_role(name: str | None, properties: dict[str, Any] | None = None) -> str | None:
    """``pilot`` / ``observer`` / ``gcs`` for a feature's ``role`` property or name, else None."""
    role = (properties or {}).get("role")
    if isinstance(role, str) and role.strip().lower() in _ROLES:
        return role.strip().lower()
    text = name or ""
    if GCS_RE.search(text):
        return "gcs"
    if OBSERVER_RE.search(text):
        return "observer"
    if PILOT_RE.search(text):
        return "pilot"
    return None


def _point(feature: _FeatureLike) -> tuple[float, float] | None:
    geom = feature.geometry_wgs84
    if not geom or geom.get("type") != "Point":
        return None
    coords = geom.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        return None
    lon, lat = float(coords[0]), float(coords[1])
    return (lon, lat) if math.isfinite(lon) and math.isfinite(lat) else None


def _height(properties: dict[str, Any] | None) -> float | None:
    raw = (properties or {}).get("height_agl_m")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    value = float(raw)
    return value if math.isfinite(value) and 0.0 <= value <= 500.0 else None


def _observers(features: list[Any]) -> tuple[list[dict[str, Any]], set[int]]:
    """Observer entries from named point features, and the indices of every such feature."""
    found: list[dict[str, Any]] = []
    indices: set[int] = set()
    for f in features:
        point = _point(f)
        role = observer_role(f.name, f.properties) if point is not None else None
        if point is None or role is None:
            continue
        indices.add(int(f.feature_index))
        if len(found) >= MAX_OBSERVERS:
            continue
        name = (f.name or f.fid or f"{role} {f.feature_index}").strip()[:80] or role.capitalize()
        entry: dict[str, Any] = {"name": name, "role": role, "lon": point[0], "lat": point[1]}
        height = _height(f.properties)
        if height is not None:
            entry["height_agl_m"] = height
        found.append(entry)
    return found, indices


def _union(features: list[Any]) -> BaseGeometry | None:
    shapes: list[BaseGeometry] = []
    for f in features:
        if not f.geometry_wgs84:
            continue
        try:
            geom = geojson_to_geometry(f.geometry_wgs84)
        except AirBriefError:
            continue
        if not geom.is_empty:
            # The module works in 2D (it drops heights): a 2D route and a 3D track in one union
            # would mix dimensions, which the GeoJSON round trip cannot hold.
            shapes.append(shapely.force_2d(geom))
    if not shapes:
        return None
    return shapes[0] if len(shapes) == 1 else shapely.unary_union(shapes)


def resolve_line_of_sight(
    resolved: dict[str, Any], selected: list[Any], all_features: Callable[[], list[Any]]
) -> None:
    """Fill ``observers`` and ``geometry`` in ``resolved`` from the dataset (see the module docstring)."""
    everything = all_features()
    found, observer_indices = _observers(everything)
    if "observers" not in resolved and found:
        resolved["observers"] = found
    if "geometry" in resolved:
        return
    geom = _union([f for f in selected if int(f.feature_index) not in observer_indices])
    if geom is None:
        geom = _union([f for f in everything if int(f.feature_index) not in observer_indices])
    if geom is None:
        raise AnalysisError(
            "no_features",
            "The line-of-sight check needs an operation area, a route or a point besides the observer "
            "positions. Select or draw one.",
            details={"kind": "line_of_sight"},
        )
    resolved["geometry"] = geometry_to_geojson(geom, decimals=8)


def line_of_sight_bounds(data: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """WGS84 bounds of the geometry, the observers and the radio target (pack selection)."""
    lons: list[float] = []
    lats: list[float] = []
    geometry = data.get("geometry")
    if geometry:
        geom = geojson_to_geometry(geometry)
        if not geom.is_empty:
            min_lon, min_lat, max_lon, max_lat = geom.bounds
            lons += [float(min_lon), float(max_lon)]
            lats += [float(min_lat), float(max_lat)]
    for point in [*(data.get("observers") or []), data.get("radio_target")]:
        if isinstance(point, dict) and point.get("lon") is not None and point.get("lat") is not None:
            lons.append(float(point["lon"]))
            lats.append(float(point["lat"]))
    if not lons:
        return None
    return min(lons), min(lats), max(lons), max(lats)


LOS_CSV_COLUMNS: list[str] = [
    "table",
    # observers
    "name", "role", "los_kind", "lon", "lat", "height_agl_m", "ground_m", "eye_amsl_m", "k_factor",
    "visible_pct", "visible_area_m2", "max_visible_m", "assumed",
    # radio paths
    "path", "from_name", "to_name", "to_lon", "to_lat", "to_height_agl_m", "distance_m", "slant_m",
    "fspl_db", "worst_clearance_pct", "worst_at_m", "max_fresnel_radius_m", "los_blocked", "below_threshold",
    "frequency_mhz",
    # radio profile samples
    "d_m", "terrain_m", "obstacle_m", "los_m", "fresnel_lower_m", "fresnel_radius_m", "clearance_pct",
    # hidden route stretches
    "start_m", "end_m", "length_m",
]
"""Columns of the line-of-sight CSV: one file, the first column names the table of each row
(``observer``, ``radio_path``, ``radio_profile``, ``route_hidden``)."""

_OBSERVER_KEYS = ("name", "role", "los_kind", "lon", "lat", "height_agl_m", "ground_m", "eye_amsl_m", "k_factor",
                  "visible_pct", "visible_area_m2", "max_visible_m", "assumed")
_PATH_KEYS = ("from_name", "to_name", "to_lon", "to_lat", "to_height_agl_m", "distance_m", "slant_m", "fspl_db",
              "worst_clearance_pct", "worst_at_m", "max_fresnel_radius_m", "los_blocked", "below_threshold")
_PROFILE_KEYS = ("d_m", "terrain_m", "obstacle_m", "los_m", "fresnel_lower_m", "fresnel_radius_m", "clearance_pct")


def _pick(row: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return {k: (json.dumps(row[k]) if isinstance(row.get(k), dict | list) else row.get(k)) for k in keys}


def line_of_sight_csv(result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows and columns of the line-of-sight CSV (observers, radio paths, profiles, hidden stretches)."""
    rows: list[dict[str, Any]] = [
        {"table": "observer", **_pick(o, _OBSERVER_KEYS)} for o in result.get("observers") or []
    ]
    radio = result.get("radio") if isinstance(result.get("radio"), dict) else None
    if radio:
        for number, path in enumerate(radio.get("paths") or [], start=1):
            if not isinstance(path, dict):
                continue
            rows.append({"table": "radio_path", "path": number, "frequency_mhz": radio.get("frequency_mhz"),
                         **_pick(path, _PATH_KEYS)})
            for sample in path.get("profile") or []:
                rows.append({"table": "radio_profile", "path": number, **_pick(sample, _PROFILE_KEYS)})
    route = result.get("route") if isinstance(result.get("route"), dict) else None
    if route:
        for seg in route.get("hidden_segments") or []:
            rows.append({"table": "route_hidden", **_pick(seg, ("start_m", "end_m", "length_m"))})
    return rows, list(LOS_CSV_COLUMNS)
