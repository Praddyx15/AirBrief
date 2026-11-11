"""Tests for the drone-show validator (airbrief_core.analyses.shows).

Show parameters are built from the sample KML through ``process_file`` (features with ``times``
become tracks; the polygons named "Show box" and "Audience" become the site geometry), the same
way the API resolves show inputs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from airbrief_core import _native
from airbrief_core.analyses import shows
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.geo.frame import LocalFrame
from airbrief_core.models.analyses import ShowParams, TrackInput
from airbrief_core.models.common import Verdict
from airbrief_core.processing import process_file
from airbrief_core.samples import (
    SAMPLES,
    SHOW_CONFLICT_MIN_DISTANCE_M,
    SHOW_CONFLICT_PAIR,
    SHOW_CONFLICT_TIME_S,
    SHOW_DRONE_COUNT,
    SHOW_ORIGIN,
    build_sample,
)

_SHOW_BOX = re.compile(r"show\s*box", re.IGNORECASE)
_AUDIENCE = re.compile(r"audience", re.IGNORECASE)


def _show_params(tmp_path: Path, sample: str, **overrides: Any) -> ShowParams:
    filename = Path(SAMPLES[sample].filename).name
    path = tmp_path / filename
    path.write_bytes(build_sample(sample))
    dataset = process_file(path, filename, workdir=tmp_path / "work")
    tracks: list[TrackInput] = []
    show_box: dict[str, Any] | None = None
    audience: dict[str, Any] | None = None
    for feature in dataset.features:
        geometry = feature.geometry_wgs84
        if geometry is None:
            continue
        if feature.times:
            tracks.append(
                TrackInput(
                    id=feature.name or str(feature.fid),
                    times=list(feature.times),
                    coordinates=[list(c) for c in geometry["coordinates"]],
                )
            )
        elif geometry["type"] == "Polygon" and feature.name:
            if _SHOW_BOX.search(feature.name):
                show_box = geometry
            elif _AUDIENCE.search(feature.name):
                audience = geometry
    params: dict[str, Any] = {"tracks": tracks, "show_box": show_box, "audience": audience}
    params.update(overrides)
    return ShowParams(**params)


@pytest.fixture
def force_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the separation kernels on the numpy/scipy fallback."""
    monkeypatch.setenv("AIRBRIEF_FORCE_PYTHON", "1")
    monkeypatch.setattr(_native, "_cpp", None)
    monkeypatch.setattr(_native, "BACKEND", "python")


def test_conflict_sample_has_exactly_one_conflict(tmp_path: Path, force_python: None) -> None:
    params = _show_params(tmp_path, "show_50_drones")
    assert len(params.tracks) == SHOW_DRONE_COUNT
    assert params.show_box is not None and params.audience is not None

    result = shows.run(params, AnalysisContext())

    assert result.backend == "python"
    assert result.drone_count == SHOW_DRONE_COUNT
    assert result.verdict == Verdict.FAIL
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert (conflict.drone_a, conflict.drone_b) == SHOW_CONFLICT_PAIR
    assert conflict.t_min_s == pytest.approx(SHOW_CONFLICT_TIME_S, abs=1e-6)
    assert conflict.start_s == pytest.approx(SHOW_CONFLICT_TIME_S, abs=1e-6)
    assert conflict.end_s == pytest.approx(SHOW_CONFLICT_TIME_S, abs=1e-6)
    assert conflict.min_distance_m == pytest.approx(SHOW_CONFLICT_MIN_DISTANCE_M, abs=0.01)
    # Both drones are at the ring centre: the midpoint is above the origin at ~50.4 m.
    east, north, up = conflict.position_enu
    assert abs(east) < 0.05 and abs(north) < 0.05
    assert up == pytest.approx(50.4, abs=0.05)

    assert result.min_separation_m == pytest.approx(SHOW_CONFLICT_MIN_DISTANCE_M, abs=0.01)
    assert result.min_separation_at_s == pytest.approx(SHOW_CONFLICT_TIME_S, abs=1e-6)
    assert result.min_separation_pair == list(SHOW_CONFLICT_PAIR)
    assert not result.events_truncated


def test_conflict_sample_on_default_backend(tmp_path: Path) -> None:
    result = shows.run(_show_params(tmp_path, "show_50_drones"), AnalysisContext())
    assert result.backend == _native.BACKEND
    assert [(c.drone_a, c.drone_b, c.t_min_s) for c in result.conflicts] == [
        (SHOW_CONFLICT_PAIR[0], SHOW_CONFLICT_PAIR[1], SHOW_CONFLICT_TIME_S)
    ]


def test_clean_sample_passes(tmp_path: Path, force_python: None) -> None:
    params = _show_params(tmp_path, "show_50_drones_clean", origin=SHOW_ORIGIN)
    result = shows.run(params, AnalysisContext())

    assert result.conflicts == []
    assert result.violations == []
    assert result.min_separation_m == pytest.approx(3.14, abs=0.02)
    assert result.duration_s == pytest.approx(126.0)
    assert result.sample_rate_hz == pytest.approx(10.0)
    assert result.frames_checked == 1261

    # Site: 120 m box, audience 70 m south of it, outside the 50 m buffer.
    assert result.site.show_box_area_m2 == pytest.approx(14_400.0, rel=1e-3)
    assert result.site.audience_area_m2 == pytest.approx(6_000.0, rel=1e-3)
    assert result.site.box_audience_distance_m == pytest.approx(70.0, abs=0.1)
    assert result.site.box_overlaps_audience_buffer is False
    assert result.verdict == Verdict.PASS

    # Playback frames: 4 Hz, cm-rounded ENU.
    assert result.frames.drone_ids[0] == "D00"
    assert len(result.frames.times) == 505
    assert len(result.frames.positions) == 505
    assert len(result.frames.positions[0]) == SHOW_DRONE_COUNT
    first = result.frames.positions[0][0]
    assert first == pytest.approx([40.0, 0.0, 0.0], abs=0.02)
    assert all(abs(v * 100.0 - round(v * 100.0)) < 1e-6 for v in result.frames.positions[200][7])

    assert len(result.drones) == SHOW_DRONE_COUNT
    assert max(d.max_altitude_m for d in result.drones) == pytest.approx(50.0, abs=0.01)
    assert all(d.max_horizontal_speed_ms <= params.max_horizontal_speed_ms for d in result.drones)
    assert len(result.layers["paths"]["features"]) == SHOW_DRONE_COUNT
    assert result.layers["paths"]["features"][0]["geometry"]["type"] == "LineString"
    assert len(result.layers["paths"]["features"][0]["geometry"]["coordinates"][0]) == 3
    assert len(result.layers["show_box"]["features"]) == 1
    roles = {f["properties"]["role"] for f in result.layers["audience_buffer"]["features"]}
    assert roles == {"audience", "audience_buffer"}


def test_tight_limits_produce_grouped_violations(tmp_path: Path, force_python: None) -> None:
    params = _show_params(
        tmp_path,
        "show_50_drones_clean",
        origin=SHOW_ORIGIN,
        ceiling_m=45.0,
        max_climb_rate_ms=1.0,
        max_events=10,
    )
    result = shows.run(params, AnalysisContext())
    assert result.verdict == Verdict.FAIL
    kinds = {v.kind for v in result.violations}
    assert kinds <= {"ceiling", "climb"}
    assert result.events_truncated
    assert len(result.violations) == 10
    ceiling = [v for v in result.violations if v.kind == "ceiling"]
    for v in ceiling:
        assert v.peak_value == pytest.approx(50.0, abs=0.01)
        assert v.limit == 45.0
        assert v.end_s > v.start_s


def test_show_box_outside_and_audience_overlap(tmp_path: Path, force_python: None) -> None:
    params = _show_params(tmp_path, "show_50_drones_clean", origin=SHOW_ORIGIN, audience_buffer_m=100.0)
    # Shrink the show box to 30 m around the origin: the 40 m ring is outside it.
    lon0, lat0, _ = SHOW_ORIGIN
    d_lon, d_lat = 15.0 / 108_000.0, 15.0 / 110_600.0
    small_box = {
        "type": "Polygon",
        "coordinates": [[
            [lon0 - d_lon, lat0 - d_lat],
            [lon0 + d_lon, lat0 - d_lat],
            [lon0 + d_lon, lat0 + d_lat],
            [lon0 - d_lon, lat0 + d_lat],
            [lon0 - d_lon, lat0 - d_lat],
        ]],
    }
    params = params.model_copy(update={"show_box": small_box})
    result = shows.run(params, AnalysisContext())
    kinds = {v.kind for v in result.violations}
    assert "geofence" in kinds
    assert result.verdict == Verdict.FAIL
    assert result.site.box_overlaps_audience_buffer is False


def test_invalid_tracks_are_rejected() -> None:
    from airbrief_core.errors import AnalysisError

    track = TrackInput(id="A", times=[0.0, 1.0], coordinates=[[77.59, 13.0, 10.0], [77.59, 13.0, 10.0]])
    with pytest.raises(AnalysisError) as info:
        shows.run(ShowParams(tracks=[track, track]), AnalysisContext())
    assert info.value.code == "invalid_params"

    still = TrackInput(id="B", times=[5.0, 5.0], coordinates=[[77.59, 13.0, 10.0], [77.59, 13.0, 10.0]])
    with pytest.raises(AnalysisError) as info:
        shows.run(ShowParams(tracks=[still]), AnalysisContext())
    assert info.value.code == "invalid_params"


def _synthetic(rows: dict[str, list[tuple[float, float, float, float]]]) -> list[TrackInput]:
    """Tracks from ``(t, east, north, up)`` rows in metres around ``SHOW_ORIGIN``."""
    frame = LocalFrame(SHOW_ORIGIN[0], SHOW_ORIGIN[1])
    out: list[TrackInput] = []
    for drone, samples in rows.items():
        lon, lat = frame.lonlat(np.array([r[1] for r in samples]), np.array([r[2] for r in samples]))
        coords = [[float(lo), float(la), r[3]] for lo, la, r in zip(lon.tolist(), lat.tolist(), samples, strict=True)]
        out.append(TrackInput(id=drone, times=[r[0] for r in samples], coordinates=coords))
    return out


# A climbs to 20 m, flies 30 m east at 3 m/s and lands; B (pad 1.2 m east of A's) climbs to 5 m,
# flies 30 m east at 3 m/s at 5 m and lands 1.2 m from where A lands.
_PAIR = {
    "A": [(0.0, 0.0, 0.0, 0.0), (10.0, 0.0, 0.0, 20.0), (20.0, 30.0, 0.0, 20.0), (30.0, 30.0, 0.0, 0.0)],
    "B": [(0.0, 1.2, 0.0, 0.0), (5.0, 1.2, 0.0, 5.0), (15.0, 31.2, 0.0, 5.0), (20.0, 31.2, 0.0, 0.0)],
}


def _pair_params(**overrides: Any) -> ShowParams:
    params: dict[str, Any] = {"tracks": _synthetic(_PAIR), "origin": SHOW_ORIGIN, "min_separation_m": 1.0}
    params.update(overrides)
    return ShowParams(**params)


def test_audience_buffer_rules_known_answers() -> None:
    base = _pair_params()
    fixed = shows.audience_buffer(base.model_copy(update={"audience_buffer_m": 80.0}), 100.0, 10.0)
    assert (fixed.rule, fixed.base_distance_m, fixed.distance_m) == ("fixed", 80.0, 80.0)
    one = shows.audience_buffer(base.model_copy(update={"audience_buffer_rule": "one_to_one"}), 100.0, 10.0)
    assert one.distance_m == pytest.approx(100.0)
    # Ballistic: 10 m/s × √(2 × 100 m / 9.80665 m/s²) = 45.160 m; +50 % → 67.740 m.
    ballistic = shows.audience_buffer(
        base.model_copy(update={"audience_buffer_rule": "ballistic", "audience_breach_margin_pct": 50.0}), 100.0, 10.0
    )
    assert ballistic.base_distance_m == pytest.approx(45.16, abs=0.005)
    assert ballistic.distance_m == pytest.approx(67.74, abs=0.005)
    assert ballistic.breach_margin_pct == 50.0
    assert "v·√(2h/g)" in ballistic.formula and "safety factor" in ballistic.formula


def test_derived_show_box_pads_and_echoed_inputs(force_python: None) -> None:
    result = shows.run(_pair_params(), AnalysisContext())
    # No show box: the tracks' extent (31.2 m × 0 m) plus 5 m on every side.
    assert result.site.show_box_source == "derived"
    assert result.site.show_box_margin_m == 5.0
    assert result.site.show_box_area_m2 == pytest.approx(41.2 * 10.0, rel=1e-3)
    assert "show_box_derived" in {i.code for i in result.issues}
    assert not [v for v in result.violations if v.kind == "geofence"]
    box = result.layers["show_box"]["features"][0]["properties"]
    assert box["source"] == "derived" and "derived from the tracks" in box["name"]
    assert result.origin_source == "given"
    assert result.altitude_reference == "relative"
    assert result.max_altitude_m == pytest.approx(20.0)
    # Pads: both drones start and end 1.2 m apart (limit 1.5 m) → two pad conflicts, verdict fail.
    pads = result.pads
    assert pads is not None
    assert (pads.takeoff_pads, pads.landing_pads) == (2, 2)
    assert pads.takeoff_min_spacing_m == pytest.approx(1.2, abs=1e-3)
    assert pads.landing_min_spacing_m == pytest.approx(1.2, abs=1e-3)
    assert sorted(c.phase for c in pads.conflicts) == ["landing", "takeoff"]
    assert {c.time_s for c in pads.conflicts} == {0.0, 30.0}
    assert len(result.layers["pad_conflicts"]["features"]) == 2
    assert "pad_spacing" in {i.code for i in result.issues}
    assert result.conflicts == [] and result.violations == []
    assert result.verdict == Verdict.FAIL

    spaced = shows.run(_pair_params(min_pad_spacing_m=1.0), AnalysisContext())
    assert spaced.pads is not None and spaced.pads.conflicts == []
    assert spaced.verdict == Verdict.PASS


def test_low_altitude_motion_and_endurance(force_python: None) -> None:
    result = shows.run(
        _pair_params(min_pad_spacing_m=1.0, min_navigation_altitude_m=10.0, endurance_s=20.0), AnalysisContext()
    )
    low = [v for v in result.violations if v.kind == "low_altitude_motion"]
    # B flies 3 m/s at 5 m (below 10 m); the 1 s window ramps up from t = 4.5 s. A moves only at 20 m.
    assert [v.drone for v in low] == ["B"]
    assert low[0].peak_value == pytest.approx(3.0, abs=0.01)
    assert low[0].limit == 0.5
    assert low[0].start_s == pytest.approx(4.7, abs=0.01)
    assert low[0].end_s == pytest.approx(15.3, abs=0.01)
    # A is above 1 m from 0.6 s to 29.4 s (28.9 s at 10 Hz) and passes 20 s at t = 20.6 s; B flies 17.9 s.
    endurance = [v for v in result.violations if v.kind == "endurance"]
    assert [v.drone for v in endurance] == ["A"]
    assert endurance[0].peak_value == pytest.approx(28.9, abs=0.01)
    assert endurance[0].limit == 20.0
    assert endurance[0].start_s == pytest.approx(20.6, abs=0.01)
    assert endurance[0].end_s == pytest.approx(29.4, abs=0.01)
    stats = {d.drone: d.airborne_s for d in result.drones}
    assert stats["A"] == pytest.approx(28.9) and stats["B"] == pytest.approx(17.9)
    assert result.verdict == Verdict.FAIL

    off = shows.run(_pair_params(min_pad_spacing_m=1.0), AnalysisContext())
    assert not {v.kind for v in off.violations} & {"low_altitude_motion", "endurance"}


def test_ballistic_audience_line_without_audience(force_python: None) -> None:
    result = shows.run(_pair_params(min_pad_spacing_m=1.0, audience_buffer_rule="ballistic"), AnalysisContext())
    rule = result.site.audience_buffer
    assert rule is not None and rule.rule == "ballistic"
    assert rule.height_m == pytest.approx(20.0)
    assert rule.speed_ms == pytest.approx(3.0, abs=0.01)
    # 3 m/s × √(2 × 20 m / 9.80665 m/s²) = 6.059 m.
    assert rule.distance_m == pytest.approx(6.059, abs=0.01)
    line = result.layers["audience_line"]["features"]
    assert len(line) == 1 and line[0]["properties"]["distance_m"] == pytest.approx(rule.distance_m)
    assert "no_audience" in {i.code for i in result.issues}


def test_clean_sample_site_rules_and_pads(tmp_path: Path, force_python: None) -> None:
    base = _show_params(tmp_path, "show_50_drones_clean", origin=SHOW_ORIGIN)
    clean = shows.run(base, AnalysisContext())
    assert clean.site.show_box_source == "given" and clean.site.show_box_margin_m is None
    assert clean.max_altitude_m == pytest.approx(50.0, abs=0.01)
    # 50 pads on a 40 m ring: neighbours 2 × 40 m × sin(π/50) = 5.023 m apart, at take-off and landing.
    assert clean.pads is not None
    assert (clean.pads.takeoff_pads, clean.pads.landing_pads) == (SHOW_DRONE_COUNT, SHOW_DRONE_COUNT)
    assert clean.pads.takeoff_min_spacing_m == pytest.approx(5.023, abs=0.002)
    assert clean.pads.landing_min_spacing_m == pytest.approx(5.023, abs=0.002)
    assert clean.pads.conflicts == []
    assert clean.site.audience_buffer is not None and clean.site.audience_buffer.rule == "fixed"
    assert clean.verdict == Verdict.PASS

    # 1:1 rule with a +50 % factor: 50 m × 1.5 = 75 m, more than the 70 m from the box to the audience.
    one = shows.run(
        base.model_copy(update={"audience_buffer_rule": "one_to_one", "audience_breach_margin_pct": 50.0}),
        AnalysisContext(),
    )
    assert one.site.audience_buffer is not None
    assert one.site.audience_buffer.distance_m == pytest.approx(75.0, abs=0.02)
    assert one.site.box_overlaps_audience_buffer is True
    assert one.violations == []
    assert one.verdict == Verdict.CAUTION

    # Pads closer than 6 m: the 50 ring neighbours at take-off and the 50 at landing.
    tight = shows.run(base.model_copy(update={"min_pad_spacing_m": 6.0}), AnalysisContext())
    assert tight.pads is not None and len(tight.pads.conflicts) == 2 * SHOW_DRONE_COUNT
    assert tight.verdict == Verdict.FAIL

    inferred = shows.run(base.model_copy(update={"origin": None}), AnalysisContext())
    assert inferred.origin_source == "inferred"


def test_airborne_start_skips_the_pad_check(force_python: None) -> None:
    rows = {
        "A": [(0.0, 0.0, 0.0, 10.0), (10.0, 10.0, 0.0, 10.0)],
        "B": [(0.0, 0.0, 10.0, 10.0), (10.0, 10.0, 10.0, 10.0)],
    }
    result = shows.run(ShowParams(tracks=_synthetic(rows), origin=SHOW_ORIGIN), AnalysisContext())
    assert result.pads is not None and result.pads.takeoff_pads == 0 and result.pads.landing_pads == 0
    assert result.pads.takeoff_min_spacing_m is None
    assert "pads_not_checked" in {i.code for i in result.issues}


def _absolute(rows: dict[str, list[tuple[float, float, float, float]]], ground: float) -> list[TrackInput]:
    """``_synthetic`` tracks with heights above sea level (the take-off pads at ``ground``)."""
    return _synthetic({d: [(t, e, n, u + ground) for t, e, n, u in samples] for d, samples in rows.items()})


def test_absolute_altitudes_without_an_origin_are_measured_from_the_pads(force_python: None) -> None:
    tracks = _absolute(_PAIR, 900.0)
    result = shows.run(
        ShowParams(tracks=tracks, altitude_reference="absolute", min_separation_m=1.0, min_pad_spacing_m=1.0),
        AnalysisContext(),
    )
    # Not 920 m: heights are above the lowest take-off position (900 m), as the API does for files.
    assert result.max_altitude_m == pytest.approx(20.0)
    assert result.frames.origin[2] == pytest.approx(900.0)
    assert result.origin_source == "inferred"
    assert not [v for v in result.violations if v.kind == "ceiling"]
    note = next(i for i in result.issues if i.code == "origin_inferred")
    assert "lowest take-off position, 900.0 m above sea level" in note.message
    assert result.verdict == Verdict.PASS


def test_origin_without_ground_altitude(force_python: None) -> None:
    tracks = _absolute(_PAIR, 900.0)
    lon, lat = SHOW_ORIGIN[0], SHOW_ORIGIN[1]
    common: dict[str, Any] = {"tracks": tracks, "altitude_reference": "absolute", "min_separation_m": 1.0, "min_pad_spacing_m": 1.0}
    picked = shows.run(ShowParams(origin=(lon, lat), **common), AnalysisContext())
    assert picked.origin_source == "given"
    assert picked.frames.origin == pytest.approx([lon, lat, 900.0])
    assert picked.max_altitude_m == pytest.approx(20.0)
    assert "origin_ground_inferred" in {i.code for i in picked.issues}

    typed = shows.run(ShowParams(origin=(lon, lat, 905.0), **common), AnalysisContext())
    assert typed.max_altitude_m == pytest.approx(15.0)
    assert "origin_ground_inferred" not in {i.code for i in typed.issues}

    # Relative heights ignore the ground altitude: a 2-value origin is the 0 m reference.
    relative = shows.run(ShowParams(origin=(lon, lat), tracks=_synthetic(_PAIR), min_separation_m=1.0, min_pad_spacing_m=1.0), AnalysisContext())
    assert relative.frames.origin == pytest.approx([lon, lat, 0.0])
    assert "origin_ground_inferred" not in {i.code for i in relative.issues}
