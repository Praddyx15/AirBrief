"""Sampling helpers: evenly spaced points along lines and regular point grids over polygons."""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from airbrief_core.errors import AnalysisError

from .crs import WGS84, local_area_crs
from .geodesic import GEOD
from .transform import get_transformer, transform_geom, unwrap_antimeridian
from .validity import lineal_part, polygonal_part

__all__ = ["MAX_BBOX_GRID_POINTS", "grid_points", "sample_line"]

MAX_BBOX_GRID_POINTS = 4_000_000
"""Upper bound on candidate points in the bounding-box grid (memory guard)."""


def _sample_part(line: LineString, spacing_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    coords = np.asarray(line.coords, dtype=float)[:, :2]
    lons, lats = coords[:, 0], coords[:, 1]
    if lons.size == 1:
        return np.zeros(1), lons.copy(), lats.copy()
    az, _, seg = GEOD.inv(lons[:-1], lats[:-1], lons[1:], lats[1:])
    seg = np.asarray(seg, dtype=float)
    az = np.asarray(az, dtype=float)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    if total <= 0.0:
        return np.zeros(1), lons[:1].copy(), lats[:1].copy()
    # Tolerate floating-point noise so an exact multiple of the spacing is not split once more.
    n = max(1, int(math.ceil(total / spacing_m * (1.0 - 1e-6))))
    dist = np.linspace(0.0, total, n + 1)
    idx = np.searchsorted(cum, dist, side="right") - 1
    idx = np.clip(idx, 0, seg.size - 1)
    offset = dist - cum[idx]
    out_lon, out_lat, _ = GEOD.fwd(lons[idx], lats[idx], az[idx], offset)
    out_lon = np.asarray(out_lon, dtype=float)
    out_lat = np.asarray(out_lat, dtype=float)
    # Pin the exact endpoints.
    out_lon[0], out_lat[0] = lons[0], lats[0]
    out_lon[-1], out_lat[-1] = lons[-1], lats[-1]
    out_lon = ((out_lon + 180.0) % 360.0) - 180.0
    return dist, out_lon, out_lat


def sample_line(line_wgs84: BaseGeometry, spacing_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Evenly spaced points along a WGS84 line, including both endpoints.

    The actual spacing is ``length / ceil(length / spacing_m)`` (never above ``spacing_m``),
    measured geodesically. Returns ``(distances_m, lons, lats)``. For a MultiLineString the
    parts are sampled in order and the distance continues across parts (gaps are not counted).

    Raises:
        AnalysisError: ``invalid_params`` for a non-positive spacing,
            ``geometry_type_not_supported`` when the geometry has no lineal part.
    """
    if not math.isfinite(spacing_m) or spacing_m <= 0.0:
        raise AnalysisError("invalid_params", "Sample spacing must be a positive number of metres", details={"spacing_m": spacing_m})
    lineal = lineal_part(line_wgs84)
    if lineal is None:
        raise AnalysisError(
            "geometry_type_not_supported",
            "A line is required for sampling",
            details={"geometry_type": None if line_wgs84 is None else line_wgs84.geom_type},
        )
    lines = [lineal] if isinstance(lineal, LineString) else list(lineal.geoms)
    dists: list[np.ndarray] = []
    lons: list[np.ndarray] = []
    lats: list[np.ndarray] = []
    base = 0.0
    for line in lines:
        d, lo, la = _sample_part(line, spacing_m)
        dists.append(d + base)
        lons.append(lo)
        lats.append(la)
        base += float(d[-1])
    return np.concatenate(dists), np.concatenate(lons), np.concatenate(lats)


def grid_points(polygon_wgs84: BaseGeometry, step_m: float, max_points: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Regular grid of points inside a WGS84 polygon, laid out in a local equal-area projection.

    Points sit at cell centres of a ``step x step`` grid. If more than ``max_points`` would
    fall inside, the step is enlarged. Returns ``(lons, lats, cell_area_m2)`` where
    ``cell_area_m2`` is the (exact, equal-area) area each point represents. A polygon too
    small to contain a grid point yields its representative point with ``cell_area_m2`` equal
    to the polygon area.

    Raises:
        AnalysisError: ``invalid_params`` for a bad step or ``max_points``;
            ``geometry_type_not_supported`` when there is no polygonal part.
    """
    if not math.isfinite(step_m) or step_m <= 0.0:
        raise AnalysisError("invalid_params", "Grid step must be a positive number of metres", details={"step_m": step_m})
    if int(max_points) < 1:
        raise AnalysisError("invalid_params", "max_points must be at least 1", details={"max_points": max_points})
    max_points = int(max_points)
    poly = polygonal_part(polygon_wgs84)
    if poly is None:
        raise AnalysisError(
            "geometry_type_not_supported",
            "An area (polygon) is required for a point grid",
            details={"geometry_type": None if polygon_wgs84 is None else polygon_wgs84.geom_type},
        )
    poly = shapely.force_2d(unwrap_antimeridian(poly))
    centre = poly.centroid
    if centre.is_empty:
        minx, miny, maxx, maxy = poly.bounds
        lon0, lat0 = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    else:
        lon0, lat0 = float(centre.x), float(centre.y)
    laea = local_area_crs(lon0, lat0)
    local = transform_geom(poly, WGS84, laea)
    if not local.is_valid:
        local = shapely.make_valid(local)
    area = float(local.area)
    inverse = get_transformer(laea, WGS84)

    def _single() -> tuple[np.ndarray, np.ndarray, float]:
        rep = local.representative_point()
        lo, la = inverse.transform(np.array([rep.x]), np.array([rep.y]))
        return np.asarray(lo, dtype=float), np.asarray(la, dtype=float), area

    if area <= 0.0 or area < float(step_m) * float(step_m):
        return _single()

    minx, miny, maxx, maxy = local.bounds
    width, height = maxx - minx, maxy - miny
    step = max(float(step_m), math.sqrt(area / max_points))
    step = max(step, math.sqrt(max(width * height, 0.0) / MAX_BBOX_GRID_POINTS))
    shapely.prepare(local)
    while True:  # terminates: step grows by >= 1 % per pass until the count fits or reaches 0
        nx = max(1, int(math.ceil(width / step - 1e-6)))
        ny = max(1, int(math.ceil(height / step - 1e-6)))
        x0 = minx + (width - (nx - 1) * step) / 2.0
        y0 = miny + (height - (ny - 1) * step) / 2.0
        gx, gy = np.meshgrid(x0 + step * np.arange(nx), y0 + step * np.arange(ny))
        gx = gx.ravel()
        gy = gy.ravel()
        inside = shapely.contains_xy(local, gx, gy)
        count = int(np.count_nonzero(inside))
        if count == 0:
            return _single()
        if count <= max_points:
            lo, la = inverse.transform(gx[inside], gy[inside])
            return np.asarray(lo, dtype=float), np.asarray(la, dtype=float), step * step
        step *= math.sqrt(count / max_points) * 1.01
