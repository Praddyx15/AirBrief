"""Airspace zone classification: aerodrome bands, user zones and data-pack zones.

Rules (docs/SPEC.md §3 "Zones", docs/features/zones.md):

* **Aerodrome bands.** Each *operational* aerodrome whose OurAirports ``type`` is listed in an
  :class:`~airbrief_core.models.common.AerodromeZoneRule` gets that rule's rings. A point is in a
  ring when ``inner_km <= distance < outer_km``. With ``ZoneRules.distance_from = "perimeter"``
  the distance is to the aerodrome's outline, measured in a local azimuthal-equidistant frame:
  its mapped boundary (pack layer ``aerodrome_perimeters``), else the convex hull of its runway
  ends buffered by half the runway width (``runways``), else the reference point minus
  ``assumed_radius_km`` (then ``distance_from = "reference_point"``: the analysis notes
  ``perimeter_approximated``). With ``"reference_point"`` it is always the last one. The ring
  polygons are ``outline.buffer(outer) − outline.buffer(inner)``: stadium-shaped around a runway.
* **Ring states.** No ceiling, a red ring or ``verify_official_map`` require permission. A ring
  with ``ceiling_exceeded_zone`` is its zone up to the ceiling and that zone (permission) above,
  judged at the planned height above ground.
* **Operational.** ``operational`` overrides by ident; otherwise every aerodrome of a ruled type
  counts, except that ``scheduled_service_only`` rules need scheduled service (unknown counts).
* **User and data-pack zones** apply by polygon containment (the boundary counts as inside),
  only when active in the planned window (``valid_from``/``valid_to`` and daily ``schedule``,
  half-open intervals) and when their vertical band reaches the flight, which may be anywhere
  from the ground to the planned height. AMSL limits are turned into heights above ground with
  the DEM at the point; WGS84 (ellipsoid) limits likewise, widened by the range of the geoid
  (EGM96 −106.2 to +85.4 m) since no geoid model is shipped. Unknown ground or height: the zone
  applies. Red zones are prohibited; a yellow zone without a ceiling requires permission; a
  green zone without a ceiling uses ``rules.default_ceiling_agl_m``.
* **Outside every zone** the point gets ``rules.default_zone`` up to ``default_ceiling_agl_m``,
  and ``default_ceiling_exceeded_zone`` above it.
* **Overlaps:** the most restrictive state wins — red, then permission, then the lowest
  ceiling, then green (see :func:`most_restrictive`).
"""

from __future__ import annotations

import math
import threading
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal

import numpy as np
import shapely
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiPoint, Point
from shapely.geometry.base import BaseGeometry
from shapely.strtree import STRtree

from ..data.aerodromes import Aerodrome
from ..data.datapack import DataPack
from ..errors import AnalysisError
from ..geo.frame import LocalFrame
from ..geo.geodesic import bearing_deg, distance_m, geodesic_area_perimeter, geodesic_length
from ..geo.transform import geojson_to_geometry, geometry_to_geojson
from ..geo.validity import iter_parts, lineal_part, polygonal_part
from ..io.geozones import schedule_intervals
from ..models.common import (
    AerodromeZoneRule,
    BBox,
    DistanceFrom,
    NearestAerodrome,
    UserZone,
    ZoneBasis,
    ZoneHit,
    ZoneKind,
    ZoneRing,
    ZoneRules,
)
from ..models.zones import BandEvidence

__all__ = [
    "TimeWindow",
    "ZoneState",
    "band_evidence",
    "classify_points",
    "default_state",
    "has_unverified_band",
    "hit_key",
    "is_operational",
    "most_restrictive",
    "nearest_aerodromes",
    "ring_geometry",
    "ring_polygons",
    "state_key",
    "touched_polygon_zones",
    "unverified_bands",
    "zone_active",
    "zone_hits_by_activity",
    "zones_for_geometry",
]

RING_QUAD_SEGS = 32
"""Quarter-circle segments of ring polygons (128 vertices per circle; < 0.04 % radial error)."""

_REACH_MARGIN = 1.05
"""Safety factor on the aerodrome pre-filter (bbox clamping is approximate in lon/lat)."""

DEFAULT_RUNWAY_WIDTH_M = 45.0
"""Width assumed for a runway without one in the data (a typical code 4 runway)."""

_TOL = 1e-6
_GEOID_N = (-106.2, 85.4)  # EGM96 undulation range, m

TimeWindow = tuple[datetime, datetime]
"""A planned window in UTC, ``[start, end)``."""


@dataclass(frozen=True)
class ZoneState:
    """The zone that applies at one location.

    ``ceiling_agl_m`` is the free-flight ceiling (``None`` when no free flight is possible:
    red, or permission required). ``source`` tells where the state came from.
    ``rule_ceiling_agl_m`` keeps a band's ceiling when the planned height is above it.
    ``ceiling_exceeded_zone`` is the zone above ``ceiling_agl_m`` of a band (or the default
    rules) that changes zone there, e.g. yellow above 60 m in the DGCA 8–12 km band.
    """

    zone: ZoneKind
    ceiling_agl_m: float | None
    permission_required: bool
    label: str
    source: Literal["aerodrome", "user", "datapack", "default"]
    name: str
    ident: str | None = None
    basis: ZoneBasis = "rule"
    rule_ceiling_agl_m: float | None = None
    distance_from: DistanceFrom | None = None
    ceiling_exceeded_zone: ZoneKind | None = None


def _key(zone: str, ceiling: float | None, permission: bool) -> tuple[int, float, int]:
    if zone == "red":
        tier = 0
    elif permission:
        tier = 1
    else:
        tier = 2
    ceiling_value = float(ceiling) if ceiling is not None else math.inf
    return tier, ceiling_value, 0 if zone == "yellow" else 1


def state_key(state: ZoneState) -> tuple[int, float, int]:
    """Sort key: smaller is more restrictive (red < permission < lower ceiling < green)."""
    return _key(state.zone, state.ceiling_agl_m, state.permission_required)


def most_restrictive(states: Iterable[ZoneState]) -> ZoneState:
    """Ties keep the first state given; an empty ``states`` raises ``invalid_params``."""
    best: ZoneState | None = None
    best_key: tuple[int, float, int] | None = None
    for state in states:
        key = state_key(state)
        if best_key is None or key < best_key:
            best, best_key = state, key
    if best is None:
        raise AnalysisError("invalid_params", "No zone states to compare")
    return best


def default_state(rules: ZoneRules, agl: float | None = None) -> ZoneState:
    """The state outside every mapped zone (``rules.default_zone`` / ``default_ceiling_agl_m``),
    or ``default_ceiling_exceeded_zone`` when ``agl`` is above that ceiling."""
    zone = rules.default_zone
    ceiling = float(rules.default_ceiling_agl_m)
    if zone == "red":
        return ZoneState(zone="red", ceiling_agl_m=None, permission_required=True,
                         label="Outside mapped zones: default rules (no flight)", source="default", name="Default rules")
    above = rules.default_ceiling_exceeded_zone
    if above is not None and agl is not None and agl > ceiling + _TOL:
        return _exceeded_default(rules)
    return ZoneState(
        zone=zone,
        ceiling_agl_m=ceiling,
        permission_required=False,
        label=f"Outside mapped zones: up to {rules.default_ceiling_agl_m:g} m AGL",
        source="default",
        name="Default rules",
        rule_ceiling_agl_m=ceiling,
        ceiling_exceeded_zone=above,
    )


def _exceeded_default(rules: ZoneRules) -> ZoneState:
    above = rules.default_ceiling_exceeded_zone or "yellow"
    return ZoneState(
        zone=above,
        ceiling_agl_m=None,
        permission_required=True,
        label=f"Above {rules.default_ceiling_agl_m:g} m AGL: {above} zone (permission required)",
        source="default",
        name="Default rules",
        rule_ceiling_agl_m=float(rules.default_ceiling_agl_m),
    )


def _ring_state(aero: Aerodrome, ring: ZoneRing, distance_from: DistanceFrom, exceeded: bool = False) -> ZoneState:
    name = aero.name or aero.ident
    ident = aero.ident or None
    if exceeded and ring.ceiling_exceeded_zone is not None and ring.ceiling_agl_m is not None:
        return ZoneState(
            zone=ring.ceiling_exceeded_zone,
            ceiling_agl_m=None,
            permission_required=True,
            label=f"{ring.label} — above {ring.ceiling_agl_m:g} m AGL: {ring.ceiling_exceeded_zone} (permission required)",
            source="aerodrome",
            name=name,
            ident=ident,
            rule_ceiling_agl_m=float(ring.ceiling_agl_m),
            distance_from=distance_from,
        )
    permission = ring.zone == "red" or ring.ceiling_agl_m is None or ring.verify_official_map
    ceiling: float | None = None
    if not permission and ring.ceiling_agl_m is not None:
        ceiling = float(ring.ceiling_agl_m)
    return ZoneState(
        zone=ring.zone,
        ceiling_agl_m=ceiling,
        permission_required=permission,
        label=ring.label,
        source="aerodrome",
        name=name,
        ident=ident,
        basis="verify_official_map" if ring.verify_official_map else "rule",
        rule_ceiling_agl_m=float(ring.ceiling_agl_m) if ring.ceiling_agl_m is not None else None,
        distance_from=distance_from,
        ceiling_exceeded_zone=ring.ceiling_exceeded_zone if ceiling is not None else None,
    )


def _user_state(zone: UserZone, source: Literal["user", "datapack"], rules: ZoneRules) -> ZoneState:
    if zone.zone == "red":
        return ZoneState(zone="red", ceiling_agl_m=None, permission_required=True,
                         label=zone.note or "Red zone: no flight", source=source, name=zone.name)
    if zone.zone == "yellow":
        if zone.ceiling_agl_m is None:
            return ZoneState(zone="yellow", ceiling_agl_m=None, permission_required=True,
                             label=zone.note or "Yellow zone: permission required", source=source, name=zone.name)
        return ZoneState(zone="yellow", ceiling_agl_m=float(zone.ceiling_agl_m), permission_required=False,
                         label=zone.note or f"Yellow zone: up to {zone.ceiling_agl_m:g} m AGL",
                         source=source, name=zone.name, rule_ceiling_agl_m=float(zone.ceiling_agl_m))
    ceiling = float(zone.ceiling_agl_m) if zone.ceiling_agl_m is not None else float(rules.default_ceiling_agl_m)
    return ZoneState(zone="green", ceiling_agl_m=ceiling, permission_required=False,
                     label=zone.note or f"Green zone: up to {ceiling:g} m AGL", source=source, name=zone.name,
                     rule_ceiling_agl_m=ceiling)


@dataclass(frozen=True)
class _PolygonZone:
    zone: UserZone
    geometry: BaseGeometry
    state: ZoneState
    source: Literal["user", "datapack"]


@dataclass(frozen=True)
class _ZoneSet:
    """The prepared polygon zones of one run, with an STRtree and their vertical bands as arrays
    (lower and upper limit per zone, the geoid widening already applied; NaN: none)."""

    zones: list[_PolygonZone]
    tree: STRtree | None
    lower: np.ndarray
    upper: np.ndarray
    agl: np.ndarray  # limits above ground (else above sea level or the ellipsoid)
    ceilings: np.ndarray  # zones with a ceiling of their own

    def near(self, bounds: tuple[float, float, float, float]) -> list[_PolygonZone]:
        """Zones whose bounding box meets ``bounds``, in their original order (ties keep it)."""
        if self.tree is None:
            return []
        min_lon, min_lat, max_lon, max_lat = bounds
        found = self.tree.query(shapely.box(min_lon - 1e-9, min_lat - 1e-9, max_lon + 1e-9, max_lat + 1e-9))
        return [self.zones[int(i)] for i in sorted(found.tolist())]

    def touching(self, geom: BaseGeometry) -> list[_PolygonZone]:
        """Zones the geometry intersects (boundary included), in their original order."""
        if self.tree is None:
            return []
        atoms = _atoms(geom)
        if atoms.size == 0:
            return []
        try:
            found = self.tree.query(atoms, predicate="intersects")[1]
        except GEOSException:
            return [pz for pz in self.near(geom.bounds) if _intersects(pz.geometry, geom)]
        return [self.zones[int(i)] for i in np.unique(found)]

    def __iter__(self) -> Iterator[_PolygonZone]:
        return iter(self.zones)


# One analysis asks for the same zones several times (point classification, zones for the
# geometry, touched zones). Parsing, repairing and preparing every zone is the expensive part,
# so the last few results are kept per thread: prepared geometries are not shared between
# threads, and entries match by object identity, so a later run never reuses another's zones.
_ZONE_CACHE = threading.local()
_ZONE_CACHE_SIZE = 4

_ZoneKey = tuple[DataPack | None, ZoneRules, tuple[UserZone, ...], bool]


def _same_key(a: _ZoneKey, b: _ZoneKey) -> bool:
    return (
        a[0] is b[0]
        and a[1] is b[1]
        and a[3] == b[3]
        and len(a[2]) == len(b[2])
        and all(x is y for x, y in zip(a[2], b[2], strict=True))
    )


def _polygon_zones(
    pack: DataPack | None,
    rules: ZoneRules,
    user_zones: Sequence[UserZone],
    include_datapack_zones: bool,
) -> _ZoneSet:
    key: _ZoneKey = (pack, rules, tuple(user_zones), include_datapack_zones)
    entries: list[tuple[_ZoneKey, _ZoneSet]] = getattr(_ZONE_CACHE, "entries", [])
    for cached_key, zones in entries:
        if _same_key(cached_key, key):
            return zones
    zones = _build_polygon_zones(pack, rules, user_zones, include_datapack_zones)
    _ZONE_CACHE.entries = [(key, zones), *entries][:_ZONE_CACHE_SIZE]
    return zones


def _build_polygon_zones(
    pack: DataPack | None,
    rules: ZoneRules,
    user_zones: Sequence[UserZone],
    include_datapack_zones: bool,
) -> _ZoneSet:
    out: list[_PolygonZone] = []
    sources: list[tuple[Literal["user", "datapack"], UserZone]] = [("user", z) for z in user_zones]
    if include_datapack_zones and pack is not None:
        sources.extend(("datapack", z) for z in pack.zones())
    for source, zone in sources:
        geom = geojson_to_geometry(zone.geometry)
        poly = polygonal_part(geom)
        if poly is None:
            raise AnalysisError(
                "invalid_geometry",
                f"Zone '{zone.name}' must be a polygon or multipolygon",
                details={"zone": zone.name, "geometry_type": geom.geom_type, "source": source},
            )
        poly = shapely.force_2d(poly)
        if not poly.is_valid:
            fixed = polygonal_part(shapely.make_valid(poly))
            if fixed is None:
                raise AnalysisError(
                    "invalid_geometry",
                    f"Zone '{zone.name}' has no valid area",
                    details={"zone": zone.name, "source": source},
                )
            poly = fixed
        shapely.prepare(poly)
        out.append(_PolygonZone(zone=zone, geometry=poly, state=_user_state(zone, source, rules), source=source))
    bands = [_band_of(pz.zone) for pz in out]
    lower = np.array([b[0] + b[1] if b else np.nan for b in bands], dtype=np.float64)
    upper = np.array([b[2] + b[3] if b else np.nan for b in bands], dtype=np.float64)
    agl = np.array([pz.zone.vertical_reference == "AGL" for pz in out], dtype=bool)
    ceilings = np.array([pz.zone.ceiling_agl_m is not None for pz in out], dtype=bool)
    tree = STRtree([pz.geometry for pz in out]) if out else None
    return _ZoneSet(out, tree, lower, upper, agl, ceilings)


def zone_active(zone: UserZone, window: TimeWindow | None) -> bool | None:
    """Whether ``zone`` is active at some time in ``window`` (None without a window)."""
    if window is None:
        return None
    return bool(schedule_intervals(zone, window[0], window[1]))


def _validity_text(zone: UserZone) -> str:
    def fmt(value: datetime | None) -> str:
        return value.strftime("%Y-%m-%d %H:%M") if value is not None else "…"

    parts = []
    if zone.valid_from or zone.valid_to:
        parts.append(f"valid {fmt(zone.valid_from)} to {fmt(zone.valid_to)} UTC")
    if zone.schedule:
        parts.append("; ".join(f"{','.join(s.days)} {s.start}–{s.end} UTC" for s in zone.schedule[:3]))
    return ", ".join(parts) or "no validity limits"


def _band_of(zone: UserZone) -> tuple[float, float, float, float] | None:
    """``(lower, lower shift, upper, upper shift)`` of a zone's vertical band, None when it has none."""
    if zone.lower_limit_m is None and zone.upper_limit_m is None:
        return None
    low_shift, high_shift = (-_GEOID_N[1], -_GEOID_N[0]) if zone.vertical_reference == "WGS84" else (0.0, 0.0)
    lower = zone.lower_limit_m if zone.lower_limit_m is not None else -math.inf
    upper = zone.upper_limit_m if zone.upper_limit_m is not None else math.inf
    return lower, low_shift, upper, high_shift


def _reaches(
    zones: _ZoneSet, z_idx: np.ndarray, agl: np.ndarray | None, ground: Callable[[], np.ndarray] | None
) -> np.ndarray:
    """Per (point, zone) pair: does the zone's vertical band overlap the flight, which may be
    anywhere from the ground to the planned height ``agl`` (one per pair)?"""
    lower, upper = zones.lower[z_idx], zones.upper[z_idx]
    banded = np.isfinite(lower) | np.isfinite(upper)
    if agl is None or not banded.any():
        return np.ones(z_idx.size, dtype=bool)
    sea = banded & ~zones.agl[z_idx]
    if sea.any():
        g = ground() if ground is not None else np.full(z_idx.size, np.nan)
        known = sea & np.isfinite(g)
        # ground unknown -> the band might reach the flight, so it counts
        lower = np.where(known, lower - np.where(known, g, 0.0), np.where(sea, -math.inf, lower))
        upper = np.where(known, upper - np.where(known, g, 0.0), np.where(sea, math.inf, upper))
    with np.errstate(invalid="ignore"):
        hit = (lower <= agl + _TOL) & (upper >= 0.0)
    return np.asarray(hit | ~banded | ~np.isfinite(agl), dtype=bool)


def _rule_for(kind: str, rules: ZoneRules) -> AerodromeZoneRule | None:
    for rule in rules.rules:
        if kind in rule.aerodrome_types:
            return rule
    return None


def _overrides(operational: Mapping[str, bool] | None) -> dict[str, bool]:
    return {str(k).strip().upper(): bool(v) for k, v in (operational or {}).items()}


def is_operational(aero: Aerodrome, rule: AerodromeZoneRule, overrides: Mapping[str, bool]) -> bool:
    """The rule's bands apply: set per ident in ``overrides`` (upper-case keys), else every
    aerodrome, except that ``scheduled_service_only`` rules need scheduled service (unknown: yes)."""
    key = aero.ident.upper()
    if key in overrides:
        return overrides[key]
    if rule.scheduled_service_only:
        return aero.scheduled_service is not False
    return True


@dataclass(frozen=True)
class _Outline:
    """What an aerodrome's bands are measured from, in its own local frame."""

    distance_from: DistanceFrom
    frame: LocalFrame
    shape: BaseGeometry | None  # None: the reference point minus radius_km
    radius_km: float  # reference point: the assumed radius; else the outline's reach from it
    approximated: bool
    wkb: bytes | None = None


_OUTLINES: weakref.WeakKeyDictionary[DataPack, dict[tuple[str, str, float], _Outline]] = weakref.WeakKeyDictionary()
_OUTLINES_LOCK = threading.Lock()


def _outline(pack: DataPack, aero: Aerodrome, rule: AerodromeZoneRule, rules: ZoneRules) -> _Outline:
    key = (aero.ident.upper(), rules.distance_from, float(rule.assumed_radius_km))
    with _OUTLINES_LOCK:
        known = _OUTLINES.setdefault(pack, {}).get(key)
    if known is not None:
        return known
    frame = LocalFrame(aero.lon, aero.lat)
    outline = _Outline("reference_point", frame, None, float(rule.assumed_radius_km),
                       approximated=rules.distance_from == "perimeter")
    if rules.distance_from == "perimeter":
        shape: BaseGeometry | None = None
        basis: DistanceFrom = "perimeter"
        perimeter = pack.perimeters().get(key[0])
        if perimeter is not None:
            shape = polygonal_part(frame.to_local(perimeter))
        if shape is None or shape.is_empty:
            runways = pack.runways().get(key[0], [])
            ends = [c for r in runways for c in (r.line.coords[0], r.line.coords[-1])]
            if ends:
                xs, ys = frame.xy([c[0] for c in ends], [c[1] for c in ends])
                width = max((r.width_m or DEFAULT_RUNWAY_WIDTH_M) for r in runways)
                shape = MultiPoint(list(zip(xs.tolist(), ys.tolist(), strict=True))).convex_hull.buffer(width / 2.0, quad_segs=8)
                basis = "runway_hull"
        if shape is not None and not shape.is_empty:
            reach = float(np.max(np.hypot(*shapely.get_coordinates(shape).T))) / 1000.0
            outline = _Outline(basis, frame, shape, reach, approximated=False, wkb=shapely.to_wkb(shape))
    with _OUTLINES_LOCK:
        _OUTLINES.setdefault(pack, {})[key] = outline
    return outline


def _reach_km(rule: AerodromeZoneRule, outline: _Outline) -> float:
    return outline.radius_km + max(float(r.outer_km) for r in rule.rings)


def _ruled_aerodromes(
    pack: DataPack | None,
    rules: ZoneRules,
    bounds: tuple[float, float, float, float] | None,
    operational: Mapping[str, bool] | None = None,
) -> Iterator[tuple[Aerodrome, AerodromeZoneRule, _Outline]]:
    """Operational aerodromes with a rule whose outermost ring may reach ``bounds`` (lon/lat bbox)."""
    if pack is None or not rules.rules:
        return
    overrides = _overrides(operational)
    for aero in pack.aerodromes():
        rule = _rule_for(aero.type, rules)
        if rule is None or not is_operational(aero, rule, overrides):
            continue
        if bounds is not None:
            min_lon, min_lat, max_lon, max_lat = bounds
            near_lon = min(max(aero.lon, min_lon), max_lon)
            near_lat = min(max(aero.lat, min_lat), max_lat)
            gap_m = float(distance_m(aero.lon, aero.lat, near_lon, near_lat))
            # cheap first cut befor building the outline: no airport reaches 30 km past its reference point
            if gap_m > (max(float(r.outer_km) for r in rule.rings) + 30.0) * 1000.0 * _REACH_MARGIN:
                continue
            outline = _outline(pack, aero, rule, rules)
            if gap_m > _reach_km(rule, outline) * 1000.0 * _REACH_MARGIN + 500.0:
                continue
        else:
            outline = _outline(pack, aero, rule, rules)
        yield aero, rule, outline


def _distances_km(outline: _Outline, aero: Aerodrome, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    if outline.shape is None:
        dist_km = np.asarray(
            distance_m(np.full(lons.shape, aero.lon), np.full(lats.shape, aero.lat), lons, lats), dtype=np.float64
        ) / 1000.0
        return np.maximum(dist_km - outline.radius_km, 0.0)
    xs, ys = outline.frame.xy(lons, lats)
    return np.asarray(shapely.distance(outline.shape, shapely.points(xs, ys)), dtype=np.float64) / 1000.0


@lru_cache(maxsize=512)
def _ring_geometry_cached(lon: float, lat: float, inner_m: float, outer_m: float) -> BaseGeometry:
    frame = LocalFrame(lon, lat)
    disk = Point(0.0, 0.0).buffer(outer_m, quad_segs=RING_QUAD_SEGS)
    if inner_m > 0.0:
        disk = disk.difference(Point(0.0, 0.0).buffer(inner_m, quad_segs=RING_QUAD_SEGS))
    return _valid(frame.to_wgs84(disk))


# Keyed by the outline's wkb: hashing a few kB is nothing next to two buffers.
@lru_cache(maxsize=512)
def _band_geometry_cached(lon: float, lat: float, wkb: bytes, inner_m: float, outer_m: float) -> BaseGeometry:
    frame = LocalFrame(lon, lat)
    shape = shapely.from_wkb(wkb)
    band = shape.buffer(outer_m, quad_segs=RING_QUAD_SEGS)
    if inner_m > 0.0:
        band = band.difference(shape.buffer(inner_m, quad_segs=RING_QUAD_SEGS))
    return _valid(frame.to_wgs84(band))


def _valid(ring: BaseGeometry) -> BaseGeometry:
    if not ring.is_valid:
        fixed = polygonal_part(shapely.make_valid(ring))
        if fixed is not None:
            return fixed
    return ring


def _band_geometry(aero: Aerodrome, rule: AerodromeZoneRule, ring: ZoneRing, outline: _Outline) -> BaseGeometry:
    if outline.wkb is None:
        radius_km = outline.radius_km
        inner_m = (radius_km + float(ring.inner_km)) * 1000.0 if ring.inner_km > 0 else 0.0
        outer_m = (radius_km + float(ring.outer_km)) * 1000.0
        return _ring_geometry_cached(float(aero.lon), float(aero.lat), inner_m, outer_m)
    return _band_geometry_cached(float(aero.lon), float(aero.lat), outline.wkb,
                                 float(ring.inner_km) * 1000.0, float(ring.outer_km) * 1000.0)


def ring_geometry(
    aero: Aerodrome, rule: AerodromeZoneRule, ring: ZoneRing, *, pack: DataPack | None = None,
    rules: ZoneRules | None = None,
) -> BaseGeometry:
    """WGS84 polygon of one aerodrome ring: around the outline that ``rules`` measure from
    (with a ``pack``), else a circle about the reference point (an annulus when ``inner_km > 0``)."""
    if pack is None or rules is None:
        return _band_geometry(aero, rule, ring, _Outline("reference_point", LocalFrame(aero.lon, aero.lat), None,
                                                         float(rule.assumed_radius_km), approximated=False))
    return _band_geometry(aero, rule, ring, _outline(pack, aero, rule, rules))


def _per_point(agl: float | np.ndarray | None, n: int) -> np.ndarray | None:
    if agl is None:
        return None
    arr = np.asarray(agl, dtype=np.float64)
    return np.full(n, float(arr)) if arr.ndim == 0 else arr.ravel()


def _ground(pack: DataPack | None, lons: np.ndarray, lats: np.ndarray) -> Callable[[], np.ndarray]:
    """Ground elevation (m AMSL) at the points, sampled on first use (NaN where unknown)."""
    sampled: list[np.ndarray] = []

    def get() -> np.ndarray:
        if not sampled:
            dem = pack.dem() if pack is not None else None
            sampled.append(np.asarray(dem.sample(lons, lats), dtype=np.float64) if dem is not None
                           else np.full(lons.shape, np.nan))
        return sampled[0]

    return get


def classify_points(
    lons: np.ndarray,
    lats: np.ndarray,
    *,
    pack: DataPack | None,
    rules: ZoneRules,
    user_zones: Sequence[UserZone] = (),
    include_datapack_zones: bool = True,
    agl: float | np.ndarray | None = None,
    operational: Mapping[str, bool] | None = None,
    window: TimeWindow | None = None,
    reach_agl: np.ndarray | None = None,
) -> list[ZoneState]:
    """The most restrictive :class:`ZoneState` at each point (one per input point, in order).

    ``agl`` is the planned height above ground (one value or one per point): it decides the
    zone above a band's ceiling and which vertical bands the flight reaches; without it heights
    are not judged. ``reach_agl`` (one per point) decides only the vertical bands: bands and open
    airspace keep their state on the ground, ceilings included (the flight audit's limits).
    Zones inactive in ``window`` are left out. Points with non-finite coordinates get the default
    state. Raises ``invalid_params`` when ``lons`` and ``lats`` differ in size,
    ``invalid_geometry`` for a zone without a polygonal area.
    """
    lo = np.asarray(lons, dtype=np.float64).ravel()
    la = np.asarray(lats, dtype=np.float64).ravel()
    if lo.shape != la.shape:
        raise AnalysisError(
            "invalid_params",
            "Longitude and latitude arrays must have the same length",
            details={"lons": int(lo.size), "lats": int(la.size)},
        )
    n = int(lo.size)
    if n == 0:
        return []
    heights = _per_point(agl, n)
    reach = _per_point(reach_agl, n) if reach_agl is not None else heights
    default = default_state(rules)
    candidates: list[ZoneState] = []
    masks: list[np.ndarray | None] = []
    pair_candidates: dict[int, int] = {}  # candidate index -> zone index, for polygon zones
    pairs: tuple[np.ndarray, np.ndarray] | None = None
    finite = np.isfinite(lo) & np.isfinite(la)
    if finite.any():
        sel = np.nonzero(finite)[0]
        flo, fla = lo[sel], la[sel]
        h = heights[sel] if heights is not None else None
        # inside a band or zone with a ceiling of its own, open airspace's yellow-above doesn't apply
        own_ceiling = np.zeros(sel.size, dtype=bool)

        def add(state: ZoneState, inside: np.ndarray) -> None:
            if inside.any():
                mask = np.zeros(n, dtype=bool)
                mask[sel[inside]] = True
                candidates.append(state)
                masks.append(mask)

        bounds = (float(flo.min()), float(fla.min()), float(flo.max()), float(fla.max()))
        for aero, rule, outline in _ruled_aerodromes(pack, rules, bounds, operational):
            dist_km = _distances_km(outline, aero, flo, fla)
            for ring in rule.rings:
                inside = (dist_km >= float(ring.inner_km)) & (dist_km < float(ring.outer_km))
                if ring.ceiling_agl_m is not None:  # ceiling_exceeded_zone needs one too
                    own_ceiling |= inside
                if ring.ceiling_exceeded_zone is not None and ring.ceiling_agl_m is not None and h is not None:
                    with np.errstate(invalid="ignore"):
                        high = inside & (h > float(ring.ceiling_agl_m) + _TOL)
                    add(_ring_state(aero, ring, outline.distance_from, exceeded=True), high)
                    inside = inside & ~high
                add(_ring_state(aero, ring, outline.distance_from), inside)
        zone_set = _polygon_zones(pack, rules, user_zones, include_datapack_zones)
        if zone_set.tree is not None:
            # (point, zone) pairs from the tree: thousands of zones never mean one pass per zone
            p_idx, z_idx = zone_set.tree.query(shapely.points(flo, fla), predicate="intersects")
            used = np.unique(z_idx)
            off = [int(z) for z in used if zone_active(zone_set.zones[int(z)].zone, window) is False]
            keep = ~np.isin(z_idx, off)
            p_idx, z_idx = p_idx[keep], z_idx[keep]
            ground = _ground(pack, flo[p_idx], fla[p_idx])
            keep = _reaches(zone_set, z_idx, reach[sel][p_idx] if reach is not None else None, ground)
            p_idx, z_idx = p_idx[keep], z_idx[keep]
            for z in np.unique(z_idx).tolist():
                pair_candidates[len(candidates)] = z
                candidates.append(zone_set.zones[z].state)
                masks.append(None)
            pairs = (sel[p_idx], z_idx)
            own_ceiling[p_idx[zone_set.ceilings[z_idx]]] = True
        if rules.default_ceiling_exceeded_zone is not None and h is not None and rules.default_zone != "red":
            with np.errstate(invalid="ignore"):
                add(_exceeded_default(rules), (h > float(rules.default_ceiling_agl_m) + _TOL) & ~own_ceiling)

    if not candidates:
        return [default] * n
    # Rank candidates by restrictiveness (stable on insertion order); a point takes the
    # best-ranked candidate covering it, or the default state when none does.
    order = sorted(range(len(candidates)), key=lambda i: (state_key(candidates[i]), i))
    rank = np.empty(len(candidates), dtype=np.int64)
    for position, index in enumerate(order):
        rank[index] = position
    none_rank = len(candidates)
    best = np.full(n, none_rank, dtype=np.int64)
    for index, mask in enumerate(masks):
        if mask is not None:
            r = int(rank[index])
            best = np.where(mask & (r < best), r, best)
    if pairs is not None and pair_candidates:
        zone_rank = np.full(len(zone_set.zones), none_rank, dtype=np.int64)
        for index, z in pair_candidates.items():
            zone_rank[z] = rank[index]
        np.minimum.at(best, pairs[0], zone_rank[pairs[1]])
    by_rank: dict[int, ZoneState] = {int(rank[i]): candidates[i] for i in range(len(candidates))}
    by_rank[none_rank] = default
    return [by_rank[int(r)] for r in best.tolist()]


def band_evidence(
    lons: np.ndarray,
    lats: np.ndarray,
    agl: float | np.ndarray | None,
    *,
    pack: DataPack | None,
    rules: ZoneRules,
    operational: Mapping[str, bool] | None = None,
    sample_kind: Literal["profile", "grid", "vertices", "track"],
) -> list[BandEvidence]:
    """Per aerodrome band the samples fall in (and "elsewhere" for those in none): how many, the
    highest planned height above ground among them and its margin to the band's ceiling."""
    lo = np.asarray(lons, dtype=np.float64).ravel()
    la = np.asarray(lats, dtype=np.float64).ravel()
    heights = _per_point(agl, lo.size)
    finite = np.isfinite(lo) & np.isfinite(la)
    if not finite.any():
        return []
    flo, fla = lo[finite], la[finite]
    h = heights[finite] if heights is not None else None
    covered = np.zeros(flo.size, dtype=bool)
    out: list[BandEvidence] = []

    def entry(mask: np.ndarray, **fields: Any) -> BandEvidence:
        worst = None
        if h is not None:
            vals = h[mask][np.isfinite(h[mask])]
            worst = round(float(vals.max()), 2) if vals.size else None
        ceiling = fields.pop("rule_ceiling")
        exceeded_zone = fields.pop("exceeded_zone")
        zone, permission = fields.pop("zone"), fields.pop("permission")
        if exceeded_zone is not None and ceiling is not None and worst is not None and worst > ceiling + _TOL:
            zone, permission = exceeded_zone, True
        margin = round(ceiling - worst, 2) if ceiling is not None and worst is not None else None
        return BandEvidence(zone=zone, permission_required=permission, ceiling_agl_m=ceiling, worst_agl_m=worst,
                            agl_margin_m=margin, samples=int(mask.sum()), sample_kind=sample_kind, **fields)

    bounds = (float(flo.min()), float(fla.min()), float(flo.max()), float(fla.max()))
    for aero, rule, outline in _ruled_aerodromes(pack, rules, bounds, operational):
        dist_km = _distances_km(outline, aero, flo, fla)
        for ring in rule.rings:
            inside = (dist_km >= float(ring.inner_km)) & (dist_km < float(ring.outer_km))
            if not inside.any():
                continue
            covered |= inside
            permission = ring.zone == "red" or ring.ceiling_agl_m is None or ring.verify_official_map
            out.append(entry(
                inside, ident=aero.ident or None, name=aero.name or aero.ident, band=ring.label,
                inner_km=float(ring.inner_km), outer_km=float(ring.outer_km), zone=ring.zone, permission=permission,
                basis="verify_official_map" if ring.verify_official_map else "rule",
                distance_from=outline.distance_from, rule_ceiling=ring.ceiling_agl_m,
                exceeded_zone=ring.ceiling_exceeded_zone,
            ))
    rest = ~covered
    if rest.any():
        ceiling = float(rules.default_ceiling_agl_m) if rules.default_zone != "red" else None
        out.append(entry(
            rest, ident=None, name="Outside aerodrome bands", band=f"Elsewhere: {rules.default_zone} up to {rules.default_ceiling_agl_m:g} m AGL",
            inner_km=0.0, outer_km=0.0, zone=rules.default_zone, permission=rules.default_zone == "red",
            distance_from=None, rule_ceiling=ceiling, exceeded_zone=rules.default_ceiling_exceeded_zone,
        ))
    return out


def has_unverified_band(rules: ZoneRules) -> bool:
    """Whether the rules have a band the published rule does not define (DGCA 2021: 0–5 km)."""
    return any(ring.verify_official_map for rule in rules.rules for ring in rule.rings)


def unverified_bands(
    lons: np.ndarray,
    lats: np.ndarray,
    *,
    pack: DataPack | None,
    rules: ZoneRules,
    operational: Mapping[str, bool] | None = None,
) -> list[str | None]:
    """Per point, the ``verify_official_map`` band it is in ("VOBG 0–5 km"), else None.

    Read from the rings themselves, not from :func:`classify_points`: there an overlapping
    permission band of another aerodrome can win the tie and hide it."""
    lo = np.asarray(lons, dtype=np.float64).ravel()
    la = np.asarray(lats, dtype=np.float64).ravel()
    out: list[str | None] = [None] * lo.size
    sel = np.nonzero(np.isfinite(lo) & np.isfinite(la))[0]
    if sel.size == 0 or not has_unverified_band(rules):
        return out
    flo, fla = lo[sel], la[sel]
    bounds = (float(flo.min()), float(fla.min()), float(flo.max()), float(fla.max()))
    for aero, rule, outline in _ruled_aerodromes(pack, rules, bounds, operational):
        rings = [r for r in rule.rings if r.verify_official_map]
        if not rings:
            continue
        dist_km = _distances_km(outline, aero, flo, fla)
        for ring in rings:
            name = f"{aero.ident or aero.name} {ring.label.split(':')[0]}"
            for i in np.nonzero((dist_km >= float(ring.inner_km)) & (dist_km < float(ring.outer_km)))[0].tolist():
                if out[sel[i]] is None:  # the first band names it, there's rarely a second
                    out[sel[i]] = name
    return out


def _atoms(geom: BaseGeometry) -> np.ndarray:
    parts = list(iter_parts(geom))
    arr = np.empty(len(parts), dtype=object)
    for i, part in enumerate(parts):
        arr[i] = part
    return arr


def _intersects(zone_geom: BaseGeometry, geom: BaseGeometry) -> bool:
    """Tested per atomic part, so GeometryCollections are safe."""
    atoms = _atoms(geom)
    if atoms.size == 0:
        return False
    try:
        return bool(np.any(shapely.intersects(zone_geom, atoms)))
    except GEOSException:
        return bool(np.any(shapely.intersects(shapely.make_valid(zone_geom), atoms)))


def _min_distance(origin: BaseGeometry, geom: BaseGeometry) -> float:
    atoms = _atoms(geom)
    if atoms.size == 0:
        return math.inf
    dist = np.asarray(shapely.distance(origin, atoms), dtype=np.float64)
    dist = dist[np.isfinite(dist)]
    return float(dist.min()) if dist.size else math.inf


class _Segments:
    """The segments of a line with their bounding boxes, built once per call: each zone then
    tests only the segments whose box meets its own."""

    def __init__(self, xy: np.ndarray) -> None:
        self.xy = xy
        self.lines = shapely.linestrings(np.stack([xy[:-1], xy[1:]], axis=1))
        self.low = np.minimum(xy[:-1], xy[1:])
        self.high = np.maximum(xy[:-1], xy[1:])
        self._last: tuple[BaseGeometry, np.ndarray] | None = None

    def touching(self, zone_geom: BaseGeometry) -> np.ndarray:
        """Indices of the segments that intersect the zone (boundary included), in order."""
        if self._last is not None and self._last[0] is zone_geom:
            return self._last[1]  # a zone's height and its first_index ask in turn
        minx, miny, maxx, maxy = zone_geom.bounds
        near = np.nonzero((self.high[:, 0] >= minx) & (self.low[:, 0] <= maxx)
                          & (self.high[:, 1] >= miny) & (self.low[:, 1] <= maxy))[0]
        try:
            hit = shapely.intersects(zone_geom, self.lines[near])
        except GEOSException:
            hit = shapely.intersects(shapely.make_valid(zone_geom), self.lines[near])
        found = near[np.asarray(hit, dtype=bool)]
        self._last = (zone_geom, found)
        return found


def _first_index(geom: BaseGeometry, zone_geom: BaseGeometry, segments: _Segments | None) -> int | None:
    """Index of the first vertex/segment of a LineString (or point of a MultiPoint) in the zone;
    ``segments`` are the LineString's, None when it has fewer than two vertices."""
    try:
        if isinstance(geom, LineString):
            if segments is not None:
                hit = segments.touching(zone_geom)
                return int(hit[0]) if hit.size else None
            coords = shapely.get_coordinates(geom)
            if coords.shape[0] == 0:
                return None
            return 0 if bool(shapely.intersects_xy(zone_geom, coords[0, 0], coords[0, 1])) else None
        if isinstance(geom, MultiPoint):
            coords = shapely.get_coordinates(geom)
            if coords.size == 0:
                return None
            hit = np.asarray(shapely.intersects_xy(zone_geom, coords[:, 0], coords[:, 1]), dtype=bool)
            return int(np.argmax(hit)) if hit.any() else None
    except GEOSException:
        return None
    return None


def _overlap(geom: BaseGeometry, zone_geom: BaseGeometry) -> tuple[float | None, float | None]:
    """Geodesic area (m²) and length (m) of ``geom`` inside the zone."""
    area: float | None = None
    length: float | None = None
    poly = polygonal_part(geom)
    if poly is not None:
        try:
            inter = poly.intersection(zone_geom)
        except GEOSException:
            inter = shapely.make_valid(poly).intersection(zone_geom)
        area = round(float(geodesic_area_perimeter(inter)[0]), 2)
    lines = lineal_part(geom)
    if lines is not None:
        try:
            inter_l = lines.intersection(zone_geom)
        except GEOSException:
            inter_l = None
        length = round(float(geodesic_length(inter_l)), 2) if inter_l is not None else None
    return area, length


def _hit(
    state: ZoneState,
    geom: BaseGeometry,
    zone_geom: BaseGeometry,
    *,
    source: Literal["aerodrome", "user", "datapack"],
    ring: str | None,
    distance_km: float | None,
    note: str | None,
    segments: _Segments | None,
    **extra: Any,
) -> ZoneHit:
    area, length = _overlap(geom, zone_geom)
    return ZoneHit(
        source=source,
        name=state.name,
        ident=state.ident,
        zone=state.zone,
        ring=ring,
        ceiling_agl_m=state.ceiling_agl_m,
        permission_required=state.permission_required,
        distance_km=round(distance_km, 3) if distance_km is not None else None,
        overlap_area_m2=area,
        overlap_length_m=length,
        first_index=_first_index(geom, zone_geom, segments),
        note=note,
        basis=state.basis,
        distance_from=state.distance_from,
        **extra,
    )


def hit_key(hit: ZoneHit) -> tuple[int, float, int, float]:
    """Sort key for zone hits: most restrictive first, then nearest."""
    tier, ceiling, colour = _key(hit.zone, hit.ceiling_agl_m, hit.permission_required)
    return tier, ceiling, colour, hit.distance_km if hit.distance_km is not None else 0.0


def _margin(limit: float | None, altitude: float | None) -> float | None:
    return round(float(limit) - float(altitude), 2) if limit is not None and altitude is not None else None


def _vertical_gap(zone: UserZone, geom: BaseGeometry, altitude: float | None, pack: DataPack | None) -> str | None:
    """Why the zone's vertical band misses the flight over ``geom`` (None: it may be reached).

    Over terrain the band is widened to the lowest and highest ground under the geometry in the
    zone, so a zone is only left out when no part of the flight can reach it."""
    band = _band_of(zone)
    if band is None or altitude is None:
        return None
    lower, low_shift, upper, high_shift = band
    if zone.vertical_reference == "AGL":
        lo, up = lower, upper
    else:
        dem = pack.dem() if pack is not None else None
        if dem is None:
            return None
        try:
            inside = geom.intersection(geojson_to_geometry(zone.geometry))
        except GEOSException:
            return None
        pts = shapely.get_coordinates(inside)
        if pts.size == 0:
            return None
        ground = np.asarray(dem.sample(pts[:, 0], pts[:, 1]), dtype=np.float64)
        ground = ground[np.isfinite(ground)]
        if ground.size == 0:
            return None
        lo = lower + low_shift - float(ground.max())
        up = upper + high_shift - float(ground.min())
    ref = zone.vertical_reference
    if lo > altitude + _TOL:
        return f"Above the planned height: the zone starts at {zone.lower_limit_m:g} m {ref}"
    if up < 0.0:
        return f"Below the ground here: the zone ends at {zone.upper_limit_m:g} m {ref}"
    return None


Fixes = tuple[np.ndarray, np.ndarray, np.ndarray]
"""A flown track's fixes as float arrays: longitudes, latitudes and heights above ground."""


def _fixes_in(zone_geom: BaseGeometry, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """Indices of the fixes inside the zone (boundary included)."""
    minx, miny, maxx, maxy = zone_geom.bounds
    near = np.nonzero((lons >= minx) & (lons <= maxx) & (lats >= miny) & (lats <= maxy))[0]
    try:
        hit = shapely.intersects_xy(zone_geom, lons[near], lats[near])
    except GEOSException:
        hit = shapely.intersects_xy(shapely.make_valid(zone_geom), lons[near], lats[near])
    return near[np.asarray(hit, dtype=bool)]


def _flown_in(inside: np.ndarray, zone_geom: BaseGeometry, agl: np.ndarray, segments: _Segments) -> float | None:
    """The highest height a track flew in a zone: the highest of its fixes inside it and of both
    ends of every segment that touches it, so a segment that leaves or crosses the zone between
    two fixes counts at its higher end."""
    touching = segments.touching(zone_geom)
    heights = np.concatenate([agl[inside], agl[touching], agl[touching + 1]])
    heights = heights[np.isfinite(heights)]
    return float(heights.max()) if heights.size else None


def zone_hits_by_activity(
    geom_wgs84: BaseGeometry,
    *,
    pack: DataPack | None,
    rules: ZoneRules,
    user_zones: Sequence[UserZone] = (),
    include_datapack_zones: bool = True,
    altitude_agl_m: float | None = None,
    operational: Mapping[str, bool] | None = None,
    window: TimeWindow | None = None,
    fixes: Fixes | None = None,
) -> tuple[list[ZoneHit], list[ZoneHit]]:
    """Zones the geometry touches, split into those that apply (most restrictive first) and
    user/pack zones that do not: inactive in ``window`` or vertically clear of the flight at
    ``altitude_agl_m`` (``active = False`` with an ``inactive_reason``).

    With ``fixes`` (a flown track whose path is ``geom_wgs84``) each band and zone is judged at
    the highest height flown inside it instead of ``altitude_agl_m``: the highest of its fixes
    inside (in a band by distance, as :func:`band_evidence` counts them) and of both ends of
    every track segment that touches it, so where the fixes fall never hides a crossing.

    Aerodrome rings report ``distance_km`` (the geometry's distance to the aerodrome outline) and
    the ring label; all hits carry the geodesic overlap area (polygons) and length (lines),
    ``first_index`` for LineStrings (first segment in the zone) and MultiPoints, and
    ``agl_margin_m`` against a ceiling (or a zone's lower limit) when the height is given.
    """
    if geom_wgs84 is None or geom_wgs84.is_empty:
        return [], []
    geom = shapely.force_2d(geom_wgs84)
    # the segments of the track and of the line, once: zones test only those near them
    flown = _Segments(np.column_stack([fixes[0], fixes[1]])) if fixes is not None else None
    path: _Segments | None = None
    if isinstance(geom, LineString) and len(geom.coords) > 1:
        xy = shapely.get_coordinates(geom)
        # a flight audit's path is its fixes: one set serves both
        path = flown if flown is not None and np.array_equal(xy, flown.xy) else _Segments(xy)
    hits: list[ZoneHit] = []
    inactive: list[ZoneHit] = []
    timed = True if window is not None else None
    for aero, rule, outline in _ruled_aerodromes(pack, rules, geom.bounds, operational):
        if outline.shape is None:
            perimeter_km = max(_min_distance(Point(0.0, 0.0), outline.frame.to_local(geom)) / 1000.0 - outline.radius_km, 0.0)
        else:
            perimeter_km = _min_distance(outline.shape, outline.frame.to_local(geom)) / 1000.0
        if not math.isfinite(perimeter_km):
            continue
        fix_km = _distances_km(outline, aero, fixes[0], fixes[1]) if fixes is not None else None
        for ring in rule.rings:
            if perimeter_km >= float(ring.outer_km):
                continue
            ring_geom = _band_geometry(aero, rule, ring, outline)
            if not _intersects(ring_geom, geom):
                continue
            altitude = altitude_agl_m
            if fixes is not None and fix_km is not None and flown is not None:
                inside = (fix_km >= float(ring.inner_km)) & (fix_km < float(ring.outer_km))
                altitude = _flown_in(inside, ring_geom, fixes[2], flown)
            exceeded = (ring.ceiling_exceeded_zone is not None and ring.ceiling_agl_m is not None
                        and altitude is not None and altitude > ring.ceiling_agl_m + _TOL)
            hits.append(
                _hit(_ring_state(aero, ring, outline.distance_from, exceeded), geom, ring_geom, source="aerodrome",
                     ring=ring.label, distance_km=perimeter_km, note=None, segments=path, active=timed,
                     agl_margin_m=_margin(ring.ceiling_agl_m, altitude))
            )
    for pz in _polygon_zones(pack, rules, user_zones, include_datapack_zones).touching(geom):
        zone = pz.zone
        altitude = altitude_agl_m
        if fixes is not None and flown is not None:
            altitude = _flown_in(_fixes_in(pz.geometry, fixes[0], fixes[1]), pz.geometry, fixes[2], flown)
        active = zone_active(zone, window)
        reason = f"Not active in the planned window ({_validity_text(zone)})" if active is False else _vertical_gap(zone, geom, altitude, pack)
        limit = zone.ceiling_agl_m if zone.ceiling_agl_m is not None else (
            zone.lower_limit_m if zone.vertical_reference == "AGL" else None)
        hit = _hit(pz.state, geom, pz.geometry, source=pz.source, ring=None, distance_km=0.0, note=zone.note,
                   segments=path, active=False if reason else active, valid_from=zone.valid_from,
                   valid_to=zone.valid_to, agl_margin_m=_margin(limit, altitude), inactive_reason=reason)
        (inactive if reason else hits).append(hit)
    hits.sort(key=hit_key)
    return hits, inactive


def zones_for_geometry(
    geom_wgs84: BaseGeometry,
    *,
    pack: DataPack | None,
    rules: ZoneRules,
    user_zones: Sequence[UserZone] = (),
    include_datapack_zones: bool = True,
    altitude_agl_m: float | None = None,
    operational: Mapping[str, bool] | None = None,
    window: TimeWindow | None = None,
) -> list[ZoneHit]:
    """Every zone the geometry touches that applies, most restrictive first (see
    :func:`zone_hits_by_activity`)."""
    return zone_hits_by_activity(
        geom_wgs84, pack=pack, rules=rules, user_zones=user_zones, include_datapack_zones=include_datapack_zones,
        altitude_agl_m=altitude_agl_m, operational=operational, window=window,
    )[0]


def touched_polygon_zones(
    geom_wgs84: BaseGeometry,
    *,
    pack: DataPack | None,
    rules: ZoneRules,
    user_zones: Sequence[UserZone] = (),
    include_datapack_zones: bool = True,
    window: TimeWindow | None = None,
) -> dict[str, Any]:
    """GeoJSON FeatureCollection of the user/data-pack zones the geometry touches (for maps).

    Zones inactive in ``window`` are included with ``active: false``. The properties keep the
    zone's validity, limits, schedule, restriction, authority, reason and source format, so
    :func:`~airbrief_core.io.geozones.zone_from_properties` rebuilds the zone from them."""
    features: list[dict[str, Any]] = []
    if geom_wgs84 is None or geom_wgs84.is_empty:
        return {"type": "FeatureCollection", "features": features}
    geom = shapely.force_2d(geom_wgs84)
    zone_set = _polygon_zones(pack, rules, user_zones, include_datapack_zones)
    numbers = {id(pz): i for i, pz in enumerate(zone_set)}
    for pz in zone_set.touching(geom):
        zone = pz.zone
        features.append(
            {
                "type": "Feature",
                "id": f"{pz.source}-{numbers[id(pz)] + 1}",
                "geometry": geometry_to_geojson(pz.geometry, 8),
                "properties": {
                    "source": pz.source,
                    "name": pz.state.name,
                    "ident": None,
                    "zone": pz.state.zone,
                    "ring": None,
                    "label": pz.state.label,
                    "ceiling_agl_m": pz.state.ceiling_agl_m,
                    "permission_required": pz.state.permission_required,
                    "note": zone.note,
                    "active": zone_active(zone, window),
                    "valid_from": zone.valid_from.isoformat() if zone.valid_from else None,
                    "valid_to": zone.valid_to.isoformat() if zone.valid_to else None,
                    "lower_limit_m": zone.lower_limit_m,
                    "upper_limit_m": zone.upper_limit_m,
                    "vertical_reference": zone.vertical_reference,
                    # what a zone file said, so the ED-318 export can write it back
                    "schedule": [s.model_dump(mode="json") for s in zone.schedule] or None,
                    "restriction": zone.restriction,
                    "authority": zone.authority,
                    "reason": zone.reason,
                    "source_format": zone.source_format,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def ring_polygons(
    pack: DataPack | None,
    rules: ZoneRules,
    bbox: BBox | None = None,
    operational: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """GeoJSON FeatureCollection of aerodrome rings for map display.

    With ``bbox`` only aerodromes whose rings can reach it are included. Properties:
    ``source="aerodrome"``, ``name``, ``ident``, ``type``, ``zone``, ``ring`` and ``label`` (the
    ring label), ``ceiling_agl_m``, ``permission_required``, ``inner_km``, ``outer_km``, ``basis``,
    ``distance_from``, ``ceiling_exceeded_zone``. Features are ordered outer rings first so inner
    rings draw on top.
    """
    features: list[dict[str, Any]] = []
    bounds = (bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat) if bbox is not None else None
    for aero, rule, outline in _ruled_aerodromes(pack, rules, bounds, operational):
        rings = sorted(enumerate(rule.rings), key=lambda t: -float(t[1].outer_km))
        for index, ring in rings:
            state = _ring_state(aero, ring, outline.distance_from)
            features.append(
                {
                    "type": "Feature",
                    "id": f"{aero.ident}-ring-{index + 1}",
                    "geometry": geometry_to_geojson(_band_geometry(aero, rule, ring, outline), 8),
                    "properties": {
                        "source": "aerodrome",
                        "name": state.name,
                        "ident": state.ident,
                        "type": aero.type,
                        "zone": state.zone,
                        "ring": ring.label,
                        "label": ring.label,
                        "ceiling_agl_m": state.ceiling_agl_m,
                        "permission_required": state.permission_required,
                        "inner_km": float(ring.inner_km),
                        "outer_km": float(ring.outer_km),
                        "basis": state.basis,
                        "distance_from": outline.distance_from,
                        "ceiling_exceeded_zone": ring.ceiling_exceeded_zone,
                    },
                }
            )
    return {"type": "FeatureCollection", "features": features}


def nearest_aerodromes(
    geom_wgs84: BaseGeometry,
    pack: DataPack | None,
    k: int = 5,
    *,
    rules: ZoneRules | None = None,
    operational: Mapping[str, bool] | None = None,
) -> list[NearestAerodrome]:
    """The ``k`` aerodromes nearest to a geometry.

    ``distance_km`` is the geodesic distance from the geometry (0 when the reference point lies
    inside it) to the aerodrome reference point; ``bearing_deg`` is from the geometry's
    centroid to the aerodrome. With ``rules``, ``operational`` says whether their bands apply.
    """
    if pack is None or k <= 0 or geom_wgs84 is None or geom_wgs84.is_empty:
        return []
    index = pack.aerodromes()
    if not index:
        return []
    geom = shapely.force_2d(geom_wgs84)
    centre = geom.centroid
    if centre.is_empty or not (math.isfinite(centre.x) and math.isfinite(centre.y)):
        centre = geom.representative_point()
    frame = LocalFrame(float(centre.x), float(centre.y))
    local = frame.to_local(geom)

    def distance_to_geometry(aero: Aerodrome, centre_km: float) -> float:
        ax, ay = frame.xy([aero.lon], [aero.lat])
        planar = _min_distance(Point(float(ax[0]), float(ay[0])), local)
        return planar / 1000.0 if math.isfinite(planar) else centre_km

    # The nearest to the centroid are not always the nearest to a long or large geometry. An
    # aerodrome within d of the geometry is within d + radius of the centroid, so after a first
    # guess of the k-th distance, every aerodrome inside that circle is a candidate.
    first = index.nearest(float(centre.x), float(centre.y), max(k * 3, k + 5))
    guesses = sorted(distance_to_geometry(a, d) for a, d in first)
    vertices = shapely.get_coordinates(local)
    radius_km = float(np.max(np.hypot(vertices[:, 0], vertices[:, 1]))) / 1000.0 if vertices.size else 0.0
    if guesses and math.isfinite(radius_km):
        reach_km = guesses[min(k, len(guesses)) - 1] + radius_km
        candidates = index.within(float(centre.x), float(centre.y), reach_km * 1.01 + 0.5)
    else:
        candidates = first
    overrides = _overrides(operational)
    out: list[NearestAerodrome] = []
    for aero, centre_km in candidates:
        dist_km = distance_to_geometry(aero, centre_km)
        rule = _rule_for(aero.type, rules) if rules is not None else None
        out.append(
            NearestAerodrome(
                ident=aero.ident,
                name=aero.name,
                type=aero.type,
                lon=round(aero.lon, 8),
                lat=round(aero.lat, 8),
                distance_km=round(dist_km, 3),
                bearing_deg=round(float(bearing_deg(float(centre.x), float(centre.y), aero.lon, aero.lat)), 1),
                elevation_m=aero.elevation_m,
                operational=is_operational(aero, rule, overrides) if rule is not None else None,
                scheduled_service=aero.scheduled_service,
            )
        )
    out.sort(key=lambda a: (a.distance_km, a.ident))
    return out[:k]
