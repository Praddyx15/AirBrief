"""Ellipsoidal (WGS84) geodesic computations via Karney's algorithms in ``pyproj.Geod``.

These are the ground truth used to cross-check projected measurements.
Inputs are lon/lat degrees; longitudes outside [-180, 180] are accepted.
"""

from __future__ import annotations

from typing import overload

import numpy as np
from numpy.typing import ArrayLike
from pyproj import Geod
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry

from .validity import iter_parts

GEOD = Geod(ellps="WGS84")
"""Shared WGS84 geodesic calculator."""

__all__ = [
    "GEOD",
    "bearing_deg",
    "distance_m",
    "geodesic_area_perimeter",
    "geodesic_length",
    "ring_area_perimeter",
]


def ring_area_perimeter(xs: ArrayLike, ys: ArrayLike) -> tuple[float, float]:
    """Unsigned geodesic area (m²) and perimeter (m) of a closed ring of lon/lat vertices."""
    lons = np.asarray(xs, dtype=float)
    lats = np.asarray(ys, dtype=float)
    if lons.size >= 2 and lons[0] == lons[-1] and lats[0] == lats[-1]:
        lons = lons[:-1]
        lats = lats[:-1]
    if lons.size < 3:
        if lons.size == 2:
            return 0.0, 2.0 * float(GEOD.line_length(lons, lats))
        return 0.0, 0.0
    area, perimeter = GEOD.polygon_area_perimeter(lons, lats)
    return abs(float(area)), float(perimeter)


def geodesic_area_perimeter(geom_wgs84: BaseGeometry | None) -> tuple[float, float]:
    """Geodesic area (m²) and perimeter (m) of the polygonal parts of a WGS84 geometry.

    Holes are subtracted from the area and added to the perimeter. Non-polygonal parts are
    ignored, so a geometry without polygons gives ``(0.0, 0.0)``.
    """
    total_area = 0.0
    total_perimeter = 0.0
    for part in iter_parts(geom_wgs84):
        if not isinstance(part, Polygon):
            continue
        ext_x, ext_y = part.exterior.coords.xy
        area, perimeter = ring_area_perimeter(ext_x, ext_y)
        for hole in part.interiors:
            hx, hy = hole.coords.xy
            h_area, h_perim = ring_area_perimeter(hx, hy)
            area -= h_area
            perimeter += h_perim
        total_area += max(area, 0.0)
        total_perimeter += perimeter
    return total_area, total_perimeter


def geodesic_length(geom_wgs84: BaseGeometry | None) -> float:
    """Total geodesic length (m) of the lineal parts (LineString, LinearRing) of a WGS84 geometry.

    Polygons and points contribute nothing; use :func:`geodesic_area_perimeter` for perimeters.
    """
    total = 0.0
    for part in iter_parts(geom_wgs84):
        if isinstance(part, LineString):
            xs, ys = part.coords.xy
            if len(xs) >= 2:
                total += float(GEOD.line_length(np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)))
    return total


@overload
def distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float: ...


@overload
def distance_m(lon1: ArrayLike, lat1: ArrayLike, lon2: ArrayLike, lat2: ArrayLike) -> float | np.ndarray: ...


def distance_m(lon1: ArrayLike, lat1: ArrayLike, lon2: ArrayLike, lat2: ArrayLike) -> float | np.ndarray:
    """Geodesic distance in metres. Scalars give a float; arrays broadcast and give an array."""
    a, b, c, d = np.broadcast_arrays(
        np.asarray(lon1, dtype=float),
        np.asarray(lat1, dtype=float),
        np.asarray(lon2, dtype=float),
        np.asarray(lat2, dtype=float),
    )
    if a.ndim == 0:
        _, _, dist = GEOD.inv(float(a), float(b), float(c), float(d))
        return float(dist)
    _, _, dist = GEOD.inv(a.ravel(), b.ravel(), c.ravel(), d.ravel())
    return np.asarray(dist, dtype=float).reshape(a.shape)


@overload
def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float: ...


@overload
def bearing_deg(lon1: ArrayLike, lat1: ArrayLike, lon2: ArrayLike, lat2: ArrayLike) -> float | np.ndarray: ...


def bearing_deg(lon1: ArrayLike, lat1: ArrayLike, lon2: ArrayLike, lat2: ArrayLike) -> float | np.ndarray:
    """Initial geodesic bearing from point 1 to point 2, degrees clockwise from north in [0, 360)."""
    a, b, c, d = np.broadcast_arrays(
        np.asarray(lon1, dtype=float),
        np.asarray(lat1, dtype=float),
        np.asarray(lon2, dtype=float),
        np.asarray(lat2, dtype=float),
    )
    if a.ndim == 0:
        az, _, _ = GEOD.inv(float(a), float(b), float(c), float(d))
        value = float(az) % 360.0
        return 0.0 if value >= 360.0 else value
    az, _, _ = GEOD.inv(a.ravel(), b.ravel(), c.ravel(), d.ravel())
    out = np.asarray(az, dtype=float) % 360.0
    out[out >= 360.0] = 0.0
    return out.reshape(a.shape)
