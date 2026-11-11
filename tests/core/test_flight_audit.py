"""Tests for the flight audit (airbrief_core.analyses.flight_audit): FlightMD reports and plain
position tracks (GPX / KML datasets), training-area exits, distance from take-off, coverage."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest

from airbrief_core.analyses import flight_audit
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.data import DataPack
from airbrief_core.integrations import parse_flightmd_report
from airbrief_core.models.analyses import FlightAuditParams
from airbrief_core.models.common import UserZone, Verdict
from airbrief_core.models.features import DatasetResult, FeatureRecord
from airbrief_core.models.presets import DEFAULT_ZONE_RULES, DISCLAIMER_AIRSPACE
from airbrief_core.models.tracks import AuditTrackInput
from airbrief_core.processing import process_file
from airbrief_core.samples import (
    FLIGHT_EXCEEDANCE_END_M,
    FLIGHT_EXCEEDANCE_START_M,
    FLIGHT_MAX_HEIGHT_M,
    FLIGHT_TOTAL_DISTANCE_M,
    TRAINING_ABOVE_120_END_S,
    TRAINING_ABOVE_120_START_S,
    TRAINING_DURATION_S,
    TRAINING_EXIT_END_M,
    TRAINING_EXIT_END_S,
    TRAINING_EXIT_MAX_OUTSIDE_M,
    TRAINING_EXIT_START_M,
    TRAINING_EXIT_START_S,
    TRAINING_FARTHEST_M,
    TRAINING_MAX_HEIGHT_M,
    build_sample,
)


def _report(name: str) -> dict[str, Any]:
    return json.loads(build_sample(name).decode("utf-8"))


@pytest.mark.parametrize("name", ["flightmd_report_v15", "flightmd_report_v12"])
def test_takeoff_relative_without_datapack(name: str) -> None:
    result = flight_audit.run(FlightAuditParams(report=_report(name)), AnalysisContext())

    assert result.altitude_reference == "takeoff_relative"
    assert result.calibration_offset_m is None
    assert result.max_height_above_takeoff_m == pytest.approx(FLIGHT_MAX_HEIGHT_M, abs=0.5)
    assert result.max_agl_m == pytest.approx(FLIGHT_MAX_HEIGHT_M, abs=0.5)
    assert result.distance_m == pytest.approx(FLIGHT_TOTAL_DISTANCE_M, abs=1.0)
    assert result.ceiling_agl_m == 120.0

    assert len(result.exceedances) == 1
    exceedance = result.exceedances[0]
    assert exceedance.start_distance_m == pytest.approx(FLIGHT_EXCEEDANCE_START_M, abs=0.5)
    assert exceedance.end_distance_m == pytest.approx(FLIGHT_EXCEEDANCE_END_M, abs=0.5)
    assert exceedance.length_m == pytest.approx(FLIGHT_EXCEEDANCE_END_M - FLIGHT_EXCEEDANCE_START_M, abs=1.0)
    assert exceedance.max_agl_m == pytest.approx(FLIGHT_MAX_HEIGHT_M, abs=0.5)
    assert exceedance.limit_m == 120.0
    assert exceedance.start_index == 116
    assert exceedance.end_index == 164

    assert result.verdict == Verdict.FAIL
    assert result.disclaimer == DISCLAIMER_AIRSPACE
    assert result.nearest_aerodrome is None
    assert result.overflown_population is None
    assert result.flight.points == 301
    assert len(result.profile) == 301
    assert result.profile[0].agl_m == 0.0

    layers = result.layers
    assert set(layers) == {"path", "exceedances", "home"}
    bands = [f["properties"]["agl_band"] for f in layers["path"]["features"]]
    assert "over" in bands and "ok" in bands and "near" in bands
    assert set(bands) <= {"ok", "near", "over"}
    assert len(layers["exceedances"]["features"]) == 1
    assert layers["home"]["features"][0]["geometry"]["type"] == "Point"


def test_schema_12_note_is_reported() -> None:
    result = flight_audit.run(FlightAuditParams(report=_report("flightmd_report_v12")), AnalysisContext())
    assert result.flight.schema_version == "1.2"
    assert any("schema 1.2 detected" in issue.message for issue in result.issues)


def test_nested_report_is_accepted() -> None:
    params = FlightAuditParams(report={"report": _report("flightmd_report_v15")}, flightmd_url="https://flightmd.example/r/1")
    result = flight_audit.run(params, AnalysisContext())
    assert len(result.exceedances) == 1
    assert result.flight.flightmd_url == "https://flightmd.example/r/1"


def test_higher_ceiling_still_respects_the_zone_ceiling() -> None:
    # The limit is min(flight ceiling, zone ceiling): raising only the flight ceiling cannot lift
    # the 120 m free-flight ceiling of the default zone.
    params = FlightAuditParams(report=_report("flightmd_report_v15"), ceiling_agl_m=150.0)
    result = flight_audit.run(params, AnalysisContext())
    assert len(result.exceedances) == 1
    assert result.exceedances[0].limit_m == 120.0
    assert result.exceedances[0].reason == "Above the 120 m default ceiling outside mapped zones"
    # The requested limit is kept, but the limit actually used is in the profile and an issue
    # says why, so no result shows "limit 150 m" as if it had applied.
    assert result.ceiling_agl_m == 150.0
    assert {p.limit_agl_m for p in result.profile} == {120.0}
    capped = [i for i in result.issues if i.code == "limit_capped_default"]
    assert len(capped) == 1
    assert capped[0].message == (
        "Your 150 m height limit is above the 120 m default ceiling outside mapped zones, "
        "so the audit used 120 m along the whole flight."
    )
    assert not any(i.code == "limit_capped_zone" for i in result.issues)


def test_limit_at_or_below_the_default_ceiling_is_not_reported_as_capped() -> None:
    result = flight_audit.run(FlightAuditParams(report=_report("flightmd_report_v15"), ceiling_agl_m=100.0), AnalysisContext())
    assert not any(i.code.startswith("limit_capped") for i in result.issues)
    assert {p.limit_agl_m for p in result.profile} == {100.0}
    assert result.exceedances[0].reason == "Above the 100 m height limit"


def test_zone_ceiling_below_the_limit_is_named() -> None:
    report = _report("flightmd_report_v15")
    track = parse_flightmd_report(report)
    lons = [p[0] for p in track.points]
    lats = [p[1] for p in track.points]
    # A yellow zone with a 60 m ceiling over the first part of the flight.
    west, south, north = min(lons) - 0.01, min(lats) - 0.01, max(lats) + 0.01
    east = west + 0.01 + (max(lons) - min(lons)) / 3
    zone = UserZone(
        name="Stadium",
        zone="yellow",
        ceiling_agl_m=60.0,
        geometry={"type": "Polygon", "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]]},
    )
    params = FlightAuditParams(report=report, ceiling_agl_m=150.0, user_zones=[zone])
    result = flight_audit.run(params, AnalysisContext())
    assert {p.limit_agl_m for p in result.profile} == {60.0, 120.0}
    codes = {i.code: i.message for i in result.issues}
    assert codes["limit_capped_zone"] == "Zone ceilings below your 150 m height limit were used inside Stadium (60 m)."
    assert codes["limit_capped_default"] == (
        "Your 150 m height limit is above the 120 m default ceiling outside mapped zones, "
        "so the audit used 120 m outside mapped zones."
    )
    reasons = {e.limit_m: e.reason for e in result.exceedances}
    assert reasons == {
        60.0: "Above the 60 m ceiling of Stadium (Yellow zone: up to 60 m AGL)",
        120.0: "Above the 120 m default ceiling outside mapped zones",
    }


def test_higher_ceiling_without_a_pack_is_never_an_all_clear() -> None:
    rules = DEFAULT_ZONE_RULES.model_copy(update={"default_ceiling_agl_m": 150.0})
    params = FlightAuditParams(report=_report("flightmd_report_v15"), ceiling_agl_m=150.0, zone_rules=rules)
    result = flight_audit.run(params, AnalysisContext())
    assert result.exceedances == []
    # No data pack: aerodrome zones were not checked, so the verdict cannot be "pass".
    assert result.verdict == Verdict.CAUTION
    assert result.assessment_complete is False
    not_assessed = [i for i in result.issues if i.code == "not_assessed"]
    assert len(not_assessed) == 1
    assert not_assessed[0].message.startswith("Airspace not assessed: no data pack covers this flight")
    assert "digitalsky.dgca.gov.in" in not_assessed[0].message
    assert not any(i.code.startswith("limit_capped") for i in result.issues)


def test_higher_ceiling_with_a_covering_pack_passes(synthetic_pack: DataPack) -> None:
    rules = DEFAULT_ZONE_RULES.model_copy(update={"default_ceiling_agl_m": 150.0})
    params = FlightAuditParams(report=_report("flightmd_report_v15"), ceiling_agl_m=150.0, zone_rules=rules)
    result = flight_audit.run(params, AnalysisContext(datapack=synthetic_pack))
    assert result.exceedances == []
    assert result.zones == []
    assert result.assessment_complete is True
    assert not any(i.code == "not_assessed" for i in result.issues)
    assert result.verdict == Verdict.PASS


def test_terrain_calibrated_with_synthetic_pack(synthetic_pack: DataPack) -> None:
    report = _report("flightmd_report_v15")
    result = flight_audit.run(FlightAuditParams(report=report), AnalysisContext(datapack=synthetic_pack))

    assert result.altitude_reference == "terrain_calibrated"
    dem = synthetic_pack.dem()
    assert dem is not None
    home_lon, home_lat, home_alt = parse_flightmd_report(report).points[0]
    terrain_home = float(dem.sample([home_lon], [home_lat])[0])
    assert result.calibration_offset_m == pytest.approx(terrain_home - home_alt, abs=0.01)
    # The calibration puts the aircraft on the terrain at take-off.
    assert result.profile[0].agl_m == pytest.approx(0.0, abs=0.01)
    assert result.profile[0].terrain_m == pytest.approx(terrain_home, abs=0.01)
    assert result.max_height_above_takeoff_m == pytest.approx(FLIGHT_MAX_HEIGHT_M, abs=0.5)
    assert result.nearest_aerodrome is not None
    assert result.min_aerodrome_distance_km == pytest.approx(result.nearest_aerodrome.distance_km, abs=1e-3)
    assert result.overflown_population is not None and result.overflown_population >= 0.0
    assert result.footprint_area_km2 is not None and result.footprint_area_km2 > 0.0


def test_terrain_raw_without_calibration(synthetic_pack: DataPack) -> None:
    params = FlightAuditParams(report=_report("flightmd_report_v15"), calibration="none")
    result = flight_audit.run(params, AnalysisContext(datapack=synthetic_pack))
    assert result.altitude_reference == "terrain_raw"
    assert result.calibration_offset_m is None


def test_use_terrain_false_falls_back_to_takeoff(synthetic_pack: DataPack) -> None:
    params = FlightAuditParams(report=_report("flightmd_report_v15"), use_terrain=False)
    result = flight_audit.run(params, AnalysisContext(datapack=synthetic_pack))
    assert result.altitude_reference == "takeoff_relative"


def test_invalid_report_raises() -> None:
    from airbrief_core.errors import AnalysisError

    with pytest.raises(AnalysisError) as info:
        flight_audit.run(FlightAuditParams(report={"metadata": {"gps_path": []}}), AnalysisContext())
    assert info.value.code == "report_invalid"


@pytest.fixture(scope="module")
def training_dataset(tmp_path_factory: pytest.TempPathFactory) -> DatasetResult:
    """The GPX training-flight sample through the real ingest pipeline."""
    folder = tmp_path_factory.mktemp("gpx")
    path = folder / "training_flight.gpx"
    path.write_bytes(build_sample("training_flight_gpx"))
    return process_file(path, "training_flight.gpx", workdir=folder / "work")


def _feature(dataset: DatasetResult, name: str) -> FeatureRecord:
    return next(f for f in dataset.features if f.name == name)


def _track_input(feature: FeatureRecord, fmt: str = "gpx") -> AuditTrackInput:
    geometry = feature.geometry_wgs84
    assert geometry is not None
    return AuditTrackInput(
        name=feature.name,
        coordinates=[list(c) for c in geometry["coordinates"]],
        times=feature.times,
        time_origin_utc=feature.time_origin_utc,
        altitude_mode=feature.altitude_mode,
        source_format=fmt,  # type: ignore[arg-type]
        feature_index=feature.index,
    )


def _box_polygon(dataset: DatasetResult) -> dict[str, Any]:
    geometry = _feature(dataset, "Training box").geometry_wgs84
    assert geometry is not None
    return {"type": "Polygon", "coordinates": [geometry["coordinates"]]}


def test_gpx_track_audit_known_answers(training_dataset: DatasetResult) -> None:
    track = _track_input(_feature(training_dataset, "Training flight"))
    result = flight_audit.run(FlightAuditParams(track=track), AnalysisContext())

    assert result.flight.source == "track"
    assert result.flight.track_name == "Training flight" and result.flight.track_format == "gpx"
    assert result.flight.report_id is None and result.flight.schema_version is None
    assert result.flight.points == TRAINING_DURATION_S + 1
    assert result.flight.duration_s == float(TRAINING_DURATION_S)
    assert result.flight.log_start_utc == "2026-03-21T04:30:00Z"
    assert result.flight.altitude_mode == "absolute"
    assert result.duration_s == float(TRAINING_DURATION_S)
    # GPX <ele> is AMSL; without a DEM the heights are relative to take-off.
    assert result.altitude_reference == "takeoff_relative"
    assert result.max_agl_m == pytest.approx(TRAINING_MAX_HEIGHT_M, abs=0.01)
    assert result.distance_m == pytest.approx(1400.0, abs=0.1)

    (over,) = result.exceedances
    assert over.start_time_s == pytest.approx(TRAINING_ABOVE_120_START_S, abs=1e-3)
    assert over.end_time_s == pytest.approx(TRAINING_ABOVE_120_END_S, abs=1e-3)
    assert over.duration_s == pytest.approx(TRAINING_ABOVE_120_END_S - TRAINING_ABOVE_120_START_S, abs=1e-3)
    assert over.start_distance_m == pytest.approx(400.0, abs=0.05)
    assert over.end_distance_m == pytest.approx(500.0, abs=0.05)
    assert result.time_above_limit_s == pytest.approx(33.333, abs=1e-3)
    assert result.max_home_distance_m == pytest.approx(TRAINING_FARTHEST_M, abs=0.01)

    # The optional checks were not asked for: no fields, no layers (old results stay stable).
    assert result.area_exits is None and result.distance_exceedances is None
    assert set(result.layers) == {"path", "exceedances", "home"}
    assert result.verdict == Verdict.FAIL
    assert result.assessment_complete is False
    assert any(i.code == "not_assessed" for i in result.issues)


def test_training_box_exit(training_dataset: DatasetResult) -> None:
    params = FlightAuditParams(
        track=_track_input(_feature(training_dataset, "Training flight")),
        training_box=_box_polygon(training_dataset),
        training_box_name="Training box",
        ceiling_agl_m=150.0,
        zone_rules=DEFAULT_ZONE_RULES.model_copy(update={"default_ceiling_agl_m": 150.0}),
    )
    result = flight_audit.run(params, AnalysisContext())
    assert result.exceedances == []
    assert result.training_box_name == "Training box"
    assert result.area_exits is not None
    (exit_,) = result.area_exits
    assert exit_.kind == "area_exit" and exit_.limit_m == 0.0
    assert exit_.start_time_s == pytest.approx(TRAINING_EXIT_START_S, abs=1e-3)
    assert exit_.end_time_s == pytest.approx(TRAINING_EXIT_END_S, abs=1e-3)
    assert exit_.duration_s == pytest.approx(60.0, abs=1e-3)
    assert exit_.start_distance_m == pytest.approx(TRAINING_EXIT_START_M, abs=0.05)
    assert exit_.end_distance_m == pytest.approx(TRAINING_EXIT_END_M, abs=0.05)
    assert exit_.peak_m == pytest.approx(TRAINING_EXIT_MAX_OUTSIDE_M, abs=0.05)
    assert result.time_outside_area_s == pytest.approx(60.0, abs=1e-3)
    assert result.verdict == Verdict.FAIL
    assert result.layers["training_box"]["features"][0]["properties"]["name"] == "Training box"
    (feature,) = result.layers["area_exits"]["features"]
    assert feature["geometry"]["type"] == "LineString"
    assert feature["properties"]["duration_s"] == pytest.approx(60.0, abs=1e-3)


def test_closed_line_training_box_is_accepted(training_dataset: DatasetResult) -> None:
    box_line = _feature(training_dataset, "Training box").geometry_wgs84
    params = FlightAuditParams(track=_track_input(_feature(training_dataset, "Training flight")), training_box=box_line)
    result = flight_audit.run(params, AnalysisContext())
    assert result.area_exits is not None and len(result.area_exits) == 1
    assert result.training_box_name == "Training box"  # the default label


def test_training_box_must_be_an_area(training_dataset: DatasetResult) -> None:
    from airbrief_core.errors import AnalysisError

    params = FlightAuditParams(
        track=_track_input(_feature(training_dataset, "Training flight")),
        training_box={"type": "Point", "coordinates": [77.488, 12.902]},
    )
    with pytest.raises(AnalysisError) as info:
        flight_audit.run(params, AnalysisContext())
    assert info.value.code == "geometry_type_not_supported"


def test_distance_from_home(training_dataset: DatasetResult) -> None:
    params = FlightAuditParams(
        track=_track_input(_feature(training_dataset, "Training flight")), max_distance_from_home_m=500.0
    )
    result = flight_audit.run(params, AnalysisContext())
    assert result.max_distance_from_home_m == 500.0
    assert result.distance_exceedances is not None
    (far,) = result.distance_exceedances
    # Beyond 500 m from x = √(500² − 100²) = 489.898 m on the y = 100 m leg (t = 197.98 s) until
    # x = 500 m on the way back (t = 260 s); farthest √(600² + 100²) = 608.28 m.
    expected_start = 180.0 + (math.sqrt(500.0**2 - 100.0**2) - 400.0) / 5.0
    assert far.start_time_s == pytest.approx(expected_start, abs=0.02)
    assert far.end_time_s == pytest.approx(260.0, abs=1e-3)
    assert far.peak_m == pytest.approx(TRAINING_FARTHEST_M, abs=0.01)
    assert far.limit_m == 500.0
    assert result.time_beyond_distance_s == pytest.approx(260.0 - expected_start, abs=0.02)
    assert len(result.layers["home_radius"]["features"]) == 1
    assert len(result.layers["distance_exceedances"]["features"]) == 1
    assert result.verdict == Verdict.FAIL


def test_track_with_a_covering_pack(training_dataset: DatasetResult, synthetic_pack: DataPack) -> None:
    params = FlightAuditParams(track=_track_input(_feature(training_dataset, "Training flight")))
    result = flight_audit.run(params, AnalysisContext(datapack=synthetic_pack))
    assert result.assessment_complete is True
    assert not any(i.code == "not_assessed" for i in result.issues)
    assert result.altitude_reference == "terrain_calibrated"
    assert result.profile[0].agl_m == pytest.approx(0.0, abs=0.01)
    assert result.max_height_above_takeoff_m == pytest.approx(TRAINING_MAX_HEIGHT_M, abs=0.01)


def test_partly_covered_track_is_partly_assessed(synthetic_pack: DataPack) -> None:
    from seed.datapack import SYNTHETIC_BBOX

    edge = SYNTHETIC_BBOX[2]
    track = AuditTrackInput(
        coordinates=[[edge - 0.002, 13.0, 900.0], [edge - 0.001, 13.0, 950.0], [edge + 0.001, 13.0, 950.0]],
        times=[0.0, 10.0, 20.0],
        altitude_mode="absolute",
    )
    result = flight_audit.run(FlightAuditParams(track=track, ceiling_agl_m=100.0), AnalysisContext(datapack=synthetic_pack))
    assert result.assessment_complete is False
    gap = next(i for i in result.issues if i.code == "not_assessed")
    assert gap.message.startswith("Airspace only partly assessed: 1 of 3 track points lie outside data pack 'demo'")


def _line_track(mode: str | None, alts: list[float] | None) -> AuditTrackInput:
    lons = [77.488, 77.489, 77.490]
    coords = [[lo, 12.902] if alts is None else [lo, 12.902, a] for lo, a in zip(lons, alts or [0.0] * 3, strict=True)]
    return AuditTrackInput(coordinates=coords, times=[0.0, 5.0, 10.0], altitude_mode=mode)


def test_heights_recorded_above_ground(synthetic_pack: DataPack) -> None:
    track = _line_track("relativeToGround", [0.0, 125.0, 0.0])
    result = flight_audit.run(FlightAuditParams(track=track), AnalysisContext(datapack=synthetic_pack))
    assert result.altitude_reference == "recorded_agl"
    assert [p.agl_m for p in result.profile] == [0.0, 125.0, 0.0]
    assert all(p.terrain_m is not None for p in result.profile)
    assert result.profile[1].altitude_amsl_m == pytest.approx((result.profile[1].terrain_m or 0.0) + 125.0, abs=0.01)
    (over,) = result.exceedances
    # 120 m is crossed 120/125 of the way up and down: t = 4.8 s and 5.2 s.
    assert over.start_time_s == pytest.approx(4.8, abs=1e-6) and over.end_time_s == pytest.approx(5.2, abs=1e-6)


def test_ambiguous_altitude_mode_reads_heights_above_takeoff() -> None:
    result = flight_audit.run(FlightAuditParams(track=_line_track("clampToGround", [5.0, 55.0, 5.0])), AnalysisContext())
    assert result.altitude_reference == "takeoff_relative"
    assert [p.agl_m for p in result.profile] == [0.0, 50.0, 0.0]
    assert any(i.code == "track_heights_assumed" for i in result.issues)
    # The profile has no AMSL value for heights relative to take-off.
    assert all(p.altitude_amsl_m is None for p in result.profile)


def test_track_heights_override() -> None:
    track = _line_track("clampToGround", [0.0, 130.0, 0.0])
    result = flight_audit.run(FlightAuditParams(track=track, track_heights="above_ground"), AnalysisContext())
    assert result.altitude_reference == "recorded_agl"
    assert not any(i.code == "track_heights_assumed" for i in result.issues)
    assert len(result.exceedances) == 1


def test_track_without_heights_is_not_assessed(synthetic_pack: DataPack) -> None:
    # A 2D track (GPX without <ele>, CSV without a height column) says nothing about heights.
    result = flight_audit.run(FlightAuditParams(track=_line_track("clampToGround", None)), AnalysisContext())
    assert result.heights_assessed is False
    assert result.max_agl_m == 0.0 and result.exceedances == []
    assert any(i.code == "heights_not_assessed" and i.severity == "warning" for i in result.issues)
    assert not any(i.code == "track_heights_assumed" for i in result.issues)
    assert result.verdict == Verdict.CAUTION

    # Even with a covering pack (zones assessed, terrain known) it is never a "pass", and the
    # terrain does not turn into fake heights (the profile stays at 0 m, terrain shown).
    covered = flight_audit.run(
        FlightAuditParams(track=_line_track("clampToGround", None)), AnalysisContext(datapack=synthetic_pack)
    )
    assert covered.assessment_complete is True and covered.heights_assessed is False
    assert covered.verdict == Verdict.CAUTION
    assert all(p.agl_m == 0.0 and p.terrain_m is not None and p.altitude_amsl_m is None for p in covered.profile)
    assert covered.calibration_offset_m is None
    assert not any(i.code == "no_dem" for i in covered.issues)


def test_track_with_heights_is_assessed() -> None:
    result = flight_audit.run(FlightAuditParams(track=_line_track("absolute", [900.0, 950.0, 900.0])), AnalysisContext())
    assert result.heights_assessed is True
    assert not any(i.code == "heights_not_assessed" for i in result.issues)


def test_csv_track_without_heights_is_2d_and_not_assessed(tmp_path: Path) -> None:
    path = tmp_path / "walk.csv"
    path.write_text(
        "time,lon,lat\n2026-02-01T08:00:00Z,77.488,12.902\n2026-02-01T08:00:10Z,77.489,12.902\n"
        "2026-02-01T08:00:20Z,77.490,12.902\n",
        encoding="utf-8",
    )
    dataset = process_file(path, "walk.csv", workdir=tmp_path / "work")
    (feature,) = dataset.features
    assert feature.altitude_mode == "clampToGround"
    result = flight_audit.run(FlightAuditParams(track=_track_input(feature, "csv")), AnalysisContext())
    assert result.heights_assessed is False and result.verdict == Verdict.CAUTION
    assert result.duration_s == 20.0


def test_kml_gx_track_dataset(tmp_path: Path) -> None:
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">
<Placemark><name>Inspection</name><gx:Track><altitudeMode>absolute</altitudeMode>
<when>2026-02-01T08:00:00Z</when><when>2026-02-01T08:00:30Z</when><when>2026-02-01T08:01:00Z</when>
<gx:coord>77.5 12.95 900</gx:coord><gx:coord>77.501 12.95 1030</gx:coord><gx:coord>77.502 12.95 900</gx:coord>
</gx:Track></Placemark></kml>"""
    path = tmp_path / "inspection.kml"
    path.write_bytes(kml)
    dataset = process_file(path, "inspection.kml", workdir=tmp_path / "work")
    result = flight_audit.run(FlightAuditParams(track=_track_input(dataset.features[0], "kml")), AnalysisContext())
    assert result.flight.track_format == "kml" and result.flight.duration_s == 60.0
    (over,) = result.exceedances
    # Up 130 m in 30 s: 120 m at 30·120/130 = 27.69 s, and down again at 60 − 27.69 = 32.31 s.
    assert over.start_time_s == pytest.approx(30.0 * 120.0 / 130.0, abs=1e-3)
    assert over.end_time_s == pytest.approx(60.0 - 30.0 * 120.0 / 130.0, abs=1e-3)


def test_report_or_track_is_required() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="FlightMD report"):
        FlightAuditParams()
    with pytest.raises(ValidationError, match="not both"):
        FlightAuditParams(report=_report("flightmd_report_v15"), track=_line_track("absolute", [0.0, 1.0, 0.0]))
    with pytest.raises(ValidationError, match="never decrease"):
        AuditTrackInput(coordinates=[[77.5, 12.9], [77.6, 12.9]], times=[5.0, 1.0])
    with pytest.raises(ValidationError, match="same length"):
        AuditTrackInput(coordinates=[[77.5, 12.9], [77.6, 12.9]], times=[0.0])
