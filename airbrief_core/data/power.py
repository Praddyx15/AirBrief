"""Power infrastructure layer of a data pack (``power.geojson``): lines, cables, substations, plants.

Features carry ``kind`` (``line`` | ``minor_line`` | ``cable`` | ``substation`` | ``plant``),
``voltage_kv`` (largest voltage, kV, or null), the raw OSM ``voltage`` tag, ``name`` and
``operator``. Lines are LineStrings/MultiLineStrings; substations and plants are polygons or points.

:class:`PowerIndex` answers "which power features lie within *r* metres of this geometry, and how
far away is each one". Distances are measured in a local azimuthal-equidistant frame centred on
the query geometry (:class:`~airbrief_core.geo.frame.LocalFrame`), accurate to well under 0.1 %
at the few-kilometre radii used for interference-proximity evidence.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from .vectors import _deg_window, _iter_features, _load_geojson

__all__ = [
    "POWER_KINDS",
    "POWER_LINE_KINDS",
    "POWER_SITE_KINDS",
    "PowerFeature",
    "PowerHit",
    "PowerIndex",
    "parse_voltage_kv",
]

POWER_LINE_KINDS: tuple[str, ...] = ("line", "minor_line", "cable")
"""OSM ``power=*`` values stored as lines."""
POWER_SITE_KINDS: tuple[str, ...] = ("substation", "plant")
"""OSM ``power=*`` values stored as sites (polygons or points)."""
POWER_KINDS: tuple[str, ...] = POWER_LINE_KINDS + POWER_SITE_KINDS

_VOLTAGE_PART = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(kv|v)?\s*$", re.IGNORECASE)
_THOUSANDS = re.compile(r"^\s*\d{1,3}(?:,\d{3})+\s*(?:kv|v)?\s*$", re.IGNORECASE)
"""``"132,000"``: a thousands separator, not a decimal comma (which ``"0,4 kV"`` is)."""


def parse_voltage_kv(value: Any) -> float | None:
    """Largest voltage in an OSM ``voltage`` tag, in kV.

    The OSM convention is volts, several circuits separated by ``;`` (``"132000;33000"`` →
    132.0). A ``kV``/``V`` suffix is honoured (``"33 kV"`` → 33.0), ``"132,000"`` is read with a
    thousands separator. Unparseable, below 1 V or implausible (> 2000 kV) values give None.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        volts = float(value)
        return round(volts / 1000.0, 3) if math.isfinite(volts) and 1.0 <= volts <= 2_000_000.0 else None
    best: float | None = None
    for part in re.split(r"[;/|]", str(value)):
        if _THOUSANDS.match(part):
            part = part.replace(",", "")
        match = _VOLTAGE_PART.match(part)
        if not match:
            continue
        number = float(match.group(1).replace(",", "."))
        unit = (match.group(2) or "v").lower()
        kv = number if unit == "kv" else number / 1000.0
        if 0.001 <= kv <= 2000.0 and (best is None or kv > best):
            best = kv
    return round(best, 3) if best is not None else None


@dataclass
class PowerFeature:
    """One power feature (WGS84 geometry) with its properties."""

    id: str
    kind: str
    geometry: BaseGeometry
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str | None:
        value = self.props.get("name")
        return str(value) if value not in (None, "") else None

    @property
    def voltage_kv(self) -> float | None:
        value = self.props.get("voltage_kv")
        if isinstance(value, int | float) and math.isfinite(float(value)) and value > 0:
            return float(value)
        return parse_voltage_kv(self.props.get("voltage"))

    def to_feature(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        geom = shapely.transform(self.geometry, lambda c: np.round(c, 8))
        props = {**self.props, "kind": self.kind, "voltage_kv": self.voltage_kv, **(extra or {})}
        return {"type": "Feature", "id": self.id, "geometry": mapping(geom), "properties": props}


@dataclass(frozen=True)
class PowerHit:
    """A power feature near a query geometry."""

    feature: PowerFeature
    distance_m: float
    nearest_lon: float
    """Point of the feature nearest to the query geometry (WGS84)."""
    nearest_lat: float


class PowerIndex:
    """Power features in a Shapely ``STRtree``."""

    def __init__(self, features: list[PowerFeature] | None = None) -> None:
        self.features: list[PowerFeature] = list(features or [])
        geoms = np.empty(len(self.features), dtype=object)
        for i, f in enumerate(self.features):
            geoms[i] = f.geometry
        self._tree = shapely.STRtree(geoms) if self.features else None

    @classmethod
    def from_geojson(cls, source: Path | str | dict[str, Any]) -> PowerIndex:
        """Build from ``power.geojson``. Features of an unknown ``kind`` are skipped."""
        items: list[PowerFeature] = []
        for fid, geom, props in _iter_features(_load_geojson(source)):
            kind = str(props.get("kind") or props.get("power") or "").lower()
            if kind not in POWER_KINDS:
                continue
            g = geom if geom.is_valid else shapely.make_valid(geom)
            if g.is_empty:
                continue
            items.append(PowerFeature(id=fid, kind=kind, geometry=g, props=props))
        return cls(items)

    def __len__(self) -> int:
        return len(self.features)

    def __iter__(self) -> Iterator[PowerFeature]:
        return iter(self.features)

    def __bool__(self) -> bool:
        return bool(self.features)

    def near_geometry(self, geom: BaseGeometry, radius_m: float) -> list[PowerHit]:
        """Features within ``radius_m`` of a WGS84 geometry, nearest first.

        Distance is 0 when a feature touches, crosses or lies inside the geometry.
        """
        from ..geo.frame import LocalFrame

        if self._tree is None or geom is None or geom.is_empty or radius_m < 0:
            return []
        min_lon, min_lat, max_lon, max_lat = geom.bounds
        dlon, dlat = _deg_window(max(abs(min_lat), abs(max_lat)), radius_m)
        window = box(min_lon - dlon, min_lat - dlat, max_lon + dlon, max_lat + dlat)
        idx = np.sort(np.asarray(self._tree.query(window, predicate="intersects"), dtype=np.int64))
        if idx.size == 0:
            return []
        frame = LocalFrame.for_geometry(geom)
        local = frame.to_local(geom)
        hits: list[PowerHit] = []
        for i in idx.tolist():
            feature = self.features[int(i)]
            other = frame.to_local(feature.geometry)
            distance = float(shapely.distance(local, other))
            if not math.isfinite(distance) or distance > radius_m:
                continue
            _, on_feature = nearest_points(local, other)
            lon, lat = frame.lonlat([on_feature.x], [on_feature.y])
            hits.append(PowerHit(
                feature=feature,
                distance_m=distance,
                nearest_lon=float(np.asarray(lon)[0]),
                nearest_lat=float(np.asarray(lat)[0]),
            ))
        hits.sort(key=lambda h: (h.distance_m, h.feature.id))
        return hits

    def to_feature_collection(self) -> dict[str, Any]:
        return {"type": "FeatureCollection", "features": [f.to_feature() for f in self.features]}
