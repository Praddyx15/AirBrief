"""Operation evidence brief (airbrief_core.brief, airbrief_core.brief_html) without the API.

Envelopes are written by hand with the fields each section reads, so every B1 addition is checked
in isolation: flight-audit training-area exits, distance from take-off and the "not assessed"
banners; show derived box, audience rule and formula, pads, endurance and origin; the ground-risk
operational volume; environment and line of sight.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from airbrief_core.brief import (
    build_brief,
    canonical_json,
    canonical_sha256,
    fmt_area,
    fmt_duration,
    fmt_length,
    fmt_number,
    fmt_offset,
    fmt_pct,
    reads_synthetic_data,
)
from airbrief_core.brief_html import brief_map_layers, render_brief_html
from airbrief_core.data.datapack import DataPackManifest, LayerEntry
from airbrief_core.models.brief import (
    MAX_BRIEF_ANALYSES,
    SECTION_ORDER,
    SYNTHETIC_WATERMARK,
    UNSIGNED_NOTE,
    Brief,
    BriefDatasetRef,
    BriefSource,
    OperationMeta,
)
from airbrief_core.models.presets import DEFAULT_ZONE_RULES

SQUARE = {"type": "Polygon", "coordinates": [[[77.60, 12.95], [77.61, 12.95], [77.61, 12.96], [77.60, 12.96], [77.60, 12.95]]]}
NNBSP = " "


def _envelope(kind: str, params: dict[str, Any], result: dict[str, Any], *, synthetic: bool = False, pack: str | None = "demo",
              finished: str = "2026-06-21T03:00:00Z") -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "airbrief_version": "0.1.0",
        "kind": kind,
        "params": params,
        "result": result,
        "datapack": pack,
        "datapack_synthetic": synthetic,
        "backend": "python",
        "started_utc": "2026-06-21T02:59:59Z",
        "finished_utc": finished,
        "duration_ms": 1000,
    }


def _source(analysis_id: str, kind: str, params: dict[str, Any], result: dict[str, Any], **kwargs: Any) -> BriefSource:
    bbox = kwargs.pop("bbox", [77.60, 12.95, 77.61, 12.96])
    dataset = kwargs.pop("dataset", None)
    return BriefSource(analysis_id=analysis_id, envelope=_envelope(kind, params, result, **kwargs), dataset=dataset, bbox=bbox)


def _airspace(**kwargs: Any) -> BriefSource:
    result = {
        "verdict": "caution", "summary": "Allows 60 m AGL.", "geometry_type": "Polygon", "altitude_agl_m": 60.0,
        "effective_ceiling_agl_m": 120.0, "permission_required": False, "max_terrain_m": 919.0, "max_altitude_amsl_m": 979.0,
        "nearest_aerodromes": [{"ident": "VOBG", "name": "HAL Airport", "type": "medium_airport", "lon": 77.66, "lat": 12.95,
                                "distance_km": 19.3, "bearing_deg": 75.0}],
        "zones": [], "terrain": {"min_m": 917.0, "max_m": 919.0, "mean_m": 918.0, "relief_m": 2.0, "samples": 100, "source": "GLO-30"},
        "obstacles": [{"id": "o1", "kind": "mast", "name": "Mast", "lon": 77.605, "lat": 12.955, "height_m": 65.0, "distance_m": 0.0,
                       "clearance_m": -5.0, "conflict": True}],
        "assessment_complete": True, "issues": [], "disclaimer": "Indicative only.", "layers": {},
    }
    result.update(kwargs.pop("result", {}))
    return _source(kwargs.pop("analysis_id", "a-airspace"), "airspace", {"geometry": SQUARE, "altitude_agl_m": 60}, result, **kwargs)


def _flight_audit(**result_overrides: Any) -> BriefSource:
    result = {
        "verdict": "fail", "flight": {"points": 301, "source": "track", "track_name": "Training flight", "track_format": "gpx"},
        "altitude_reference": "recorded_agl", "distance_m": 2400.0, "max_agl_m": 85.0, "max_height_above_takeoff_m": 85.0,
        "ceiling_agl_m": 120.0, "profile": [], "exceedances": [], "zones": [], "disclaimer": "Audit evidence.", "issues": [],
        "layers": {}, "assessment_complete": True, "heights_assessed": True, "duration_s": 300.0, "time_above_limit_s": 0.0,
        "max_home_distance_m": 640.0, "training_box_name": "Training box",
        "area_exits": [{"kind": "area_exit", "start_index": 180, "end_index": 240, "start_distance_m": 1500.0, "end_distance_m": 1900.0,
                        "length_m": 400.0, "start_time_s": 180.0, "end_time_s": 240.0, "duration_s": 60.0, "peak_m": 85.0, "limit_m": 0.0}],
        "time_outside_area_s": 60.0, "max_distance_from_home_m": 500.0,
        "distance_exceedances": [{"kind": "distance", "start_index": 198, "end_index": 240, "start_distance_m": 1600.0,
                                  "end_distance_m": 1900.0, "length_m": 300.0, "start_time_s": 197.98, "end_time_s": 240.0,
                                  "duration_s": 42.02, "peak_m": 640.0, "limit_m": 500.0}],
        "time_beyond_distance_s": 42.02,
    }
    result.update(result_overrides)
    return _source("a-audit", "flight_audit", {"max_distance_from_home_m": 500, "track": {"coordinates": [[77.6, 12.95], [77.61, 12.96]]}},
                   result)


def _show() -> BriefSource:
    result = {
        "verdict": "fail", "drone_count": 50, "duration_s": 120.0, "sample_rate_hz": 10.0, "frames_checked": 1200,
        "min_separation_m": 0.8, "min_separation_at_s": 48.0, "min_separation_pair": ["D07", "D32"],
        "conflicts": [{"drone_a": "D07", "drone_b": "D32", "start_s": 47.9, "end_s": 48.1, "t_min_s": 48.0, "min_distance_m": 0.8,
                       "position_enu": [1.0, 2.0, 30.0]}],
        "violations": [{"kind": "endurance", "drone": "D01", "start_s": 0.0, "end_s": 120.0, "peak_value": 610.0, "limit": 600.0}],
        "drones": [{"drone": "D01", "airborne_s": 610.0, "distance_m": 900.0, "max_altitude_m": 50.8, "max_horizontal_speed_ms": 6.0,
                    "max_climb_rate_ms": 2.0, "max_descent_rate_ms": 2.0, "max_acceleration_ms2": 2.0}],
        "site": {"show_box_area_m2": 20000.0, "audience_area_m2": 5000.0, "box_audience_distance_m": 75.0,
                 "box_overlaps_audience_buffer": False, "show_box_source": "derived", "show_box_margin_m": 5.0,
                 "audience_buffer": {"rule": "ballistic", "base_distance_m": 40.0, "breach_margin_pct": 50.0, "distance_m": 60.0,
                                     "height_m": 50.8, "speed_ms": 8.0, "formula": "v·√(2h/g) × 1.5 = 60 m"}},
        "frames": {"origin": [77.592, 13.0005, 900.0], "times": [], "drone_ids": [], "positions": []},
        "backend": "python", "issues": [], "layers": {}, "altitude_reference": "absolute", "origin_source": "inferred",
        "max_altitude_m": 50.8,
        "pads": {"limit_m": 6.0, "takeoff_pads": 50, "landing_pads": 50, "takeoff_min_spacing_m": 2.0, "takeoff_pair": ["D01", "D02"],
                 "landing_min_spacing_m": 2.0, "landing_pair": ["D01", "D02"],
                 "conflicts": [{"phase": "takeoff", "drone_a": "D01", "drone_b": "D02", "distance_m": 2.0, "limit_m": 6.0, "time_s": 0.0}],
                 "conflicts_truncated": False},
    }
    params = {"tracks": [{"id": "D01", "times": [0, 1], "coordinates": [[77.592, 13.0005, 0], [77.592, 13.0006, 5]]}],
              "endurance_s": 600, "min_separation_m": 1.5}
    return _source("a-show", "show", params, result, synthetic=True)


def _ground_risk_volume() -> BriefSource:
    result = {
        "igrc": 5, "in_scope": True, "small_uas_rule": False, "column": "≤ 1 m, ≤ 25 m/s", "density_band": "< 5,000 people/km²",
        "density_statistic": "max", "max_density_per_km2": 4789.0, "p95_density_per_km2": 4500.0, "mean_density_per_km2": 4000.0,
        "population_in_footprint": 8598.0, "footprint_area_km2": 1.76, "buffer_m": 150.0, "table_reference": "SORA 2.5 Table 2",
        "method": "volume", "disclaimer": "Evidence for a SORA-style assessment.", "issues": [], "layers": {},
        "evidence_label": "SORA Step 2 / Step 8 evidence — not a GRC, ARC or SAIL",
        "volume": {"aircraft": "multirotor", "speed_ms": 15.0, "fg_height_m": 120.0, "s_cv_m": 30.5, "h_cv_m": 140.0, "grb_m": 120.0,
                   "grb_rule": "one_to_one", "adjacent_outer_km": 5.0, "adjacent_distance_km": 2.7, "grid_m": 200.0, "grid_source": "auto",
                   "terms": [{"name": "S_CM", "label": "Contingency manoeuvre", "axis": "horizontal", "value_m": 11.47,
                              "formula": "S_CM = v²/(2·g·tan θ)", "source": "SORA 2.5 Annex A"}],
                   "reference": "SORA 2.5"},
        "adjacent_mean_density_per_km2": 3100.0, "assemblies": [],
    }
    return _source("a-ground", "ground_risk", {"geometry": SQUARE, "volume": {}}, result)


def _section(brief: Brief, key: str) -> Any:
    return next(s for s in brief.sections if s.key == key)


def _labels(part: Any) -> set[str]:
    return {f.label for f in part.facts}


def test_canonical_json_sorts_keys_and_drops_whitespace() -> None:
    assert canonical_json({"b": 1, "a": [1.5, "é", None]}) == '{"a":[1.5,"é",null],"b":1}'.encode()
    assert canonical_sha256({"b": 1, "a": 2}) == canonical_sha256({"a": 2, "b": 1})
    assert canonical_sha256({"a": 2}) == hashlib.sha256(b'{"a":2}').hexdigest()
    # A JSON file re-read and re-serialised canonically hashes the same (floats round-trip).
    value = {"h": 120.0, "x": 0.1 + 0.2, "n": [1, 2]}
    assert canonical_sha256(json.loads(json.dumps(value, indent=2))) == canonical_sha256(value)
    with pytest.raises(ValueError):
        canonical_json({"bad": float("nan")})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (fmt_length(45.2), f"45.2{NNBSP}m"),
        (fmt_length(1234.5), f"1.23{NNBSP}km"),
        (fmt_area(12_000), f"1.2{NNBSP}ha"),
        (fmt_area(2_500_000), f"2.5{NNBSP}km²"),
        (fmt_duration(65), f"1{NNBSP}min 05{NNBSP}s"),
        (fmt_duration(3725), f"1{NNBSP}h 02{NNBSP}min"),
        (fmt_offset(330), "UTC+05:30"),
        (fmt_offset(-210), "UTC−03:30"),
        (fmt_number(-0.0001, decimals=1), "0.0"),
        (fmt_number(-12.5), "−12.5"),
        (fmt_number(1234567), "1,234,567"),
        (fmt_pct(12.345), "12.3%"),
        (fmt_length(None), "—"),
    ],
)
def test_metric_formatting_matches_the_web_app(text: str, expected: str) -> None:
    assert text == expected


def test_synthetic_data_counts_only_for_kinds_that_read_the_pack() -> None:
    assert reads_synthetic_data("airspace", True) is True
    assert reads_synthetic_data("airspace", False) is False
    assert reads_synthetic_data("show", True) is False  # a show check reads no pack layer


def test_every_section_is_present_and_missing_ones_are_not_assessed() -> None:
    brief = build_brief([_airspace()])
    assert [s.key for s in brief.sections] == list(SECTION_ORDER)
    status = {s.key: s.status for s in brief.sections}
    assert status["operation"] == "info" and status["airspace"] == "assessed"
    assert status["heights"] == "assessed" and status["obstacles"] == "assessed"
    for key in ("ground_risk", "environment", "line_of_sight", "survey", "flight_audit", "show", "evtol"):
        section = _section(brief, key)
        assert section.status == "not_assessed" and section.reason and not section.parts


def test_brief_needs_one_to_twelve_analyses() -> None:
    with pytest.raises(ValueError):
        build_brief([])
    with pytest.raises(ValueError):
        build_brief([_airspace(analysis_id=f"a{i}") for i in range(MAX_BRIEF_ANALYSES + 1)])


def test_airspace_not_assessed_is_a_partial_section_with_a_banner() -> None:
    gap = {"assessment_complete": False, "terrain": None, "issues": [
        {"code": "not_assessed", "message": "No data pack covers this area.", "severity": "warning"}]}
    brief = build_brief([_airspace(result=gap)])
    airspace = _section(brief, "airspace")
    assert airspace.status == "partial"
    assert airspace.parts[0].banners == ["Airspace not assessed: No data pack covers this area."]
    heights = _section(brief, "heights")
    assert heights.status == "partial" and heights.parts[0].banners[0].startswith("Heights not assessed")


def test_height_reference_fields_from_the_heights_module() -> None:
    result = {
        "height_reference": "takeoff", "min_agl_m": 30.0, "agl_min_m": 30.0, "agl_max_m": 120.0,
        "takeoff": {"lon": 77.6, "lat": 12.95, "elevation_m": 900.0, "source": "first_vertex"},
        "below_min_agl": [{"start_m": 400.0, "end_m": 650.0, "length_m": 250.0, "peak_agl_m": 21.0, "limit_m": 30.0}],
        "above_ceiling": [],
        "dem": {"source": "glo30", "title": "Copernicus GLO-30", "caveat": "A surface model: includes roofs and canopy.", "synthetic": False},
    }
    heights = _section(build_brief([_airspace(result=result)]), "heights").parts[0]
    facts = {f.label: f for f in heights.facts}
    assert facts["Height reference"].text == "Above take-off"
    assert facts["Height above ground"].text == f"30–120{NNBSP}m"
    assert facts["Take-off ground"].value == 900.0
    assert facts["Below the minimum height"].text == "1 stretch" and facts["Below the minimum height"].tone == "warn"
    assert facts["Above the ceiling"].text == "0 stretches"
    assert facts["Terrain model"].note == "A surface model: includes roofs and canopy."
    (table,) = heights.tables
    assert table.rows == [[f"400{NNBSP}m", f"650{NNBSP}m", f"250{NNBSP}m", f"21{NNBSP}m"]]


def test_flight_audit_training_area_distance_and_not_assessed() -> None:
    part = _section(build_brief([_flight_audit()]), "flight_audit").parts[0]
    facts = {f.label: f for f in part.facts}
    assert facts["Outside the training area"].text == f"1{NNBSP}min 00{NNBSP}s"
    assert facts["Outside the training area"].tone_label == "Left it 1 time"
    assert facts["Farthest from take-off"].note == f"Limit 500{NNBSP}m" and facts["Farthest from take-off"].tone == "danger"
    captions = [t.caption for t in part.tables]
    assert "Segments outside Training box" in captions and f"Beyond 500{NNBSP}m from take-off" in captions
    exits = next(t for t in part.tables if t.caption == "Segments outside Training box")
    assert exits.rows[0] == [f"1.5{NNBSP}km", f"1.9{NNBSP}km", f"400{NNBSP}m", f"1{NNBSP}min 00{NNBSP}s", f"85{NNBSP}m"]

    flat = _section(build_brief([_flight_audit(heights_assessed=False, assessment_complete=False)]), "flight_audit")
    assert flat.status == "partial"
    banners = flat.parts[0].banners
    assert banners[0].startswith("Airspace not assessed") and banners[1].startswith("Heights not assessed")
    assert {f.label: f for f in flat.parts[0].facts}["Max height above ground"].text == "Not recorded"
    assert flat.parts[0].summary == "Heights not assessed: the track has no altitudes."


def test_show_box_audience_rule_pads_endurance_and_origin() -> None:
    part = _section(build_brief([_show()]), "show").parts[0]
    facts = {f.label: f for f in part.facts}
    assert facts["Show box"].note == f"Derived from the tracks (+5{NNBSP}m margin)"
    assert facts["Audience distance"].text == f"60{NNBSP}m"
    assert facts["Audience distance"].note == "Ballistic: v·√(2h/g) × 1.5 = 60 m"
    assert facts["Pad spacing"].tone == "danger" and facts["Pad spacing"].tone_label == "1 too close"
    assert facts["Endurance"].text == f"10{NNBSP}min 10{NNBSP}s" and facts["Endurance"].tone_label == "1 drone over"
    assert facts["Origin"].text.endswith("(the mean first position)")
    assert facts["Origin"].note == f"Heights above sea level, measured from 900{NNBSP}m at the origin"
    assert [t.caption for t in part.tables] == ["Separation conflicts", "Dynamic and site violations", f"Pads closer than 6{NNBSP}m"]
    # A show reads no pack layer: synthetic pack or not, no watermark.
    assert build_brief([_show()]).watermark is None


def test_ground_risk_operational_volume() -> None:
    part = _section(build_brief([_ground_risk_volume()]), "ground_risk").parts[0]
    assert part.summary == "SORA Step 2 / Step 8 evidence — not a GRC, ARC or SAIL."
    assert {"Contingency volume", "Ground risk buffer", "Adjacent area density", "Flight geography height"} <= _labels(part)
    terms = next(t for t in part.tables if t.caption == "Operational volume terms (SORA 2.5)")
    assert terms.rows[0][2] == "S_CM = v²/(2·g·tan θ)"


def test_environment_and_line_of_sight_sections() -> None:
    env = _source("a-env", "environment", {"geometry": SQUARE, "date": "2026-06-21"}, {
        "point": [77.6, 12.95], "geometry_type": "Polygon", "date": "2026-06-21", "utc_offset_min": 330, "utc_offset_label": "UTC+05:30",
        "height_agl_m": 120.0,
        "sun": {"sunrise": {"utc": "", "local": "05:55"}, "sunset": {"utc": "", "local": "18:48"},
                "civil_dusk": {"utc": "", "local": "19:12"}, "solar_noon": {"utc": "", "local": "12:21"}, "max_elevation_deg": 79.5,
                "daylight_minutes": 773.0},
        "daylight": {"profile": "faa_107", "profile_label": "FAA Part 107 (US)", "rule": "", "statement": "Window extends 42 min past sunset.",
                     "window": {"start": "", "end": "", "start_local": "17:30", "end_local": "19:30", "start_minute": 1050,
                                "end_minute": 1170, "minutes": 120},
                     "minutes_outside": 18.0, "minutes_lighting": 23.0,
                     "window_segments": [{"kind": "civil_twilight", "status": "lighting", "start_local": "18:48", "end_local": "19:12",
                                          "minutes": 24.0}]},
        "magnetic": {"declination_deg": -1.2, "inclination_deg": 10.9, "model": "WMM2025", "blackout": False, "caution": False,
                     "within_validity": True},
        "wind": None, "emi": [{"id": "p1", "kind": "power_line", "name": "Synthetic 220 kV line", "voltage_kv": 220.0, "distance_m": 0.0,
                               "lon": 77.6, "lat": 12.95, "inside": True, "source": "power"}],
        "emi_assessed": True, "emi_radius_m": 300.0, "sun_window": {}, "issues": [], "method": "NOAA", "disclaimer": "Evidence.",
    })
    los = _source("a-los", "line_of_sight", {"geometry": SQUARE}, {
        "geometry_type": "Polygon", "target_height_agl_m": 120.0, "combined_visible_pct": 87.5, "radio_los_pct": 92.0,
        "observers": [{"name": "Pilot", "role": "pilot", "lon": 77.6, "lat": 12.95, "height_agl_m": 1.7, "k_factor": 1.17,
                       "los_kind": "visual", "visible_pct": 87.5, "assumed": True}],
        "grid": {"grid_m": 30.0, "dem_source": "GLO-30", "dem_resolution_m": 30.0},
        "radio": {"frequency_mhz": 2400.0, "clearance_threshold_pct": 60.0, "station_name": "Pilot", "paths_blocked": 0,
                  "paths_below_threshold": 1, "worst_clearance_pct": 41.0, "max_fspl_db": 98.6,
                  "paths": [{"to_name": "Far corner", "distance_m": 900.0, "worst_clearance_pct": 41.0, "fspl_db": 98.6}]},
        "vlos_guidance": {"guidance_m": 500.0}, "largest_hidden_patch": None, "method": "", "disclaimer": "Geometric evidence.",
        "issues": [],
    })
    brief = build_brief([env, los])
    environment = _section(brief, "environment").parts[0]
    facts = {f.label: f for f in environment.facts}
    assert facts["Sunset"].text == "18:48 UTC+05:30"
    assert facts["Planned window"].tone == "warn" and facts["Planned window"].tone_label == "Outside the profile"
    assert facts["Nearest power line"].text == "Crosses"
    assert facts["Magnetic declination"].note.startswith("West of true north")
    sight = _section(brief, "line_of_sight").parts[0]
    facts = {f.label: f for f in sight.facts}
    assert facts["Area in visual line of sight"].text == "87.5%"
    assert facts["Worst Fresnel clearance"].tone == "warn" and facts["Worst Fresnel clearance"].tone_label == "Below 60%"
    assert facts["Worst Fresnel clearance"].note == f"First Fresnel zone at 2.4{NNBSP}GHz"


def test_operation_aircraft_from_a_preset_with_overrides_and_cross_checks() -> None:
    ground = _ground_risk_volume()
    ground.envelope["params"].update({"max_dimension_m": 0.5, "max_speed_ms": 12.0})
    far = _airspace(analysis_id="a-far", bbox=[78.0, 13.0, 78.01, 13.01],
                    dataset=BriefDatasetRef(id="d2", filename="other.kml", sha256="ab" * 32))
    near = _airspace(dataset=BriefDatasetRef(id="d1", filename="plots.kml", sha256="cd" * 32))
    operation = OperationMeta.model_validate({
        "operator": "  Example  ", "aircraft": {"preset": "mapping_quad", "takeoff_mass_kg": 4.6},
        "planned_start_utc": "2026-06-21T09:00:00+05:30", "planned_end_utc": "2026-06-21T10:00:00+05:30", "utc_offset_min": 330,
    })
    brief = build_brief([near, far, ground], operation)
    assert brief.operation.operator == "Example"
    assert brief.operation.planned_start_utc == "2026-06-21T03:30:00Z"
    aircraft = brief.operation.aircraft
    assert aircraft is not None and aircraft.source == "preset_with_overrides"
    assert aircraft.takeoff_mass_kg == 4.6 and aircraft.max_speed_ms == 18.0
    codes = [w.code for w in brief.warnings]
    assert {"different_datasets", "areas_differ", "several_runs", "aircraft_exceeds_assumption"} <= set(codes)
    assert {d.id for d in brief.provenance.inputs} == {"d1", "d2"}


def test_separate_areas_give_one_grouped_warning_and_duplicates_are_said_once() -> None:
    near_a = _airspace(analysis_id="a1")
    near_b = _airspace(analysis_id="a2", bbox=[77.605, 12.955, 77.62, 12.97])  # overlaps a1
    far = _airspace(analysis_id="a3", bbox=[78.0, 13.0, 78.01, 13.01])
    ground = [_ground_risk_volume(), _ground_risk_volume().model_copy(update={"analysis_id": "a-ground-2"})]
    brief = build_brief([near_a, near_b, far, *ground], OperationMeta.model_validate({"aircraft": {"preset": "mapping_quad"}}))
    areas = [w for w in brief.warnings if w.code == "areas_differ"]
    assert len(areas) == 1 and "2 separate areas" in areas[0].message
    aircraft = [w for w in brief.warnings if w.code == "aircraft_exceeds_assumption"]
    assert len(aircraft) == 1  # two ground-risk runs with the same assumptions: one warning


def test_brief_hash_ignores_order_and_generation_time_but_not_content() -> None:
    sources = [_airspace(), _ground_risk_volume(), _flight_audit()]
    a = build_brief(sources, generated_utc=datetime(2026, 1, 1, tzinfo=UTC))
    b = build_brief(list(reversed(sources)), generated_utc=datetime(2027, 1, 1, tzinfo=UTC))
    assert a.provenance.generated_utc != b.provenance.generated_utc
    assert a.provenance.brief_sha256 == b.provenance.brief_sha256
    assert re.fullmatch(r"[0-9a-f]{64}", a.provenance.brief_sha256)
    assert [x.kind for x in a.provenance.analyses] == ["airspace", "ground_risk", "flight_audit"]
    assert a.provenance.analyses[0].params_sha256 == canonical_sha256({"geometry": SQUARE, "altitude_agl_m": 60})
    c = build_brief(sources, OperationMeta(notes="Changed."))
    assert c.provenance.brief_sha256 != a.provenance.brief_sha256


def test_synthetic_packs_watermark_and_mixed_briefs_mark_each_part() -> None:
    synthetic = _airspace(synthetic=True)
    assert build_brief([synthetic]).watermark == SYNTHETIC_WATERMARK
    assert build_brief([synthetic]).sections[1].parts[0].banners == []  # said once, by the watermark
    real = _ground_risk_volume()
    mixed = build_brief([synthetic, real])
    assert mixed.synthetic is True
    assert any("synthetic demo data pack" in b for b in _section(mixed, "airspace").parts[0].banners)
    assert not _section(mixed, "ground_risk").parts[0].banners


def test_html_is_self_contained_escaped_and_marked_as_evidence() -> None:
    source = _airspace(synthetic=True, result={"layers": {
        "input": {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": SQUARE, "properties": {}}]},
        "grid": {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": SQUARE, "properties": {}}]},
    }})
    brief = build_brief([source], OperationMeta(operator="<script>alert(1)</script>", pilot_position=(77.605, 12.955)))
    layers = brief_map_layers([source], brief.operation.pilot_position)
    assert [layer.key for layer in layers] == ["airspace:input", "operation:pilot"]  # grids stay out of the map
    html = render_brief_html(brief, layers)
    assert html.startswith("<!doctype html>")
    assert "default-src 'none'" in html and "<script>" not in html and "&lt;script&gt;" in html
    assert not re.search(r"""(?:src|href|url)\s*[=(]\s*["']?https?:""", html)
    assert "Evidence, not authorisation" in html and 'class="watermark"' in html and "@page" in html
    # Every section is on the page: assessed ones as cards, the rest in the "Not assessed" list.
    missing = [s for s in brief.sections if s.status == "not_assessed"]
    for section in brief.sections:
        card, listed = f"<h2>{section.title} <span class=\"badge", f"<b>{section.title}</b> <span class=\"badge muted\">"
        assert (listed if section in missing else card) in html, section.key
    assert html.count('<span class="badge muted">Not assessed</span>') == len(missing) == 7
    assert '<svg viewBox="0 0 ' in html and "role=\"img\"" in html
    # The synthetic cross-check is said by the banner and watermark, not repeated as a card.
    assert 'aria-label="Cross-checks"' not in html and html.count("SYNTHETIC DEMO DATA") == 2


def test_html_escapes_every_user_text() -> None:
    """Operator, reference (in the title), notes and feature names from the data all reach the
    page as text: no element, attribute or entity of theirs survives."""
    payload = '<img src=x onerror="alert(1)">&amp;"\'</title><script>x</script>'
    source = _airspace(result={
        "obstacles": [{"id": "o1", "kind": "mast", "name": payload, "lon": 77.605, "lat": 12.955, "height_m": 65.0,
                       "distance_m": 0.0, "clearance_m": -5.0, "conflict": True}],
        "zones": [{"name": payload, "zone": "red", "ident": "Z<1>", "ceiling_agl_m": 0.0, "permission_required": True,
                   "distance_km": 0.0}],
    })
    operation = OperationMeta(operator=payload, reference="R</title><script>", notes=payload)
    brief = build_brief([source], operation)
    html = render_brief_html(brief, brief_map_layers([source], None))
    assert "<script" not in html.lower() and "<img" not in html.lower() and "onerror=\"" not in html
    assert "</title><script>" not in html and "</title>&lt;script" not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;amp;&quot;&#x27;" in html
    assert "<title>Operation evidence brief — R&lt;/title&gt;&lt;script&gt;</title>" in html


# WEAK-09 (non-default safety parameters), MISS-01 (signing), MISS-02 (pack content hash)


def _airspace_with(**params: Any) -> BriefSource:
    source = _airspace()
    source.envelope["params"].update(params)
    return source


def test_non_default_airspace_parameters_are_named_in_a_banner_and_a_cross_check() -> None:
    no_rings = DEFAULT_ZONE_RULES.model_copy(update={"rules": []}).model_dump(mode="json")
    stadium = {"name": "Stadium", "zone": "green", "geometry": SQUARE}
    source = _airspace_with(include_datapack_zones=False, zone_rules=no_rings, user_zones=[stadium], obstacle_search_m=50,
                            vertical_clearance_m=5, profile_spacing_m=200, takeoff_elevation_m=900)
    brief = build_brief([source])
    (warning,) = [w for w in brief.warnings if w.code == "non_default_parameters"]
    assert warning.severity.value == "warning" and warning.message.startswith("Airspace")
    for named in (
        "data-pack zones left out (include_datapack_zones: false)",
        "custom aerodrome zone rules instead of the defaults (zone_rules)",
        "1 zone drawn by the user (user_zones)",
        f"obstacle search radius 50{NNBSP}m instead of 300{NNBSP}m (obstacle_search_m)",
        f"obstacle clearance 5{NNBSP}m instead of 30{NNBSP}m (vertical_clearance_m)",
        f"terrain profile spacing 200{NNBSP}m instead of 30{NNBSP}m (profile_spacing_m)",
        f"take-off ground elevation entered by hand as 900{NNBSP}m instead of read from the DEM (takeoff_elevation_m)",
    ):
        assert named in warning.message, named
    part = _section(brief, "airspace").parts[0]
    (banner,) = [b for b in part.banners if b.startswith("Run with non-default safety parameters")]
    assert "include_datapack_zones: false" in banner and "(obstacle_search_m)" in banner
    html = render_brief_html(brief, [])
    assert 'aria-label="Cross-checks"' in html and html.count("data-pack zones left out (include_datapack_zones: false)") == 2


def test_default_or_stricter_parameters_are_not_flagged() -> None:
    stricter = _airspace_with(zone_rules=DEFAULT_ZONE_RULES.model_dump(mode="json"), include_datapack_zones=True,
                              obstacle_search_m=1000, vertical_clearance_m=60, min_agl_m=50, profile_spacing_m=10)
    for brief in (build_brief([_airspace()]), build_brief([stricter])):
        assert "non_default_parameters" not in {w.code for w in brief.warnings}
        assert _section(brief, "airspace").parts[0].banners == []
    looser = build_brief([_airspace_with(min_agl_m=10)])
    (warning,) = [w for w in looser.warnings if w.code == "non_default_parameters"]
    assert f"minimum height above ground 10{NNBSP}m instead of 30{NNBSP}m (min_agl_m)" in warning.message


def test_flight_audit_without_data_pack_zones_is_flagged_too() -> None:
    audit = _flight_audit()
    audit.envelope["params"]["include_datapack_zones"] = False
    brief = build_brief([audit])
    (warning,) = [w for w in brief.warnings if w.code == "non_default_parameters"]
    assert warning.message.startswith("Flight audit") and "include_datapack_zones: false" in warning.message
    assert any("include_datapack_zones: false" in b for b in _section(brief, "flight_audit").parts[0].banners)


def test_provenance_says_whether_the_bundle_is_signed() -> None:
    unsigned = build_brief([_airspace()])
    assert unsigned.provenance.signing_key_id is None and unsigned.provenance.signature_note == UNSIGNED_NOTE
    assert "detect accidental corruption, not deliberate edits" in render_brief_html(unsigned, [])
    signed = build_brief([_airspace()], signing_key_id="ab12cd34ef567890")
    assert signed.provenance.signing_key_id == "ab12cd34ef567890"
    assert signed.provenance.signature_note.startswith("Bundles from this instance are signed (key ab12cd34ef567890)")
    assert "carries no signature" in signed.provenance.signature_note
    assert signed.provenance.brief_sha256 != unsigned.provenance.brief_sha256
    assert "Bundles from this instance are signed (key ab12cd34ef567890)" in render_brief_html(signed, [])


def test_pack_content_hash_is_in_the_provenance_and_a_changed_pack_is_flagged() -> None:
    mirror = "https://overpass.private.coffee/api/interpreter"
    manifest = DataPackManifest(
        name="demo", bbox=[77.0, 12.0, 78.0, 13.0], content_sha256="b" * 64,
        layers={"obstacles": LayerEntry(file="obstacles.geojson", source="osm_overpass", sha256="c" * 64, source_url=mirror)},
    )
    same = _airspace()
    same.envelope["datapack_sha256"] = "b" * 64
    brief = build_brief([same], packs={"demo": manifest})
    (pack,) = brief.provenance.datapacks
    assert pack.content_sha256 == "b" * 64
    assert pack.layers[0].sha256 == "c" * 64 and pack.layers[0].source_url == mirror
    assert brief.provenance.analyses[0].datapack_sha256 == "b" * 64
    assert "datapack_changed" not in {w.code for w in brief.warnings}
    html = render_brief_html(brief, [])
    assert "b" * 64 in html and "osm_overpass via overpass.private.coffee" in html

    older = _airspace()
    older.envelope["datapack_sha256"] = "a" * 64
    (warning,) = [w for w in build_brief([older], packs={"demo": manifest}).warnings if w.code == "datapack_changed"]
    assert "'demo'" in warning.message and "Run the analysis again" in warning.message
