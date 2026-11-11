"""Aerodromes from an OurAirports-format CSV, with nearest/within queries (geodesic distances).

Required columns: ``ident, type, name, latitude_deg, longitude_deg``. Optional: ``elevation_ft``,
``iso_country``, ``municipality``, ``iata_code``, ``scheduled_service``. ``type`` keeps the
OurAirports vocabulary (``large_airport``, ``medium_airport``, ``small_airport``, ``heliport``,
``seaplane_base``, ``balloonport``); ``closed`` rows are dropped.

Data packs may also carry aerodrome boundaries (``aerodrome_perimeters.geojson``, polygons with an
``ident``) and runways (``runways.geojson``, lines from one runway end to the other with
``airport_ident`` and ``width_m``), read by :func:`read_perimeters` and :func:`read_runways`.
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from ..errors import DataPackError
from .vectors import _iter_features, _load_geojson

FT_TO_M = 0.3048
OURAIRPORTS_COLUMNS = (
    "ident",
    "type",
    "name",
    "latitude_deg",
    "longitude_deg",
    "elevation_ft",
    "iso_country",
    "municipality",
    "iata_code",
    "scheduled_service",
)
"""Columns written to data-pack ``aerodromes.csv`` files (a subset of OurAirports)."""

ACTIVE_TYPES = frozenset(
    {"large_airport", "medium_airport", "small_airport", "heliport", "seaplane_base", "balloonport"}
)


@dataclass(frozen=True)
class Aerodrome:
    """One aerodrome reference point."""

    ident: str
    name: str
    type: str
    lon: float
    lat: float
    elevation_m: float | None = None
    municipality: str | None = None
    iso_country: str | None = None
    #: IATA code (``iata_code`` column), e.g. "BLR"; None when the aerodrome has none.
    iata: str | None = None
    #: OurAirports ``scheduled_service`` (yes/no); None when the column is missing or empty.
    scheduled_service: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        out = float(text)
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def _yes_no(value: str | None) -> bool | None:
    text = (value or "").strip().lower()
    return True if text == "yes" else False if text == "no" else None


class AerodromeIndex(list[Aerodrome]):
    """A list of :class:`Aerodrome` with geodesic proximity queries."""

    @classmethod
    def from_csv(cls, path: Path | str, *, types: Iterable[str] | None = None) -> AerodromeIndex:
        """Read an OurAirports-format CSV file."""
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8-sig")
        except FileNotFoundError as exc:
            raise DataPackError("layer_missing", f"Aerodrome file not found: {p.name}") from exc
        return cls.from_csv_text(text, types=types)

    @classmethod
    def from_csv_text(cls, text: str, *, types: Iterable[str] | None = None) -> AerodromeIndex:
        """Parse OurAirports CSV text. Rows without valid coordinates are skipped."""
        wanted = frozenset(types) if types is not None else ACTIVE_TYPES
        reader = csv.DictReader(io.StringIO(text))
        fields = set(reader.fieldnames or [])
        required = {"ident", "type", "name", "latitude_deg", "longitude_deg"}
        if not required.issubset(fields):
            raise DataPackError(
                "datapack_invalid",
                "Aerodrome CSV lacks required OurAirports columns",
                details={"missing": sorted(required - fields)},
            )
        items: list[Aerodrome] = []
        for row in reader:
            kind = (row.get("type") or "").strip()
            if kind not in wanted:
                continue
            lat = _float_or_none(row.get("latitude_deg"))
            lon = _float_or_none(row.get("longitude_deg"))
            if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            elev_ft = _float_or_none(row.get("elevation_ft"))
            items.append(
                Aerodrome(
                    ident=(row.get("ident") or "").strip(),
                    name=(row.get("name") or "").strip(),
                    type=kind,
                    lon=lon,
                    lat=lat,
                    elevation_m=round(elev_ft * FT_TO_M, 1) if elev_ft is not None else None,
                    municipality=(row.get("municipality") or "").strip() or None,
                    iso_country=(row.get("iso_country") or "").strip() or None,
                    iata=(row.get("iata_code") or "").strip().upper() or None,
                    scheduled_service=_yes_no(row.get("scheduled_service")),
                )
            )
        return cls(items)

    def _distances_km(self, lon: float, lat: float) -> np.ndarray:
        from ..geo.geodesic import distance_m

        if not self:
            return np.empty(0)
        lons = np.fromiter((a.lon for a in self), dtype=np.float64, count=len(self))
        lats = np.fromiter((a.lat for a in self), dtype=np.float64, count=len(self))
        d = distance_m(np.full(lons.shape, float(lon)), np.full(lats.shape, float(lat)), lons, lats)
        return np.asarray(d, dtype=np.float64) / 1000.0

    def nearest(self, lon: float, lat: float, k: int = 1, *, types: Iterable[str] | None = None) -> list[tuple[Aerodrome, float]]:
        """The ``k`` closest aerodromes as ``(aerodrome, distance_km)``, nearest first."""
        if k <= 0 or not self:
            return []
        dist = self._distances_km(lon, lat)
        allowed = frozenset(types) if types is not None else None
        order = np.argsort(dist, kind="stable")
        out: list[tuple[Aerodrome, float]] = []
        for i in order:
            a = self[int(i)]
            if allowed is not None and a.type not in allowed:
                continue
            out.append((a, float(dist[i])))
            if len(out) >= k:
                break
        return out

    def within(self, lon: float, lat: float, radius_km: float, *, types: Iterable[str] | None = None) -> list[tuple[Aerodrome, float]]:
        """All aerodromes within ``radius_km`` as ``(aerodrome, distance_km)``, nearest first."""
        if not self:
            return []
        dist = self._distances_km(lon, lat)
        allowed = frozenset(types) if types is not None else None
        order = np.argsort(dist, kind="stable")
        return [
            (self[int(i)], float(dist[i]))
            for i in order
            if dist[i] <= radius_km and (allowed is None or self[int(i)].type in allowed)
        ]

    def by_ident(self, ident: str) -> Aerodrome | None:
        """Look up an aerodrome by its ICAO/OurAirports ident (case-insensitive)."""
        key = ident.strip().upper()
        for a in self:
            if a.ident.upper() == key:
                return a
        return None

    def of_types(self, types: Iterable[str]) -> AerodromeIndex:
        """A new index with only the given OurAirports types."""
        wanted = frozenset(types)
        return AerodromeIndex(a for a in self if a.type in wanted)

    def to_feature_collection(self) -> dict[str, Any]:
        """GeoJSON FeatureCollection of the reference points."""
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": a.ident,
                    "geometry": {"type": "Point", "coordinates": [round(a.lon, 8), round(a.lat, 8)]},
                    "properties": {k: v for k, v in a.to_dict().items() if k not in ("lon", "lat")},
                }
                for a in self
            ],
        }


def write_aerodromes_csv(path: Path, aerodromes: Iterable[dict[str, Any]]) -> int:
    """Write rows (dicts keyed by :data:`OURAIRPORTS_COLUMNS`) to an OurAirports-format CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OURAIRPORTS_COLUMNS), extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in aerodromes:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in OURAIRPORTS_COLUMNS})
            count += 1
    return count


@dataclass(frozen=True)
class Runway:
    """One runway: a line between its two ends (lon/lat) and its width in metres when known."""

    airport_ident: str
    line: LineString
    width_m: float | None = None


def read_perimeters(path: Path) -> dict[str, BaseGeometry]:
    """Aerodrome boundary polygons keyed by the upper-case ``ident`` property (first one wins)."""
    out: dict[str, BaseGeometry] = {}
    for _fid, geom, props in _iter_features(_load_geojson(path)):
        ident = str(props.get("ident") or "").strip().upper()
        if not ident or ident in out or geom.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        out[ident] = geom if geom.is_valid else shapely.make_valid(geom)
    return out


def read_runways(path: Path) -> dict[str, list[Runway]]:
    """Runways keyed by the upper-case ``airport_ident`` property."""
    out: dict[str, list[Runway]] = {}
    for _fid, geom, props in _iter_features(_load_geojson(path)):
        ident = str(props.get("airport_ident") or "").strip().upper()
        if not ident or not isinstance(geom, LineString) or geom.length == 0:
            continue
        width = _float_or_none(str(props.get("width_m")) if props.get("width_m") is not None else None)
        out.setdefault(ident, []).append(Runway(ident, geom, width if width and width > 0 else None))
    return out


__all__ = [
    "ACTIVE_TYPES",
    "OURAIRPORTS_COLUMNS",
    "Aerodrome",
    "AerodromeIndex",
    "Runway",
    "read_perimeters",
    "read_runways",
    "write_aerodromes_csv",
]
