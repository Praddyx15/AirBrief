"""Terrain and obstacle helpers shared by the airspace, corridor and audit modules.

* :func:`terrain_profile` — terrain heights sampled evenly along a line (``geo.sampling.sample_line``).
* :func:`terrain_summary` — min/max/mean/relief of the DEM over any geometry
  (polygons on a grid, lines every ``step_m``, points directly).
* :func:`obstacle_hits` — data-pack obstacles within a search distance of a geometry, flagged as
  conflicts when ``height_m + vertical_clearance_m > altitude_agl_m``. Obstacle ``height_m`` is
  above ground level; with a DEM the ground elevation (``base_m``) and the obstacle top above
  mean sea level (``top_amsl_m``) are added. A plan flown at a constant level
  (``aircraft_amsl_m``, heights above take-off or AMSL) is compared at each obstacle with the
  height above *its* ground, ``aircraft_amsl_m − base_m``.
* :func:`obstacle_height_source` — how an obstacle's height is known (``tag``, ``levels`` or
  ``default``: an assumed per-kind height when OSM maps none, see ``seed/datapack.py``).

All heights are metres; ``None`` marks values that cannot be computed (no DEM, nodata, outside).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from shapely.geometry.base import BaseGeometry

from ..data.datapack import DataPack
from ..data.raster import RasterSampler
from ..data.vectors import PointFeature
from ..geo.sampling import sample_line
from ..models.common import ObstacleHit, ProfilePoint, TerrainSummary

__all__ = ["obstacle_height", "obstacle_height_source", "obstacle_hits", "terrain_profile", "terrain_summary"]

SUMMARY_MAX_POINTS = 100_000
"""Upper bound on DEM samples for :func:`terrain_summary` (the grid step grows beyond it)."""


def _finite_or_none(value: float, digits: int = 2) -> float | None:
    v = float(value)
    return round(v, digits) if math.isfinite(v) else None


def terrain_profile(line_wgs84: BaseGeometry, dem: RasterSampler | None, spacing_m: float) -> list[ProfilePoint]:
    """Points every ``spacing_m`` (at most) along a WGS84 line, endpoints included.

    ``terrain_m`` is the bilinear DEM height (``None`` without a DEM or outside it). Altitude
    fields are left for the caller to fill. ``sample_line`` raises ``invalid_params`` for a
    non-positive spacing and ``geometry_type_not_supported`` when there is no line.
    """
    distances, lons, lats = sample_line(line_wgs84, spacing_m)
    if dem is not None and lons.size:
        terrain = np.asarray(dem.sample(lons, lats), dtype=np.float64)
    else:
        terrain = np.full(lons.shape, np.nan)
    return [
        ProfilePoint(
            distance_m=round(float(d), 2),
            lon=round(float(lo), 8),
            lat=round(float(la), 8),
            terrain_m=_finite_or_none(t),
        )
        for d, lo, la, t in zip(distances.tolist(), lons.tolist(), lats.tolist(), terrain.tolist(), strict=True)
    ]


def terrain_summary(geom_wgs84: BaseGeometry, dem: RasterSampler | None, step_m: float = 30.0) -> TerrainSummary | None:
    """Terrain statistics over a geometry, or ``None`` without a DEM or without valid samples."""
    if dem is None or geom_wgs84 is None or geom_wgs84.is_empty:
        return None
    step = float(step_m) if math.isfinite(step_m) and step_m > 0 else 30.0
    stats = dem.stats(geom_wgs84, step, max_points=SUMMARY_MAX_POINTS)
    if not stats.get("valid") or stats.get("min") is None or stats.get("max") is None:
        return None
    lo = float(stats["min"])
    hi = float(stats["max"])
    return TerrainSummary(
        min_m=round(lo, 2),
        max_m=round(hi, 2),
        mean_m=round(float(stats["mean"]), 2),
        relief_m=round(hi - lo, 2),
        samples=int(stats["valid"]),
        source=str(dem.name),
    )


def obstacle_height(props: dict[str, Any]) -> float | None:
    """The ``height_m`` property as a finite, non-negative float (``None`` if unusable)."""
    raw = props.get("height_m")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0.0:
        return None
    return value


HEIGHT_SOURCES = ("tag", "levels", "default")
"""Values of the obstacle ``height_source`` property written by the pack builder."""


def obstacle_height_source(props: dict[str, Any]) -> str | None:
    """The ``height_source`` property (``tag``, ``levels`` or ``default``), or None when absent."""
    raw = props.get("height_source")
    return raw if isinstance(raw, str) and raw in HEIGHT_SOURCES else None


def obstacle_hits(
    geom_wgs84: BaseGeometry,
    pack: DataPack | None,
    *,
    search_m: float,
    altitude_agl_m: float,
    vertical_clearance_m: float,
    dem: RasterSampler | None = None,
    limit: int = 200,
    aircraft_amsl_m: float | None = None,
) -> list[ObstacleHit]:
    """Obstacles within ``search_m`` of a geometry, conflicts first, then nearest first.

    ``clearance_m`` is the vertical gap between the planned altitude and the obstacle top
    (``altitude_agl_m − height_m``; negative when the obstacle is taller). An obstacle is a
    conflict when ``height_m + vertical_clearance_m > altitude_agl_m``. Obstacles without a
    usable ``height_m`` are skipped. At most ``limit`` hits are returned.

    With ``aircraft_amsl_m`` (a constant flight level) the planned height at an obstacle whose
    ground is known is ``aircraft_amsl_m − base_m``; ``altitude_agl_m`` is used where it is not.
    """
    if pack is None or geom_wgs84 is None or geom_wgs84.is_empty or limit <= 0:
        return []
    index = pack.obstacles()
    if not index:
        return []
    found = index.near_geometry(geom_wgs84, max(0.0, float(search_m)))
    usable: list[tuple[PointFeature, float, float]] = []
    for feat, dist in found:
        height_value = obstacle_height(feat.props)
        if height_value is not None:
            usable.append((feat, float(dist), height_value))
    if not usable:
        return []
    if dem is not None:
        lons = np.fromiter((f.lon for f, _, _ in usable), dtype=np.float64, count=len(usable))
        lats = np.fromiter((f.lat for f, _, _ in usable), dtype=np.float64, count=len(usable))
        bases = np.asarray(dem.sample(lons, lats), dtype=np.float64)
    else:
        bases = np.full(len(usable), np.nan)
    margin = float(vertical_clearance_m)
    hits: list[ObstacleHit] = []
    for (feat, dist, height), base in zip(usable, bases.tolist(), strict=True):
        base_m = _finite_or_none(base)
        name = feat.get("name")
        alt = float(altitude_agl_m)
        if aircraft_amsl_m is not None and math.isfinite(base):
            alt = float(aircraft_amsl_m) - float(base)
        source = obstacle_height_source(feat.props)
        hits.append(
            ObstacleHit(
                id=str(feat.id),
                kind=str(feat.get("kind") or "obstacle"),
                name=str(name) if name not in (None, "") else None,
                lon=round(feat.lon, 8),
                lat=round(feat.lat, 8),
                height_m=round(height, 2),
                base_m=base_m,
                top_amsl_m=round(base_m + height, 2) if base_m is not None else None,
                distance_m=round(float(dist), 2),
                clearance_m=round(alt - height, 2),
                conflict=height + margin > alt + 1e-9,
                height_source=source,
                height_assumed=source == "default",
            )
        )
    hits.sort(key=lambda h: (not h.conflict, h.distance_m, h.id))
    return hits[: int(limit)]
