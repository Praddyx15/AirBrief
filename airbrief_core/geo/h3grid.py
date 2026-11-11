"""H3 hexagonal grid helpers (h3-py v4 API).

H3 takes ``(lat, lng)`` order; everything here takes and returns lon/lat (GeoJSON order).
"""

from __future__ import annotations

import math
from typing import Any

import h3
import numpy as np
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from airbrief_core.errors import AnalysisError

from .geodesic import distance_m, geodesic_area_perimeter
from .transform import geometry_to_geojson, unwrap_antimeridian
from .validity import polygonal_part

__all__ = [
    "cell_area_m2",
    "cell_centroids",
    "cell_polygon",
    "cells_for_polygon",
    "cells_to_feature_collection",
    "neighbours",
    "ring_radius_k",
]

MAX_RES = 15


def _wrap_lon(lon: float) -> float:
    return ((float(lon) + 180.0) % 360.0) - 180.0


def _ring_latlng(coords: Any) -> list[tuple[float, float]]:
    pts = [(float(c[1]), _wrap_lon(c[0])) for c in coords]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def _to_h3_poly(poly: Polygon) -> h3.LatLngPoly | None:
    outer = _ring_latlng(poly.exterior.coords)
    if len(outer) < 3:
        return None
    holes = [ring for ring in (_ring_latlng(r.coords) for r in poly.interiors) if len(ring) >= 3]
    return h3.LatLngPoly(outer, *holes)


def _cells_at(polys: list[Polygon], res: int) -> set[str]:
    cells: set[str] = set()
    for poly in polys:
        shape = _to_h3_poly(poly)
        if shape is not None:
            cells.update(h3.polygon_to_cells(shape, res))
    return cells


def cells_for_polygon(geom_wgs84: BaseGeometry, res: int, max_cells: int) -> tuple[list[str], bool]:
    """H3 cells whose centres fall inside the polygonal part of a WGS84 geometry.

    ``max_cells`` is enforced before generating: if the estimated count
    (geodesic area / average hexagon area) exceeds it, the resolution is coarsened until it
    fits; if the generated list still exceeds it, the cells nearest the polygon centroid are
    kept. In both cases ``truncated=True`` (and the cells may be at a coarser resolution than
    requested; use ``h3.get_resolution``). A polygon smaller than one cell yields the cell
    containing its representative point. Cells are returned sorted.

    Raises:
        AnalysisError: ``invalid_params`` for a bad resolution or ``max_cells``;
            ``geometry_type_not_supported`` when the geometry has no polygonal part.
    """
    if not 0 <= int(res) <= MAX_RES:
        raise AnalysisError("invalid_params", "H3 resolution must be between 0 and 15", details={"res": res})
    if int(max_cells) < 1:
        raise AnalysisError("invalid_params", "max_cells must be at least 1", details={"max_cells": max_cells})
    res = int(res)
    max_cells = int(max_cells)
    if geom_wgs84 is None or geom_wgs84.is_empty:
        return [], False
    poly_geom = polygonal_part(geom_wgs84)
    if poly_geom is None:
        raise AnalysisError(
            "geometry_type_not_supported",
            "An area (polygon) is required to build an H3 grid",
            details={"geometry_type": geom_wgs84.geom_type},
        )
    polys = [poly_geom] if isinstance(poly_geom, Polygon) else list(poly_geom.geoms)

    truncated = False
    area_m2, _ = geodesic_area_perimeter(poly_geom)
    while res > 0 and area_m2 / h3.average_hexagon_area(res, unit="m^2") > max_cells:
        res -= 1
        truncated = True

    cells = _cells_at(polys, res)
    if not cells:
        rep = unwrap_antimeridian(poly_geom).representative_point()
        cells = {h3.latlng_to_cell(float(rep.y), _wrap_lon(rep.x), res)}

    ordered = sorted(cells)
    if len(ordered) > max_cells:
        centre = unwrap_antimeridian(poly_geom).centroid
        lons, lats = cell_centroids(ordered)
        dist = np.asarray(distance_m(np.full(lons.shape, centre.x), np.full(lats.shape, centre.y), lons, lats))
        keep = np.argsort(dist, kind="stable")[:max_cells]
        ordered = sorted(ordered[i] for i in keep)
        truncated = True
    return ordered, truncated


def cell_centroids(cells: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """Cell centre longitudes and latitudes as float arrays (same order as ``cells``)."""
    lons = np.empty(len(cells), dtype=float)
    lats = np.empty(len(cells), dtype=float)
    for i, cell in enumerate(cells):
        lat, lng = h3.cell_to_latlng(cell)
        lons[i] = lng
        lats[i] = lat
    return lons, lats


def cell_polygon(cell: str) -> Polygon:
    """Cell boundary as a lon/lat Polygon (unwrapped across the antimeridian if needed)."""
    boundary = h3.cell_to_boundary(cell)
    ring = [(float(lng), float(lat)) for lat, lng in boundary]
    lons = [p[0] for p in ring]
    if max(lons) - min(lons) > 180.0:
        ring = [(lng + 360.0 if lng < 0.0 else lng, lat) for lng, lat in ring]
    return Polygon(ring)


def cells_to_feature_collection(cells: list[str], props: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """GeoJSON FeatureCollection of cell polygons; each feature has ``id`` and ``properties.h3``
    set to the cell index, merged with ``props[cell]`` when present."""
    features: list[dict[str, Any]] = []
    for cell in cells:
        properties: dict[str, Any] = {"h3": cell}
        properties.update(props.get(cell, {}))
        features.append(
            {
                "type": "Feature",
                "id": cell,
                "geometry": geometry_to_geojson(cell_polygon(cell), 8),
                "properties": properties,
            }
        )
    return {"type": "FeatureCollection", "features": features}


def cell_area_m2(cell: str) -> float:
    """Exact area of one cell in square metres."""
    return float(h3.cell_area(cell, unit="m^2"))


def ring_radius_k(res: int, radius_m: float) -> int:
    """Smallest grid-disk ``k`` whose rings reach ``radius_m`` from a cell centre.

    Adjacent cell centres are about ``sqrt(3) * average edge length`` apart.
    """
    if not 0 <= int(res) <= MAX_RES:
        raise AnalysisError("invalid_params", "H3 resolution must be between 0 and 15", details={"res": res})
    if not math.isfinite(radius_m) or radius_m <= 0.0:
        return 0
    spacing = math.sqrt(3.0) * float(h3.average_hexagon_edge_length(int(res), unit="m"))
    return max(0, int(math.ceil(radius_m / spacing)))


def neighbours(cell: str) -> list[str]:
    """The cells sharing an edge with ``cell`` (6, or 5 for pentagons), sorted."""
    return sorted(c for c in h3.grid_disk(cell, 1) if c != cell)
