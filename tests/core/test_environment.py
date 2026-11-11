"""Environment module: NOAA sun times, WMM2025, wind power law, EMI proximity, daylight profiles.

Reference values
----------------
* Sun times and positions: NOAA's solar calculator closed forms
  (https://gml.noaa.gov/grad/solcalc/main.js: ``calcSunriseSetUTC`` with its second pass,
  ``calcSolNoon`` with its second pass and ``calcAzEl``), re-implemented independently of
  ``airbrief_core.geo.sun`` and evaluated on 2026-10-11. Civil dawn/dusk use the same
  ``calcSunriseSetUTC`` routine with the zenith 96° instead of 90.833° (NOAA's calculator does not
  print twilight). Times are UTC, rounded to the second; solar noon is the instant of hour angle 0.
* Magnetic field: the official WMM2025 test values file published by NOAA NCEI
  (``WMM2025_TestValues.txt`` / ``WMM2025_TestValues.pdf`` in ``WMM2025COF.zip``, the archive whose
  ``WMM.COF`` this repository ships byte for byte as ``WMM2025.COF``), plus a table in the layout
  of the WMM technical report's test values (80° N 0° E, 0° 120° E, 80° S 240° E at 0 and 100 km).
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, Polygon, box

from airbrief_core.analyses import REGISTRY
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.environment import (
    daylight_check,
    emi_proximity,
    local_midnight_utc,
    offset_label,
    sun_times,
    sun_window,
    wind_at_height,
)
from airbrief_core.data import DataPack
from airbrief_core.data.datapack import write_manifest
from airbrief_core.data.power import PowerFeature, PowerIndex, parse_voltage_kv
from airbrief_core.errors import AirBriefError
from airbrief_core.geo import sun as solar
from airbrief_core.geo.wmm import WMM_COF_PATH, decimal_year, load_wmm, parse_cof, wmm
from airbrief_core.models.environment import EnvironmentParams, EnvironmentResult
from seed.datapack import (
    SYNTHETIC_POWER_LINES,
    power_from_overpass,
    power_query,
)

BENGALURU = (77.5946, 12.9716)
SURVEY_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[77.485, 12.900], [77.497, 12.900], [77.497, 12.904], [77.490, 12.910], [77.485, 12.910], [77.485, 12.900]]],
}


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _utc_seconds(iso: str) -> float:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()


# (name, lon, lat, date, local UTC offset min, sunrise, sunset, civil dawn, civil dusk, solar noon) —
# times in UTC from NOAA main.js
NOAA_TIMES = [
    ("bengaluru-june", 77.5946, 12.9716, "2026-06-21", 330, "00:24:40", "13:18:08", "00:01:16", "13:41:32", "06:51:24"),
    ("bengaluru-december", 77.5946, 12.9716, "2026-12-21", 330, "01:06:39", "12:28:32", "00:43:33", "12:51:37", "06:47:35"),
    ("bengaluru-october", 77.5946, 12.9716, "2026-10-11", 330, "00:39:28", "12:33:10", "00:18:07", "12:54:31", "06:36:24"),
    ("equator-equinox", 0.0, 0.0, "2026-03-20", 0, "06:04:10", "18:10:41", "05:43:31", "18:31:21", "12:07:26"),
    ("equator-june", 0.0, 0.0, "2026-06-21", 0, "05:58:08", "18:05:31", "05:35:36", "18:28:03", "12:01:49"),
    ("60n-june", 10.75, 60.0, "2026-06-21", 120, "01:52:45", "20:44:53", "00:06:04", "22:31:32", "11:18:49"),
    ("60n-december", 10.75, 60.0, "2026-12-21", 60, "08:18:56", "14:11:10", "07:21:07", "15:09:00", "11:15:04"),
]


@pytest.mark.parametrize("use_local_offset", [False, True])
@pytest.mark.parametrize(("name", "lon", "lat", "day", "offset", "rise", "set_", "dawn", "dusk", "noon"), NOAA_TIMES)
def test_sun_times_match_noaa_within_one_minute(
    name: str, lon: float, lat: float, day: str, offset: int, rise: str, set_: str, dawn: str, dusk: str, noon: str,
    use_local_offset: bool,
) -> None:
    """The events of a date do not depend on the display offset (UTC 0 or the local zone).

    The spec asks for ±1 min; the same equations solved by bisection agree with NOAA's iterated
    closed forms to about a second (both sides are rounded to the second here)."""
    summary = sun_times(lon, lat, date.fromisoformat(day), offset if use_local_offset else 0)
    expected = {"sunrise": rise, "sunset": set_, "civil_dawn": dawn, "civil_dusk": dusk, "solar_noon": noon}
    for key, hhmmss in expected.items():
        event = getattr(summary, key)
        assert event is not None, f"{name}: {key} missing"
        diff = abs(_utc_seconds(event.utc) - _ts(f"{day} {hhmmss}"))
        assert diff <= 60.0, f"{name}: {key} {event.utc} vs {hhmmss}"
        assert diff <= 2.0, f"{name}: {key} {event.utc} vs {hhmmss}"
    assert not summary.polar_day and not summary.polar_night


def test_solar_noon_is_the_hour_angle_zero() -> None:
    """Solar noon (NOAA ``calcSolNoon``) is hour angle 0: in December at 13° N the sun is then due
    south (azimuth 180°) and at its highest."""
    summary = sun_times(77.5946, 12.9716, date(2026, 12, 21), 330)
    t = _utc_seconds(summary.solar_noon.utc)
    at_noon = solar.solar_position(77.5946, 12.9716, [t - 60.0, t, t + 60.0])
    assert float(at_noon.azimuth_deg[1]) == pytest.approx(180.0, abs=0.02)
    assert at_noon.elevation_deg[1] > max(at_noon.elevation_deg[0], at_noon.elevation_deg[2])


def test_sun_events_are_within_seconds_of_noaa() -> None:
    """Sunrise/sunset and twilight solve the same equation as NOAA: a few seconds, not a minute."""
    summary = sun_times(77.5946, 12.9716, date(2026, 6, 21), 330)
    assert summary.sunrise is not None and summary.civil_dusk is not None
    assert abs(_utc_seconds(summary.sunrise.utc) - _ts("2026-06-21 00:24:40")) <= 3
    assert abs(_utc_seconds(summary.civil_dusk.utc) - _ts("2026-06-21 13:41:32")) <= 3


def test_local_display_follows_the_offset() -> None:
    summary = sun_times(77.5946, 12.9716, date(2026, 6, 21), 330)
    assert summary.sunrise is not None and summary.sunset is not None
    assert summary.sunrise.local == "05:55" and summary.sunrise.day_offset == 0  # 00:24:40Z + 5:30
    assert summary.sunset.local == "18:48"
    assert offset_label(330) == "UTC+05:30"
    assert offset_label(-210) == "UTC-03:30"
    assert offset_label(0) == "UTC+00:00"


def test_late_civil_dusk_belongs_to_the_evening_of_the_date() -> None:
    """At 60°N in June civil dusk (22:31Z) falls after local midnight in CEST: next local day."""
    summary = sun_times(10.75, 60.0, date(2026, 6, 21), 120)
    assert summary.civil_dusk is not None
    assert summary.civil_dusk.local in ("00:31", "00:32") and summary.civil_dusk.day_offset == 1  # 22:31:32Z
    assert summary.civil_dawn is not None and summary.civil_dawn.day_offset == 0


@pytest.mark.parametrize(
    ("lon", "lat", "when", "azimuth", "elevation"),
    [
        (77.5946, 12.9716, "2026-06-21 06:30", 25.1037, 78.3728),
        (10.75, 60.0, "2026-12-21 11:00", 176.5244, 6.6354),
        (0.0, 0.0, "2026-03-20 12:00", 91.3350, 88.1412),
        (77.5946, 12.9716, "2026-10-11 01:00", 98.1224, 4.3110),
    ],
)
def test_solar_position_matches_noaa_calcazel(lon: float, lat: float, when: str, azimuth: float, elevation: float) -> None:
    pos = solar.solar_position(lon, lat, _ts(when))
    assert float(pos.azimuth_deg[0]) == pytest.approx(azimuth, abs=1e-3)
    assert float(pos.elevation_deg[0]) == pytest.approx(elevation, abs=1e-3)


def test_polar_day_and_night_at_78n() -> None:
    lon, lat = 15.65, 78.22  # Longyearbyen
    june = sun_times(lon, lat, date(2026, 6, 21), 120)
    assert june.polar_day and not june.polar_night
    assert june.sunrise is None and june.sunset is None
    assert june.daylight_minutes == pytest.approx(1440.0)
    assert min(p.elevation_deg for p in june.track) > 0.0
    december = sun_times(lon, lat, date(2026, 12, 21), 60)
    assert december.polar_night and not december.polar_day
    assert december.sunrise is None and december.civil_dawn is None  # max elevation ≈ −11.7°: no civil twilight
    assert december.max_elevation_deg < -6.0
    check = daylight_check(lon, lat, date(2026, 12, 21), 60, "faa_107")
    assert [s.kind for s in check.segments] == ["night"]
    assert check.segments[0].minutes == pytest.approx(1440.0)


def test_track_covers_the_local_day_every_five_minutes() -> None:
    summary = sun_times(77.5946, 12.9716, date(2026, 10, 11), 330)
    assert len(summary.track) == 289
    assert summary.track[0].minute == 0.0 and summary.track[-1].minute == 1440.0
    assert summary.track[0].t == "2026-10-10T18:30:00Z"
    peak = max(summary.track, key=lambda p: p.elevation_deg)
    assert peak.elevation_deg == pytest.approx(summary.max_elevation_deg, abs=0.2)


# Bengaluru 2026-06-21 (NOAA): sunset 13:18:08Z = 18:48:08 IST, civil dusk 13:41:32Z = 19:11:32 IST.
SUNSET_IST_MIN = 18 * 60 + 48 + 8 / 60
DUSK_IST_MIN = 19 * 60 + 11 + 32 / 60
WINDOW = ("18:00", "20:00")
WINDOW_END_MIN = 20 * 60


def test_window_spanning_dusk_india_daylight() -> None:
    check = daylight_check(*BENGALURU, date(2026, 6, 21), 330, "india_daylight", WINDOW)
    assert check.minutes_outside == pytest.approx(WINDOW_END_MIN - SUNSET_IST_MIN, abs=0.1)  # 71.9 min
    assert check.minutes_lighting == pytest.approx(0.0)
    assert check.statement == "Window extends 72 min past sunset."
    assert [s.kind for s in check.window_segments] == ["day", "civil_twilight", "night"]
    assert [s.status for s in check.window_segments] == ["allowed", "outside", "outside"]


def test_window_spanning_dusk_faa_107() -> None:
    check = daylight_check(*BENGALURU, date(2026, 6, 21), 330, "faa_107", WINDOW)
    assert check.minutes_lighting == pytest.approx(DUSK_IST_MIN - SUNSET_IST_MIN, abs=0.1)  # 23.4 min
    assert check.minutes_outside == pytest.approx(WINDOW_END_MIN - DUSK_IST_MIN, abs=0.1)  # 48.5 min (night)
    assert [s.status for s in check.window_segments] == ["allowed", "lighting", "outside"]
    assert "23 min past sunset in civil twilight (anti-collision lighting required)" in check.statement
    assert "48 min past civil dusk at night (flagged)" in check.statement


def test_window_spanning_dusk_custom_elevation() -> None:
    india = daylight_check(*BENGALURU, date(2026, 6, 21), 330, "india_daylight", WINDOW)
    horizon = daylight_check(*BENGALURU, date(2026, 6, 21), 330, "custom", WINDOW, 0.0)
    assert india.minutes_outside is not None and horizon.minutes_outside is not None
    # Descending, the apparent elevation reaches 0° about a minute before the geometric −0.833° sunset.
    assert india.minutes_outside < horizon.minutes_outside < india.minutes_outside + 3.0
    twilight = daylight_check(*BENGALURU, date(2026, 6, 21), 330, "custom", WINDOW, -6.0)
    assert twilight.minutes_outside == pytest.approx(WINDOW_END_MIN - DUSK_IST_MIN, abs=0.6)
    high = daylight_check(*BENGALURU, date(2026, 6, 21), 330, "custom", WINDOW, 10.0)
    assert high.minutes_outside is not None and high.minutes_outside > horizon.minutes_outside + 30.0
    assert "after it drops below 10°" in high.statement


def test_window_inside_daylight_and_across_midnight() -> None:
    inside = daylight_check(*BENGALURU, date(2026, 6, 21), 330, "india_daylight", ("09:00", "11:30"))
    assert inside.minutes_outside == pytest.approx(0.0)
    assert inside.statement == "The planned window 09:00–11:30 lies entirely within the profile."
    night = daylight_check(*BENGALURU, date(2026, 6, 21), 330, "india_daylight", ("23:00", "01:00"))
    assert night.window is not None and night.window.crosses_midnight
    assert night.window.minutes == pytest.approx(120.0)
    assert night.window.end_local == "01:00 (+1 d)"
    assert night.minutes_outside == pytest.approx(120.0)
    assert night.statement == "Window extends 2 h past sunset."
    no_window = daylight_check(*BENGALURU, date(2026, 6, 21), 330, "faa_107")
    assert no_window.window is None and no_window.minutes_outside is None
    assert sum(s.minutes for s in no_window.segments) == pytest.approx(1440.0)
    assert [s.kind for s in no_window.segments] == ["night", "civil_twilight", "day", "civil_twilight", "night"]


def test_window_statements_without_sunrise() -> None:
    """Polar night at 78° N: no sunrise, sunset or civil dawn/dusk to name, so the statement gives
    totals and the reason instead of "before sunrise" (which would not exist)."""
    lon, lat = 15.65, 78.22
    india = daylight_check(lon, lat, date(2026, 12, 21), 60, "india_daylight", ("10:00", "14:00"))
    assert india.minutes_outside == pytest.approx(240.0)
    assert india.statement == "4 h of the window outside the profile; polar night: the sun does not rise on this date."
    faa = daylight_check(lon, lat, date(2026, 12, 21), 60, "faa_107", ("10:00", "14:00"))
    assert faa.statement == "4 h at night (flagged); polar night: the sun does not rise on this date."
    custom = daylight_check(lon, lat, date(2026, 12, 21), 60, "custom", ("10:00", "14:00"), 5.0)
    assert custom.statement == "The sun stays below 5° on this date: 4 h of the window outside the profile."
    # Tromsø, 28 Nov: the sun stays below the horizon but civil twilight comes at midday.
    tromso = daylight_check(18.96, 69.65, date(2026, 11, 28), 60, "faa_107", ("09:00", "15:00"))
    assert tromso.minutes_lighting is not None and tromso.minutes_lighting > 300.0
    assert "in civil twilight (anti-collision lighting required)" in tromso.statement
    assert "before sunrise" not in tromso.statement and "past sunset" not in tromso.statement
    assert tromso.statement.endswith("polar night: the sun does not rise on this date.")


def test_window_across_midnight_in_a_summer_night_faa() -> None:
    """Helsinki, 21 June, 22:00–02:00 EEST: day until sunset 22:50, civil twilight to civil dusk
    00:42 (+1 d), then night; minutes split exactly at those events."""
    check = daylight_check(24.94, 60.17, date(2026, 6, 21), 180, "faa_107", ("22:00", "02:00"))
    assert check.window is not None and check.window.crosses_midnight
    summary = sun_times(24.94, 60.17, date(2026, 6, 21), 180)
    assert summary.sunset is not None and summary.civil_dusk is not None and summary.civil_dusk.day_offset == 1
    t0 = local_midnight_utc(date(2026, 6, 21), 180)
    sunset_min = (_utc_seconds(summary.sunset.utc) - t0) / 60.0
    dusk_min = (_utc_seconds(summary.civil_dusk.utc) - t0) / 60.0
    assert check.minutes_lighting == pytest.approx(dusk_min - sunset_min, abs=0.05)
    assert check.minutes_outside == pytest.approx(26 * 60 - dusk_min, abs=0.05)
    assert [s.kind for s in check.window_segments] == ["day", "civil_twilight", "night"]
    assert check.window_segments[-1].end_local == "02:00 (+1 d)"


def test_sun_window_for_photogrammetry() -> None:
    result = sun_window(*BENGALURU, date(2026, 6, 21), 330, 30.0, 60.0, 79.5)
    assert result.reached and len(result.segments) == 2  # morning and afternoon
    t0 = local_midnight_utc(date(2026, 6, 21), 330)
    for seg in result.segments:
        start = solar.solar_position(*BENGALURU, t0 + seg.start_minute * 60).elevation_deg[0]
        end = solar.solar_position(*BENGALURU, t0 + seg.end_minute * 60).elevation_deg[0]
        assert {round(float(start)), round(float(end))} == {30, 60}
    assert result.segments[0].minutes == pytest.approx(result.segments[1].minutes, abs=2.0)
    polar = sun_window(15.65, 78.22, date(2026, 12, 21), 60, 30.0, 60.0, -11.7)
    assert not polar.reached and polar.segments == []


# Test points in the layout of the WMM technical report's table (they agree with the shipped model
# to 0.1 nT): date, height km, lat, lon, X, Y, Z, H, F, I, D, GV (NaN → None)
WMM_REPORT = [
    (2025.0, 0.0, 80.0, 0.0, 6521.6, 145.9, 54791.5, 6523.2, 55178.5, 83.21, 1.28, 1.28),
    (2025.0, 0.0, 0.0, 120.0, 39677.8, -109.6, -10580.2, 39677.9, 41064.3, -14.93, -0.16, None),
    (2025.0, 0.0, -80.0, 240.0, 6117.5, 15751.9, -52022.5, 16898.1, 54698.2, -72.00, 68.78, -51.22),
    (2025.0, 100.0, 80.0, 0.0, 6216.0, 92.4, 52598.8, 6216.7, 52964.9, 83.26, 0.85, 0.85),
    (2025.0, 100.0, 0.0, 120.0, 37688.6, -96.2, -10152.1, 37688.7, 39032.1, -15.08, -0.15, None),
    (2025.0, 100.0, -80.0, 240.0, 5907.6, 14780.3, -49540.7, 15917.1, 52035.0, -72.19, 68.21, -51.79),
    (2027.5, 0.0, 80.0, 0.0, 6500.8, 294.5, 54869.4, 6507.5, 55253.9, 83.24, 2.59, 2.59),
    (2027.5, 0.0, 0.0, 120.0, 39701.6, -167.4, -10381.8, 39702.0, 41036.9, -14.65, -0.24, None),
    (2027.5, 0.0, -80.0, 240.0, 6200.7, 15730.3, -51783.7, 16908.3, 54474.2, -71.92, 68.49, -51.51),
    (2027.5, 100.0, 80.0, 0.0, 6196.7, 233.8, 52670.5, 6201.1, 53034.3, 83.29, 2.16, 2.16),
    (2027.5, 100.0, 0.0, 120.0, 37711.5, -148.7, -9969.8, 37711.8, 39007.4, -14.81, -0.23, None),
    (2027.5, 100.0, -80.0, 240.0, 5984.0, 14760.1, -49317.7, 15927.0, 51825.7, -72.10, 67.93, -52.07),
]


@pytest.mark.parametrize(("year", "alt", "lat", "lon", "x", "y", "z", "h", "f", "incl", "decl", "gv"), WMM_REPORT)
def test_wmm2025_report_test_values(
    year: float, alt: float, lat: float, lon: float, x: float, y: float, z: float, h: float, f: float,
    incl: float, decl: float, gv: float | None,
) -> None:
    e = wmm(lon, lat, alt, year)
    assert e.declination_deg == pytest.approx(decl, abs=0.01)
    assert e.inclination_deg == pytest.approx(incl, abs=0.01)
    assert e.total_nt == pytest.approx(f, abs=1.0)
    assert e.horizontal_nt == pytest.approx(h, abs=0.1)
    assert (e.north_nt, e.east_nt, e.down_nt) == pytest.approx((x, y, z), abs=0.1)
    if gv is None:
        assert e.grid_variation_deg is None
    else:
        assert e.grid_variation_deg == pytest.approx(gv, abs=0.01)


# Secular variation rows of the same official table: date, height km, lat, lon, Ḣ, Ḟ (nT/yr), İ, Ḋ (°/yr)
WMM_REPORT_SV = [
    (2025.0, 0.0, 80.0, 0.0, -7.0, 30.1, 0.01, 0.52),
    (2025.0, 0.0, 0.0, 120.0, 9.6, -11.2, 0.11, -0.03),
    (2025.0, 0.0, -80.0, 240.0, 4.0, -89.6, 0.03, -0.12),
    (2025.0, 100.0, 80.0, 0.0, -6.9, 27.6, 0.01, 0.52),
    (2025.0, 100.0, 0.0, 120.0, 9.2, -10.0, 0.11, -0.03),
    (2025.0, 100.0, -80.0, 240.0, 3.9, -83.8, 0.03, -0.11),
    (2027.5, 0.0, 80.0, 0.0, -5.6, 30.3, 0.01, 0.53),
    (2027.5, 0.0, 0.0, 120.0, 9.6, -10.7, 0.11, -0.03),
    (2027.5, 0.0, -80.0, 240.0, 4.2, -89.5, 0.04, -0.12),
    (2027.5, 100.0, 80.0, 0.0, -5.6, 27.8, 0.01, 0.52),
    (2027.5, 100.0, 0.0, 120.0, 9.3, -9.7, 0.11, -0.03),
    (2027.5, 100.0, -80.0, 240.0, 4.0, -83.7, 0.03, -0.11),
]


@pytest.mark.parametrize(("year", "alt", "lat", "lon", "h_dot", "f_dot", "i_dot", "d_dot"), WMM_REPORT_SV)
def test_wmm2025_report_secular_variation(
    year: float, alt: float, lat: float, lon: float, h_dot: float, f_dot: float, i_dot: float, d_dot: float
) -> None:
    """Annual change, published to 0.1 nT/yr and 0.01°/yr: agreement within half the last digit."""
    e = wmm(lon, lat, alt, year)
    assert e.horizontal_dot_nt == pytest.approx(h_dot, abs=0.05 + 1e-9)
    assert e.total_dot_nt == pytest.approx(f_dot, abs=0.05 + 1e-9)
    assert e.inclination_dot_deg == pytest.approx(i_dot, abs=0.005 + 1e-9)
    assert e.declination_dot_deg == pytest.approx(d_dot, abs=0.005 + 1e-9)


# Rows of NOAA's official WMM2025_TestValues.txt (100 rows; these 16 span every epoch, both poles,
# the blackout and caution zones and India): decimal year, height above the ellipsoid km, lat, lon,
# D, I (°), H, X, Y, Z, F (nT), Ḋ, İ (°/yr), Ḣ, Ḟ (nT/yr). The full file agrees to < 0.005° and < 0.001 nT.
WMM_OFFICIAL = [
    (2025.0, 28, 89, -121, -99.77, 88.47, 1504.298146, -255.388723, -1482.460628, 56194.288771, 56214.419888, 2.491706, -0.009987, 10.285640, 18.343917),
    (2025.0, 48, 80, -96, -29.91, 87.77, 2164.285547, 1875.982280, -1079.269389, 55623.044051, 55665.134163, 1.248417, -0.057537, 55.469239, -9.982652),
    (2025.0, 51, -33, 109, -5.49, -67.50, 21838.046477, 21737.778822, -2090.274098, -52710.003920, 57054.752538, 0.069861, 0.026375, 29.809763, 14.490015),
    (2025.0, 18, 0, 21, 1.29, -26.06, 29282.246275, 29274.811882, 659.800118, -14316.722540, 32594.761714, 0.030544, 0.079429, -8.761425, -31.844958),
    (2025.5, 63, 26, 81, 0.51, 41.07, 34803.980117, 34802.613433, 308.431881, 30332.056989, 46166.554053, 0.018525, 0.021888, 20.342054, 42.353703),
    (2025.5, 50, -70, -133, 57.21, -71.94, 16656.709403, 9021.847823, 14001.865232, -51084.838301, 53731.803175, -0.036998, 0.045634, 8.590794, -103.537175),
    (2026.0, 69, 23, 63, 1.17, 35.92, 34565.858527, 34558.605297, 708.078839, 25043.405885, 42684.549360, 0.013234, 0.010624, 23.857027, 35.194683),
    (2026.0, 82, -64, 87, -81.74, -75.40, 13974.248411, 2007.074870, -13829.362571, -53663.514288, 55453.154865, -0.187260, -0.029553, -23.144309, 17.996086),
    (2026.5, 64, 10, 78, -1.53, 7.53, 39441.702532, 39427.724480, -1049.971869, 5214.811025, 39784.948821, 0.031231, 0.021184, 27.794943, 29.981674),
    (2026.5, 12, -79, 115, -137.58, -77.37, 13023.480845, -9613.650418, -8785.714482, -58104.306533, 59545.961164, -0.241722, 0.014808, 7.471685, -34.496496),
    (2027.0, 54, 54, 178, 0.63, 65.46, 20617.099795, 20615.871559, 225.041793, 45149.631876, 49634.202547, -0.170729, 0.012944, -3.909066, 15.145168),
    (2027.5, 0, -13, -59, -17.49, -15.26, 22401.493010, 21365.849306, -6732.560620, -6112.313520, 23220.406233, -0.173690, -0.385680, -68.873971, -28.743372),
    (2028.0, 75, 79, 125, -18.59, 87.42, 2582.332595, 2447.595900, -823.235047, 57308.751756, 57366.902212, -0.910648, 0.022931, -21.221891, 38.087754),
    (2028.5, 11, 34, 0, 1.57, 46.77, 29089.194286, 29078.234597, 798.434048, 30945.577813, 42471.284539, 0.111314, 0.010464, 15.380882, 30.707939),
    (2029.0, 50, 87, -154, -73.48, 89.07, 906.920459, 257.877420, -869.484879, 55992.308183, 55999.652503, 1.562405, -0.064156, 62.930173, 14.423442),
    (2029.5, 77, -18, 138, 4.45, -47.55, 31847.600506, 31751.497580, 2472.257962, -34817.395113, 47186.021876, -0.036580, 0.012491, 0.895474, -9.919684),
]


@pytest.mark.parametrize(
    ("year", "alt", "lat", "lon", "decl", "incl", "h", "x", "y", "z", "f", "d_dot", "i_dot", "h_dot", "f_dot"), WMM_OFFICIAL
)
def test_wmm2025_official_test_values(
    year: float, alt: float, lat: float, lon: float, decl: float, incl: float, h: float, x: float, y: float, z: float,
    f: float, d_dot: float, i_dot: float, h_dot: float, f_dot: float,
) -> None:
    """Spec tolerance: declination ±0.01°, total field ±1 nT. D and I are published to 0.01°; the
    intensities to 1 µnT, which the synthesis matches to 0.01 nT."""
    e = wmm(lon, lat, alt, year)
    assert e.declination_deg == pytest.approx(decl, abs=0.01)
    assert e.inclination_deg == pytest.approx(incl, abs=0.01)
    assert e.total_nt == pytest.approx(f, abs=1.0)
    assert (e.horizontal_nt, e.north_nt, e.east_nt, e.down_nt, e.total_nt) == pytest.approx((h, x, y, z, f), abs=0.01)
    assert (e.declination_dot_deg, e.inclination_dot_deg) == pytest.approx((d_dot, i_dot), abs=1e-4)
    assert (e.horizontal_dot_nt, e.total_dot_nt) == pytest.approx((h_dot, f_dot), abs=1e-3)
    assert e.blackout == (h < 2000.0) and e.caution == (2000.0 <= h < 6000.0)


def test_shipped_cof_is_the_official_file() -> None:
    """``WMM2025.COF`` is NOAA's ``WMM.COF`` from ``WMM2025COF.zip`` byte for byte (line endings aside)."""
    data = WMM_COF_PATH.read_bytes()
    digest = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
    assert digest == "dfa8597825af4e0b87ff4198a5b4fb661b3c49f4cd090cd0164e0259b075582f"


def test_wmm_blackout_and_caution_zones() -> None:
    pole = wmm(-121.0, 89.0, 28.0, 2025.0)  # near the north magnetic pole
    assert pole.horizontal_nt < 2000.0 and pole.blackout and not pole.caution
    near = wmm(-96.0, 80.0, 48.0, 2025.0)  # northern Canada
    assert 2000.0 <= near.horizontal_nt < 6000.0
    assert near.caution and not near.blackout
    bengaluru = wmm(77.5946, 12.9716, 1.0, 2026.5)
    assert not bengaluru.caution and not bengaluru.blackout
    assert bengaluru.within_validity
    assert not wmm(77.5946, 12.9716, 1.0, 2031.0).within_validity


def test_wmm_model_file_and_parser() -> None:
    model = load_wmm()
    # NOAA's README asks to verify the header "2025.0  WMM-2025  11/13/2024".
    assert model.name == "WMM2025" and model.epoch == 2025.0 and model.release_date == "11/13/2024"
    assert model.n_max == 12
    assert model.g[1, 0] == pytest.approx(-29351.8)
    assert model.h[1, 1] == pytest.approx(4545.4)
    assert decimal_year(date(2026, 1, 1)) == 2026.0
    assert decimal_year(date(2028, 12, 31), 1.0) == pytest.approx(2029.0)  # leap year: 366 days
    with pytest.raises(AirBriefError):
        parse_cof("")
    with pytest.raises(AirBriefError):
        parse_cof("    2025.0   WMM-2025   11/13/2024\n  1  0  -29351.8\n")


def test_power_law_one_seventh() -> None:
    wind = wind_at_height(10.0, 100.0, reference_height_m=10.0, shear_exponent=1.0 / 7.0, wind_limit_ms=12.0)
    assert wind.wind_at_height_ms == pytest.approx(10.0 * 10.0 ** (1.0 / 7.0), abs=1e-3)
    assert round(wind.wind_at_height_ms, 1) == 13.9
    assert wind.margin_ms == pytest.approx(12.0 - 13.895, abs=1e-3)
    assert wind.statement == "est. 13.9 m/s at 100 m vs 12 m/s rated (−1.9 m/s)"


def test_power_law_gust_lower_height_and_zero_shear() -> None:
    gusty = wind_at_height(8.0, 120.0, gust_ms=11.0, wind_limit_ms=15.0)
    factor = (120.0 / 10.0) ** (1.0 / 7.0)
    assert gusty.gust_at_height_ms == pytest.approx(11.0 * factor, abs=1e-3)
    assert gusty.gust_margin_ms == pytest.approx(15.0 - 11.0 * factor, abs=1e-3)
    assert "gusts" in gusty.statement
    assert wind_at_height(10.0, 5.0).wind_at_height_ms < 10.0
    assert wind_at_height(10.0, 400.0, shear_exponent=0.0).wind_at_height_ms == pytest.approx(10.0)
    assert wind_at_height(10.0, 50.0).margin_ms is None


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("132000;33000", 132.0),
        ("220000", 220.0),
        ("33 kV", 33.0),
        ("11kV", 11.0),
        ("400", 0.4),
        ("11000/415", 11.0),
        ("66000; 11000", 66.0),
        ("132,000", 132.0),
        ("0,4 kV", 0.4),
        ("0,4", None),
        (220000, 220.0),
        ("medium", None),
        ("", None),
        ("0", None),
        ("3000000", None),
        (None, None),
        (True, None),
    ],
)
def test_voltage_tag_parsing(tag: object, expected: float | None) -> None:
    assert parse_voltage_kv(tag) == expected


def test_power_overpass_conversion() -> None:
    payload = {
        "elements": [
            {"type": "way", "id": 1, "tags": {"power": "line", "voltage": "132000;33000", "name": "North line"},
             "geometry": [{"lon": 77.0, "lat": 12.0}, {"lon": 77.01, "lat": 12.0}]},
            {"type": "way", "id": 1, "tags": {"power": "line"},
             "geometry": [{"lon": 77.0, "lat": 12.0}, {"lon": 77.01, "lat": 12.0}]},
            {"type": "way", "id": 2, "tags": {"power": "cable", "location": "underground"},
             "geometry": [{"lon": 77.0, "lat": 12.1}, {"lon": 77.0, "lat": 12.11}]},
            {"type": "node", "id": 3, "lon": 77.02, "lat": 12.02, "tags": {"power": "substation", "voltage": "66 kV"}},
            {"type": "way", "id": 4, "tags": {"power": "plant", "plant:source": "solar"},
             "geometry": [{"lon": 77.1, "lat": 12.1}, {"lon": 77.11, "lat": 12.1}, {"lon": 77.11, "lat": 12.11},
                          {"lon": 77.1, "lat": 12.1}]},
            {"type": "relation", "id": 5, "tags": {"power": "substation"},
             "members": [{"type": "way", "role": "outer", "geometry": [
                 {"lon": 77.2, "lat": 12.2}, {"lon": 77.21, "lat": 12.2}, {"lon": 77.21, "lat": 12.21},
                 {"lon": 77.2, "lat": 12.21}, {"lon": 77.2, "lat": 12.2}]}]},
            {"type": "node", "id": 6, "lon": 77.3, "lat": 12.3, "tags": {"power": "tower"}},
            {"type": "node", "id": 7, "lon": 77.3, "lat": 12.3, "tags": {"power": "line"}},
        ]
    }
    feats = power_from_overpass(payload)
    by_id = {f["id"]: f for f in feats}
    assert set(by_id) == {"osm-w1", "osm-w2", "osm-n3", "osm-w4", "osm-r5"}
    assert by_id["osm-w1"]["geometry"]["type"] == "LineString"
    assert by_id["osm-w1"]["properties"]["voltage_kv"] == 132.0
    assert by_id["osm-w1"]["properties"]["name"] == "North line"
    assert by_id["osm-w2"]["properties"]["kind"] == "cable"
    assert by_id["osm-w2"]["properties"]["location"] == "underground"
    assert by_id["osm-n3"]["geometry"]["type"] == "Point" and by_id["osm-n3"]["properties"]["voltage_kv"] == 66.0
    assert by_id["osm-w4"]["geometry"]["type"] == "Polygon" and by_id["osm-w4"]["properties"]["kind"] == "plant"
    assert by_id["osm-r5"]["geometry"]["type"] == "Polygon"
    query = power_query((77.3, 12.7, 77.95, 13.3))
    assert 'way["power"~"^(line|minor_line|cable)$"];' in query
    assert 'nwr["power"~"^(substation|plant)$"];' in query
    assert query.endswith("out geom;")


def test_emi_distance_to_a_synthetic_line() -> None:
    """A north–south line 0.001° east of a square: distance = 0.001° × 111 320 × cos φ ≈ 108.5 m."""
    square = box(77.0, 12.9, 77.002, 12.902)
    lon_line = 77.003
    line = LineString([(lon_line, 12.89), (lon_line, 12.91)])
    index = PowerIndex([PowerFeature(id="l1", kind="line", geometry=line, props={"voltage": "33000"})])
    hits = index.near_geometry(square, 300.0)
    assert len(hits) == 1
    expected = 0.001 * 111_320.0 * math.cos(math.radians(12.901))  # 0.001° of longitude ≈ 108.5 m
    assert hits[0].distance_m == pytest.approx(expected, rel=5e-3)
    assert hits[0].feature.voltage_kv == 33.0
    assert hits[0].nearest_lon == pytest.approx(lon_line, abs=1e-7)
    assert 12.9 - 1e-6 <= hits[0].nearest_lat <= 12.902 + 1e-6  # opposite the square's east side
    assert index.near_geometry(square, 100.0) == []
    crossing = PowerIndex([PowerFeature(id="l2", kind="line", geometry=LineString([(76.99, 12.901), (77.01, 12.901)]))])
    assert crossing.near_geometry(square, 10.0)[0].distance_m == 0.0


def test_synthetic_pack_has_the_power_layer(synthetic_pack: DataPack) -> None:
    assert synthetic_pack.has("power")
    power = synthetic_pack.power()
    kinds = sorted((f.kind, f.voltage_kv) for f in power)
    assert kinds == [("line", 33.0), ("line", 220.0), ("substation", 220.0)]
    assert len(SYNTHETIC_POWER_LINES) == 2


def test_emi_on_the_survey_area(synthetic_pack: DataPack) -> None:
    geom = Polygon(SURVEY_POLYGON["coordinates"][0])
    evidence = emi_proximity(geom, synthetic_pack, 300.0)
    assert evidence.power_assessed and evidence.masts_assessed and not evidence.truncated
    distances = [i.distance_m for i in evidence.items]
    assert distances == sorted(distances)
    by_name = {i.name: i for i in evidence.items}
    line220 = by_name["Synthetic 220 kV line (Kengeri–Peenya)"]
    assert line220.kind == "power_line" and line220.voltage_kv == 220.0 and line220.distance_m == 0.0 and line220.inside
    feeder = by_name["Synthetic 33 kV feeder"]
    assert feeder.voltage_kv == 33.0
    assert feeder.distance_m == pytest.approx(0.00037 * 111_320.0 * math.cos(math.radians(12.902)), abs=1.0)  # ≈ 40 m
    assert by_name["Synthetic 220/33 kV substation"].kind == "substation"
    assert 150.0 < by_name["Synthetic 220/33 kV substation"].distance_m < 300.0
    assert by_name["Synthetic telecom tower near survey area"].kind == "comm_tower"
    assert by_name["Synthetic mast near survey area"].kind == "mast"
    assert {i.kind for i in evidence.items}.isdisjoint({"pylon"})
    labels = {f["properties"]["label"] for f in evidence.features}
    assert "220 kV power line · crosses the area" in labels
    assert "Mast · inside the area" in labels
    assert emi_proximity(geom, synthetic_pack, 30.0).items[-1].distance_m <= 30.0


def test_emi_without_power_layer_lists_pylons(tmp_path: Path) -> None:
    root = tmp_path / "nopower"
    root.mkdir()
    (root / "obstacles.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "p1", "geometry": {"type": "Point", "coordinates": [77.0005, 12.9005]},
         "properties": {"kind": "power_tower", "height_m": 35}},
        {"type": "Feature", "id": "b1", "geometry": {"type": "Point", "coordinates": [77.0006, 12.9006]},
         "properties": {"kind": "building", "height_m": 40}},
    ]}), encoding="utf-8")
    write_manifest(root, {"name": "nopower", "bbox": [76.9, 12.8, 77.1, 13.0],
                          "layers": {"obstacles": {"file": "obstacles.geojson"}}})
    pack = DataPack(root)
    evidence = emi_proximity(box(77.0, 12.9, 77.001, 12.901), pack, 100.0)
    assert not evidence.power_assessed
    assert [(i.kind, i.height_m) for i in evidence.items] == [("pylon", 35.0)]
    params = EnvironmentParams(geometry={"type": "Point", "coordinates": [77.0005, 12.9005]}, date=date(2026, 6, 21))
    result = REGISTRY["environment"].run(params, AnalysisContext(datapack=pack))
    assert "power_not_assessed" in {i.code for i in result.issues}


def test_emi_search_beyond_the_pack_is_flagged(synthetic_pack: DataPack) -> None:
    """The synthetic pack ends at 77.30° E: a point 100 m inside it with a 300 m search distance
    reaches beyond it, so the (possibly incomplete) list is flagged; 50 m keeps it inside."""
    lon = 77.30 + 100.0 / (111_320.0 * math.cos(math.radians(12.9)))
    params = EnvironmentParams(geometry={"type": "Point", "coordinates": [lon, 12.9]}, date=date(2026, 6, 21))
    result = REGISTRY["environment"].run(params, AnalysisContext(datapack=synthetic_pack))
    assert "emi_partial_coverage" in {i.code for i in result.issues}
    inside = REGISTRY["environment"].run(params.model_copy(update={"emi_radius_m": 50.0}), AnalysisContext(datapack=synthetic_pack))
    assert "emi_partial_coverage" not in {i.code for i in inside.issues}


def test_emi_with_a_pack_without_obstacles(tmp_path: Path) -> None:
    root = tmp_path / "poweronly"
    root.mkdir()
    (root / "power.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "l1", "geometry": {"type": "LineString", "coordinates": [[77.0, 12.9], [77.01, 12.9]]},
         "properties": {"kind": "line", "voltage": "11000"}},
    ]}), encoding="utf-8")
    write_manifest(root, {"name": "poweronly", "bbox": [76.9, 12.8, 77.1, 13.0], "layers": {"power": {"file": "power.geojson"}}})
    params = EnvironmentParams(geometry={"type": "Point", "coordinates": [77.005, 12.9005]}, date=date(2026, 6, 21))
    result = REGISTRY["environment"].run(params, AnalysisContext(datapack=DataPack(root)))
    codes = {i.code for i in result.issues}
    assert "masts_not_assessed" in codes and "power_not_assessed" not in codes
    assert [(i.kind, i.voltage_kv) for i in result.emi] == [("power_line", 11.0)]
    assert result.emi[0].distance_m == pytest.approx(0.0005 * 110_574.0, rel=0.01)  # ≈ 55 m north of the line


def test_run_on_mixed_geometry() -> None:
    """A dataset of a point, a line and a polygon resolves to a GeometryCollection: the evaluation
    point is its centroid and the search ring covers every part."""
    geometry = {"type": "GeometryCollection", "geometries": [
        {"type": "Point", "coordinates": [77.49, 12.90]},
        {"type": "LineString", "coordinates": [[77.49, 12.90], [77.50, 12.91]]},
        SURVEY_POLYGON,
    ]}
    params = EnvironmentParams(geometry=geometry, date=date(2026, 6, 21), utc_offset_min=330)
    result = REGISTRY["environment"].run(params, AnalysisContext(datapack=None))
    assert result.geometry_type and len(result.point) == 2
    assert result.sun.sunrise is not None and result.sun.sunrise.local == "05:55"
    ring = result.layers["emi_search"]["features"][0]["geometry"]
    assert ring["type"] in ("Polygon", "MultiPolygon")


def test_run_on_the_survey_area(synthetic_pack: DataPack) -> None:
    params = EnvironmentParams(
        geometry=SURVEY_POLYGON,
        date=date(2026, 6, 21),
        window_start="17:30",
        window_end="19:30",
        utc_offset_min=330,
        daylight_profile="faa_107",
        height_agl_m=100.0,
        surface_wind_ms=10.0,
        gust_ms=13.0,
        wind_limit_ms=12.0,
    )
    steps: list[tuple[str, float]] = []
    result = REGISTRY["environment"].run(params, AnalysisContext(datapack=synthetic_pack, progress=lambda s, f: steps.append((s, f))))
    assert isinstance(result, EnvironmentResult)
    assert [f for _, f in steps] == sorted(f for _, f in steps)
    assert result.utc_offset_label == "UTC+05:30" and result.date == "2026-06-21"
    assert result.sun.sunrise is not None and result.sun.sunrise.local == "05:55"
    # The survey centroid (77.49° E) is 0.1° west of the city centre: events about 25 s later.
    sunset, dusk = result.sun.sunset, result.sun.civil_dusk
    assert sunset is not None and dusk is not None
    t0 = local_midnight_utc(date(2026, 6, 21), 330)
    sunset_min = (_utc_seconds(sunset.utc) - t0) / 60.0
    dusk_min = (_utc_seconds(dusk.utc) - t0) / 60.0
    assert sunset_min == pytest.approx(SUNSET_IST_MIN, abs=1.0) and dusk_min == pytest.approx(DUSK_IST_MIN, abs=1.0)
    assert result.daylight.minutes_lighting == pytest.approx(dusk_min - sunset_min, abs=0.05)
    assert result.daylight.minutes_outside == pytest.approx(19.5 * 60 - dusk_min, abs=0.05)
    assert result.magnetic.model == "WMM2025" and result.magnetic.valid_until == "2029-12-31"
    assert -2.0 < result.magnetic.declination_deg < 0.0  # Bengaluru 2026: about −1.1°
    assert 900.0 < result.magnetic.altitude_m < 1100.0  # synthetic DEM ground ≈ 918 m + 100 m
    assert result.wind is not None and result.wind.margin_ms == pytest.approx(-1.895, abs=0.01)
    assert result.emi_assessed and result.emi_radius_m == 300.0
    assert result.emi[0].distance_m == 0.0
    assert set(result.layers) == {"input", "point", "emi", "emi_search"}
    assert len(result.layers["emi"]["features"]) == len(result.emi)
    codes = {i.code for i in result.issues}
    assert {"window_outside_profile", "wind_above_limit", "synthetic_data", "power_line_crossing"} <= codes
    assert any("Declination" in fact for fact in result.facts)
    assert "NOAA" in result.method and "WMM2025" in result.method and "power law" in result.method
    assert "not a go/no-go" in result.disclaimer
    dumped = result.model_dump()
    assert "verdict" not in dumped
    assert EnvironmentResult.model_validate(json.loads(json.dumps(dumped))).sun.solar_noon.local == result.sun.solar_noon.local


def test_run_without_a_data_pack_or_wind() -> None:
    params = EnvironmentParams(geometry={"type": "Point", "coordinates": [0.0, 0.0]}, date=date(2026, 3, 20))
    result = REGISTRY["environment"].run(params, AnalysisContext(datapack=None))
    codes = {i.code for i in result.issues}
    assert {"emi_not_assessed", "no_terrain"} <= codes
    assert not result.emi_assessed and result.emi == [] and result.wind is None
    assert result.daylight.window is None and result.daylight.minutes_outside is None
    assert result.magnetic.altitude_m == 120.0
    disabled = REGISTRY["environment"].run(
        params.model_copy(update={"include_emi": False}), AnalysisContext(datapack=None)
    )
    assert "emi_not_assessed" not in {i.code for i in disabled.issues}
    assert "emi_search" not in disabled.layers


@pytest.mark.parametrize(
    "bad",
    [
        {"window_start": "18:00"},
        {"window_start": "25:00", "window_end": "26:00"},
        {"window_start": "6:00", "window_end": "08:00"},
        {"window_start": "08:00", "window_end": "08:00"},
        {"gust_ms": 12.0},
        {"surface_wind_ms": 10.0, "gust_ms": 8.0},
        {"sun_min_deg": 60.0, "sun_max_deg": 30.0},
        {"utc_offset_min": 900},
        {"daylight_profile": "night_only"},
        {"unexpected": 1},
    ],
)
def test_invalid_params_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EnvironmentParams.model_validate({"geometry": SURVEY_POLYGON, "date": "2026-06-21", **bad})


def test_date_is_required() -> None:
    with pytest.raises(ValidationError):
        EnvironmentParams.model_validate({"geometry": SURVEY_POLYGON})
