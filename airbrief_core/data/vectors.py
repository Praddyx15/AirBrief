"""Vector layers of a data pack: point features (obstacles, POIs) and polygons (water).

* :class:`PointIndex` — points with properties; radius queries use geodesic distance, geometry
  queries use a local azimuthal-equidistant frame (``geo.frame.LocalFrame``).
* :class:`PolygonIndex` — polygons in a Shapely ``STRtree`` for intersection and
  point-in-polygon tests.

GeoJSON input is WGS84 ``[lon, lat]``. Non-point features given to a :class:`PointIndex` are
reduced to their representative point.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.errors import GEOSException
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from ..errors import DataPackError

log = logging.getLogger(__name__)

_M_PER_DEG_LAT = 110_540.0  # conservative lower bound of metres per degree of latitude
_M_PER_DEG_LON_EQ = 111_320.0


@dataclass
class PointFeature:
    """A point with an id and free-form properties (e.g. ``kind``, ``height_m``, ``name``)."""

    id: str
    lon: float
    lat: float
    props: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.props.get(key, default)

    def to_feature(self) -> dict[str, Any]:
        return {
            "type": "Feature",
            "id": self.id,
            "geometry": {"type": "Point", "coordinates": [round(self.lon, 8), round(self.lat, 8)]},
            "properties": dict(self.props),
        }


@dataclass
class PolygonFeature:
    """A (multi)polygon with an id and properties."""

    id: str
    geometry: BaseGeometry
    props: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.props.get(key, default)

    def to_feature(self, decimals: int | None = 8) -> dict[str, Any]:
        geom = self.geometry
        if decimals is not None:
            geom = shapely.transform(geom, lambda c: np.round(c, decimals))
        return {
            "type": "Feature",
            "id": self.id,
            "geometry": mapping(geom),
            "properties": dict(self.props),
        }


def _load_geojson(source: Path | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, dict):
        return source
    p = Path(source)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataPackError("layer_missing", f"Layer file not found: {p.name}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("Data-pack layer %s is not valid GeoJSON: %s", p, exc)
        raise DataPackError("datapack_invalid", f"The data-pack layer '{p.name}' is not valid GeoJSON.") from exc
    if not isinstance(data, dict):
        raise DataPackError("datapack_invalid", f"Layer {p.name} is not a GeoJSON object")
    return data


def _iter_features(data: dict[str, Any]) -> Iterator[tuple[str, BaseGeometry, dict[str, Any]]]:
    kind = data.get("type")
    if kind == "FeatureCollection":
        feats = data.get("features") or []
    elif kind == "Feature":
        feats = [data]
    else:
        raise DataPackError("datapack_invalid", "Expected a GeoJSON Feature or FeatureCollection")
    for i, feat in enumerate(feats):
        if not isinstance(feat, dict) or feat.get("geometry") is None:
            continue
        props = dict(feat.get("properties") or {})
        fid = feat.get("id", props.get("id", i))
        try:
            geom = shape(feat["geometry"])
        except (ValueError, TypeError, KeyError, AttributeError, GEOSException):
            continue
        if geom.is_empty:
            continue
        yield str(fid), geom, props


def _deg_window(lat: float, radius_m: float) -> tuple[float, float]:
    """Half-widths in degrees (lon, lat) that safely contain a radius around a latitude."""
    dlat = radius_m / _M_PER_DEG_LAT
    cos_lat = max(math.cos(math.radians(min(89.0, abs(lat) + dlat))), 1e-6)
    dlon = radius_m / (_M_PER_DEG_LON_EQ * cos_lat * 0.995)
    return dlon * 1.01 + 1e-9, dlat * 1.01 + 1e-9


class PointIndex:
    """Collection of :class:`PointFeature` with proximity queries."""

    def __init__(self, features: Iterable[PointFeature] = ()) -> None:
        self.features: list[PointFeature] = list(features)
        self.lons = np.fromiter((f.lon for f in self.features), dtype=np.float64, count=len(self.features))
        self.lats = np.fromiter((f.lat for f in self.features), dtype=np.float64, count=len(self.features))

    @classmethod
    def from_geojson(cls, source: Path | str | dict[str, Any]) -> PointIndex:
        """Build from a GeoJSON file or dict. Non-point geometries use their representative point."""
        items: list[PointFeature] = []
        for fid, geom, props in _iter_features(_load_geojson(source)):
            pt = geom if geom.geom_type == "Point" else geom.representative_point()
            if not (math.isfinite(pt.x) and math.isfinite(pt.y)):
                continue
            items.append(PointFeature(id=fid, lon=float(pt.x), lat=float(pt.y), props=props))
        return cls(items)

    def __len__(self) -> int:
        return len(self.features)

    def __iter__(self) -> Iterator[PointFeature]:
        return iter(self.features)

    def __getitem__(self, index: int) -> PointFeature:
        return self.features[index]

    def __bool__(self) -> bool:
        return bool(self.features)

    def _candidates(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> np.ndarray:
        mask = (self.lons >= min_lon) & (self.lons <= max_lon) & (self.lats >= min_lat) & (self.lats <= max_lat)
        return np.nonzero(mask)[0]

    def near(self, lon: float, lat: float, radius_m: float) -> list[tuple[PointFeature, float]]:
        """Features within ``radius_m`` (geodesic) of a point, as ``(feature, distance_m)``, nearest first."""
        from ..geo.geodesic import distance_m

        if not self.features or radius_m < 0:
            return []
        dlon, dlat = _deg_window(lat, radius_m)
        idx = self._candidates(lon - dlon, lat - dlat, lon + dlon, lat + dlat)
        if idx.size == 0:
            return []
        d = np.asarray(
            distance_m(np.full(idx.shape, float(lon)), np.full(idx.shape, float(lat)), self.lons[idx], self.lats[idx]),
            dtype=np.float64,
        )
        keep = d <= radius_m
        pairs = sorted(zip(idx[keep].tolist(), d[keep].tolist(), strict=True), key=lambda t: t[1])
        return [(self.features[i], float(dist)) for i, dist in pairs]

    def near_geometry(self, geom: BaseGeometry, radius_m: float) -> list[tuple[PointFeature, float]]:
        """Features within ``radius_m`` of a WGS84 geometry, as ``(feature, distance_m)``.

        Distance is 0 for points inside a polygon. Measured in a local azimuthal-equidistant
        frame centred on the geometry, which is accurate to well under 0.1 % for the
        few-tens-of-kilometre extents analyses use.
        """
        from ..geo.frame import LocalFrame

        if not self.features or geom is None or geom.is_empty or radius_m < 0:
            return []
        min_lon, min_lat, max_lon, max_lat = geom.bounds
        dlon, dlat = _deg_window(max(abs(min_lat), abs(max_lat)), radius_m)
        idx = self._candidates(min_lon - dlon, min_lat - dlat, max_lon + dlon, max_lat + dlat)
        if idx.size == 0:
            return []
        frame = LocalFrame.for_geometry(geom)
        local = frame.to_local(geom)
        xs, ys = frame.xy(self.lons[idx], self.lats[idx])
        pts = shapely.points(np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64))
        d = np.asarray(shapely.distance(local, pts), dtype=np.float64)
        keep = d <= radius_m
        pairs = sorted(zip(idx[keep].tolist(), d[keep].tolist(), strict=True), key=lambda t: t[1])
        return [(self.features[i], float(dist)) for i, dist in pairs]

    def in_polygon(self, geom: BaseGeometry) -> list[PointFeature]:
        """Features inside (or on the boundary of) a WGS84 polygon."""
        if not self.features or geom is None or geom.is_empty:
            return []
        min_lon, min_lat, max_lon, max_lat = geom.bounds
        idx = self._candidates(min_lon, min_lat, max_lon, max_lat)
        if idx.size == 0:
            return []
        pts = shapely.points(self.lons[idx], self.lats[idx])
        mask = shapely.intersects(geom, pts)
        return [self.features[int(i)] for i in idx[mask]]

    def in_bbox(self, bbox: Sequence[float]) -> list[PointFeature]:
        """Features inside ``(min_lon, min_lat, max_lon, max_lat)``."""
        idx = self._candidates(*(float(v) for v in bbox))
        return [self.features[int(i)] for i in idx]

    def to_feature_collection(self, features: Iterable[PointFeature] | None = None) -> dict[str, Any]:
        items = self.features if features is None else list(features)
        return {"type": "FeatureCollection", "features": [f.to_feature() for f in items]}


class PolygonIndex:
    """Collection of :class:`PolygonFeature` with an ``STRtree`` spatial index."""

    def __init__(self, features: Iterable[PolygonFeature] = ()) -> None:
        self.features: list[PolygonFeature] = list(features)
        self._geoms = np.empty(len(self.features), dtype=object)
        for i, f in enumerate(self.features):
            self._geoms[i] = f.geometry
        self._tree = shapely.STRtree(self._geoms) if self.features else None

    @classmethod
    def from_geojson(cls, source: Path | str | dict[str, Any]) -> PolygonIndex:
        """Build from a GeoJSON file or dict; only polygonal geometries are kept (made valid)."""
        items: list[PolygonFeature] = []
        for fid, geom, props in _iter_features(_load_geojson(source)):
            g = geom if geom.is_valid else shapely.make_valid(geom)
            if g.geom_type not in ("Polygon", "MultiPolygon"):
                parts = [p for p in shapely.get_parts(g) if p.geom_type in ("Polygon", "MultiPolygon")]
                if not parts:
                    continue
                g = shapely.union_all(parts)
            if g.is_empty:
                continue
            items.append(PolygonFeature(id=fid, geometry=g, props=props))
        return cls(items)

    def __len__(self) -> int:
        return len(self.features)

    def __iter__(self) -> Iterator[PolygonFeature]:
        return iter(self.features)

    def __getitem__(self, index: int) -> PolygonFeature:
        return self.features[index]

    def __bool__(self) -> bool:
        return bool(self.features)

    def intersecting(self, geom: BaseGeometry) -> list[PolygonFeature]:
        """Polygons intersecting a WGS84 geometry, in index order."""
        if self._tree is None or geom is None or geom.is_empty:
            return []
        idx = np.sort(np.asarray(self._tree.query(geom, predicate="intersects"), dtype=np.int64))
        return [self.features[int(i)] for i in idx]

    def contains_xy(self, lons: Sequence[float] | np.ndarray | float, lats: Sequence[float] | np.ndarray | float) -> np.ndarray:
        """Boolean array: is each point inside (or on the edge of) any polygon. Shape follows the inputs."""
        x, y = np.broadcast_arrays(np.asarray(lons, dtype=np.float64), np.asarray(lats, dtype=np.float64))
        shape_ = x.shape
        out = np.zeros(x.size, dtype=bool)
        if self._tree is None or x.size == 0:
            return out.reshape(shape_)
        finite = np.isfinite(x.ravel()) & np.isfinite(y.ravel())
        if not finite.any():
            return out.reshape(shape_)
        sel = np.nonzero(finite)[0]
        pts = shapely.points(x.ravel()[sel], y.ravel()[sel])
        pairs = self._tree.query(pts, predicate="intersects")
        if pairs.size:
            out[sel[np.unique(pairs[0])]] = True
        return out.reshape(shape_)

    def to_feature_collection(self, features: Iterable[PolygonFeature] | None = None) -> dict[str, Any]:
        items = self.features if features is None else list(features)
        return {"type": "FeatureCollection", "features": [f.to_feature() for f in items]}


__all__ = ["PointFeature", "PointIndex", "PolygonFeature", "PolygonIndex"]
