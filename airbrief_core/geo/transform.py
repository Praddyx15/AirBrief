"""Geometry coordinate transforms and GeoJSON conversion.

* Transforms always use ``always_xy=True`` (lon/lat, easting/northing order) and are cached.
* Only the horizontal coordinates are transformed; Z values pass through unchanged.
* :func:`geometry_to_geojson` emits plain nested lists (never tuples) and keeps Z when present.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from airbrief_core.errors import AnalysisError

from .crs import WGS84, horizontal_crs

__all__ = [
    "apply_xy",
    "geojson_to_geometry",
    "geometry_to_geojson",
    "get_transformer",
    "to_wgs84",
    "transform_geom",
    "unwrap_antimeridian",
]


@lru_cache(maxsize=512)
def _transformer_for(src_wkt: str, dst_wkt: str) -> Transformer:
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_wkt(dst_wkt), always_xy=True)


@lru_cache(maxsize=2048)
def _wkt(crs: CRS) -> str:
    return horizontal_crs(crs).to_wkt()


def get_transformer(src: CRS, dst: CRS) -> Transformer:
    """Cached ``always_xy`` transformer between the horizontal parts of two CRSs."""
    return _transformer_for(_wkt(src), _wkt(dst))


def apply_xy(geom: BaseGeometry, func: Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]) -> BaseGeometry:
    """Apply ``func(xs, ys) -> (xs, ys)`` to every vertex, keeping Z values untouched."""
    if geom.is_empty:
        return geom
    has_z = bool(geom.has_z)

    def _mapper(coords: np.ndarray) -> np.ndarray:
        out = np.array(coords, dtype=float, copy=True)
        if out.shape[0] == 0:
            return out
        xs, ys = func(out[:, 0], out[:, 1])
        out[:, 0] = np.asarray(xs, dtype=float)
        out[:, 1] = np.asarray(ys, dtype=float)
        return out

    return shapely.transform(geom, _mapper, include_z=has_z)


def transform_geom(geom: BaseGeometry, src: CRS, dst: CRS) -> BaseGeometry:
    """Transform a geometry's x/y from ``src`` to ``dst`` (always_xy). Z passes through.

    Raises:
        AnalysisError: ``invalid_geometry`` if finite input coordinates become non-finite
            (outside the projection's domain).
    """
    if geom.is_empty:
        return geom
    if _wkt(src) == _wkt(dst):
        return geom
    transformer = get_transformer(src, dst)

    def _xy(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        tx, ty = transformer.transform(xs, ys)
        tx = np.asarray(tx, dtype=float)
        ty = np.asarray(ty, dtype=float)
        finite_in = np.isfinite(xs) & np.isfinite(ys)
        finite_out = np.isfinite(tx) & np.isfinite(ty)
        if np.any(finite_in & ~finite_out):
            raise AnalysisError(
                "invalid_geometry",
                "Some coordinates could not be transformed between coordinate systems",
                details={"src": src.name, "dst": dst.name},
            )
        return tx, ty

    return apply_xy(geom, _xy)


def to_wgs84(geom: BaseGeometry, src: CRS) -> BaseGeometry:
    """Transform a geometry to WGS84 lon/lat (Z passes through)."""
    return transform_geom(geom, src, WGS84)


def unwrap_antimeridian(geom: BaseGeometry) -> BaseGeometry:
    """Make a lon/lat geometry that crosses the antimeridian continuous.

    If shifting negative longitudes by +360 reduces the geometry's longitude span, it is
    done (results may then hold longitudes in ``(180, 360)``). Otherwise the geometry is
    returned unchanged.
    """
    if geom.is_empty:
        return geom
    coords = shapely.get_coordinates(geom)
    lons = coords[:, 0]
    lons = lons[np.isfinite(lons)]
    if lons.size == 0:
        return geom
    span = float(lons.max() - lons.min())
    if span <= 180.0:
        return geom
    shifted = np.where(lons < 0.0, lons + 360.0, lons)
    if float(shifted.max() - shifted.min()) >= span:
        return geom

    def _xy(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return np.where(xs < 0.0, xs + 360.0, xs), ys

    return apply_xy(geom, _xy)


def _coord(values: Any, decimals: int | None) -> list[float]:
    out: list[float] = []
    for i, v in enumerate(values):
        fv = float(v)
        if i >= 2 and not math.isfinite(fv):
            break
        if decimals is not None:
            fv = round(fv, decimals)
            if fv == 0.0:
                fv = 0.0
        out.append(fv)
    return out


def _coords(seq: Any, decimals: int | None) -> list[list[float]]:
    return [_coord(c, decimals) for c in seq]


def geometry_to_geojson(geom: BaseGeometry, decimals: int | None = 8) -> dict[str, Any]:
    """Convert a Shapely geometry to a GeoJSON geometry dict with list coordinates.

    ``decimals`` rounds every ordinate (``None`` keeps full precision). Z is preserved when
    present; NaN Z values (mixed-dimension collections) are dropped for that vertex.
    """
    gtype = geom.geom_type
    if gtype == "GeometryCollection":
        return {
            "type": "GeometryCollection",
            "geometries": [geometry_to_geojson(g, decimals) for g in geom.geoms],  # type: ignore[attr-defined]
        }
    if geom.is_empty:
        geojson_type = "LineString" if gtype == "LinearRing" else gtype
        return {"type": geojson_type, "coordinates": []}
    if gtype == "Point":
        return {"type": "Point", "coordinates": _coord(geom.coords[0], decimals)}
    if gtype in ("LineString", "LinearRing"):
        return {"type": "LineString", "coordinates": _coords(geom.coords, decimals)}
    if gtype == "Polygon":
        return {"type": "Polygon", "coordinates": _polygon_coords(geom, decimals)}
    if gtype == "MultiPoint":
        return {
            "type": "MultiPoint",
            "coordinates": [_coord(p.coords[0], decimals) for p in geom.geoms if not p.is_empty],  # type: ignore[attr-defined]
        }
    if gtype == "MultiLineString":
        return {
            "type": "MultiLineString",
            "coordinates": [_coords(ls.coords, decimals) for ls in geom.geoms if not ls.is_empty],  # type: ignore[attr-defined]
        }
    if gtype == "MultiPolygon":
        return {
            "type": "MultiPolygon",
            "coordinates": [_polygon_coords(p, decimals) for p in geom.geoms if not p.is_empty],  # type: ignore[attr-defined]
        }
    raise AnalysisError("geometry_type_not_supported", f"Geometry type {gtype} cannot be converted to GeoJSON")


def _polygon_coords(poly: Any, decimals: int | None) -> list[list[list[float]]]:
    rings = [_coords(poly.exterior.coords, decimals)]
    rings.extend(_coords(r.coords, decimals) for r in poly.interiors)
    return rings


_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}


def geojson_to_geometry(obj: dict[str, Any]) -> BaseGeometry:
    """Parse a GeoJSON Geometry, Feature or FeatureCollection into a Shapely geometry.

    A FeatureCollection becomes a GeometryCollection of its non-null feature geometries.

    Raises:
        AnalysisError: ``invalid_geometry`` for anything that is not valid GeoJSON geometry.
    """
    if not isinstance(obj, dict):
        raise AnalysisError("invalid_geometry", "Geometry must be a GeoJSON object")
    gtype = obj.get("type")
    if gtype == "Feature":
        inner = obj.get("geometry")
        if inner is None:
            raise AnalysisError("invalid_geometry", "Feature has no geometry")
        return geojson_to_geometry(inner)
    if gtype == "FeatureCollection":
        features = obj.get("features")
        if not isinstance(features, list):
            raise AnalysisError("invalid_geometry", "FeatureCollection has no 'features' list")
        parts = [
            geojson_to_geometry(f["geometry"])
            for f in features
            if isinstance(f, dict) and f.get("geometry") is not None
        ]
        if not parts:
            raise AnalysisError("invalid_geometry", "FeatureCollection has no geometries")
        return shapely.GeometryCollection(parts)
    if gtype not in _GEOMETRY_TYPES:
        raise AnalysisError("invalid_geometry", f"Unsupported GeoJSON type: {gtype!r}")
    try:
        geom = shape(obj)
    except Exception as exc:  # shapely raises several exception types for malformed input
        raise AnalysisError("invalid_geometry", f"Invalid {gtype} geometry: {exc}") from exc
    if not geom.is_empty:
        coords = shapely.get_coordinates(geom)
        if not np.all(np.isfinite(coords)):
            raise AnalysisError("invalid_geometry", "Geometry contains non-finite coordinates")
    return geom
