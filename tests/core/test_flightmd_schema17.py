"""FlightMD schema 1.6 / 1.7 in the report adapter and the flight audit: armed windows,
log-relative finding times, parse warnings, ``Finding.params``, per-point GPS times and the
planning brief hash."""

from __future__ import annotations

import json
from typing import Any

import pytest

from airbrief_core.analyses import flight_audit
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.brief import build_brief
from airbrief_core.integrations import parse_flightmd_report
from airbrief_core.integrations.flightmd import detect_schema_version
from airbrief_core.models.analyses import FlightAuditParams
from airbrief_core.models.brief import BriefSource
from airbrief_core.samples import build_sample

BRIEF_SHA = "3f" * 32
LOG_SHA = "a1" * 32
T0 = 20.0  # log-relative time of the first GPS point; one point per second after it


def _v15() -> dict[str, Any]:
    return json.loads(build_sample("flightmd_report_v15").decode("utf-8"))


def _v17() -> dict[str, Any]:
    report = _v15()
    n = len(report["metadata"]["gps_path"])
    report["schema_version"] = "1.7"
    report["metadata"].update(
        {
            "armed_windows": [[15.0, 330.0]],
            "parse_warnings": ["3 GPS samples without a fix were skipped", "  "],
            "gps_path_t_s": [T0 + k for k in range(n)],
            "planning_brief_sha256": BRIEF_SHA.upper(),
            "log_sha256": LOG_SHA,
            "source_format": "ardupilot_bin",
            "airframe_name": "Generic Quad",
        }
    )
    report["findings"] = [
        {"id": "osc", "severity": "critical", "category": "oscillation", "title": "Roll oscillation",
         "t_start_s": T0 + 116.0, "t_end_s": T0 + 130.0, "params": ["ATC_RAT_RLL_P"], "kind": "oscillation.roll",
         "recommendation": "Lower ATC_RAT_RLL_P."},
        {"id": "pre", "severity": "warning", "category": "ekf", "title": "EKF variance before GPS lock",
         "t_start_s": 5.0, "t_end_s": 8.0, "params": []},
        {"id": "none", "severity": "info", "category": "system", "title": "Logging rate", "params": []},
        {"id": "sag", "severity": "warning", "category": "battery", "title": "Voltage sag",
         "timestamp_start_ms": 200500, "timestamp_end_ms": 210000,
         "param_changes": [{"param_name": "BATT_LOW_VOLT", "current_value": 14.0, "suggested_value": 14.4}]},
    ]
    return report


# --- the adapter --------------------------------------------------------------------------------


def test_reads_the_17_fields() -> None:
    track = parse_flightmd_report(_v17())
    info = track.info

    assert track.schema_version == "1.7"
    assert not any("schema" in n for n in track.notes)
    assert info.armed_windows == [[15.0, 330.0]]
    assert info.parse_warnings == ["3 GPS samples without a fix were skipped"]
    assert info.planning_brief_sha256 == BRIEF_SHA
    assert info.log_sha256 == LOG_SHA
    assert info.source_format == "ardupilot_bin"
    assert info.airframe_name == "Generic Quad"
    assert info.gps_times is True
    assert track.times is not None and len(track.times) == len(track.points) == 301
    assert track.times[0] == T0
    assert info.duration_s == 300.0

    by_id = {f.id: f for f in track.findings}
    assert by_id["osc"].t_start_s == T0 + 116.0
    assert by_id["osc"].params == ["ATC_RAT_RLL_P"]
    assert by_id["osc"].severity == "critical"
    assert by_id["none"].t_start_s is None
    # 1.6-style finding: ms times, names from param_changes
    assert by_id["sag"].t_start_s == 200.5
    assert by_id["sag"].t_end_s == 210.0
    assert by_id["sag"].params == ["BATT_LOW_VOLT"]


@pytest.mark.parametrize(
    ("declared", "metadata", "finding", "effective"),
    [
        ("1.2", {"armed_windows": None}, {}, "1.6"),
        (None, {"parse_warnings": []}, {}, "1.6"),
        ("1.5", {}, {"t_start_s": 1.0}, "1.6"),
        ("1.6", {"planning_brief_sha256": None}, {}, "1.7"),
        ("1.5", {}, {"params": []}, "1.7"),
    ],
)
def test_later_fields_win_over_an_older_declared_version(
    declared: str | None, metadata: dict[str, Any], finding: dict[str, Any], effective: str
) -> None:
    report = {"schema_version": declared, "metadata": {"gps_path": [], **metadata}, "findings": [{"id": "x", **finding}]}
    version, note = detect_schema_version(report)
    assert version == effective
    assert note is not None and f"treated as schema {effective}" in note


def test_declared_17_is_trusted() -> None:
    assert detect_schema_version({"schema_version": "1.7", "metadata": {}}) == ("1.7", None)


def test_unknown_major_version_says_so() -> None:
    version, note = detect_schema_version({"schema_version": "2.0", "metadata": {}})
    assert version == "2.0"
    assert note is not None and "newer than this reader" in note


def test_gps_times_follow_the_dropped_no_fix_points() -> None:
    report = _v17()
    report["metadata"]["gps_path"][5] = [0, 0, 0]
    track = parse_flightmd_report(report)
    assert track.times is not None and len(track.times) == len(track.points) == 300
    assert track.times[4] == T0 + 4 and track.times[5] == T0 + 6


@pytest.mark.parametrize(
    "times",
    [
        [1.0, 2.0],  # wrong length
        "1,2,3",
        "decreasing",
        "nan",
    ],
)
def test_unusable_gps_times_are_dropped_with_a_note(times: Any) -> None:
    report = _v17()
    n = len(report["metadata"]["gps_path"])
    if times == "decreasing":
        times = [T0 + k for k in range(n)]
        times[100] = 0.0
    elif times == "nan":
        times = [T0 + k for k in range(n)]
        times[7] = float("nan")
    report["metadata"]["gps_path_t_s"] = times
    track = parse_flightmd_report(report)
    assert track.times is None
    assert track.info.gps_times is False
    assert any("gps_path_t_s" in n for n in track.notes)


def test_malformed_optional_fields_never_fail_the_report() -> None:
    report = _v17()
    report["metadata"]["planning_brief_sha256"] = "not-a-hash"
    report["metadata"]["armed_windows"] = [[10.0, 20.0], [30.0, 25.0], ["a", 1], [40.0]]
    report["metadata"]["parse_warnings"] = "one string, not a list"
    report["findings"].append("not an object")
    report["findings"].append({"id": "odd", "severity": "catastrophic", "t_start_s": 50.0, "t_end_s": 40.0})

    track = parse_flightmd_report(report)
    assert track.info.planning_brief_sha256 is None
    assert any("planning_brief_sha256" in n for n in track.notes)
    assert track.info.armed_windows == [[10.0, 20.0]]
    assert any("3 armed window(s)" in n for n in track.notes)
    assert track.info.parse_warnings == []
    odd = next(f for f in track.findings if f.id == "odd")
    assert odd.severity == "info"
    assert odd.t_end_s is None
    assert len(track.findings) == 5


def test_15_report_is_unchanged() -> None:
    track = parse_flightmd_report(_v15())
    assert track.times is None
    assert track.info.armed_windows is None
    assert track.info.parse_warnings == []
    assert track.info.planning_brief_sha256 is None
    # old findings have no times
    assert [f.placement for f in track.findings] == ["no_time", "no_time"]


# --- the audit ----------------------------------------------------------------------------------


def test_audit_places_findings_on_the_track() -> None:
    result = flight_audit.run(FlightAuditParams(report=_v17()), AnalysisContext())
    by_id = {f.id: f for f in result.findings}

    osc = by_id["osc"]
    assert osc.placement == "placed"
    assert osc.armed is True
    point = result.profile[116]  # one profile point per fix for this 301-point flight
    assert osc.lon == pytest.approx(point.lon, abs=1e-7)
    assert osc.lat == pytest.approx(point.lat, abs=1e-7)
    assert osc.agl_m == pytest.approx(point.agl_m, abs=0.01)
    assert osc.distance_m == pytest.approx(point.distance_m, abs=0.01)
    assert osc.limit_agl_m == 120.0
    assert osc.zone is not None

    assert by_id["pre"].placement == "outside_track"
    assert by_id["pre"].armed is False
    assert by_id["none"].placement == "no_time"
    assert by_id["none"].armed is None
    assert by_id["sag"].placement == "placed"

    layer = result.layers["findings"]["features"]
    assert [f["properties"]["id"] for f in layer] == ["osc", "sag"]
    assert layer[0]["geometry"]["coordinates"] == [osc.lon, osc.lat]

    assert result.duration_s == 300.0
    # exceedance times are log-relative too, in step with the findings
    assert result.exceedances[0].start_time_s == pytest.approx(T0 + 116, abs=1.0)
    codes = [i.code for i in result.issues]
    assert "flightmd_parse_warning" in codes
    assert "findings_not_placed" not in codes
    assert result.flight.planning_brief_sha256 == BRIEF_SHA


def test_finding_between_two_fixes_is_interpolated() -> None:
    report = _v17()
    report["findings"] = [{"id": "mid", "severity": "info", "title": "Mid", "t_start_s": T0 + 10.5}]
    result = flight_audit.run(FlightAuditParams(report=report), AnalysisContext())
    mid = result.findings[0]
    a, b = result.profile[10], result.profile[11]
    assert mid.distance_m == pytest.approx((a.distance_m + b.distance_m) / 2, abs=0.01)
    assert mid.lon == pytest.approx((a.lon + b.lon) / 2, abs=1e-7)


def test_without_gps_times_findings_are_listed_not_placed() -> None:
    report = _v17()
    del report["metadata"]["gps_path_t_s"]
    result = flight_audit.run(FlightAuditParams(report=report), AnalysisContext())
    assert {f.placement for f in result.findings} == {"no_track_times", "no_time"}
    assert "findings" not in result.layers
    issue = next(i for i in result.issues if i.code == "findings_not_placed")
    assert issue.message.startswith("3 FlightMD findings have a time")
    assert result.duration_s is None


def test_15_audit_has_no_findings_layer() -> None:
    result = flight_audit.run(FlightAuditParams(report=_v15()), AnalysisContext())
    assert set(result.layers) == {"path", "exceedances", "home"}
    assert all(f.placement == "no_time" for f in result.findings)


def test_brief_shows_the_planning_brief_and_findings() -> None:
    result = flight_audit.run(FlightAuditParams(report=_v17()), AnalysisContext()).model_dump(mode="json")
    envelope: dict[str, Any] = {
        "schema_version": "1.0", "airbrief_version": "0.1.0", "kind": "flight_audit", "params": {"report": {}},
        "result": result, "datapack": None, "datapack_synthetic": False, "backend": "python",
        "started_utc": "2026-06-21T02:59:59Z", "finished_utc": "2026-06-21T03:00:00Z", "duration_ms": 1000,
    }
    brief = build_brief([BriefSource(analysis_id="a-audit", envelope=envelope, dataset=None, bbox=None)])
    part = next(s for s in brief.sections if s.key == "flight_audit").parts[0]
    facts = {f.label: f for f in part.facts}
    assert facts["Planning brief"].text == f"SHA-256 {BRIEF_SHA[:16]}…"
    assert facts["Planning brief"].note == BRIEF_SHA
    assert facts["FlightMD findings"].value == 4
    assert facts["FlightMD findings"].note == "2 while armed, 2 placed on the track"
