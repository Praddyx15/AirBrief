"""Tests for the FlightMD report adapter (airbrief_core.integrations.flightmd)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from airbrief_core.errors import AnalysisError
from airbrief_core.integrations import FlightTrack, parse_flightmd_report
from airbrief_core.samples import (
    FLIGHT_HOME,
    FLIGHT_HOME_ALT_AMSL_M,
    FLIGHT_MAX_HEIGHT_M,
    build_sample,
)


def _report(name: str) -> dict[str, Any]:
    return json.loads(build_sample(name).decode("utf-8"))


@pytest.mark.parametrize(("name", "version"), [("flightmd_report_v15", "1.5"), ("flightmd_report_v12", "1.2")])
def test_parses_sample_reports(name: str, version: str) -> None:
    report = _report(name)
    track = parse_flightmd_report(report, flightmd_url="https://flightmd.example/r/42")
    assert isinstance(track, FlightTrack)
    assert track.schema_version == version
    assert track.info.schema_version == version
    assert len(track.points) == 301
    assert track.info.points == 301
    assert track.info.flightmd_url == "https://flightmd.example/r/42"
    assert track.info.location_name == "Bengaluru, India"
    assert track.info.overall_score == pytest.approx(78.4)
    assert track.info.file_name == "2026-03-14_06-12-08_survey.bin"
    if version == "1.5":
        assert track.info.letter_grade == "B+"
    else:
        assert track.info.letter_grade is None
        assert any("schema 1.2 detected" in note for note in track.notes)

    # gps_path is [lat, lon, alt]; the track is [lon, lat, alt].
    first_raw = report["metadata"]["gps_path"][0]
    lon, lat, alt = track.points[0]
    assert (lon, lat, alt) == (first_raw[1], first_raw[0], first_raw[2])
    assert lon == pytest.approx(FLIGHT_HOME[0], abs=1e-6)
    assert lat == pytest.approx(FLIGHT_HOME[1], abs=1e-6)
    assert alt == pytest.approx(FLIGHT_HOME_ALT_AMSL_M)
    assert max(p[2] for p in track.points) - alt == pytest.approx(FLIGHT_MAX_HEIGHT_M, abs=0.01)


def test_nested_report_and_json_text() -> None:
    report = _report("flightmd_report_v15")
    nested = parse_flightmd_report({"status": "ok", "report": report})
    direct = parse_flightmd_report(report)
    assert nested.points == direct.points
    assert nested.info.report_id == direct.info.report_id == "fmd-20260314-bng-0042"
    from_text = parse_flightmd_report(json.dumps(report))
    assert from_text.points == direct.points


def test_pydantic_like_object_with_model_dump() -> None:
    report = _report("flightmd_report_v12")

    class FakeReport:
        def model_dump(self, mode: str = "python") -> dict[str, Any]:
            assert mode == "json"
            return copy.deepcopy(report)

    track = parse_flightmd_report(FakeReport())
    assert len(track.points) == 301
    assert track.schema_version == "1.2"


def test_hard_coded_schema_quirk_is_corrected() -> None:
    report = _report("flightmd_report_v15")
    report["schema_version"] = "1.2"  # upstream builder hard-codes 1.2
    track = parse_flightmd_report(report)
    assert track.schema_version == "1.5"
    assert any("treated as schema 1.5" in note for note in track.notes)


def test_schema_10_layout_with_top_level_gps_path() -> None:
    report = {
        "report_id": "old",
        "gps_path": [[12.9, 77.5, 900.0], [12.901, 77.501, 910.0], [12.902, 77.502, 905.0]],
    }
    track = parse_flightmd_report(report)
    assert track.schema_version == "1.0"
    assert track.points[1] == [77.501, 12.901, 910.0]
    assert track.info.location_name is None


def test_object_points_and_no_fix_points_are_tolerated() -> None:
    report = {
        "schema_version": "1.5",
        "metadata": {
            "gps_path": [
                {"lat": 0, "lon": 0, "alt": 0},
                {"lat": 12.9, "lng": 77.5, "altitude": 900},
                {"latitude": 12.91, "longitude": 77.51, "alt_m": 905},
            ]
        },
    }
    track = parse_flightmd_report(report)
    assert track.points == [[77.5, 12.9, 900.0], [77.51, 12.91, 905.0]]
    assert any("no fix" in note for note in track.notes)


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"metadata": {}},
        {"metadata": {"gps_path": "12.9,77.5,900"}},
        {"metadata": {"gps_path": [[12.9, 77.5, 900.0]]}},
        {"metadata": {"gps_path": [[12.9, 77.5, 900.0], [12.9, 77.5]]}},
        {"metadata": {"gps_path": [[12.9, 77.5, 900.0], [12.9, "east", 900.0]]}},
        {"metadata": {"gps_path": [[12.9, 77.5, 900.0], [12.9, 77.5, float("nan")]]}},
        {"metadata": {"gps_path": [[12.9, 77.5, 900.0], [95.0, 77.5, 900.0]]}},
        {"metadata": {"gps_path": [[12.9, 77.5, 900.0], [12.9, 181.0, 900.0]]}},
        {"metadata": {"gps_path": [[12.9, 77.5, 900.0], [True, 77.5, 900.0]]}},
        {"metadata": {"gps_path": [[0.0, 0.0, 0.0], [12.9, 77.5, 900.0]]}},
        {"report": [1, 2, 3]},
    ],
)
def test_invalid_reports_raise_report_invalid(report: dict[str, Any]) -> None:
    with pytest.raises(AnalysisError) as info:
        parse_flightmd_report(report)
    assert info.value.code == "report_invalid"


@pytest.mark.parametrize("value", [None, 42, "not json", b"\xff\xfe"])
def test_non_report_inputs_raise_report_invalid(value: object) -> None:
    with pytest.raises(AnalysisError) as info:
        parse_flightmd_report(value)
    assert info.value.code == "report_invalid"
