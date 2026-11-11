"""Sun position, sunrise/sunset, civil twilight and solar noon (NOAA solar calculator equations).

The equations are those of the NOAA Global Monitoring Laboratory solar calculator
(https://gml.noaa.gov/grad/solcalc/, ``main.js`` and the NOAA_Solar_Calculations spreadsheet),
which follow Meeus, *Astronomical Algorithms* (1991). Time is UT (ΔT is ignored, as NOAA does).

* Julian century ``T = (JD − 2451545)/36525``.
* Mean longitude ``L0 = 280.46646 + T(36000.76983 + 0.0003032T)`` (mod 360°), mean anomaly
  ``M = 357.52911 + T(35999.05029 − 0.0001537T)``, orbit eccentricity
  ``e = 0.016708634 − T(0.000042037 + 0.0000001267T)``.
* Equation of centre ``C = sin M (1.914602 − T(0.004817 + 0.000014T)) + sin 2M (0.019993 −
  0.000101T) + 0.000289 sin 3M``; apparent longitude ``λ = L0 + C − 0.00569 − 0.00478 sin Ω``
  with ``Ω = 125.04 − 1934.136T``.
* Obliquity ``ε = ε0 + 0.00256 cos Ω``, ``ε0 = 23° 26′ (21.448 − T(46.815 + T(0.00059 −
  0.001813T)))″``; declination ``δ = asin(sin ε sin λ)``.
* Equation of time (minutes) ``E = 4·deg(y sin 2L0 − 2e sin M + 4ey sin M cos 2L0 −
  ½y² sin 4L0 − 1.25e² sin 2M)`` with ``y = tan²(ε/2)``.
* True solar time (minutes) ``= UTC minutes + E + 4·lon``; hour angle ``H = TST/4 − 180°``;
  ``cos z = sin φ sin δ + cos φ cos δ cos H``; azimuth clockwise from north as NOAA computes it.
* Apparent elevation adds NOAA's refraction approximation (Saemundsson-style piecewise fit).

Events are the instants at which the *geometric* elevation crosses a threshold: −0.833° for
sunrise and sunset (34′ of refraction + the 16′ solar semi-diameter, NOAA's zenith 90.833°) and
−6° for civil dawn and dusk. Each day is sampled every minute and every sign change is bisected
to below one second, which reproduces NOAA's iterated closed form to a few seconds and also finds
polar days, polar nights and grazing days. Solar noon is the instant of ``H = 0`` (NOAA's
two-pass iteration).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = [
    "CIVIL_TWILIGHT_ELEVATION_DEG",
    "SUNRISE_ELEVATION_DEG",
    "Crossing",
    "SolarPosition",
    "crossings",
    "elevation_intervals",
    "julian_day",
    "refraction_deg",
    "solar_noon",
    "solar_position",
    "sun_declination_eqtime",
]

UNIX_EPOCH_JD = 2440587.5
"""Julian day of 1970-01-01T00:00:00Z."""

SUNRISE_ELEVATION_DEG = -0.833
"""Geometric elevation of the sun's centre at sunrise/sunset (NOAA zenith 90.833°)."""

CIVIL_TWILIGHT_ELEVATION_DEG = -6.0
"""Geometric elevation of the sun's centre at civil dawn/dusk."""

SECONDS_PER_DAY = 86_400.0
_SCAN_STEP_S = 60.0
_BISECT_TOL_S = 0.5

FloatArray = NDArray[np.float64]


def julian_day(t_unix: ArrayLike) -> FloatArray:
    """Julian day (UT) of Unix timestamps in seconds."""
    return np.asarray(t_unix, dtype=np.float64) / SECONDS_PER_DAY + UNIX_EPOCH_JD


def sun_declination_eqtime(t_unix: ArrayLike) -> tuple[FloatArray, FloatArray]:
    """Solar declination (degrees) and equation of time (minutes) at Unix timestamps (NOAA)."""
    t = (julian_day(t_unix) - 2451545.0) / 36525.0
    l0 = np.mod(280.46646 + t * (36000.76983 + t * 0.0003032), 360.0)
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    m_rad = np.radians(m)
    c = (
        np.sin(m_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + np.sin(2.0 * m_rad) * (0.019993 - 0.000101 * t)
        + np.sin(3.0 * m_rad) * 0.000289
    )
    omega = np.radians(125.04 - 1934.136 * t)
    apparent_long = l0 + c - 0.00569 - 0.00478 * np.sin(omega)
    seconds = 21.448 - t * (46.8150 + t * (0.00059 - t * 0.001813))
    obliquity = 23.0 + (26.0 + seconds / 60.0) / 60.0 + 0.00256 * np.cos(omega)
    eps = np.radians(obliquity)
    declination = np.degrees(np.arcsin(np.sin(eps) * np.sin(np.radians(apparent_long))))
    y = np.tan(eps / 2.0) ** 2
    l0_rad = np.radians(l0)
    eqtime = 4.0 * np.degrees(
        y * np.sin(2.0 * l0_rad)
        - 2.0 * e * np.sin(m_rad)
        + 4.0 * e * y * np.sin(m_rad) * np.cos(2.0 * l0_rad)
        - 0.5 * y * y * np.sin(4.0 * l0_rad)
        - 1.25 * e * e * np.sin(2.0 * m_rad)
    )
    return declination, eqtime


def refraction_deg(geometric_elevation_deg: ArrayLike) -> FloatArray:
    """NOAA's atmospheric refraction approximation (degrees) for a geometric elevation.

    0 above 85°; ``(58.1/tan e − 0.07/tan³ e + 0.000086/tan⁵ e)″`` above 5°;
    ``(1735 + e(−518.2 + e(103.4 + e(−12.79 + 0.711e))))″`` above −0.575°;
    ``(−20.774/tan e)″`` below.
    """
    elev = np.asarray(geometric_elevation_deg, dtype=np.float64)
    out = np.zeros_like(elev)
    with np.errstate(divide="ignore", invalid="ignore"):
        te = np.tan(np.radians(elev))
        high = (elev > 5.0) & (elev <= 85.0)
        low = (elev > -0.575) & (elev <= 5.0)
        below = elev <= -0.575
        out = np.where(high, 58.1 / te - 0.07 / te**3 + 0.000086 / te**5, out)
        out = np.where(low, 1735.0 + elev * (-518.2 + elev * (103.4 + elev * (-12.79 + elev * 0.711))), out)
        out = np.where(below, -20.774 / te, out)
    return np.asarray(np.where(np.isfinite(out), out / 3600.0, 0.0), dtype=np.float64)


@dataclass(frozen=True)
class SolarPosition:
    """Sun position at a set of instants (arrays aligned with the input timestamps)."""

    geometric_elevation_deg: FloatArray
    elevation_deg: FloatArray
    """Apparent elevation: geometric + NOAA refraction."""
    azimuth_deg: FloatArray
    """Clockwise from true north, 0–360°."""


def _hour_angle_deg(lon: float, t_unix: FloatArray, eqtime: FloatArray) -> FloatArray:
    minutes = np.mod(t_unix, SECONDS_PER_DAY) / 60.0
    true_solar = np.mod(minutes + eqtime + 4.0 * lon, 1440.0)
    hour_angle = true_solar / 4.0 - 180.0
    return np.where(hour_angle < -180.0, hour_angle + 360.0, hour_angle)


def _geometric(lon: float, lat: float, t_unix: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
    """(zenith°, declination°, hour angle°) at Unix timestamps."""
    decl, eqtime = sun_declination_eqtime(t_unix)
    hour_angle = _hour_angle_deg(lon, t_unix, eqtime)
    lat_r = math.radians(lat)
    decl_r = np.radians(decl)
    csz = math.sin(lat_r) * np.sin(decl_r) + math.cos(lat_r) * np.cos(decl_r) * np.cos(np.radians(hour_angle))
    zenith = np.degrees(np.arccos(np.clip(csz, -1.0, 1.0)))
    return zenith, decl, hour_angle


def geometric_elevation(lon: float, lat: float, t_unix: ArrayLike) -> FloatArray:
    """Geometric (unrefracted) elevation of the sun's centre, in degrees."""
    t = np.atleast_1d(np.asarray(t_unix, dtype=np.float64))
    zenith, _, _ = _geometric(lon, lat, t)
    return 90.0 - zenith


def apparent_elevation(lon: float, lat: float, t_unix: ArrayLike) -> FloatArray:
    """Apparent elevation (geometric + refraction), in degrees."""
    geo = geometric_elevation(lon, lat, t_unix)
    return geo + refraction_deg(geo)


def solar_position(lon: float, lat: float, t_unix: ArrayLike) -> SolarPosition:
    """Sun elevation (geometric and apparent) and azimuth at Unix timestamps (NOAA ``calcAzEl``)."""
    t = np.atleast_1d(np.asarray(t_unix, dtype=np.float64))
    zenith, decl, hour_angle = _geometric(lon, lat, t)
    lat_r = math.radians(lat)
    zen_r = np.radians(zenith)
    denom = math.cos(lat_r) * np.sin(zen_r)
    with np.errstate(divide="ignore", invalid="ignore"):
        arg = (math.sin(lat_r) * np.cos(zen_r) - np.sin(np.radians(decl))) / denom
    az = 180.0 - np.degrees(np.arccos(np.clip(arg, -1.0, 1.0)))
    az = np.where(hour_angle > 0.0, -az, az)
    # Zenith or a pole: NOAA's fallback (due south in the north, due north in the south).
    az = np.where(np.abs(denom) > 0.001, az, 180.0 if lat > 0.0 else 0.0)
    az = np.mod(az, 360.0)
    geo = 90.0 - zenith
    return SolarPosition(
        geometric_elevation_deg=geo,
        elevation_deg=geo + refraction_deg(geo),
        azimuth_deg=az,
    )


def solar_noon(lon: float, t_near_unix: float) -> float:
    """Unix time of solar noon (hour angle 0) nearest ``t_near_unix`` (NOAA two-pass iteration)."""
    day_start = math.floor(t_near_unix / SECONDS_PER_DAY) * SECONDS_PER_DAY
    noon = float(t_near_unix)
    for _ in range(3):
        _, eq = sun_declination_eqtime(noon)
        offset_min = 720.0 - 4.0 * lon - float(np.ravel(eq)[0])
        candidate = day_start + offset_min * 60.0
        # Choose the occurrence (± whole days) closest to the requested instant.
        shift = round((t_near_unix - candidate) / SECONDS_PER_DAY)
        noon = candidate + shift * SECONDS_PER_DAY
    return noon


@dataclass(frozen=True)
class Crossing:
    """An instant at which the elevation crosses ``threshold_deg``."""

    t: float
    threshold_deg: float
    rising: bool


ElevationFn = Callable[[FloatArray], FloatArray]


def _bisect(fn: ElevationFn, threshold: float, a: float, b: float, fa: float) -> float:
    lo, hi, flo = a, b, fa
    while hi - lo > _BISECT_TOL_S:
        mid = 0.5 * (lo + hi)
        fm = float(fn(np.array([mid]))[0]) - threshold
        if (fm >= 0.0) == (flo >= 0.0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _elevation_fn(lon: float, lat: float, apparent: bool) -> ElevationFn:
    if apparent:
        return lambda t: apparent_elevation(lon, lat, t)
    return lambda t: geometric_elevation(lon, lat, t)


def crossings(
    lon: float,
    lat: float,
    t0: float,
    t1: float,
    thresholds: list[float] | tuple[float, ...],
    *,
    apparent: bool = False,
) -> list[Crossing]:
    """Every crossing of each threshold elevation in ``[t0, t1)``, sorted by time.

    ``apparent=False`` (default) uses the geometric elevation (sunrise, twilight conventions);
    ``apparent=True`` the refracted one (photographic sun-elevation windows).
    """
    if t1 <= t0:
        return []
    fn = _elevation_fn(lon, lat, apparent)
    n = max(2, int(math.ceil((t1 - t0) / _SCAN_STEP_S)) + 1)
    ts = np.linspace(t0, t1, n)
    elev = fn(ts)
    found: list[Crossing] = []
    for threshold in thresholds:
        diff = elev - threshold
        sign = diff >= 0.0
        changes = np.nonzero(sign[1:] != sign[:-1])[0]
        for i in changes.tolist():
            t = _bisect(fn, threshold, float(ts[i]), float(ts[i + 1]), float(diff[i]))
            if t0 <= t < t1:
                found.append(Crossing(t=t, threshold_deg=float(threshold), rising=not bool(sign[i])))
    found.sort(key=lambda c: (c.t, c.threshold_deg))
    return found


def elevation_intervals(
    lon: float,
    lat: float,
    t0: float,
    t1: float,
    thresholds: list[float] | tuple[float, ...],
    *,
    apparent: bool = False,
) -> list[tuple[float, float, float]]:
    """``[t0, t1)`` split at every threshold crossing: ``(start, end, elevation at the middle)``.

    Within each interval the elevation stays on one side of every threshold, so the midpoint
    elevation classifies the whole interval.
    """
    if t1 <= t0:
        return []
    cuts = [c.t for c in crossings(lon, lat, t0, t1, thresholds, apparent=apparent)]
    edges = [t0, *[t for t in cuts if t0 < t < t1], t1]
    edges = sorted(set(edges))
    fn = _elevation_fn(lon, lat, apparent)
    mids = np.array([(a + b) / 2.0 for a, b in zip(edges[:-1], edges[1:], strict=True)], dtype=np.float64)
    mid_elev = fn(mids) if mids.size else np.empty(0)
    return [
        (float(a), float(b), float(e))
        for (a, b), e in zip(zip(edges[:-1], edges[1:], strict=True), mid_elev.tolist(), strict=True)
        if b - a > 1e-6
    ]
