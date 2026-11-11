"""Geometry validity, repair and decomposition helpers."""

from __future__ import annotations

from collections.abc import Iterator

import shapely
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

__all__ = [
    "drop_z",
    "geometry_type_name",
    "iter_parts",
    "lineal_part",
    "polygonal_part",
    "repair",
]


def iter_parts(geom: BaseGeometry | None) -> Iterator[BaseGeometry]:
    """Yield the non-empty atomic parts (Point, LineString, LinearRing, Polygon) of a geometry,
    flattening Multi* types and (nested) GeometryCollections in order."""
    if geom is None or geom.is_empty:
        return
    if hasattr(geom, "geoms"):
        for sub in geom.geoms:  # type: ignore[attr-defined]
            yield from iter_parts(sub)
        return
    yield geom


def repair(geom: BaseGeometry | None) -> tuple[BaseGeometry | None, bool]:
    """Repair an invalid geometry with ``make_valid``.

    Returns ``(geometry, repaired)``. Valid, empty or ``None`` geometries are returned as is
    with ``repaired=False``. When a polygonal input still has a polygonal part after repair,
    only that part is kept (collapsed slivers become lines/points and are dropped).
    """
    if geom is None or geom.is_empty or geom.is_valid:
        return geom, False
    fixed = shapely.make_valid(geom)
    was_polygonal = geom.geom_type in ("Polygon", "MultiPolygon")
    if was_polygonal:
        poly = polygonal_part(fixed)
        if poly is not None:
            return poly, True
    return fixed, True


def drop_z(geom: BaseGeometry) -> BaseGeometry:
    if geom.is_empty or not geom.has_z:
        return geom
    return shapely.force_2d(geom)


def polygonal_part(geom: BaseGeometry | None) -> Polygon | MultiPolygon | None:
    """All non-empty polygons of a geometry as a Polygon or MultiPolygon (``None`` if there are none)."""
    polys = [p for p in iter_parts(geom) if isinstance(p, Polygon)]
    if not polys:
        return None
    if len(polys) == 1:
        return polys[0]
    return MultiPolygon(polys)


def lineal_part(geom: BaseGeometry | None) -> LineString | MultiLineString | None:
    """All non-empty lines (LinearRings become LineStrings) as a LineString or MultiLineString."""
    lines: list[LineString] = []
    for part in iter_parts(geom):
        if part.geom_type == "LinearRing":
            lines.append(LineString(part.coords))
        elif isinstance(part, LineString):
            lines.append(part)
    if not lines:
        return None
    if len(lines) == 1:
        return lines[0]
    return MultiLineString(lines)


def geometry_type_name(geom: BaseGeometry | None) -> str:
    """GeoJSON-style type name; ``"None"`` for a missing geometry, ``"LineString"`` for rings."""
    if geom is None:
        return "None"
    name = geom.geom_type
    return "LineString" if name == "LinearRing" else name
