"""Flight audit: where did a flown track go relative to the ground, its limits and airspace?

The flight is a FlightMD report or a plain position track (a dataset's KML ``gx:Track``, GPX
``trk`` or CSV track, resolved by the API). Both become the same internal track.

Algorithm (docs/SPEC.md §3 *Flight audit*):

1. **Parse.** A FlightMD report goes through
   :func:`~airbrief_core.integrations.flightmd.parse_flightmd_report` (schemas 1.0/1.2/1.5,
   nested ``"report"``; ``gps_path`` ``[lat, lon, alt]`` → ``[lon, lat, alt]``; altitudes AMSL).
   A position track goes through
   :func:`~airbrief_core.integrations.position_track.parse_position_track` (``[lon, lat, alt]``
   with optional times). The home point is the first fix. Distances along the track are
   cumulative geodesic (WGS84) distances; times are the track's own (seconds from its start).
2. **How heights are read.** FlightMD altitudes are AMSL. A track's ``track_heights`` (``auto``
   follows its altitude mode): ``amsl`` (``absolute``) — as FlightMD; ``above_ground``
   (``relativeToGround``) — the recorded value *is* the height above ground; ``above_takeoff``
   (``clampToGround`` or another mode) — heights above the take-off point (AMSL calibrated at
   take-off whatever ``calibration`` says). A 2D track (no altitudes at all) has no heights to
   audit: the profile shows 0 m, ``heights_assessed = False`` and an issue
   ``heights_not_assessed`` says so.
3. **Altitude reference** (the DEM is optional):

   * DEM available, ``use_terrain`` and ``calibration="takeoff"``: the altitude is shifted so
     that the aircraft sits on the terrain at home, ``offset = terrain(home) − alt(home)``, and
     ``AGL = alt + offset − terrain`` → ``terrain_calibrated`` (GPS altitude bias cancels out).
   * DEM available and ``calibration="none"``: ``AGL = alt − terrain`` → ``terrain_raw``.
   * No DEM (or ``use_terrain=False``, or the track is outside the DEM): ``AGL = alt − alt(home)``
     → ``takeoff_relative``.
   * Heights recorded above ground: ``AGL = alt`` → ``recorded_agl`` (the DEM only adds the
     terrain to the profile).

   Terrain gaps (nodata) along the track are filled by linear interpolation along the track.
4. **Limits.** The rules are the run's ``zone_profile`` (DGCA 2021 bands from the airport
   perimeter by default, as in the airspace check; ``zone_rules`` makes it ``custom``). Each
   point's limit is ``min(ceiling_agl_m, zone ceiling)`` where the zone comes from
   :func:`~airbrief_core.analyses.zones.classify_points` (aerodrome bands, user zones and
   data-pack zones) at the ground, so a band's free-flight ceiling (60 m in the DGCA 8–12 km band)
   is a limit. A zone with vertical limits applies only at the fixes where the flight was within
   them (``reach_agl``: a fix's own height or a neighbour's, the ends of the segments it is on, as
   the zone hits count): flown under a zone that starts at 200 m, the open-airspace ceiling is the
   limit. Zones without a free-flight ceiling (red, permission required, the DGCA 0–5 km band
   to verify on Digital Sky) keep the flight ceiling as the limit and are reported through
   ``zones`` and the verdict instead.
   Where a zone ceiling (or the default ceiling outside mapped zones) is below the requested
   ceiling, an info issue says so (``limit_capped_default`` / ``limit_capped_zone``), and the
   profile's ``limit_agl_m`` carries the limit actually used at each point.
   With ``band_ceiling = "permission"`` a ceiling with a yellow zone above it (the DGCA 8–12 km
   band's 60 m, open airspace's 120 m) is no limit: the points are classified again with those
   bands raised above the flight ceiling, so the hard limits under them still apply, and the runs
   above such a ceiling are ``above_band_ceiling`` (flown in the yellow zone, permission
   required) instead of exceedances. A red zone above a ceiling stays a limit.
5. **Runs beyond a limit.** Height exceedances are runs of consecutive points with
   ``AGL > limit``. Each run starts and ends at the linear crossing of ``excess = 0`` between
   neighbouring fixes, ``f = −e₀ / (e₁ − e₀)``, applied to the distance along the track, the time
   and the position (0 / the end when the run touches an end of the track). With times,
   ``duration = t_end − t_start`` and ``time_above_limit_s`` is their sum.
6. **Training area** (``training_box``, optional): ``excess`` is the signed horizontal distance to
   the area's boundary (positive outside, negative inside; local azimuthal-equidistant frame), so
   a run is a "left the area" segment; ``peak_m`` is the farthest point outside.
7. **Distance from take-off** (``max_distance_from_home_m``, optional, VLOS guidance):
   ``excess = geodesic distance from home − limit``. ``max_home_distance_m`` is always reported.
8. **Zones** touched by the path: :func:`~airbrief_core.analyses.zones.zone_hits_by_activity`
   with the track's fixes, so each band and zone is judged at the highest height flown inside it:
   the highest of its fixes inside (the same ones ``band_evidence`` counts) and of both ends of
   every track segment that touches it, so where the fixes fall never hides a crossing. A band
   flown above its ceiling is the zone above it; zones whose vertical limits the flight never
   reached inside them are left out, with an issue ``zones_inactive``.
   **Bands**: :func:`~airbrief_core.analyses.zones.band_evidence` over the track's fixes (the
   highest height flown in each band and its margin to the band's ceiling). The ``zones`` layer
   holds the bands near the track and the zones it touched, as in the airspace check.
   **Nearest aerodrome**: :func:`~airbrief_core.analyses.zones.nearest_aerodromes`.
   **Coverage**: without a data pack, without its ``aerodromes`` layer, or for fixes outside the
   pack's bounds, aerodrome zones were not checked: ``assessment_complete = False`` and an issue
   ``not_assessed``. The result never implies "clear" there.
9. **Population overflown** (when the data pack has a population layer): the path buffered by
   ``footprint_buffer_m`` in a local frame, integrated with ``RasterSampler.integrate_density``.
10. **FlightMD findings** (schema 1.6+, ``findings``): each finding's ``armed`` says whether its
    time window overlaps one of the report's armed windows (None without armed windows). With
    per-point GPS times (``metadata.gps_path_t_s``, 1.7) a finding is placed where it started:
    the fixes either side of ``t_start_s`` give the position, distance and AGL by linear
    interpolation in time, and the nearer fix gives the limit and the zone. Without GPS times
    the findings are listed only (``no_track_times``, issue ``findings_not_placed``); a start
    outside the GPS track's time span is ``outside_track``. FlightMD's ``parse_warnings`` become
    ``flightmd_parse_warning`` issues.

Verdict: ``fail`` on any height exceedance, a red zone touched, a training-area exit or a
distance exceedance; ``caution`` on a yellow zone that needs permission (``above_band_ceiling``
included), when the airspace was not (fully) assessed or when a 2D track left the heights
unassessed; otherwise ``pass``. It is evidence about the recorded track, not a compliance finding.

Layers: ``path`` (LineStrings split where the AGL band changes; ``agl_band`` is ``ok`` below
``limit − 10 m``, ``near`` up to the limit and ``over`` above it), ``exceedances`` (the
interpolated portions above the limit) and ``home`` (the take-off point); with the optional
checks also ``training_box`` + ``area_exits`` and ``home_radius`` + ``distance_exceedances``;
``above_band_ceiling`` with ``band_ceiling = "permission"``; ``findings`` (points) when a FlightMD
finding could be placed; ``zones`` when there are bands or zones near the track.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from ..data.datapack import DataPack
from ..data.raster import RasterSampler
from ..errors import AnalysisError
from ..geo.frame import LocalFrame
from ..geo.geodesic import GEOD, geodesic_area_perimeter
from ..geo.sampling import grid_points
from ..geo.transform import geojson_to_geometry, geometry_to_geojson
from ..geo.validity import polygonal_part, repair
from ..integrations.flightmd import FlightTrack, parse_flightmd_report
from ..integrations.position_track import parse_position_track
from ..models.analyses import AuditExceedance, AuditFinding, AuditSegment, FlightAuditParams, FlightAuditResult
from ..models.common import BBox, GeoJSON, Issue, NearestAerodrome, ProfilePoint, Severity, Verdict, ZoneRing, ZoneRules
from ..models.presets import DIGITAL_SKY_URL, DISCLAIMER_AIRSPACE, profile_rules
from .base import AnalysisContext
from .heights import dem_info, index_runs
from .zones import (
    ZoneState,
    band_evidence,
    classify_points,
    nearest_aerodromes,
    ring_polygons,
    touched_polygon_zones,
    zone_hits_by_activity,
)

__all__ = ["NEAR_MARGIN_M", "PROFILE_MAX_POINTS", "run"]

NEAR_MARGIN_M = 10.0
"""A segment within this many metres below its limit is in the ``near`` band."""

PROFILE_MAX_POINTS = 2_000
"""Largest number of profile points returned (longer tracks are decimated, keeping maxima)."""

_POPULATION_STEP_M = 50.0
_POPULATION_MAX_POINTS = 200_000
_HOME_RING_VERTICES = 72

AltitudeReference = Literal["terrain_calibrated", "terrain_raw", "takeoff_relative", "recorded_agl"]
HeightsMode = Literal["amsl", "above_ground", "above_takeoff"]
Band = Literal["ok", "near", "over"]


def _r(value: float | None, decimals: int = 2) -> float | None:
    if value is None or not np.isfinite(value):
        return None
    out = round(float(value), decimals)
    return 0.0 if out == 0.0 else out


def _rf(value: float, decimals: int = 2) -> float:
    out = round(float(value), decimals)
    return 0.0 if out == 0.0 else out


def _feature(geometry: GeoJSON, properties: dict[str, Any]) -> GeoJSON:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _collection(features: list[GeoJSON]) -> GeoJSON:
    return {"type": "FeatureCollection", "features": features}


def _cumulative_distance(lons: NDArray[np.float64], lats: NDArray[np.float64]) -> NDArray[np.float64]:
    _, _, seg = GEOD.inv(lons[:-1], lats[:-1], lons[1:], lats[1:])
    return np.concatenate([[0.0], np.cumsum(np.asarray(seg, dtype=np.float64))])


def _fill_gaps(values: NDArray[np.float64], distance: NDArray[np.float64]) -> NDArray[np.float64]:
    """Linearly interpolate NaNs along the track (ends hold the nearest valid value)."""
    valid = np.isfinite(values)
    if valid.all() or not valid.any():
        return values
    return np.interp(distance, distance[valid], values[valid])


def _runs(mask: NDArray[np.bool_]) -> list[tuple[int, int]]:
    return index_runs(mask)


def _fraction(e0: float, e1: float) -> float:
    """Where ``excess`` crosses zero between two fixes, as a fraction of the step (linear)."""
    denom = e1 - e0
    if denom == 0.0:
        return 1.0
    return min(1.0, max(0.0, -e0 / denom))


def _coord(lon: float, lat: float, alt: float) -> list[float]:
    return [_rf(lon, 8), _rf(lat, 8), _rf(alt, 2)]


@dataclass
class _Track:
    """The flight as arrays, shared by every check."""

    lons: NDArray[np.float64]
    lats: NDArray[np.float64]
    alts: NDArray[np.float64]
    distance: NDArray[np.float64]
    times: NDArray[np.float64] | None

    @property
    def n(self) -> int:
        return int(self.lons.size)

    def at(self, i0: int, i1: int, f: float) -> tuple[float, float | None, list[float]]:
        """Distance, time and ``[lon, lat, alt]`` a fraction ``f`` of the way from fix i0 to i1."""
        def lerp(values: NDArray[np.float64]) -> float:
            return float(values[i0] + f * (values[i1] - values[i0]))

        time = lerp(self.times) if self.times is not None else None
        return lerp(self.distance), time, _coord(lerp(self.lons), lerp(self.lats), lerp(self.alts))


@dataclass
class _Run:
    """A run of fixes with ``excess > 0`` and its interpolated ends."""

    start: int
    end: int
    d_start: float
    d_end: float
    t_start: float | None
    t_end: float | None
    coords: list[list[float]]

    @property
    def duration(self) -> float | None:
        if self.t_start is None or self.t_end is None:
            return None
        return self.t_end - self.t_start


def _excess_runs(track: _Track, excess: NDArray[np.float64]) -> list[_Run]:
    """Runs where ``excess > 0``, each from crossing to crossing (see step 5 of the docstring)."""
    out: list[_Run] = []
    last = track.n - 1
    for start, end in _runs(excess > 0.0):
        if start == 0:
            d0, t0, p0 = track.at(0, 0, 0.0)
        else:
            d0, t0, p0 = track.at(start - 1, start, _fraction(float(excess[start - 1]), float(excess[start])))
        if end == last:
            d1, t1, p1 = track.at(last, last, 0.0)
        else:
            d1, t1, p1 = track.at(end, end + 1, _fraction(float(excess[end]), float(excess[end + 1])))
        coords = [p0, *(_coord(track.lons[k], track.lats[k], track.alts[k]) for k in range(start, end + 1)), p1]
        out.append(_Run(start=start, end=end, d_start=d0, d_end=d1, t_start=t0, t_end=t1, coords=coords))
    return out


def _times_fields(run: _Run) -> dict[str, float | None]:
    duration = run.duration
    return {
        "start_time_s": _r(run.t_start, 3),
        "end_time_s": _r(run.t_end, 3),
        "duration_s": _r(duration, 3) if duration is not None else None,
    }


def _profile_indices(n: int, agl: NDArray[np.float64]) -> NDArray[np.int64]:
    """Indices kept in the profile: all, or an even decimation plus each bucket's maximum."""
    if n <= PROFILE_MAX_POINTS:
        return np.arange(n, dtype=np.int64)
    buckets = PROFILE_MAX_POINTS // 2
    edges = np.linspace(0, n, buckets + 1).astype(np.int64)
    keep: list[int] = [0, n - 1]
    for a, b in zip(edges[:-1].tolist(), edges[1:].tolist(), strict=True):
        if b <= a:
            continue
        keep.append(a)
        segment = agl[a:b]
        if np.isfinite(segment).any():
            keep.append(a + int(np.nanargmax(segment)))
    return np.unique(np.asarray(keep, dtype=np.int64))


def _band(max_agl: float, limit: float) -> Band:
    if max_agl > limit:
        return "over"
    if max_agl >= limit - NEAR_MARGIN_M:
        return "near"
    return "ok"


def _limit_reason(states: list[ZoneState], index: int, ceiling: float) -> str:
    state = states[index]
    zone_ceiling = state.ceiling_agl_m
    if zone_ceiling is not None and zone_ceiling < ceiling:
        if state.source == "default":
            return f"Above the {zone_ceiling:g} m default ceiling outside mapped zones"
        where = f"{state.name} ({state.label})" if state.label and state.label != state.name else state.name
        return f"Above the {zone_ceiling:g} m ceiling of {where}"
    return f"Above the {ceiling:g} m height limit"


def _capped_limit_issues(states: list[ZoneState], ceiling: float) -> list[Issue]:
    """Where zone ceilings undercut the requested one, so the result never shows it as applied."""
    default: list[float] = []
    zoned: dict[str, float] = {}
    for s in states:
        if s.ceiling_agl_m is None or s.ceiling_agl_m >= ceiling:
            continue
        if s.source == "default":
            default.append(float(s.ceiling_agl_m))
        else:
            name = s.name or s.label
            zoned[name] = min(zoned.get(name, float("inf")), float(s.ceiling_agl_m))
    issues: list[Issue] = []
    if default:
        value = min(default)
        where = "along the whole flight" if len(default) == len(states) else "outside mapped zones"
        issues.append(
            Issue(
                code="limit_capped_default",
                message=(
                    f"Your {ceiling:g} m height limit is above the {value:g} m default ceiling outside mapped zones, "
                    f"so the audit used {value:g} m {where}."
                ),
                severity=Severity.INFO,
            )
        )
    if zoned:
        names = ", ".join(f"{name} ({value:g} m)" for name, value in sorted(zoned.items(), key=lambda kv: (kv[1], kv[0])))
        issues.append(
            Issue(
                code="limit_capped_zone",
                message=f"Zone ceilings below your {ceiling:g} m height limit were used inside {names}.",
                severity=Severity.INFO,
            )
        )
    return issues


def _raised_band_ceilings(rules: ZoneRules, ceiling: float) -> ZoneRules:
    """The rules with every yellow-above band ceiling raised to at least the flight ceiling, so
    such a band never wins over a zone with a lower ceiling where they overlap."""
    def ring(r: ZoneRing) -> ZoneRing:
        if r.ceiling_exceeded_zone != "yellow" or r.ceiling_agl_m is None:
            return r
        return r.model_copy(update={"ceiling_agl_m": max(float(r.ceiling_agl_m), ceiling)})

    raised = [rule.model_copy(update={"rings": [ring(r) for r in rule.rings]}) for rule in rules.rules]
    return rules.model_copy(update={"rules": raised})


def _segment_reach(agl: NDArray[np.float64]) -> NDArray[np.float64]:
    """Per fix, the highest of its own height and its neighbours': the ends of the segments it is
    on, as the zone hits count them. A fix whose own height is missing stays NaN (unknown: every
    zone counts there); a neighbour's NaN never hides a known height."""
    reach = agl.copy()
    reach[:-1] = np.fmax(reach[:-1], agl[1:])
    reach[1:] = np.fmax(reach[1:], agl[:-1])
    reach[np.isnan(agl)] = np.nan
    return reach


def _yellow_ceiling(state: ZoneState) -> float | None:
    """The ceiling above which the state's band (or open airspace) is yellow, else None."""
    return state.ceiling_agl_m if state.ceiling_exceeded_zone == "yellow" else None


def _heights_mode(params: FlightAuditParams, flight: FlightTrack, issues: list[Issue]) -> HeightsMode:
    """How the track's altitudes are read (step 2 of the module docstring)."""
    if params.track is None:
        return "amsl"
    if not flight.has_heights:
        return "above_takeoff"
    if params.track_heights != "auto":
        return params.track_heights
    mode = (flight.altitude_mode or "").strip()
    if mode == "absolute":
        return "amsl"
    if mode in ("relativeToGround", "relativeToSeaFloor"):
        return "above_ground"
    issues.append(
        Issue(
            code="track_heights_assumed",
            message=(
                f"The track's altitude mode is {mode or 'not stated'}, which does not say what its altitudes are "
                "measured from: they were read as heights above the take-off point. Choose how heights are read if "
                "that is wrong."
            ),
            severity=Severity.WARNING,
        )
    )
    return "above_takeoff"


def _coverage_gap(pack: DataPack | None, lons: NDArray[np.float64], lats: NDArray[np.float64]) -> str | None:
    """Why aerodrome zones were not checked along the flight, or None when they were everywhere."""
    if pack is None:
        return "Airspace not assessed: no data pack covers this flight — aerodrome zones were not checked."
    if "aerodromes" not in pack.layers:
        return f"Airspace not assessed: data pack '{pack.name}' has no aerodrome layer — aerodrome zones were not checked."
    b = pack.bbox
    inside = (lons >= b.min_lon) & (lons <= b.max_lon) & (lats >= b.min_lat) & (lats <= b.max_lat)
    if not inside.any():
        return f"Airspace not assessed: the flight lies outside data pack '{pack.name}' — aerodrome zones were not checked."
    if not inside.all():
        outside = int((~inside).sum())
        return (
            f"Airspace only partly assessed: {outside} of {inside.size} track points lie outside data pack "
            f"'{pack.name}' — aerodrome zones were not checked there."
        )
    return None


def _area_polygon(value: GeoJSON) -> BaseGeometry:
    """The training area as a 2D polygon (a closed line is accepted as its outline)."""
    geom = geojson_to_geometry(value)
    if geom.is_empty:
        raise AnalysisError("invalid_geometry", "The training area is empty", details={"parameter": "training_box"})
    geom = shapely.force_2d(geom)
    if isinstance(geom, LineString) and geom.is_closed and len(geom.coords) >= 4:
        geom = Polygon(geom.coords)
    repaired, _ = repair(geom)
    poly = polygonal_part(repaired) if repaired is not None else None
    if poly is None or poly.is_empty:
        raise AnalysisError(
            "geometry_type_not_supported",
            "The training area must be a polygon (or a closed line)",
            details={"parameter": "training_box", "geometry_type": geom.geom_type},
        )
    return poly


def _home_ring(lon: float, lat: float, radius_m: float) -> GeoJSON:
    azimuths = np.linspace(0.0, 360.0, _HOME_RING_VERTICES, endpoint=False)
    ring_lons, ring_lats, _ = GEOD.fwd(np.full(azimuths.size, lon), np.full(azimuths.size, lat), azimuths, np.full(azimuths.size, radius_m))
    ring = [[_rf(x, 8), _rf(y, 8)] for x, y in zip(np.asarray(ring_lons).tolist(), np.asarray(ring_lats).tolist(), strict=True)]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def _segment(kind: Literal["area_exit", "distance"], run: _Run, peak: float, limit: float) -> AuditSegment:
    return AuditSegment(
        kind=kind,
        start_index=run.start,
        end_index=run.end,
        start_distance_m=_rf(run.d_start),
        end_distance_m=_rf(run.d_end),
        length_m=_rf(run.d_end - run.d_start),
        peak_m=_rf(peak),
        limit_m=_rf(limit),
        **_times_fields(run),
    )


def _segment_feature(index: int, segment: AuditSegment, coords: list[list[float]]) -> GeoJSON:
    return _feature(
        {"type": "LineString", "coordinates": coords},
        {"index": index, **segment.model_dump(exclude={"start_index", "end_index"})},
    )


def _overlaps_armed(finding: AuditFinding, windows: list[list[float]] | None) -> bool | None:
    if windows is None or finding.t_start_s is None:
        return None
    start = finding.t_start_s
    end = finding.t_end_s if finding.t_end_s is not None else start
    return any(w0 <= end and start <= w1 for w0, w1 in windows)


def _place_findings(
    findings: list[AuditFinding],
    track: _Track,
    agl: NDArray[np.float64],
    limit: NDArray[np.float64],
    states: list[ZoneState],
    armed_windows: list[list[float]] | None,
) -> tuple[list[AuditFinding], list[GeoJSON]]:
    times = track.times
    placed: list[AuditFinding] = []
    features: list[GeoJSON] = []
    for finding in findings:
        armed = _overlaps_armed(finding, armed_windows)
        t = finding.t_start_s
        if t is None:
            placed.append(finding.model_copy(update={"armed": armed, "placement": "no_time"}))
            continue
        if times is None:
            placed.append(finding.model_copy(update={"armed": armed, "placement": "no_track_times"}))
            continue
        if t < times[0] or t > times[-1]:
            placed.append(finding.model_copy(update={"armed": armed, "placement": "outside_track"}))
            continue
        # fixes either side of t; equal times (several fixes in one second) give f = 0
        k = min(int(np.searchsorted(times, t, side="right")) - 1, times.size - 1)
        k1 = min(k + 1, times.size - 1)
        span = float(times[k1] - times[k])
        f = (t - float(times[k])) / span if span > 0 else 0.0
        near = k1 if f > 0.5 else k
        state = states[near]
        lon = _rf(track.lons[k] + f * (track.lons[k1] - track.lons[k]), 8)
        lat = _rf(track.lats[k] + f * (track.lats[k1] - track.lats[k]), 8)
        updated = finding.model_copy(
            update={
                "armed": armed,
                "placement": "placed",
                "lon": lon,
                "lat": lat,
                "distance_m": _rf(track.distance[k] + f * (track.distance[k1] - track.distance[k])),
                "agl_m": _rf(agl[k] + f * (agl[k1] - agl[k])),
                "limit_agl_m": _rf(limit[near]),
                "zone": state.zone,
                "zone_name": state.name,
            }
        )
        placed.append(updated)
        features.append(
            _feature(
                {"type": "Point", "coordinates": [lon, lat]},
                {"index": len(placed) - 1, **updated.model_dump(exclude={"lon", "lat", "recommendation"})},
            )
        )
    return placed, features


def run(params: FlightAuditParams, ctx: AnalysisContext) -> FlightAuditResult:
    """Audit a flown track against terrain, limits, zones and population (see above)."""
    issues: list[Issue] = list(ctx.issues)
    if params.track is not None:
        ctx.step("Reading position track", 0.02)
        flight = parse_position_track(params.track)
        note_code = "track_note"
    else:
        ctx.step("Reading FlightMD report", 0.02)
        flight = parse_flightmd_report(params.report, flightmd_url=params.flightmd_url)
        note_code = "flightmd_note"
    for note in flight.notes:
        issues.append(Issue(code=note_code, message=note, severity=Severity.INFO))
    for warning in flight.info.parse_warnings:
        issues.append(Issue(code="flightmd_parse_warning", message=f"FlightMD parser: {warning}", severity=Severity.WARNING))
    mode = _heights_mode(params, flight, issues)

    pts = np.asarray(flight.points, dtype=np.float64)
    lons = np.ascontiguousarray(pts[:, 0])
    lats = np.ascontiguousarray(pts[:, 1])
    alts = np.ascontiguousarray(pts[:, 2])
    n = lons.size
    home_lon, home_lat, home_alt = float(lons[0]), float(lats[0]), float(alts[0])
    distance = _cumulative_distance(lons, lats)
    total_distance = float(distance[-1])
    times = np.asarray(flight.times, dtype=np.float64) if flight.times is not None else None
    track = _Track(lons=lons, lats=lats, alts=alts, distance=distance, times=times)
    path_geom: BaseGeometry = LineString(np.column_stack([lons, lats])) if total_distance > 0 else Point(home_lon, home_lat)
    pack = ctx.datapack
    rules = profile_rules(params.zone_profile, params.zone_rules)

    ctx.step("Terrain", 0.15)
    reference: AltitudeReference = "recorded_agl" if mode == "above_ground" else "takeoff_relative"
    offset: float | None = None
    terrain: NDArray[np.float64] | None = None
    dem: RasterSampler | None = None
    heights_assessed = flight.has_heights
    fallback = (
        "the profile has no terrain"
        if mode == "above_ground" or not heights_assessed
        else "heights are relative to the take-off point"
    )
    if params.use_terrain and pack is not None and ctx.has_layer("dem"):
        dem = pack.dem()
    elif params.use_terrain and mode != "above_ground" and heights_assessed:
        issues.append(
            Issue(
                code="no_dem",
                message="No elevation model available: heights are relative to the take-off point.",
                severity=Severity.INFO,
            )
        )
    if dem is not None:
        sampled = np.asarray(dem.sample(lons, lats), dtype=np.float64)
        if not np.isfinite(sampled).any():
            issues.append(
                Issue(
                    code="out_of_coverage",
                    message=f"The flight is outside the elevation model of data pack '{pack.name if pack else ''}': {fallback}.",
                    severity=Severity.WARNING,
                )
            )
        else:
            missing = int((~np.isfinite(sampled)).sum())
            if missing:
                issues.append(
                    Issue(
                        code="terrain_gaps",
                        message=f"{missing} of {n} track points have no terrain value; they were interpolated along the track.",
                        severity=Severity.WARNING,
                    )
                )
            terrain = _fill_gaps(sampled, distance)
    calibration = "takeoff" if mode == "above_takeoff" else params.calibration
    altitude_amsl: NDArray[np.float64] | None
    if not heights_assessed:
        # A 2D track: nothing says how high it flew. Heights stay 0 m (no exceedance can be found)
        # and the result says the height checks were not assessed; the verdict is never "pass".
        agl = np.zeros(n, dtype=np.float64)
        altitude_amsl = None
        height_above_takeoff = agl.copy()
        issues.append(
            Issue(
                code="heights_not_assessed",
                message=(
                    "Heights not assessed: the track has no altitudes, so the height above ground and the height "
                    "limit could not be checked (the profile shows 0 m). Use a track with altitudes (GPX <ele>, a CSV "
                    "height column) to audit heights."
                ),
                severity=Severity.WARNING,
            )
        )
    elif mode == "above_ground":
        agl = alts.copy()
        altitude_amsl = terrain + alts if terrain is not None else None
        height_above_takeoff = altitude_amsl - altitude_amsl[0] if altitude_amsl is not None else alts - home_alt
    elif terrain is not None:
        if calibration == "takeoff":
            offset = float(terrain[0]) - home_alt
            altitude_amsl = alts + offset
            reference = "terrain_calibrated"
        else:
            altitude_amsl = alts.copy()
            reference = "terrain_raw"
        agl = altitude_amsl - terrain
        height_above_takeoff = alts - home_alt
    else:
        # FlightMD altitudes are AMSL (shown as such); relative track heights have no AMSL value.
        altitude_amsl = alts.copy() if mode == "amsl" else None
        agl = alts - home_alt
        height_above_takeoff = agl.copy()
    ctx.check_cancelled()

    ctx.step("Airspace zones", 0.3)
    # the heights flown say which zones' vertical bands the flight was in; band and open-airspace
    # ceilings stay the limits (a 2D track: every zone counts)
    reach = _segment_reach(agl) if heights_assessed else None
    states = classify_points(
        lons,
        lats,
        pack=pack,
        rules=rules,
        user_zones=params.user_zones,
        include_datapack_zones=params.include_datapack_zones,
        reach_agl=reach,
    )
    ceiling = float(params.ceiling_agl_m)
    limit_states = states
    if params.band_ceiling == "permission":
        free = classify_points(
            lons,
            lats,
            pack=pack,
            rules=_raised_band_ceilings(rules, ceiling),
            user_zones=params.user_zones,
            include_datapack_zones=params.include_datapack_zones,
            reach_agl=reach,
        )
        limit_states = [replace(s, ceiling_agl_m=None) if _yellow_ceiling(s) is not None else s for s in free]
    zone_ceiling = np.asarray(
        [s.ceiling_agl_m if s.ceiling_agl_m is not None else np.inf for s in limit_states], dtype=np.float64
    )
    limit = np.minimum(ceiling, zone_ceiling)
    issues.extend(_capped_limit_issues(limit_states, ceiling))
    ctx.check_cancelled()
    zones, unreached = zone_hits_by_activity(
        path_geom,
        pack=pack,
        rules=rules,
        user_zones=params.user_zones,
        include_datapack_zones=params.include_datapack_zones,
        fixes=(lons, lats, agl) if heights_assessed else None,
    )
    if unreached:
        names = ", ".join(sorted({z.name for z in unreached}))
        issues.append(
            Issue(
                code="zones_inactive",
                message=f"Not counted, the flight never reached their heights: {names}.",
                severity=Severity.INFO,
            )
        )
    aerodrome_bands = band_evidence(lons, lats, agl if heights_assessed else None, pack=pack, rules=rules, sample_kind="track")
    gap = _coverage_gap(pack, lons, lats)
    if gap is not None:
        issues.append(
            Issue(
                code="not_assessed",
                message=f"{gap} Treat the flight as unverified there and check it on DGCA Digital Sky ({DIGITAL_SKY_URL}).",
                severity=Severity.WARNING,
            )
        )
    ctx.check_cancelled()

    ctx.step("Exceedances", 0.45)
    excess = agl - limit
    exceedances: list[AuditExceedance] = []
    exceedance_features: list[GeoJSON] = []
    for run_ in _excess_runs(track, excess):
        segment_agl = agl[run_.start : run_.end + 1]
        peak = run_.start + int(np.argmax(segment_agl))
        run_limit = float(limit[run_.start : run_.end + 1].min())
        reason = _limit_reason(limit_states, peak, ceiling)
        exceedance = AuditExceedance(
            start_index=int(run_.start),
            end_index=int(run_.end),
            start_distance_m=_rf(run_.d_start),
            end_distance_m=_rf(run_.d_end),
            length_m=_rf(run_.d_end - run_.d_start),
            max_agl_m=_rf(np.max(segment_agl)),
            limit_m=_rf(run_limit),
            reason=reason,
            **_times_fields(run_),
        )
        exceedances.append(exceedance)
        exceedance_features.append(
            _feature(
                {"type": "LineString", "coordinates": run_.coords},
                {"index": len(exceedances) - 1, **exceedance.model_dump(exclude={"start_index", "end_index"})},
            )
        )
    above_band: list[AuditExceedance] | None = None
    above_features: list[GeoJSON] = []
    if params.band_ceiling == "permission":
        above_band = []
        top = np.asarray([_yellow_ceiling(s) for s in states], dtype=np.float64)  # None -> NaN
        # outside those bands: far below, so a run starts and ends at fixes inside one
        for run_ in _excess_runs(track, np.where(np.isfinite(top), agl - top, -1e6)):
            peak = run_.start + int(np.argmax(agl[run_.start : run_.end + 1]))
            state = states[peak]
            where = "outside mapped zones" if state.source == "default" else f"of {state.name} ({state.label})"
            above = AuditExceedance(
                start_index=int(run_.start),
                end_index=int(run_.end),
                start_distance_m=_rf(run_.d_start),
                end_distance_m=_rf(run_.d_end),
                length_m=_rf(run_.d_end - run_.d_start),
                max_agl_m=_rf(np.max(agl[run_.start : run_.end + 1])),
                limit_m=_rf(np.nanmin(top[run_.start : run_.end + 1])),
                reason=f"Flown in yellow airspace above the {top[peak]:g} m ceiling {where}: permission required",
                **_times_fields(run_),
            )
            above_band.append(above)
            above_features.append(
                _feature(
                    {"type": "LineString", "coordinates": run_.coords},
                    {"index": len(above_band) - 1, **above.model_dump(exclude={"start_index", "end_index"})},
                )
            )
        if above_band:
            issues.append(
                Issue(
                    code="above_band_ceiling",
                    message=(
                        f"{len(above_band)} segment{'s' if len(above_band) != 1 else ''} above a band's free-flight ceiling "
                        "counted as flight in the yellow zone above it, permission required, not as exceedances "
                        "(band_ceiling: permission). Check that the permission was held."
                    ),
                    severity=Severity.WARNING,
                )
            )
    ctx.check_cancelled()

    ctx.step("Area and distance checks", 0.55)
    _, _, home_dist_raw = GEOD.inv(np.full(n, home_lon), np.full(n, home_lat), lons, lats)
    home_dist = np.asarray(home_dist_raw, dtype=np.float64)
    extra_layers: dict[str, GeoJSON] = {}
    area_exits: list[AuditSegment] | None = None
    box_name: str | None = None
    if params.training_box is not None:
        box_wgs = _area_polygon(params.training_box)
        box_name = (params.training_box_name or "").strip() or "Training box"
        frame = LocalFrame.for_geometry(shapely.union(box_wgs, path_geom))
        box_local = frame.to_local(box_wgs)
        x, y = frame.xy(lons, lats)
        local_pts = shapely.points(np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))
        outside_dist = shapely.distance(local_pts, box_local)
        inside_dist = shapely.distance(local_pts, box_local.boundary)
        signed = np.where(outside_dist > 0.0, outside_dist, -inside_dist).astype(np.float64)
        area_exits = []
        exit_features: list[GeoJSON] = []
        for run_ in _excess_runs(track, signed):
            segment = _segment("area_exit", run_, float(signed[run_.start : run_.end + 1].max()), 0.0)
            area_exits.append(segment)
            exit_features.append(_segment_feature(len(area_exits) - 1, segment, run_.coords))
        extra_layers["training_box"] = _collection(
            [_feature(geometry_to_geojson(box_wgs, decimals=8), {"name": box_name, "role": "training_box"})]
        )
        extra_layers["area_exits"] = _collection(exit_features)
    distance_exceedances: list[AuditSegment] | None = None
    max_home = params.max_distance_from_home_m
    if max_home is not None:
        distance_exceedances = []
        far_features: list[GeoJSON] = []
        for run_ in _excess_runs(track, home_dist - float(max_home)):
            segment = _segment("distance", run_, float(home_dist[run_.start : run_.end + 1].max()), float(max_home))
            distance_exceedances.append(segment)
            far_features.append(_segment_feature(len(distance_exceedances) - 1, segment, run_.coords))
        extra_layers["home_radius"] = _collection(
            [_feature(_home_ring(home_lon, home_lat, float(max_home)), {"name": "Maximum distance from take-off", "radius_m": float(max_home)})]
        )
        extra_layers["distance_exceedances"] = _collection(far_features)
    ctx.check_cancelled()

    ctx.step("Aerodromes", 0.6)
    nearest: NearestAerodrome | None = None
    nearest_list = nearest_aerodromes(path_geom, pack, k=1, rules=rules)
    if nearest_list:
        nearest = nearest_list[0]

    ctx.step("Population overflown", 0.7)
    population: float | None = None
    max_density: float | None = None
    footprint_km2: float | None = None
    pop = pack.population() if pack is not None and ctx.has_layer("population") else None
    if pop is not None:
        frame = LocalFrame.for_geometry(path_geom)
        local_path = frame.to_local(path_geom)
        radius = max(float(params.footprint_buffer_m), 1.0)
        footprint_wgs = shapely.force_2d(frame.to_wgs84(local_path.buffer(radius, quad_segs=8)))
        footprint_km2 = float(geodesic_area_perimeter(footprint_wgs)[0]) / 1e6
        population = float(pop.integrate_density(footprint_wgs, step_m=_POPULATION_STEP_M, max_points=_POPULATION_MAX_POINTS))
        g_lon, g_lat, _ = grid_points(footprint_wgs, _POPULATION_STEP_M, _POPULATION_MAX_POINTS)
        density = np.concatenate(
            [np.asarray(pop.density_per_km2(g_lon, g_lat), dtype=np.float64), np.asarray(pop.density_per_km2(lons, lats), dtype=np.float64)]
        )
        finite_density = density[np.isfinite(density)]
        max_density = float(finite_density.max()) if finite_density.size else None
    else:
        issues.append(
            Issue(
                code="no_population",
                message="No population layer available: population overflown was not estimated.",
                severity=Severity.INFO,
            )
        )
    ctx.check_cancelled()

    ctx.step("Profile", 0.8)
    profile: list[ProfilePoint] = []
    for k in _profile_indices(n, agl).tolist():
        profile.append(
            ProfilePoint(
                distance_m=_rf(distance[k]),
                lon=_rf(lons[k], 8),
                lat=_rf(lats[k], 8),
                terrain_m=_r(terrain[k]) if terrain is not None else None,
                altitude_amsl_m=_r(altitude_amsl[k]) if altitude_amsl is not None else None,
                agl_m=_rf(agl[k]),
                limit_agl_m=_rf(limit[k]),
            )
        )

    findings, finding_features = _place_findings(flight.findings, track, agl, limit, states, flight.info.armed_windows)
    unplaced = sum(f.placement == "no_track_times" for f in findings)
    if unplaced:
        issues.append(
            Issue(
                code="findings_not_placed",
                message=(
                    f"{unplaced} FlightMD finding{'s have' if unplaced != 1 else ' has'} a time but the report has no "
                    "time per GPS point (metadata.gps_path_t_s, FlightMD 1.7), so they are listed but not placed on the map."
                ),
                severity=Severity.INFO,
            )
        )

    ctx.step("Layers", 0.9)
    path_features: list[GeoJSON] = []
    if n >= 2:
        seg_max = np.maximum(agl[:-1], agl[1:])
        seg_limit = np.minimum(limit[:-1], limit[1:])
        bands = [_band(float(a), float(b)) for a, b in zip(seg_max.tolist(), seg_limit.tolist(), strict=True)]
        start = 0
        for s in range(1, len(bands) + 1):
            if s < len(bands) and bands[s] == bands[start]:
                continue
            idx = range(start, s + 1)
            props: dict[str, Any] = {
                "agl_band": bands[start],
                "start_distance_m": _rf(distance[start]),
                "end_distance_m": _rf(distance[s]),
                "max_agl_m": _rf(seg_max[start:s].max()),
                "limit_m": _rf(seg_limit[start:s].min()),
            }
            if times is not None:
                props["start_time_s"] = _rf(times[start], 3)
                props["end_time_s"] = _rf(times[s], 3)
            path_features.append(
                _feature({"type": "LineString", "coordinates": [_coord(lons[k], lats[k], alts[k]) for k in idx]}, props)
            )
            start = s
    home_features = [
        _feature(
            {"type": "Point", "coordinates": _coord(home_lon, home_lat, home_alt)},
            {
                "name": "Home (take-off)",
                "altitude_amsl_m": _r(altitude_amsl[0]) if altitude_amsl is not None else None,
                "terrain_m": _r(terrain[0]) if terrain is not None else None,
            },
        )
    ]
    layers: dict[str, GeoJSON] = {
        "path": _collection(path_features),
        "exceedances": _collection(exceedance_features),
        "home": _collection(home_features),
        **extra_layers,
    }
    if above_band is not None:
        layers["above_band_ceiling"] = _collection(above_features)
    if finding_features:
        layers["findings"] = _collection(finding_features)
    # Last layer, so an export cut at its feature cap drops the bands before the flight.
    zone_features = ring_polygons(pack, rules, BBox.from_bounds(path_geom.bounds))["features"]
    zone_features += touched_polygon_zones(path_geom, pack=pack, rules=rules, user_zones=params.user_zones,
                                           include_datapack_zones=params.include_datapack_zones)["features"]
    if zone_features:
        layers["zones"] = _collection(zone_features)

    red = any(z.zone == "red" for z in zones)
    permission = any(z.zone == "yellow" and z.permission_required for z in zones)
    if exceedances or red or area_exits or distance_exceedances:
        verdict = Verdict.FAIL
    elif permission or above_band or gap is not None or not heights_assessed:
        # Unchecked aerodrome zones (or heights a 2D track cannot show) are never an all-clear.
        verdict = Verdict.CAUTION
    else:
        verdict = Verdict.PASS

    def total_time(segments: list[Any] | None) -> float | None:
        if times is None or segments is None:
            return None
        return _rf(sum(s.duration_s or 0.0 for s in segments), 3)

    ctx.step("Done", 1.0)
    return FlightAuditResult(
        verdict=verdict,
        flight=flight.info,
        altitude_reference=reference,
        calibration_offset_m=_r(offset),
        distance_m=_rf(total_distance),
        max_agl_m=_rf(np.max(agl)),
        max_height_above_takeoff_m=_rf(np.max(height_above_takeoff)),
        ceiling_agl_m=ceiling,
        profile=profile,
        exceedances=exceedances,
        zones=zones,
        nearest_aerodrome=nearest,
        min_aerodrome_distance_km=_r(nearest.distance_km, 3) if nearest is not None else None,
        overflown_population=_r(population, 1),
        max_density_per_km2=_r(max_density, 1),
        footprint_area_km2=_r(footprint_km2, 4),
        disclaimer=DISCLAIMER_AIRSPACE,
        issues=issues,
        layers=layers,
        assessment_complete=gap is None,
        heights_assessed=heights_assessed,
        duration_s=_r(float(times[-1] - times[0]), 3) if times is not None else None,
        time_above_limit_s=total_time(exceedances),
        max_home_distance_m=_rf(float(home_dist.max())),
        training_box_name=box_name,
        area_exits=area_exits,
        time_outside_area_s=total_time(area_exits),
        max_distance_from_home_m=float(max_home) if max_home is not None else None,
        distance_exceedances=distance_exceedances,
        time_beyond_distance_s=total_time(distance_exceedances),
        dem=dem_info(pack) if terrain is not None else None,
        zone_profile=params.zone_profile,
        band_evidence=aerodrome_bands,
        band_ceiling=params.band_ceiling,
        above_band_ceiling=above_band,
        findings=findings,
    )
