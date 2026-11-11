"""Drone-show validator: separation conflicts, dynamic limits, geofence and site checks.

Algorithm (docs/SPEC.md §3 *Shows*):

1. **Local frame.** Every track is projected to east/north metres with
   :class:`~airbrief_core.geo.frame.LocalFrame` centred on ``origin`` (lon, lat); up is
   ``z = alt`` for ``altitude_reference="relative"`` or ``alt − origin_alt`` for ``"absolute"``.
   Without an origin, the mean of the drones' first positions is used (``origin_source=
   "inferred"``); its altitude is 0, or for ``"absolute"`` the lowest first altitude (the take-off
   pads). An origin given as ``[lon, lat]`` without a ground altitude gets the same altitude. The
   API fills ``origin`` and ``altitude_reference`` from tracks stored with absolute altitudes; the
   result echoes what was used.
2. **Resampling.** All drones are linearly interpolated onto one grid at ``sample_rate_hz`` over
   ``[min t, max t]`` (the tracks share one time base). Before its first sample or after its last,
   a drone holds that position. Duplicate timestamps keep the last position. Very large shows
   (more than :data:`MAX_DRONE_FRAMES` drone-frames) are checked at a lower rate, with a warning.
3. **Airborne** means ``z > airborne_threshold_m``. Only airborne drones take part in the
   separation and geofence checks.
4. **Separation.** :func:`airbrief_core._native.pairs_within` lists every airborne pair closer
   than ``min_separation_m`` (strict) per frame; consecutive frames of the same pair form one
   :class:`ShowConflict` carrying the time and distance of closest approach and the ENU midpoint
   of the pair there. :func:`~airbrief_core._native.min_separation_frames` gives the overall
   minimum separation (and the pair and time where it occurs).
5. **Dynamics.** Windowed central differences over ``dynamics_window_s``:
   ``v(t) = (p(t + w/2) − p(t − w/2)) / w`` and the same applied to ``v`` for acceleration (at
   the ends of the show the window is one-sided). Horizontal speed, climb rate, descent rate and
   3D acceleration magnitude are compared to their limits; consecutive frames over a limit form
   one :class:`ShowViolation` with the peak value.
   Optional checks: with ``endurance_s``, a drone whose total airborne time exceeds it is an
   ``endurance`` violation (from the moment the limit is passed to its last airborne frame; peak
   = airborne time); with ``min_navigation_altitude_m``, horizontal speed above
   ``low_altitude_speed_tolerance_ms`` while ``z`` is below that altitude is a
   ``low_altitude_motion`` violation (drones should climb vertically before they move).
6. **Show box.** The polygon given (``show_box_source="given"``) or, when there is none, the
   axis-aligned east/north rectangle around every track position widened by
   ``show_box_margin_m`` (``"derived"``, labelled "derived from the tracks"). An airborne drone
   outside a given box (2D) is a ``geofence`` violation (peak = the largest distance outside,
   limit 0); a derived box holds every position by construction, so it is not a geofence.
7. **Audience distance** (:class:`~airbrief_core.models.tracks.ShowAudienceBuffer`):
   ``audience_buffer_rule`` ``fixed`` uses ``audience_buffer_m``; ``one_to_one`` the show's
   greatest height above the origin ``h`` (the 1:1 rule); ``ballistic`` the drag-free fall-out
   distance ``v·√(2h/g)`` (:func:`~airbrief_core.analyses.groundrisk.ballistic_distance_m`,
   g = 9.80665 m/s²) with ``h`` and the greatest horizontal speed ``v`` measured over the show
   (step 5). The distance used is ``base × (1 + audience_breach_margin_pct / 100)`` — a
   configurable safety factor. An airborne drone inside the audience polygon buffered by that
   distance is an ``audience`` violation (peak = the smallest distance to the audience polygon,
   limit = the distance). ``z > ceiling_m`` is a ``ceiling`` violation.
8. **Site checks.** Show-box and audience areas (geodesic), their separation, and whether the
   box overlaps the audience buffer (a site warning → verdict caution). The ``audience_line``
   layer is the show box widened by the audience distance: the audience belongs outside it.
9. **Pads.** Drones at or below ``airborne_threshold_m`` at their first (last) position stand on
   a take-off (landing) pad. Their smallest horizontal spacing is compared with
   ``min_pad_spacing_m``; closer pairs are pad conflicts (:class:`ShowPadSpacing`), at the show's
   first (last) time, when every drone holds its first (last) position.
10. **Frames.** Positions are re-interpolated at ``playback_rate_hz`` and rounded to 0.01 m for
    the playback timeline.

Verdict: ``fail`` on any conflict, violation or pad conflict, ``caution`` on a site warning, else
``pass``. At most ``max_events`` conflicts plus violations are listed (conflicts first);
``events_truncated`` flags the rest; at most :data:`MAX_PAD_CONFLICTS` pad conflicts are listed.
The verdict always reflects every event. The result is evidence about the choreography as given,
not an approval of the show.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely.geometry.base import BaseGeometry

from .. import _native
from ..errors import AnalysisError
from ..geo.frame import LocalFrame
from ..geo.geodesic import geodesic_area_perimeter
from ..geo.transform import geojson_to_geometry, geometry_to_geojson
from ..geo.validity import polygonal_part, repair
from ..models.analyses import (
    DroneStats,
    ShowConflict,
    ShowFrames,
    ShowParams,
    ShowResult,
    ShowSiteChecks,
    ShowViolation,
    TrackInput,
)
from ..models.common import GeoJSON, Issue, Severity, Verdict
from ..models.tracks import ShowAudienceBuffer, ShowPadConflict, ShowPadSpacing
from .base import AnalysisContext
from .groundrisk import GRAVITY_MS2, ballistic_distance_m

__all__ = [
    "MAX_DRONE_FRAMES",
    "MAX_PAD_CONFLICTS",
    "MAX_PLAYBACK_DRONE_FRAMES",
    "audience_buffer",
    "derived_show_box",
    "pad_spacing",
    "run",
]

MAX_DRONE_FRAMES = 6_000_000
"""Largest drones × frames grid checked at the requested rate (memory guard)."""

# Close pair-frames listed before the show is refused, to keep memory bounded. More than this
# means degenrate input: drones stacked on one point, or coordinates not in metres.
MAX_CLOSE_PAIRS = 500_000

MAX_PLAYBACK_DRONE_FRAMES = 1_000_000
"""Largest drones × frames returned for playback (response-size guard)."""

MAX_PAD_CONFLICTS = 200
"""Most take-off plus landing pad conflicts listed (``pads.conflicts_truncated`` flags the rest)."""

_MAX_PAD_PAIRS = 100_000
_MIN_EFFECTIVE_RATE_HZ = 0.2
_EPS = 1e-9

ViolationKind = Literal[
    "speed_h", "climb", "descent", "acceleration", "geofence", "audience", "ceiling",
    "endurance", "low_altitude_motion",
]


@dataclass
class _Track:
    """One drone's cleaned trajectory: sorted unique times and lon/lat/alt."""

    id: str
    t: NDArray[np.float64]
    lon: NDArray[np.float64]
    lat: NDArray[np.float64]
    alt: NDArray[np.float64]


@dataclass
class _RawViolation:
    kind: ViolationKind
    drone: int
    start: int
    end: int
    peak: float
    limit: float


def _clean_track(track: TrackInput) -> _Track:
    """Duplicate timestamps keep the last position."""
    t = np.asarray(track.times, dtype=np.float64)
    try:
        coords = np.asarray([row[:3] for row in track.coordinates], dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise AnalysisError("invalid_params", f"Track '{track.id}' has non-numeric coordinates") from exc
    if not (np.isfinite(t).all() and np.isfinite(coords).all()):
        raise AnalysisError("invalid_params", f"Track '{track.id}' has non-finite times or coordinates", details={"drone": track.id})
    lon, lat = coords[:, 0], coords[:, 1]
    if np.any(np.abs(lat) > 90.0) or np.any(np.abs(lon) > 180.0):
        raise AnalysisError(
            "invalid_params",
            f"Track '{track.id}' has coordinates outside WGS84 lon/lat range (expected [lon, lat, alt])",
            details={"drone": track.id},
        )
    order = np.argsort(t, kind="stable")
    t = t[order]
    coords = coords[order]
    keep = np.ones(t.size, dtype=bool)
    keep[:-1] = np.diff(t) > 0.0
    t = t[keep]
    coords = coords[keep]
    return _Track(
        id=track.id,
        t=t,
        lon=np.ascontiguousarray(coords[:, 0]),
        lat=np.ascontiguousarray(coords[:, 1]),
        alt=np.ascontiguousarray(coords[:, 2]),
    )


def _polygon_param(value: GeoJSON | None, label: str) -> BaseGeometry | None:
    if value is None:
        return None
    geom = geojson_to_geometry(value)
    geom, _ = repair(geom)
    poly = polygonal_part(geom) if geom is not None else None
    if poly is None or poly.is_empty:
        raise AnalysisError(
            "geometry_type_not_supported",
            f"The {label} must be a polygon",
            details={"parameter": label, "geometry_type": None if geom is None else geom.geom_type},
        )
    return shapely.force_2d(poly)


def _interp_tracks(
    tracks: list[_Track], xs: list[NDArray[np.float64]], ys: list[NDArray[np.float64]],
    zs: list[NDArray[np.float64]], grid: NDArray[np.float64], ctx: AnalysisContext,
) -> NDArray[np.float64]:
    """``(T, N, 3)`` ENU positions on ``grid`` (holding the end positions outside each track)."""
    out = np.empty((grid.size, len(tracks), 3), dtype=np.float64)
    for k, tr in enumerate(tracks):
        if k % 200 == 0:
            ctx.check_cancelled()
        out[:, k, 0] = np.interp(grid, tr.t, xs[k])
        out[:, k, 1] = np.interp(grid, tr.t, ys[k])
        out[:, k, 2] = np.interp(grid, tr.t, zs[k])
    return out


def _time_grid(t0: float, duration: float, rate: float) -> NDArray[np.float64]:
    count = int(math.floor(duration * rate + 1e-6)) + 1
    return t0 + np.arange(count, dtype=np.float64) / rate


def _runs(mask: NDArray[np.bool_]) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.int64]]:
    """Runs of consecutive ``True`` frames per drone: ``(drone, start, end_inclusive)`` arrays."""
    frames, drones = mask.shape
    padded = np.zeros((frames + 2, drones), dtype=np.int8)
    padded[1:-1] = mask
    step = np.diff(padded, axis=0)
    st_t, st_n = np.nonzero(step == 1)
    en_t, en_n = np.nonzero(step == -1)
    s_order = np.lexsort((st_t, st_n))
    e_order = np.lexsort((en_t, en_n))
    return (
        st_n[s_order].astype(np.int64),
        st_t[s_order].astype(np.int64),
        (en_t[e_order] - 1).astype(np.int64),
    )


def _group_violations(
    kind: ViolationKind,
    mask: NDArray[np.bool_],
    values: NDArray[np.float64],
    limit: float,
    *,
    peak: Literal["max", "min"] = "max",
) -> list[_RawViolation]:
    drones, starts, ends = _runs(mask)
    out: list[_RawViolation] = []
    for n, s, e in zip(drones.tolist(), starts.tolist(), ends.tolist(), strict=True):
        segment = values[s : e + 1, n]
        value = float(np.max(segment) if peak == "max" else np.min(segment))
        out.append(_RawViolation(kind=kind, drone=n, start=s, end=e, peak=value, limit=float(limit)))
    return out


def _group_conflicts(
    pairs: list[tuple[int, int, int, float]],
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.int64], NDArray[np.int64], NDArray[np.int64], NDArray[np.float64]]:
    """Group ``(frame, i, j, dist)`` rows into per-pair events over consecutive frames.

    Returns arrays ``(i, j, start_frame, end_frame, closest_frame, min_dist)`` sorted by start
    frame then pair. The closest frame is the earliest frame with the minimum distance.
    """
    if not pairs:
        empty_i = np.empty(0, dtype=np.int64)
        return empty_i, empty_i, empty_i, empty_i, empty_i, np.empty(0, dtype=np.float64)
    arr = np.asarray(pairs, dtype=np.float64)
    frame = arr[:, 0].astype(np.int64)
    ii = arr[:, 1].astype(np.int64)
    jj = arr[:, 2].astype(np.int64)
    dist = arr[:, 3]
    order = np.lexsort((frame, jj, ii))
    frame, ii, jj, dist = frame[order], ii[order], jj[order], dist[order]
    new_event = np.ones(frame.size, dtype=bool)
    new_event[1:] = (ii[1:] != ii[:-1]) | (jj[1:] != jj[:-1]) | (frame[1:] != frame[:-1] + 1)
    event_id = np.cumsum(new_event) - 1
    first = np.flatnonzero(new_event)
    last = np.append(first[1:] - 1, frame.size - 1)
    best_order = np.lexsort((frame, dist, event_id))
    is_first = np.ones(best_order.size, dtype=bool)
    is_first[1:] = event_id[best_order[1:]] != event_id[best_order[:-1]]
    best = best_order[is_first]
    ev_i = ii[first]
    ev_j = jj[first]
    ev_start = frame[first]
    ev_end = frame[last]
    ev_best = frame[best]
    ev_dist = dist[best]
    chrono = np.lexsort((ev_j, ev_i, ev_start))
    return ev_i[chrono], ev_j[chrono], ev_start[chrono], ev_end[chrono], ev_best[chrono], ev_dist[chrono]


def _central_difference(values: NDArray[np.float64], half: int, rate: float) -> NDArray[np.float64]:
    """Windowed central difference along axis 0 (one-sided at the ends)."""
    frames = values.shape[0]
    idx = np.arange(frames)
    hi = np.minimum(idx + half, frames - 1)
    lo = np.maximum(idx - half, 0)
    span = (hi - lo).astype(np.float64) / rate
    span = np.where(span > 0.0, span, np.inf)
    shape = (frames,) + (1,) * (values.ndim - 1)
    return (values[hi] - values[lo]) / span.reshape(shape)


def _r(value: float, decimals: int = 3) -> float:
    out = round(float(value), decimals)
    return 0.0 if out == 0.0 else out


def _feature(geometry: GeoJSON | None, properties: dict[str, Any]) -> GeoJSON:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def _collection(features: list[GeoJSON]) -> GeoJSON:
    return {"type": "FeatureCollection", "features": features}


def derived_show_box(
    xs: list[NDArray[np.float64]], ys: list[NDArray[np.float64]], margin_m: float
) -> BaseGeometry:
    """The east/north rectangle around every track position, widened by ``margin_m`` (step 6)."""
    all_x = np.concatenate(xs)
    all_y = np.concatenate(ys)
    return shapely.box(
        float(np.min(all_x)) - margin_m,
        float(np.min(all_y)) - margin_m,
        float(np.max(all_x)) + margin_m,
        float(np.max(all_y)) + margin_m,
    )


def audience_buffer(params: ShowParams, max_height_m: float, max_speed_ms: float) -> ShowAudienceBuffer:
    """The audience distance for ``params.audience_buffer_rule`` (step 7 of the module docstring)."""
    height = max(0.0, float(max_height_m))
    speed = max(0.0, float(max_speed_ms))
    pct = float(params.audience_breach_margin_pct)
    rule = params.audience_buffer_rule
    if rule == "one_to_one":
        base = height
        formula = f"1:1 rule: the greatest height above the origin, {base:.1f} m"
    elif rule == "ballistic":
        base = ballistic_distance_m(speed, height)
        formula = (
            f"Ballistic fall-out v·√(2h/g) = {speed:.2f} m/s × √(2 × {height:.1f} m / {GRAVITY_MS2:g} m/s²) "
            f"= {base:.1f} m (greatest horizontal speed and height of the show)"
        )
    else:
        base = float(params.audience_buffer_m)
        formula = f"Fixed distance {base:g} m"
    distance = base * (1.0 + pct / 100.0)
    if pct > 0:
        formula += f"; × (1 + {pct:g} %) safety factor = {distance:.1f} m"
    return ShowAudienceBuffer(
        rule=rule,
        base_distance_m=_r(base, 2),
        breach_margin_pct=pct,
        distance_m=_r(distance, 2),
        height_m=_r(height, 2),
        speed_ms=_r(speed, 3),
        formula=formula,
    )


@dataclass
class _Pads:
    spacing: ShowPadSpacing
    total_conflicts: int
    features: list[GeoJSON]
    phases_unchecked: list[str]


def pad_spacing(
    tracks: list[_Track], xs: list[NDArray[np.float64]], ys: list[NDArray[np.float64]],
    zs: list[NDArray[np.float64]], params: ShowParams, t_start: float, t_end: float,
) -> _Pads:
    """Take-off and landing pad spacing (step 9 of the module docstring)."""
    limit = float(params.min_pad_spacing_m)
    spacing = ShowPadSpacing(limit_m=limit, takeoff_pads=0, landing_pads=0)
    features: list[GeoJSON] = []
    unchecked: list[str] = []
    total = 0
    phases: tuple[tuple[Literal["takeoff", "landing"], int, float], ...] = (("takeoff", 0, t_start), ("landing", -1, t_end))
    for phase, at, time_s in phases:
        ground = [k for k in range(len(tracks)) if float(zs[k][at]) <= params.airborne_threshold_m]
        if phase == "takeoff":
            spacing.takeoff_pads = len(ground)
        else:
            spacing.landing_pads = len(ground)
        if len(ground) < 2:
            unchecked.append("take-off" if phase == "takeoff" else "landing")
            continue
        pos = np.zeros((1, len(ground), 3), dtype=np.float64)
        pos[0, :, 0] = [float(xs[k][at]) for k in ground]
        pos[0, :, 1] = [float(ys[k][at]) for k in ground]
        active = np.ones((1, len(ground)), dtype=bool)
        dmin, imin, jmin = _native.min_separation_frames(pos, active)
        pair = [tracks[ground[int(imin[0])]].id, tracks[ground[int(jmin[0])]].id]
        if phase == "takeoff":
            spacing.takeoff_min_spacing_m = _r(dmin[0], 3)
            spacing.takeoff_pair = pair
        else:
            spacing.landing_min_spacing_m = _r(dmin[0], 3)
            spacing.landing_pair = pair
        try:
            close = _native.pairs_within(pos, active, limit, max_pairs=_MAX_PAD_PAIRS)
        except _native.TooManyPairs:
            # Pads stacked on one spot: list the closest pair and say the list is incomplete.
            spacing.conflicts_truncated = True
            close = [(0, int(imin[0]), int(jmin[0]), float(dmin[0]))]
            total += _MAX_PAD_PAIRS
        else:
            total += len(close)
        for _, i, j, dist in close:
            a, b = tracks[ground[i]], tracks[ground[j]]
            if len(spacing.conflicts) >= MAX_PAD_CONFLICTS:
                spacing.conflicts_truncated = True
                break
            conflict = ShowPadConflict(
                phase=phase, drone_a=a.id, drone_b=b.id, distance_m=_r(dist, 3), limit_m=limit, time_s=_r(time_s)
            )
            spacing.conflicts.append(conflict)
            features.append(
                _feature(
                    {
                        "type": "LineString",
                        "coordinates": [
                            [_r(float(a.lon[at]), 8), _r(float(a.lat[at]), 8)],
                            [_r(float(b.lon[at]), 8), _r(float(b.lat[at]), 8)],
                        ],
                    },
                    conflict.model_dump(),
                )
            )
    return _Pads(spacing=spacing, total_conflicts=total, features=features, phases_unchecked=unchecked)


def _endurance_violations(airborne: NDArray[np.bool_], rate: float, endurance_s: float) -> list[_RawViolation]:
    """Drones airborne for longer than ``endurance_s`` in total (frames count ``1 / rate`` s)."""
    cumulative = np.cumsum(airborne, axis=0, dtype=np.float64) / rate
    out: list[_RawViolation] = []
    for n in np.flatnonzero(cumulative[-1] > endurance_s + _EPS).tolist():
        start = int(np.argmax(cumulative[:, n] > endurance_s + _EPS))
        end = int(np.flatnonzero(airborne[:, n])[-1])
        out.append(
            _RawViolation(
                kind="endurance", drone=n, start=start, end=max(start, end), peak=float(cumulative[-1, n]),
                limit=float(endurance_s),
            )
        )
    return out


def run(params: ShowParams, ctx: AnalysisContext) -> ShowResult:
    """Validate a drone show (see the module docstring)."""
    issues: list[Issue] = list(ctx.issues)
    ctx.step("Preparing tracks", 0.02)

    if not params.tracks:
        raise AnalysisError("no_tracks", "No drone tracks were given")
    seen: set[str] = set()
    for track in params.tracks:
        if track.id in seen:
            raise AnalysisError("invalid_params", f"Drone id '{track.id}' is used by more than one track", details={"drone": track.id})
        seen.add(track.id)
    tracks = [_clean_track(t) for t in params.tracks]
    ids = [t.id for t in tracks]
    n_drones = len(tracks)

    origin_source: Literal["given", "inferred"] = "given" if params.origin is not None else "inferred"
    absolute = params.altitude_reference == "absolute"
    # Absolute (above sea level) altitudes are measured from the lowest take-off position unless
    # the origin states its ground altitude (the API applies the same rule to files read as absolute).
    pad_ground = float(min(float(t.alt[0]) for t in tracks)) if absolute else 0.0
    if params.origin is not None:
        lon0, lat0 = float(params.origin[0]), float(params.origin[1])
        if len(params.origin) > 2:
            alt0 = float(params.origin[2])
        else:
            alt0 = pad_ground
            if absolute:
                issues.append(
                    Issue(
                        code="origin_ground_inferred",
                        message=(
                            f"The origin has no ground altitude: heights are measured from the lowest take-off "
                            f"position, {alt0:.1f} m above sea level."
                        ),
                        severity=Severity.INFO,
                    )
                )
    else:
        lon0 = float(np.mean([t.lon[0] for t in tracks]))
        lat0 = float(np.mean([t.lat[0] for t in tracks]))
        alt0 = pad_ground
        where = f"the lowest take-off position, {alt0:.1f} m above sea level" if absolute else "altitude 0 m"
        issues.append(
            Issue(
                code="origin_inferred",
                message=f"No origin given: using the mean of the first positions ({lon0:.6f}, {lat0:.6f}) with {where}.",
                severity=Severity.INFO,
            )
        )
    frame = LocalFrame(lon0, lat0)

    xs: list[NDArray[np.float64]] = []
    ys: list[NDArray[np.float64]] = []
    zs: list[NDArray[np.float64]] = []
    for tr in tracks:
        x, y = frame.xy(tr.lon, tr.lat)
        xs.append(x)
        ys.append(y)
        zs.append(tr.alt - alt0 if params.altitude_reference == "absolute" else tr.alt.copy())

    t0 = float(min(t.t[0] for t in tracks))
    t1 = float(max(t.t[-1] for t in tracks))
    duration = t1 - t0
    if duration <= 0.0:
        raise AnalysisError("invalid_params", "The tracks span no time: at least two distinct timestamps are needed")

    rate = float(params.sample_rate_hz)
    if (math.floor(duration * rate) + 1) * n_drones > MAX_DRONE_FRAMES:
        reduced = (MAX_DRONE_FRAMES / n_drones - 1.0) / duration
        if reduced < _MIN_EFFECTIVE_RATE_HZ:
            raise AnalysisError(
                "invalid_params",
                "The show is too large to validate (drones × duration)",
                details={"drones": n_drones, "duration_s": duration, "max_drone_frames": MAX_DRONE_FRAMES},
            )
        reduced = math.floor(reduced * 100.0) / 100.0
        issues.append(
            Issue(
                code="sample_rate_reduced",
                message=f"Large show: checked at {reduced:g} Hz instead of {rate:g} Hz to stay within memory limits.",
                severity=Severity.WARNING,
            )
        )
        rate = reduced

    ctx.step("Resampling", 0.1)
    grid = _time_grid(t0, duration, rate)
    pos = _interp_tracks(tracks, xs, ys, zs, grid, ctx)
    n_frames = grid.size
    up = pos[:, :, 2]
    airborne = up > params.airborne_threshold_m

    ctx.step("Checking separation", 0.25)
    try:
        pairs = _native.pairs_within(pos, airborne, params.min_separation_m, max_pairs=MAX_CLOSE_PAIRS)
    except _native.TooManyPairs as exc:
        raise AnalysisError(
            "too_many_conflicts",
            f"More than {MAX_CLOSE_PAIRS:,} drone pairs are closer than {params.min_separation_m:g} m over the show, "
            "so it cannot be checked as given. Check that positions are in metres, that drones waiting on the "
            "ground are below the airborne threshold, and the minimum separation.",
            details={"max_close_pairs": MAX_CLOSE_PAIRS},
        ) from exc
    ctx.check_cancelled()
    dmin, imin, jmin = _native.min_separation_frames(pos, airborne)
    ctx.check_cancelled()

    min_sep: float | None = None
    min_sep_at: float | None = None
    min_sep_pair: list[str] | None = None
    finite = np.isfinite(dmin)
    if finite.any():
        k = int(np.argmin(np.where(finite, dmin, np.inf)))
        min_sep = _r(dmin[k])
        min_sep_at = _r(grid[k])
        min_sep_pair = [ids[int(imin[k])], ids[int(jmin[k])]]
    else:
        issues.append(
            Issue(
                code="no_airborne_pairs",
                message="Fewer than two drones were airborne at any time; separation was not evaluated.",
                severity=Severity.INFO,
            )
        )

    ev_i, ev_j, ev_start, ev_end, ev_best, ev_dist = _group_conflicts(pairs)
    total_conflicts = int(ev_i.size)

    ctx.step("Checking dynamics", 0.45)
    half = max(1, int(round(params.dynamics_window_s * rate / 2.0)))
    if abs(2.0 * half / rate - params.dynamics_window_s) > 1e-6:
        issues.append(
            Issue(
                code="dynamics_window_adjusted",
                message=f"Dynamics window rounded to {2.0 * half / rate:g} s (whole frames at {rate:g} Hz).",
                severity=Severity.INFO,
            )
        )
    vel = _central_difference(pos, half, rate)
    ctx.check_cancelled()
    acc = _central_difference(vel, half, rate)
    ctx.check_cancelled()
    h_speed = np.hypot(vel[:, :, 0], vel[:, :, 1])
    climb = np.maximum(vel[:, :, 2], 0.0)
    descent = np.maximum(-vel[:, :, 2], 0.0)
    accel = np.sqrt(np.einsum("tnk,tnk->tn", acc, acc))
    del acc

    raw: list[_RawViolation] = []
    raw += _group_violations("speed_h", h_speed > params.max_horizontal_speed_ms + _EPS, h_speed, params.max_horizontal_speed_ms)
    raw += _group_violations("climb", climb > params.max_climb_rate_ms + _EPS, climb, params.max_climb_rate_ms)
    raw += _group_violations("descent", descent > params.max_descent_rate_ms + _EPS, descent, params.max_descent_rate_ms)
    raw += _group_violations("acceleration", accel > params.max_acceleration_ms2 + _EPS, accel, params.max_acceleration_ms2)
    if params.endurance_s is not None:
        raw += _endurance_violations(airborne, rate, float(params.endurance_s))
    if params.min_navigation_altitude_m is not None:
        tolerance = float(params.low_altitude_speed_tolerance_ms)
        low_motion = (up < float(params.min_navigation_altitude_m)) & (h_speed > tolerance + _EPS)
        raw += _group_violations("low_altitude_motion", low_motion, h_speed, tolerance)
    ctx.check_cancelled()

    ctx.step("Checking geofence", 0.6)
    raw += _group_violations("ceiling", up > params.ceiling_m + _EPS, up, params.ceiling_m)
    # For the audience distance. Height from the track vertices (exact for linear tracks).
    max_height = float(max(float(np.max(z)) for z in zs))
    max_speed = float(np.max(h_speed)) if h_speed.size else 0.0
    buffer_rule = audience_buffer(params, max_height, max_speed)
    audience_distance = float(buffer_rule.distance_m)

    box_wgs = _polygon_param(params.show_box, "show box")
    audience_wgs = _polygon_param(params.audience, "audience area")
    flat_x = pos[:, :, 0].ravel()
    flat_y = pos[:, :, 1].ravel()
    flat_air = airborne.ravel()

    site = ShowSiteChecks(audience_buffer=buffer_rule)
    box_local: BaseGeometry
    audience_local: BaseGeometry | None = None
    buffer_local: BaseGeometry | None = None
    site_warning = False

    if box_wgs is not None:
        site.show_box_source = "given"
        box_local = frame.to_local(box_wgs)
        shapely.prepare(box_local)
        site.show_box_area_m2 = _r(geodesic_area_perimeter(box_wgs)[0], 1)
        inside = shapely.contains_xy(box_local, flat_x, flat_y)
        outside = (~inside) & flat_air
        if outside.any():
            dist_out = np.zeros(flat_x.size, dtype=np.float64)
            pts = shapely.points(flat_x[outside], flat_y[outside])
            dist_out[outside] = shapely.distance(pts, box_local)
            raw += _group_violations(
                "geofence", outside.reshape(airborne.shape), dist_out.reshape(airborne.shape), 0.0
            )
    else:
        margin = float(params.show_box_margin_m)
        box_local = derived_show_box(xs, ys, margin)
        box_wgs = shapely.force_2d(frame.to_wgs84(box_local))
        site.show_box_source = "derived"
        site.show_box_margin_m = margin
        site.show_box_area_m2 = _r(geodesic_area_perimeter(box_wgs)[0], 1)
        minx, miny, maxx, maxy = box_local.bounds
        issues.append(
            Issue(
                code="show_box_derived",
                message=(
                    f"No show box polygon was given: the box was derived from the tracks ({maxx - minx:.0f} m × "
                    f"{maxy - miny:.0f} m, their horizontal extent plus a {margin:g} m margin). Every position is inside "
                    "it by construction, so the geofence was not checked; add a polygon named \"Show box\" to check it."
                ),
                severity=Severity.INFO,
            )
        )

    if audience_wgs is not None:
        audience_local = frame.to_local(audience_wgs)
        buffer_local = (
            audience_local.buffer(audience_distance, quad_segs=16)
            if audience_distance > 0
            else audience_local
        )
        shapely.prepare(buffer_local)
        site.audience_area_m2 = _r(geodesic_area_perimeter(audience_wgs)[0], 1)
        within = shapely.contains_xy(buffer_local, flat_x, flat_y) & flat_air
        if within.any():
            dist_aud = np.full(flat_x.size, np.inf, dtype=np.float64)
            pts = shapely.points(flat_x[within], flat_y[within])
            dist_aud[within] = shapely.distance(pts, audience_local)
            raw += _group_violations(
                "audience",
                within.reshape(airborne.shape),
                dist_aud.reshape(airborne.shape),
                audience_distance,
                peak="min",
            )
    else:
        issues.append(
            Issue(
                code="no_audience",
                message=(
                    "No audience area was given: the audience buffer was not checked. The audience line on the map "
                    f"marks {audience_distance:g} m from the show box."
                ),
                severity=Severity.INFO,
            )
        )

    if audience_local is not None and buffer_local is not None:
        site.box_audience_distance_m = _r(shapely.distance(box_local, audience_local), 2)
        site.box_overlaps_audience_buffer = bool(box_local.intersects(buffer_local))
        if site.box_overlaps_audience_buffer:
            site_warning = True
            issues.append(
                Issue(
                    code="box_overlaps_audience_buffer",
                    message=(
                        f"The show box comes within {site.box_audience_distance_m:g} m of the audience, "
                        f"inside the {audience_distance:g} m buffer."
                    ),
                    severity=Severity.WARNING,
                )
            )
    ctx.check_cancelled()

    pads = pad_spacing(tracks, xs, ys, zs, params, t0, t1)
    if pads.total_conflicts:
        sp = pads.spacing
        closest = [
            (d, p) for d, p in ((sp.takeoff_min_spacing_m, sp.takeoff_pair), (sp.landing_min_spacing_m, sp.landing_pair))
            if d is not None and p is not None
        ]
        d_min, pair = min(closest, key=lambda item: item[0])
        issues.append(
            Issue(
                code="pad_spacing",
                message=(
                    f"{'More than ' if sp.conflicts_truncated else ''}{pads.total_conflicts} take-off or landing pad pair(s) "
                    f"are closer than {sp.limit_m:g} m (closest {pair[0]} – {pair[1]}, {d_min:g} m apart)."
                ),
                severity=Severity.WARNING,
            )
        )
    if pads.phases_unchecked:
        issues.append(
            Issue(
                code="pads_not_checked",
                message=(
                    f"Fewer than two drones stand on the ground (at or below {params.airborne_threshold_m:g} m) at "
                    f"{' and '.join(pads.phases_unchecked)}: their pad spacing was not checked."
                ),
                severity=Severity.INFO,
            )
        )

    ctx.step("Summarising", 0.75)
    total_violations = len(raw)
    raw.sort(key=lambda v: (v.start, v.drone, v.kind))
    max_events = int(params.max_events)
    keep_conflicts = min(total_conflicts, max_events)
    keep_violations = min(total_violations, max_events - keep_conflicts)
    truncated = keep_conflicts < total_conflicts or keep_violations < total_violations
    if truncated:
        issues.append(
            Issue(
                code="events_truncated",
                message=(
                    f"{total_conflicts} conflict(s) and {total_violations} violation(s) found; "
                    f"only the first {max_events} events are listed."
                ),
                severity=Severity.WARNING,
            )
        )

    conflicts: list[ShowConflict] = []
    for k in range(keep_conflicts):
        i, j, best = int(ev_i[k]), int(ev_j[k]), int(ev_best[k])
        mid = (pos[best, i] + pos[best, j]) / 2.0
        conflicts.append(
            ShowConflict(
                drone_a=ids[i],
                drone_b=ids[j],
                start_s=_r(grid[int(ev_start[k])]),
                end_s=_r(grid[int(ev_end[k])]),
                t_min_s=_r(grid[best]),
                min_distance_m=_r(ev_dist[k]),
                position_enu=[_r(v, 2) for v in mid.tolist()],
            )
        )
    violations = [
        ShowViolation(
            kind=v.kind,
            drone=ids[v.drone],
            start_s=_r(grid[v.start]),
            end_s=_r(grid[v.end]),
            peak_value=_r(v.peak),
            limit=v.limit,
        )
        for v in raw[:keep_violations]
    ]

    steps = np.linalg.norm(np.diff(pos, axis=0), axis=2) if n_frames > 1 else np.zeros((1, n_drones))
    distance = steps.sum(axis=0)
    airborne_s = airborne.sum(axis=0) / rate
    drones = [
        DroneStats(
            drone=ids[n],
            airborne_s=_r(airborne_s[n], 2),
            distance_m=_r(distance[n], 2),
            max_altitude_m=_r(np.max(up[:, n]), 2),
            max_horizontal_speed_ms=_r(np.max(h_speed[:, n])),
            max_climb_rate_ms=_r(climb[:, n].max()),
            max_descent_rate_ms=_r(descent[:, n].max()),
            max_acceleration_ms2=_r(accel[:, n].max()),
        )
        for n in range(n_drones)
    ]
    del vel, h_speed, climb, descent, accel

    ctx.step("Building playback frames", 0.85)
    playback_rate = float(params.playback_rate_hz)
    if (math.floor(duration * playback_rate) + 1) * n_drones > MAX_PLAYBACK_DRONE_FRAMES:
        reduced = max((MAX_PLAYBACK_DRONE_FRAMES / n_drones - 1.0) / duration, 1e-3)
        issues.append(
            Issue(
                code="playback_rate_reduced",
                message=f"Playback frames reduced to {reduced:.3g} Hz to keep the result small.",
                severity=Severity.INFO,
            )
        )
        playback_rate = reduced
    play_grid = _time_grid(t0, duration, playback_rate)
    play_pos = np.round(_interp_tracks(tracks, xs, ys, zs, play_grid, ctx), 2) + 0.0
    frames = ShowFrames(
        origin=[lon0, lat0, alt0],
        times=[_r(t) for t in play_grid.tolist()],
        drone_ids=ids,
        positions=play_pos.tolist(),
    )

    ctx.step("Building layers", 0.95)
    conflict_count = np.zeros(n_drones, dtype=np.int64)
    np.add.at(conflict_count, ev_i, 1)
    np.add.at(conflict_count, ev_j, 1)
    violation_count = np.zeros(n_drones, dtype=np.int64)
    for v in raw:
        violation_count[v.drone] += 1
    path_features: list[GeoJSON] = []
    for n, tr in enumerate(tracks):
        coords = [
            [_r(lo, 8), _r(la, 8), _r(al, 2)]
            for lo, la, al in zip(tr.lon.tolist(), tr.lat.tolist(), tr.alt.tolist(), strict=True)
        ]
        geometry: GeoJSON = (
            {"type": "LineString", "coordinates": coords}
            if len(coords) >= 2
            else {"type": "Point", "coordinates": coords[0]}
        )
        path_features.append(
            _feature(
                geometry,
                {
                    "drone": tr.id,
                    "conflicts": int(conflict_count[n]),
                    "violations": int(violation_count[n]),
                    "max_altitude_m": drones[n].max_altitude_m,
                },
            )
        )
    derived = site.show_box_source == "derived"
    box_features: list[GeoJSON] = [
        _feature(
            geometry_to_geojson(box_wgs, decimals=8),
            {
                "name": "Show box (derived from the tracks)" if derived else "Show box",
                "role": "show_box",
                "source": site.show_box_source,
                "margin_m": site.show_box_margin_m,
                "area_m2": site.show_box_area_m2,
            },
        )
    ]
    audience_features: list[GeoJSON] = []
    if audience_wgs is not None and buffer_local is not None:
        audience_features.append(
            _feature(
                geometry_to_geojson(audience_wgs),
                {"name": "Audience", "role": "audience", "area_m2": site.audience_area_m2},
            )
        )
        if audience_distance > 0:
            audience_features.append(
                _feature(
                    geometry_to_geojson(frame.to_wgs84(buffer_local)),
                    {"name": "Audience buffer", "role": "audience_buffer", "buffer_m": audience_distance},
                )
            )
    line_features: list[GeoJSON] = []
    if audience_distance > 0:
        line_features.append(
            _feature(
                geometry_to_geojson(shapely.force_2d(frame.to_wgs84(box_local.buffer(audience_distance, quad_segs=16))), decimals=8),
                {
                    "name": "Audience line",
                    "role": "audience_line",
                    "distance_m": audience_distance,
                    "rule": buffer_rule.rule,
                },
            )
        )
    layers = {
        "paths": _collection(path_features),
        "show_box": _collection(box_features),
        "audience_buffer": _collection(audience_features),
        "audience_line": _collection(line_features),
        "pad_conflicts": _collection(pads.features),
    }

    if total_conflicts or total_violations or pads.total_conflicts:
        verdict = Verdict.FAIL
    elif site_warning:
        verdict = Verdict.CAUTION
    else:
        verdict = Verdict.PASS

    ctx.step("Done", 1.0)
    return ShowResult(
        verdict=verdict,
        drone_count=n_drones,
        duration_s=_r(duration),
        sample_rate_hz=rate,
        frames_checked=int(n_frames),
        min_separation_m=min_sep,
        min_separation_at_s=min_sep_at,
        min_separation_pair=min_sep_pair,
        conflicts=conflicts,
        violations=violations,
        events_truncated=truncated,
        drones=drones,
        site=site,
        frames=frames,
        backend=_native.BACKEND,
        issues=issues,
        layers=layers,
        altitude_reference=params.altitude_reference,
        origin_source=origin_source,
        max_altitude_m=_r(max_height, 2),
        pads=pads.spacing,
    )
