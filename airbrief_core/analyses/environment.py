"""Environment evidence: sun and civil twilight, daylight profiles, magnetic field, wind at height
and electromagnetic-interference (EMI) proximity for a planned operation.

Procedure (docs/features/environment.md):

1. **Point.** Sun, magnetic field and wind are evaluated at the geometry's centroid (the
   representative point when a polygon's centroid falls outside it). EMI uses the whole geometry.
2. **Local day.** ``date`` 00:00 to 24:00 at ``utc_offset_min`` minutes east of UTC. The offset
   only labels times: every computation is in UTC.
3. **Sun** (:mod:`airbrief_core.geo.sun`, NOAA solar calculator equations). Solar noon N is the
   instant of hour angle 0 nearest local midday. As NOAA does, sunrise and civil dawn are the last
   upward crossings of −0.833° and −6° (geometric elevation of the sun's centre) in the 12 h before
   N, sunset and civil dusk the first downward crossings in the 12 h after N; with no crossing the
   day is a polar day (sun above −0.833° at N) or polar night. The 24 h track is sampled every
   5 min (apparent elevation, with NOAA's refraction).
4. **Daylight check.** The local day and the planned window are split at every crossing of −6°
   and −0.833° (geometric) and, for the custom profile, of the minimum elevation (apparent). Each
   piece is night (< −6°), civil twilight (−6° to −0.833°) or day, and gets the profile's status:
   ``india_daylight`` allows day only; ``faa_107`` allows day, marks civil twilight "lighting
   required" (14 CFR 107.29) and flags night; ``custom`` allows apparent elevation ≥ the minimum.
   Minutes outside = the window's pieces outside the profile. There is never a "fit to fly" verdict.
5. **Magnetic field** (:mod:`airbrief_core.geo.wmm`, WMM2025) at the point, at a height above the
   WGS84 ellipsoid taken as DEM ground + ``height_agl_m`` (the geoid undulation is ignored: well
   under 0.01° of declination and about 2.5 nT per 100 m), at the window start (else local noon).
   Blackout H < 2000 nT, caution H < 6000 nT (WMM2025 report).
6. **Wind at height**: power law ``v(z) = v_ref·(z/z_ref)^α`` for the mean wind and the gust;
   margin = rated limit − estimate (a number, not a verdict).
7. **EMI proximity**: power lines, cables, substations and plants from the pack's ``power`` layer
   and communication towers and masts from its ``obstacles`` layer within ``emi_radius_m`` of the
   geometry (0 m when touching or inside), nearest first. Proximity evidence, not an interference
   prediction. A search area reaching beyond the pack's bbox is reported (``emi_partial_coverage``).
8. **Photogrammetry sun window**: when the apparent elevation is between ``sun_min_deg`` and
   ``sun_max_deg`` during the local day.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import shapely
from shapely.geometry import Point, box
from shapely.geometry.base import BaseGeometry

from ..errors import AnalysisError
from ..geo import sun as solar
from ..geo.frame import LocalFrame
from ..geo.transform import geojson_to_geometry, geometry_to_geojson, unwrap_antimeridian
from ..geo.validity import drop_z, geometry_type_name, polygonal_part, repair
from ..geo.wmm import decimal_year, load_wmm, wmm
from ..models.common import Issue, Severity
from ..models.environment import (
    DAYLIGHT_PROFILES,
    DISCLAIMER_ENVIRONMENT,
    DaylightCheck,
    DaylightProfile,
    DaylightSegment,
    EmiItem,
    EmiKind,
    EnvironmentParams,
    EnvironmentResult,
    MagneticSummary,
    PlannedWindow,
    SunEvent,
    SunSummary,
    SunTrackPoint,
    SunWindow,
    TimeSegment,
    WindAtHeight,
)
from .base import AnalysisContext

if TYPE_CHECKING:
    from ..data.datapack import DataPack

__all__ = [
    "MAX_EMI_ITEMS",
    "TRACK_STEP_S",
    "daylight_check",
    "emi_proximity",
    "local_midnight_utc",
    "magnetic_summary",
    "offset_label",
    "run",
    "sun_times",
    "sun_track",
    "sun_window",
    "wind_at_height",
]

TRACK_STEP_S = 300.0
"""Sampling step of the 24 h sun track (5 min)."""

MAX_EMI_ITEMS = 50
"""Most EMI items listed (nearest first)."""

EMI_CLIP_FACTOR = 3.0
"""Power features are clipped to the geometry's bounds + this × the EMI radius for the map."""

OBSTACLE_EMI_KINDS: dict[str, EmiKind] = {"communication_tower": "comm_tower", "mast": "mast"}
POWER_EMI_KINDS: dict[str, EmiKind] = {
    "line": "power_line",
    "minor_line": "minor_line",
    "cable": "cable",
    "substation": "substation",
    "plant": "plant",
}
EMI_LABEL: dict[str, str] = {
    "power_line": "power line",
    "minor_line": "minor power line",
    "cable": "power cable",
    "substation": "substation",
    "plant": "power plant",
    "comm_tower": "communication tower",
    "mast": "mast",
    "pylon": "pylon",
}
_LINE_KINDS = frozenset({"power_line", "minor_line", "cable"})

SECONDS_PER_DAY = solar.SECONDS_PER_DAY
_HALF_DAY = SECONDS_PER_DAY / 2.0


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _utc(t: float) -> datetime:
    """The UTC datetime of a Unix time (``datetime.fromtimestamp`` refuses times before 1970 and
    after 3000 on Windows; the date range of the parameters is 1901–2099)."""
    return _EPOCH + timedelta(seconds=t)


def local_midnight_utc(day: date, utc_offset_min: int) -> float:
    """Unix time of 00:00 local on ``day`` in a zone ``utc_offset_min`` minutes east of UTC."""
    return datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() - utc_offset_min * 60.0


def offset_label(utc_offset_min: int) -> str:
    """``UTC+05:30`` style label of an offset in minutes east of UTC."""
    sign = "+" if utc_offset_min >= 0 else "-"
    hours, minutes = divmod(abs(int(utc_offset_min)), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def _iso(t: float) -> str:
    return _utc(round(t)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local(t: float, day: date, utc_offset_min: int) -> tuple[str, int]:
    """Local ``HH:MM`` (rounded to the minute) and its day relative to ``day``."""
    rounded = round(t / 60.0) * 60.0
    local = _utc(rounded) + timedelta(minutes=utc_offset_min)
    return local.strftime("%H:%M"), (local.date() - day).days


def _hhmm_with_day(text: str, day_offset: int) -> str:
    if day_offset == 0:
        return text
    return f"{text} ({'+' if day_offset > 0 else '−'}{abs(day_offset)} d)"


def _segment_fields(t_start: float, t_end: float, t0: float, day: date, offset: int) -> dict[str, Any]:
    start_local, start_day = _local(t_start, day, offset)
    end_local, end_day = _local(t_end, day, offset)
    return {
        "start": _iso(t_start),
        "end": _iso(t_end),
        "start_local": _hhmm_with_day(start_local, start_day),
        "end_local": _hhmm_with_day(end_local, end_day),
        "start_minute": round((t_start - t0) / 60.0, 2),
        "end_minute": round((t_end - t0) / 60.0, 2),
        "minutes": round((t_end - t_start) / 60.0, 2),
    }


def _parse_hhmm(text: str) -> int:
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


@dataclass(frozen=True)
class _SunInstants:
    noon: float
    sunrise: float | None
    sunset: float | None
    civil_dawn: float | None
    civil_dusk: float | None
    polar_day: bool
    polar_night: bool


def _sun_instants(lon: float, lat: float, t0: float) -> _SunInstants:
    noon = solar.solar_noon(lon, t0 + _HALF_DAY)
    found = solar.crossings(
        lon, lat, noon - _HALF_DAY, noon + _HALF_DAY,
        [solar.SUNRISE_ELEVATION_DEG, solar.CIVIL_TWILIGHT_ELEVATION_DEG],
    )

    def last_rise(threshold: float) -> float | None:
        times = [c.t for c in found if c.rising and c.threshold_deg == threshold and c.t <= noon]
        return max(times) if times else None

    def first_set(threshold: float) -> float | None:
        times = [c.t for c in found if not c.rising and c.threshold_deg == threshold and c.t >= noon]
        return min(times) if times else None

    sunrise = last_rise(solar.SUNRISE_ELEVATION_DEG)
    sunset = first_set(solar.SUNRISE_ELEVATION_DEG)
    any_horizon = any(c.threshold_deg == solar.SUNRISE_ELEVATION_DEG for c in found)
    noon_elev = float(solar.geometric_elevation(lon, lat, noon)[0])
    return _SunInstants(
        noon=noon,
        sunrise=sunrise,
        sunset=sunset,
        civil_dawn=last_rise(solar.CIVIL_TWILIGHT_ELEVATION_DEG),
        civil_dusk=first_set(solar.CIVIL_TWILIGHT_ELEVATION_DEG),
        polar_day=not any_horizon and noon_elev > solar.SUNRISE_ELEVATION_DEG,
        polar_night=not any_horizon and noon_elev <= solar.SUNRISE_ELEVATION_DEG,
    )


def sun_track(lon: float, lat: float, t0: float, t1: float, step_s: float = TRACK_STEP_S) -> list[tuple[float, float, float]]:
    """``(t, apparent elevation°, azimuth°)`` from ``t0`` to ``t1`` inclusive every ``step_s``."""
    n = int(math.floor((t1 - t0) / step_s + 1e-9)) + 1
    ts = t0 + np.arange(n, dtype=np.float64) * step_s
    pos = solar.solar_position(lon, lat, ts)
    return [
        (float(t), float(e), float(a))
        for t, e, a in zip(ts.tolist(), pos.elevation_deg.tolist(), pos.azimuth_deg.tolist(), strict=True)
    ]


def _event(t: float | None, lon: float, lat: float, day: date, offset: int) -> SunEvent | None:
    if t is None:
        return None
    pos = solar.solar_position(lon, lat, t)
    local, day_offset = _local(t, day, offset)
    return SunEvent(
        utc=_iso(t),
        local=local,
        day_offset=day_offset,
        azimuth_deg=round(float(pos.azimuth_deg[0]), 2),
        elevation_deg=round(float(pos.elevation_deg[0]), 2),
    )


def sun_times(lon: float, lat: float, day: date, utc_offset_min: int = 0, *, track_step_s: float = TRACK_STEP_S) -> SunSummary:
    """Sunrise, sunset, civil dawn and dusk, solar noon, polar flags and the 24 h track of a local day."""
    t0 = local_midnight_utc(day, utc_offset_min)
    inst = _sun_instants(lon, lat, t0)
    noon_event = _event(inst.noon, lon, lat, day, utc_offset_min)
    assert noon_event is not None
    day_minutes = sum(
        (b - a) / 60.0
        for a, b, e in solar.elevation_intervals(lon, lat, t0, t0 + SECONDS_PER_DAY, [solar.SUNRISE_ELEVATION_DEG])
        if e >= solar.SUNRISE_ELEVATION_DEG
    )
    track = [
        SunTrackPoint(t=_iso(t), minute=round((t - t0) / 60.0, 2), elevation_deg=round(e, 3), azimuth_deg=round(a, 3))
        for t, e, a in sun_track(lon, lat, t0, t0 + SECONDS_PER_DAY, track_step_s)
    ]
    return SunSummary(
        sunrise=_event(inst.sunrise, lon, lat, day, utc_offset_min),
        sunset=_event(inst.sunset, lon, lat, day, utc_offset_min),
        civil_dawn=_event(inst.civil_dawn, lon, lat, day, utc_offset_min),
        civil_dusk=_event(inst.civil_dusk, lon, lat, day, utc_offset_min),
        solar_noon=noon_event,
        polar_day=inst.polar_day,
        polar_night=inst.polar_night,
        max_elevation_deg=noon_event.elevation_deg if noon_event.elevation_deg is not None else 0.0,
        daylight_minutes=round(day_minutes, 2),
        track=track,
    )


def _kind(geometric_elev: float) -> str:
    if geometric_elev >= solar.SUNRISE_ELEVATION_DEG:
        return "day"
    if geometric_elev >= solar.CIVIL_TWILIGHT_ELEVATION_DEG:
        return "civil_twilight"
    return "night"


def _status(profile: DaylightProfile, kind: str, apparent_elev: float, min_elev: float) -> str:
    if profile == "custom":
        return "allowed" if apparent_elev >= min_elev else "outside"
    if kind == "day":
        return "allowed"
    spec = DAYLIGHT_PROFILES[profile]
    return spec.civil_twilight if kind == "civil_twilight" else spec.night


def _split(
    lon: float, lat: float, t_start: float, t_end: float, profile: DaylightProfile, min_elev: float
) -> list[tuple[float, float, str, str]]:
    """``[t_start, t_end)`` as ``(start, end, kind, status)`` pieces, adjacent equal pieces merged."""
    cuts = [c.t for c in solar.crossings(lon, lat, t_start, t_end, [solar.SUNRISE_ELEVATION_DEG, solar.CIVIL_TWILIGHT_ELEVATION_DEG])]
    if profile == "custom":
        cuts += [c.t for c in solar.crossings(lon, lat, t_start, t_end, [min_elev], apparent=True)]
    edges = sorted({t_start, t_end, *[t for t in cuts if t_start < t < t_end]})
    if len(edges) < 2:
        return []
    mids = np.array([(a + b) / 2.0 for a, b in zip(edges[:-1], edges[1:], strict=True)])
    geo = solar.geometric_elevation(lon, lat, mids)
    app = geo + solar.refraction_deg(geo)
    pieces: list[tuple[float, float, str, str]] = []
    for (a, b), g, ap in zip(zip(edges[:-1], edges[1:], strict=True), geo.tolist(), app.tolist(), strict=True):
        if b - a <= 1e-6:
            continue
        kind = _kind(g)
        status = _status(profile, kind, ap, min_elev)
        if pieces and pieces[-1][2] == kind and pieces[-1][3] == status and abs(pieces[-1][1] - a) < 1e-6:
            pieces[-1] = (pieces[-1][0], b, kind, status)
        else:
            pieces.append((a, b, kind, status))
    return pieces


def _segments(pieces: Iterable[tuple[float, float, str, str]], t0: float, day: date, offset: int) -> list[DaylightSegment]:
    return [
        DaylightSegment(kind=kind, status=status, **_segment_fields(a, b, t0, day, offset))  # type: ignore[arg-type]
        for a, b, kind, status in pieces
    ]


def _minutes_text(minutes: float) -> str:
    whole = int(round(minutes))
    if whole >= 120:
        hours, rest = divmod(whole, 60)
        return f"{hours} h {rest} min" if rest else f"{hours} h"
    return f"{whole} min"


def _missing_events_note(inst: _SunInstants) -> str:
    """Why window minutes cannot be named against sunrise/sunset or civil dawn/dusk ("" if they can)."""
    if inst.polar_night:
        return "polar night: the sun does not rise on this date"
    if inst.polar_day:
        return "polar day: the sun does not set on this date"
    missing = [
        name
        for name, t in (("sunrise", inst.sunrise), ("sunset", inst.sunset), ("civil dawn", inst.civil_dawn), ("civil dusk", inst.civil_dusk))
        if t is None
    ]
    return f"no {' or '.join(missing)} on this date" if missing else ""


def _daylight_statement(
    profile: DaylightProfile,
    window: PlannedWindow | None,
    pieces: list[tuple[float, float, str, str]],
    inst: _SunInstants,
    min_elev: float,
    reaches_min: bool,
) -> str:
    """One evidence sentence for the planned window. Minutes are named against sunrise/sunset,
    civil dawn/dusk or the minimum elevation only when that event exists on the date (polar days
    and nights, and the days around them, have none): otherwise they are totals with the reason."""
    if window is None:
        return "No planned window given: the 24 h ribbon shows night, civil twilight and day for the date."
    noon = inst.noon

    def minutes(status: str, after_noon: bool | None = None, kind: str | None = None) -> float:
        total = 0.0
        for a, b, k, s in pieces:
            if s != status or (kind is not None and k != kind):
                continue
            if after_noon is None:
                total += b - a
            elif after_noon:
                total += max(0.0, b - max(a, noon))
            else:
                total += max(0.0, min(b, noon) - a)
        return total / 60.0

    span = f"{window.start_local}–{window.end_local}"
    outside = minutes("outside")
    lighting = minutes("lighting")
    if outside < 0.5 and lighting < 0.5:
        return f"The planned window {span} lies entirely within the profile."
    parts: list[str] = []
    unnamed = 0.0  # minutes that cannot be placed before sunrise / past sunset (no such event)
    if profile == "india_daylight":
        for after_noon, event, text in (
            (False, inst.sunrise, "window starts {} before sunrise"),
            (True, inst.sunset, "window extends {} past sunset"),
        ):
            m = minutes("outside", after_noon)
            if event is None:
                unnamed += m
            elif m >= 0.5:
                parts.append(text.format(_minutes_text(m)))
        if unnamed >= 0.5:
            parts.append(f"{_minutes_text(unnamed)} of the window outside the profile")
    elif profile == "faa_107":
        unnamed_night = 0.0
        for after_noon, sun_event, dark_event, edge, dark_edge in (
            (False, inst.sunrise, inst.civil_dawn, "before sunrise", "before civil dawn"),
            (True, inst.sunset, inst.civil_dusk, "past sunset", "past civil dusk"),
        ):
            twilight = minutes("lighting", after_noon)
            night = minutes("outside", after_noon, "night")
            if sun_event is None:
                unnamed += twilight
            elif twilight >= 0.5:
                parts.append(f"{_minutes_text(twilight)} {edge} in civil twilight (anti-collision lighting required)")
            if dark_event is None:
                unnamed_night += night
            elif night >= 0.5:
                parts.append(f"{_minutes_text(night)} {dark_edge} at night (flagged)")
        if unnamed >= 0.5:
            parts.append(f"{_minutes_text(unnamed)} in civil twilight (anti-collision lighting required)")
        if unnamed_night >= 0.5:
            parts.append(f"{_minutes_text(unnamed_night)} at night (flagged)")
        unnamed += unnamed_night
    elif not reaches_min:
        parts.append(f"the sun stays below {min_elev:g}° on this date: {_minutes_text(outside)} of the window outside the profile")
    else:
        before, after = minutes("outside", False), minutes("outside", True)
        if before >= 0.5:
            parts.append(f"{_minutes_text(before)} before the sun reaches {min_elev:g}°")
        if after >= 0.5:
            parts.append(f"{_minutes_text(after)} after it drops below {min_elev:g}°")
    note = _missing_events_note(inst) if unnamed >= 0.5 else ""
    if note:
        parts.append(note)
    text = "; ".join(parts)
    return text[:1].upper() + text[1:] + "."


def daylight_check(
    lon: float,
    lat: float,
    day: date,
    utc_offset_min: int,
    profile: DaylightProfile,
    window: tuple[str, str] | None = None,
    min_elevation_deg: float = 0.0,
) -> DaylightCheck:
    """Night / civil twilight / day for the local day and the planned window under a profile."""
    t0 = local_midnight_utc(day, utc_offset_min)
    spec = DAYLIGHT_PROFILES[profile]
    day_pieces = _split(lon, lat, t0, t0 + SECONDS_PER_DAY, profile, min_elevation_deg)
    planned: PlannedWindow | None = None
    window_pieces: list[tuple[float, float, str, str]] = []
    if window is not None:
        ws = t0 + _parse_hhmm(window[0]) * 60.0
        we = t0 + _parse_hhmm(window[1]) * 60.0
        crosses = we <= ws
        if crosses:
            we += SECONDS_PER_DAY
        planned = PlannedWindow(crosses_midnight=crosses, **_segment_fields(ws, we, t0, day, utc_offset_min))
        window_pieces = _split(lon, lat, ws, we, profile, min_elevation_deg)
    inst = _sun_instants(lon, lat, t0)
    reaches_min = float(solar.apparent_elevation(lon, lat, inst.noon)[0]) >= min_elevation_deg
    outside =sum(b - a for a, b, _k, s in window_pieces if s == "outside") / 60.0 if planned else None
    lighting = sum(b - a for a, b, _k, s in window_pieces if s == "lighting") / 60.0 if planned else None
    return DaylightCheck(
        profile=profile,
        profile_label=spec.label if profile != "custom" else f"Sun at least {min_elevation_deg:g}° high",
        rule=spec.rule,
        min_elevation_deg=float(min_elevation_deg) if profile == "custom" else None,
        window=planned,
        minutes_outside=round(outside, 2) if outside is not None else None,
        minutes_lighting=round(lighting, 2) if lighting is not None else None,
        segments=_segments(day_pieces, t0, day, utc_offset_min),
        window_segments=_segments(window_pieces, t0, day, utc_offset_min),
        statement=_daylight_statement(profile, planned, window_pieces, inst, min_elevation_deg, reaches_min),
    )


def sun_window(lon: float, lat: float, day: date, utc_offset_min: int, min_deg: float, max_deg: float, max_elevation_deg: float) -> SunWindow:
    """When the apparent sun elevation is between ``min_deg`` and ``max_deg`` on the local day."""
    t0 = local_midnight_utc(day, utc_offset_min)
    segments = [
        TimeSegment(**_segment_fields(a, b, t0, day, utc_offset_min))
        for a, b, e in solar.elevation_intervals(lon, lat, t0, t0 + SECONDS_PER_DAY, [min_deg, max_deg], apparent=True)
        if min_deg <= e <= max_deg
    ]
    return SunWindow(
        min_deg=float(min_deg),
        max_deg=float(max_deg),
        segments=segments,
        total_minutes=round(sum(s.minutes for s in segments), 2),
        reached=max_elevation_deg >= min_deg,
    )


def magnetic_summary(lon: float, lat: float, altitude_m: float, at_unix: float) -> MagneticSummary:
    """WMM2025 elements at a point, ``altitude_m`` above the ellipsoid, at a Unix time."""
    moment = _utc(at_unix)
    fraction = (moment.hour * 3600 + moment.minute * 60 + moment.second) / SECONDS_PER_DAY
    year = decimal_year(moment.date(), fraction)
    model = load_wmm()
    e = wmm(lon, lat, altitude_m / 1000.0, year, model)
    return MagneticSummary(
        declination_deg=round(e.declination_deg, 3),
        inclination_deg=round(e.inclination_deg, 3),
        horizontal_nt=round(e.horizontal_nt, 1),
        total_nt=round(e.total_nt, 1),
        north_nt=round(e.north_nt, 1),
        east_nt=round(e.east_nt, 1),
        down_nt=round(e.down_nt, 1),
        declination_change_deg_per_year=round(e.declination_dot_deg, 4),
        grid_variation_deg=round(e.grid_variation_deg, 3) if e.grid_variation_deg is not None else None,
        model=e.model,
        model_epoch=model.epoch,
        valid_until=f"{int(model.valid_until) - 1}-12-31",
        within_validity=e.within_validity,
        altitude_m=round(altitude_m, 1),
        decimal_year=round(year, 4),
        blackout=e.blackout,
        caution=e.caution,
    )


def _signed(value: float) -> str:
    return f"{'+' if value >= 0 else '−'}{abs(value):.1f}"


def wind_at_height(
    surface_wind_ms: float,
    height_agl_m: float,
    *,
    reference_height_m: float = 10.0,
    shear_exponent: float = 1.0 / 7.0,
    gust_ms: float | None = None,
    wind_limit_ms: float | None = None,
) -> WindAtHeight:
    """Power-law wind at height: ``v(z) = v_ref·(z/z_ref)^α`` for the mean wind and the gust.

    Example: 10 m/s at 10 m with α = 1/7 → 13.9 m/s at 100 m. Margins are ``limit − estimate``.
    """
    if not (math.isfinite(height_agl_m) and height_agl_m > 0 and reference_height_m > 0):
        raise AnalysisError("invalid_params", "Heights for the wind estimate must be positive",
                            details={"height_agl_m": height_agl_m, "reference_height_m": reference_height_m})
    factor = (height_agl_m / reference_height_m) ** shear_exponent
    wind = surface_wind_ms * factor
    gust = gust_ms * factor if gust_ms is not None else None
    margin = wind_limit_ms - wind if wind_limit_ms is not None else None
    gust_margin = wind_limit_ms - gust if wind_limit_ms is not None and gust is not None else None
    statement = f"est. {wind:.1f} m/s at {height_agl_m:g} m"
    if gust is not None:
        statement += f" (gusts {gust:.1f} m/s)"
    if wind_limit_ms is not None and margin is not None:
        statement += f" vs {wind_limit_ms:g} m/s rated ({_signed(margin)} m/s"
        if gust_margin is not None:
            statement += f", gusts {_signed(gust_margin)} m/s"
        statement += ")"
    return WindAtHeight(
        surface_wind_ms=float(surface_wind_ms),
        gust_ms=float(gust_ms) if gust_ms is not None else None,
        reference_height_m=float(reference_height_m),
        height_agl_m=float(height_agl_m),
        shear_exponent=round(float(shear_exponent), 6),
        factor=round(factor, 6),
        wind_at_height_ms=round(wind, 3),
        gust_at_height_ms=round(gust, 3) if gust is not None else None,
        wind_limit_ms=float(wind_limit_ms) if wind_limit_ms is not None else None,
        margin_ms=round(margin, 3) if margin is not None else None,
        gust_margin_ms=round(gust_margin, 3) if gust_margin is not None else None,
        statement=statement,
    )


@dataclass
class EmiEvidence:
    """EMI items (nearest first), their map features and what could be assessed."""

    items: list[EmiItem]
    features: list[dict[str, Any]]
    truncated: bool
    power_assessed: bool
    masts_assessed: bool


def _target_noun(geom: BaseGeometry) -> str:
    """What the analysed geometry is called in labels: area, route or point."""
    if polygonal_part(geom) is not None:
        return "area"
    return "point" if geom.geom_type in ("Point", "MultiPoint") else "route"


def _emi_label(kind: str, voltage_kv: float | None, distance_m: float, target: str = "area") -> str:
    """E.g. "220 kV power line · crosses the area", "Mast · 85 m"."""
    noun = EMI_LABEL.get(kind, kind)
    head = f"{voltage_kv:g} kV {noun}" if voltage_kv is not None else noun[:1].upper() + noun[1:]
    if distance_m > 0.05:
        where = f"{distance_m:.0f} m"
    elif target == "point":
        where = "at the point"
    elif kind in _LINE_KINDS:
        where = f"crosses the {target}"
    else:
        where = "inside the area" if target == "area" else "on the route"
    return f"{head} · {where}"


def emi_proximity(geom: BaseGeometry, pack: DataPack | None, radius_m: float, max_items: int = MAX_EMI_ITEMS) -> EmiEvidence:
    """Power infrastructure and communication masts within ``radius_m`` of a WGS84 geometry."""
    if pack is None:
        return EmiEvidence([], [], False, False, False)
    target = _target_noun(geom)
    candidates: list[tuple[EmiItem, dict[str, Any]]] = []
    min_lon, min_lat, max_lon, max_lat = geom.bounds
    pad_lat = EMI_CLIP_FACTOR * radius_m / 110_540.0
    pad_lon = EMI_CLIP_FACTOR * radius_m / (111_320.0 * max(0.05, math.cos(math.radians(max(abs(min_lat), abs(max_lat))))))
    clip = box(min_lon - pad_lon, min_lat - pad_lat, max_lon + pad_lon, max_lat + pad_lat)

    power_assessed = pack.has("power")
    if power_assessed:
        for hit in pack.power().near_geometry(geom, radius_m):
            f = hit.feature
            kind = POWER_EMI_KINDS[f.kind]
            item = EmiItem(
                id=f.id,
                kind=kind,
                name=f.name,
                voltage_kv=f.voltage_kv,
                distance_m=round(hit.distance_m, 1),
                lon=round(hit.nearest_lon, 7),
                lat=round(hit.nearest_lat, 7),
                inside=hit.distance_m <= 0.05,
                source="power",
            )
            shown = f.geometry.intersection(clip) if kind in _LINE_KINDS else f.geometry
            if shown.is_empty:
                shown = f.geometry
            feature = {
                "type": "Feature",
                "id": f.id,
                "geometry": geometry_to_geojson(shown, 7),
                "properties": {
                    "kind": kind,
                    "name": f.name,
                    "voltage_kv": f.voltage_kv,
                    "distance_m": item.distance_m,
                    "label": _emi_label(kind, f.voltage_kv, hit.distance_m, target),
                    "source": "power",
                },
            }
            candidates.append((item, feature))

    masts_assessed = pack.has("obstacles")
    if masts_assessed:
        wanted = dict(OBSTACLE_EMI_KINDS)
        if not power_assessed:
            wanted["power_tower"] = "pylon"  # no power layer: pylons are the best power evidence
        for point, distance in pack.obstacles().near_geometry(geom, radius_m):
            mast_kind = wanted.get(str(point.get("kind") or ""))
            if mast_kind is None:
                continue
            kind = mast_kind
            height = point.get("height_m")
            height_m = float(height) if isinstance(height, int | float) and math.isfinite(float(height)) else None
            name = point.get("name")
            item = EmiItem(
                id=str(point.id),
                kind=kind,
                name=str(name) if name else None,
                height_m=height_m,
                distance_m=round(distance, 1),
                lon=round(point.lon, 7),
                lat=round(point.lat, 7),
                inside=distance <= 0.05,
                source="obstacles",
            )
            feature = {
                "type": "Feature",
                "id": str(point.id),
                "geometry": {"type": "Point", "coordinates": [round(point.lon, 7), round(point.lat, 7)]},
                "properties": {
                    "kind": kind,
                    "name": item.name,
                    "voltage_kv": None,
                    "height_m": height_m,
                    "distance_m": item.distance_m,
                    "label": _emi_label(kind, None, distance, target),
                    "source": "obstacles",
                },
            }
            candidates.append((item, feature))

    candidates.sort(key=lambda c: (c[0].distance_m, c[0].kind, c[0].id))
    kept = candidates[:max_items]
    return EmiEvidence(
        items=[c[0] for c in kept],
        features=[c[1] for c in kept],
        truncated=len(candidates) > max_items,
        power_assessed=power_assessed,
        masts_assessed=masts_assessed,
    )


def _parse_geometry(obj: dict[str, Any]) -> BaseGeometry:
    geom = geojson_to_geometry(obj)
    if geom.is_empty:
        raise AnalysisError("invalid_geometry", "The operation geometry is empty")
    geom = drop_z(geom)
    fixed, _ = repair(geom)
    if fixed is None or fixed.is_empty:
        raise AnalysisError("invalid_geometry", "The operation geometry is empty after repair")
    return fixed


def _evaluation_point(geom: BaseGeometry) -> Point:
    # Across the antimeridian the plain lon/lat centroid lands near 0° (the other side of the
    # Earth): take it of the unwrapped geometry and bring the longitude back to ±180°.
    unwrapped = unwrap_antimeridian(geom)
    centre = unwrapped.centroid
    poly = polygonal_part(unwrapped)
    if poly is not None and not poly.is_empty and not poly.contains(centre):
        centre = unwrapped.representative_point()
    return Point(centre.x - 360.0, centre.y) if centre.x > 180.0 else centre


def _ground_m(ctx: AnalysisContext, lon: float, lat: float) -> float | None:
    if not ctx.has_layer("dem") or ctx.datapack is None:
        return None
    dem = ctx.datapack.dem()
    if dem is None:
        return None
    value = float(np.asarray(dem.sample([lon], [lat]), dtype=np.float64).ravel()[0])
    return value if math.isfinite(value) else None


def _feature(geom: BaseGeometry, props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry_to_geojson(geom, 8), "properties": props}


def _fc(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _event_text(event: SunEvent | None) -> str:
    if event is None:
        return "none"
    return _hhmm_with_day(event.local, event.day_offset)


def _sun_fact(summary: SunSummary, label: str) -> str:
    if summary.polar_day:
        return "Polar day: the sun does not set on this date"
    if summary.polar_night:
        return "Polar night: the sun does not rise on this date"
    return (
        f"Sunrise {_event_text(summary.sunrise)}, sunset {_event_text(summary.sunset)}, "
        f"civil dusk {_event_text(summary.civil_dusk)} ({label})"
    )


def _method_text(params: EnvironmentParams, ground_m: float | None, magnetic: MagneticSummary, emi_radius: float | None) -> str:
    profile = DAYLIGHT_PROFILES[params.daylight_profile]
    altitude = (
        f"DEM ground {ground_m:.0f} m + {params.height_agl_m:g} m AGL"
        if ground_m is not None
        else f"{params.height_agl_m:g} m (no terrain model: ground taken as the ellipsoid)"
    )
    parts = [
        "Sun: NOAA solar calculator equations (Meeus); events are crossings of the sun centre's geometric "
        "elevation: −0.833° for sunrise/sunset (refraction + semi-diameter) and −6° for civil dawn/dusk, "
        "bisected to under a second around the solar noon of the local date; the track and elevations "
        "shown are apparent (NOAA refraction).",
        f"Daylight profile '{profile.label}': {profile.rule}",
        f"Magnetic field: {magnetic.model} spherical-harmonic model (degree 12, epoch {magnetic.model_epoch:g}, "
        f"secular variation applied to {magnetic.decimal_year:.3f}) at {altitude}; blackout H < 2000 nT, "
        "caution H < 6000 nT.",
    ]
    if params.surface_wind_ms is not None:
        parts.append(
            f"Wind: power law v(z) = v_ref·(z/z_ref)^α with v_ref = {params.surface_wind_ms:g} m/s at "
            f"z_ref = {params.reference_height_m:g} m and α = {params.shear_exponent:.3f}; a near-neutral "
            "approximation, not a forecast."
        )
    if emi_radius is not None:
        parts.append(
            f"EMI: power lines, cables, substations and plants (pack 'power' layer) and communication towers "
            f"and masts (pack 'obstacles') within {emi_radius:g} m of the whole geometry, measured in a local "
            "azimuthal-equidistant frame; proximity evidence, not an interference prediction."
        )
    parts.append(
        f"Photogrammetry sun window: apparent elevation between {params.sun_min_deg:g}° and {params.sun_max_deg:g}°."
    )
    return " ".join(parts)


def run(params: EnvironmentParams, ctx: AnalysisContext) -> EnvironmentResult:
    """Environment evidence for an operation geometry. Uses the ``dem``, ``power`` and
    ``obstacles`` layers when the data pack has them; works without a data pack (no EMI then)."""
    issues: list[Issue] = []
    ctx.step("Preparing geometry", 0.05)
    geom = _parse_geometry(params.geometry)
    point = _evaluation_point(geom)
    lon, lat = float(point.x), float(point.y)
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise AnalysisError("invalid_geometry", "The operation geometry is not in WGS84 degrees",
                            details={"lon": lon, "lat": lat})
    day = params.date
    offset = int(params.utc_offset_min)
    label = offset_label(offset)
    t0 = local_midnight_utc(day, offset)

    ctx.step("Sun position and twilight", 0.15)
    sun_summary = sun_times(lon, lat, day, offset)
    if sun_summary.polar_day:
        issues.append(Issue(code="polar_day", message="Polar day: the sun stays above the horizon all day.", severity=Severity.INFO))
    elif sun_summary.polar_night:
        issues.append(Issue(code="polar_night", message="Polar night: the sun stays below the horizon all day.", severity=Severity.INFO))

    ctx.step("Daylight profile", 0.3)
    window = (params.window_start, params.window_end) if params.window_start and params.window_end else None
    daylight = daylight_check(lon, lat, day, offset, params.daylight_profile, window, params.min_sun_elevation_deg)
    if daylight.window is not None:
        if daylight.window.crosses_midnight:
            issues.append(Issue(
                code="window_crosses_midnight",
                message=f"The planned window ends on the next day ({daylight.window.end_local}).",
                severity=Severity.INFO,
            ))
        if (daylight.minutes_outside or 0.0) >= 0.5:
            issues.append(Issue(code="window_outside_profile", message=daylight.statement, severity=Severity.WARNING))
        elif (daylight.minutes_lighting or 0.0) >= 0.5:
            issues.append(Issue(code="lighting_required", message=daylight.statement, severity=Severity.INFO))

    ctx.step("Magnetic field (WMM2025)", 0.45)
    ground = _ground_m(ctx, lon, lat)
    if ground is None:
        issues.append(Issue(
            code="no_terrain",
            message="No terrain model here: the magnetic field is evaluated at the planned height above the ellipsoid.",
            severity=Severity.INFO,
        ))
    at = t0 + (_parse_hhmm(params.window_start) * 60.0 if params.window_start else _HALF_DAY)
    magnetic = magnetic_summary(lon, lat, (ground or 0.0) + params.height_agl_m, at)
    if not magnetic.within_validity:
        issues.append(Issue(
            code="wmm_out_of_range",
            message=f"{magnetic.model} is valid from {magnetic.model_epoch:g} to {magnetic.valid_until}; "
                    f"the date {day.isoformat()} is outside it, so the field is extrapolated.",
            severity=Severity.WARNING,
        ))
    if magnetic.blackout:
        issues.append(Issue(
            code="magnetic_blackout",
            message=f"Horizontal field {magnetic.horizontal_nt:.0f} nT is below 2000 nT: WMM blackout zone, compass headings are unreliable.",
            severity=Severity.CRITICAL,
        ))
    elif magnetic.caution:
        issues.append(Issue(
            code="magnetic_caution",
            message=f"Horizontal field {magnetic.horizontal_nt:.0f} nT is below 6000 nT: WMM caution zone, compass accuracy may be degraded.",
            severity=Severity.WARNING,
        ))

    ctx.step("Wind at height", 0.55)
    wind: WindAtHeight | None = None
    if params.surface_wind_ms is not None:
        wind = wind_at_height(
            params.surface_wind_ms,
            params.height_agl_m,
            reference_height_m=params.reference_height_m,
            shear_exponent=params.shear_exponent,
            gust_ms=params.gust_ms,
            wind_limit_ms=params.wind_limit_ms,
        )
        if wind.margin_ms is not None and wind.margin_ms < 0:
            issues.append(Issue(
                code="wind_above_limit",
                message=f"Estimated wind at {params.height_agl_m:g} m ({wind.wind_at_height_ms:.1f} m/s) is "
                        f"{-wind.margin_ms:.1f} m/s above the {wind.wind_limit_ms:g} m/s rated limit.",
                severity=Severity.WARNING,
            ))
        elif wind.gust_margin_ms is not None and wind.gust_margin_ms < 0:
            issues.append(Issue(
                code="gust_above_limit",
                message=f"Estimated gusts at {params.height_agl_m:g} m ({wind.gust_at_height_ms or 0.0:.1f} m/s) are "
                        f"{-wind.gust_margin_ms:.1f} m/s above the {wind.wind_limit_ms:g} m/s rated limit.",
                severity=Severity.WARNING,
            ))

    ctx.step("Interference proximity", 0.7)
    emi = EmiEvidence([], [], False, False, False)
    emi_radius: float | None = None
    search_ring: BaseGeometry | None = None
    if params.include_emi:
        emi_radius = float(params.emi_radius_m)
        emi = emi_proximity(geom, ctx.datapack, emi_radius)
        frame = LocalFrame.for_geometry(geom)
        search_ring = frame.to_wgs84(frame.to_local(geom).buffer(emi_radius, quad_segs=16))
        if not search_ring.is_valid:
            search_ring = shapely.make_valid(search_ring)
        if ctx.datapack is not None and not ctx.datapack.covers(search_ring.bounds):
            issues.append(Issue(
                code="emi_partial_coverage",
                message=f"The {emi_radius:g} m search area extends beyond data pack '{ctx.datapack.name}': power "
                        "lines and masts outside the pack were not checked, so the list may be incomplete.",
                severity=Severity.WARNING,
            ))
        if ctx.datapack is None:
            issues.append(Issue(
                code="emi_not_assessed",
                message="No data pack covers this area: power lines, substations and masts were not checked.",
                severity=Severity.WARNING,
            ))
        else:
            if not emi.power_assessed:
                issues.append(Issue(
                    code="power_not_assessed",
                    message=f"Data pack '{ctx.datapack.name}' has no power layer: power lines and substations were "
                            "not checked (pylons from the obstacle layer are listed instead).",
                    severity=Severity.WARNING,
                ))
            if not emi.masts_assessed:
                issues.append(Issue(
                    code="masts_not_assessed",
                    message=f"Data pack '{ctx.datapack.name}' has no obstacle layer: communication towers and masts "
                            "were not checked.",
                    severity=Severity.WARNING,
                ))
            if ctx.datapack.synthetic:
                issues.append(Issue(
                    code="synthetic_data",
                    message=f"Data pack '{ctx.datapack.name}' is synthetic: power lines and masts are invented for testing.",
                    severity=Severity.WARNING,
                ))
        if emi.truncated:
            issues.append(Issue(
                code="emi_truncated",
                message=f"More than {MAX_EMI_ITEMS} features within {emi_radius:g} m; the nearest {MAX_EMI_ITEMS} are listed.",
                severity=Severity.INFO,
            ))
        crossing = [i for i in emi.items if i.inside and i.kind in _LINE_KINDS]
        if crossing:
            first = crossing[0]
            volts = f"{first.voltage_kv:g} kV " if first.voltage_kv is not None else ""
            issues.append(Issue(
                code="power_line_crossing",
                message=f"A {volts}{EMI_LABEL[first.kind]} crosses or touches the {_target_noun(geom)}. Proximity evidence: aircraft "
                        "makers warn of compass interference near power lines.",
                severity=Severity.WARNING,
            ))

    ctx.step("Sun window", 0.82)
    window_result = sun_window(lon, lat, day, offset, params.sun_min_deg, params.sun_max_deg, sun_summary.max_elevation_deg)
    if not window_result.reached:
        issues.append(Issue(
            code="sun_window_not_reached",
            message=f"The sun does not reach {params.sun_min_deg:g}° on this date (maximum {sun_summary.max_elevation_deg:.1f}°).",
            severity=Severity.INFO,
        ))

    ctx.step("Building layers", 0.9)
    facts = [_sun_fact(sun_summary, label)]
    if daylight.window is not None:
        facts.append(daylight.statement)
    facts.append(
        f"Declination {_signed(magnetic.declination_deg)}° ({magnetic.model}), inclination {magnetic.inclination_deg:.1f}°"
    )
    if wind is not None:
        facts.append(wind.statement)
    nearest_power = next((i for i in emi.items if i.source == "power"), None)
    if nearest_power is not None:
        label_text = _emi_label(nearest_power.kind, nearest_power.voltage_kv, nearest_power.distance_m, _target_noun(geom))
        facts.append(label_text.replace(" · ", " ") if nearest_power.inside else label_text.replace(" · ", " ") + " away")

    layers: dict[str, Any] = {
        "input": _fc([_feature(geom, {"role": "input", "geometry_type": geometry_type_name(geom)})]),
        "point": _fc([_feature(point, {
            "role": "evaluation_point",
            "sunrise": sun_summary.sunrise.local if sun_summary.sunrise else None,
            "sunset": sun_summary.sunset.local if sun_summary.sunset else None,
            "declination_deg": magnetic.declination_deg,
            "utc_offset": label,
        })]),
        "emi": _fc(emi.features),
    }
    if emi_radius is not None and search_ring is not None:
        layers["emi_search"] = _fc([_feature(search_ring, {"role": "emi_search", "radius_m": emi_radius})])
    issues.extend(ctx.issues)

    return EnvironmentResult(
        point=[round(lon, 7), round(lat, 7)],
        geometry_type=geometry_type_name(geom),
        date=day.isoformat(),
        utc_offset_min=offset,
        utc_offset_label=label,
        height_agl_m=float(params.height_agl_m),
        sun=sun_summary,
        daylight=daylight,
        magnetic=magnetic,
        wind=wind,
        emi=emi.items,
        emi_assessed=bool(params.include_emi and ctx.datapack is not None),
        emi_radius_m=emi_radius,
        emi_truncated=emi.truncated,
        sun_window=window_result,
        facts=facts,
        issues=issues,
        method=_method_text(params, ground, magnetic, emi_radius),
        disclaimer=DISCLAIMER_ENVIRONMENT,
        layers=layers,
    )
