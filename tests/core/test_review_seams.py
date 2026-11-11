"""Regression tests from the final review of the upgrade, where modules meet (core).

* The brief reads each module's height reference: an AMSL airspace plan, an AMSL eVTOL cruise,
  an area plan below the minimum height above ground.
* "Not assessed" rules: the brief's banners, environment interference without a data pack, and
  the airspace check of a geometry only partly inside the data pack (as the flight audit).
* Data-pack folders left by the safe swap of ``seed.datapack`` never shadow the real pack.
* Performance caps: a corridor survey that would need millions of line pieces is refused.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from shapely.geometry import box, mapping, shape

from airbrief_core.analyses import environment, heights, line_of_sight, survey
from airbrief_core.analyses.airspace import run as airspace_run
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.brief import build_brief, fmt_height
from airbrief_core.data import DataPack, DataPackRegistry
from airbrief_core.errors import AnalysisCancelled, AnalysisError
from airbrief_core.models.analyses import AirspaceParams, SurveyParams
from airbrief_core.models.brief import Brief, BriefSource
from airbrief_core.models.environment import EnvironmentParams
from airbrief_core.models.common import Verdict

SQUARE = {"type": "Polygon", "coordinates": [[[77.60, 12.95], [77.61, 12.95], [77.61, 12.96], [77.60, 12.96], [77.60, 12.95]]]}
NNBSP = " "


def _source(kind: str, params: dict[str, Any], result: dict[str, Any], analysis_id: str = "a-1") -> BriefSource:
    envelope = {
        "schema_version": "1.0", "airbrief_version": "0.1.0", "kind": kind, "params": params, "result": result,
        "datapack": "demo", "datapack_synthetic": False, "backend": "python", "started_utc": "2026-06-21T02:59:59Z",
        "finished_utc": "2026-06-21T03:00:00Z", "duration_ms": 1000,
    }
    return BriefSource(analysis_id=analysis_id, envelope=envelope, bbox=[77.60, 12.95, 77.61, 12.96])


def _facts(brief: Brief, section: str) -> dict[str, Any]:
    part = next(s for s in brief.sections if s.key == section).parts[0]
    return {f.label: f for f in part.facts}


def _airspace_result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "verdict": "pass", "summary": "Checked.", "geometry_type": "Polygon", "altitude_agl_m": 120.0,
        "effective_ceiling_agl_m": 120.0, "permission_required": False, "max_terrain_m": 918.0, "max_altitude_amsl_m": 1038.0,
        "nearest_aerodromes": [], "zones": [], "terrain": {"min_m": 914.0, "max_m": 918.0, "mean_m": 916.0, "relief_m": 4.0,
                                                          "samples": 100, "source": "GLO-30"},
        "obstacles": [], "assessment_complete": True, "issues": [], "disclaimer": "Indicative only.", "layers": {},
    }
    result.update(overrides)
    return result


def test_brief_compares_the_ceiling_with_the_height_above_ground_of_an_amsl_plan() -> None:
    # 1,050 m AMSL over ground at 914–918 m flies 132–136 m above ground: above a 120 m ceiling.
    high = _airspace_result(altitude_agl_m=1050.0, height_reference="amsl", agl_min_m=132.0, agl_max_m=136.0,
                            flight_level_amsl_m=1050.0, verdict="caution")
    facts = _facts(build_brief([_source("airspace", {"geometry": SQUARE, "altitude_agl_m": 1050, "height_reference": "amsl"}, high)]),
                   "airspace")
    assert facts["Free-flight ceiling"].tone == "warn"
    assert facts["Free-flight ceiling"].tone_label == "Below the planned height"
    # 1,000 m AMSL flies 82–86 m above ground: within the 120 m ceiling, although 1,000 > 120.
    low = _airspace_result(altitude_agl_m=1000.0, height_reference="amsl", agl_min_m=82.0, agl_max_m=86.0,
                           flight_level_amsl_m=1000.0)
    facts = _facts(build_brief([_source("airspace", {"geometry": SQUARE, "altitude_agl_m": 1000, "height_reference": "amsl"}, low)]),
                   "airspace")
    assert facts["Free-flight ceiling"].tone == "neutral" and facts["Free-flight ceiling"].tone_label is None


def test_brief_heights_flags_an_area_plan_below_the_minimum_without_route_stretches() -> None:
    # A polygon has no route, so no stretches: the AGL range still shows it is below the minimum.
    result = _airspace_result(altitude_agl_m=500.0, height_reference="amsl", agl_min_m=-418.0, agl_max_m=-414.0,
                              min_agl_m=30.0, below_min_agl=[], above_ceiling=[], flight_level_amsl_m=500.0, verdict="fail")
    brief = build_brief([_source("airspace", {"geometry": SQUARE, "altitude_agl_m": 500, "height_reference": "amsl"}, result)])
    below = _facts(brief, "heights")["Below the minimum height"]
    assert below.tone == "warn" and below.tone_label == "Found"
    assert below.text == f"Lowest {fmt_height(-418.0)}" and below.note == f"Minimum 30{NNBSP}m"
    # A plan above the minimum everywhere keeps "0 stretches" / ok.
    fine = _airspace_result(altitude_agl_m=1050.0, height_reference="amsl", agl_min_m=132.0, agl_max_m=136.0, min_agl_m=30.0,
                            below_min_agl=[], above_ceiling=[], flight_level_amsl_m=1050.0)
    brief = build_brief([_source("airspace", {"geometry": SQUARE, "altitude_agl_m": 1050, "height_reference": "amsl"}, fine)])
    below = _facts(brief, "heights")["Below the minimum height"]
    assert below.tone == "ok" and below.text == "0 stretches"


def test_brief_survey_height_of_an_amsl_plan_is_above_the_takeoff_ground() -> None:
    # SurveyResult.altitude_agl_m is the height above the take-off ground the layout uses: for a
    # 1,000 m AMSL plan over a 916 m take-off it is 84 m, never "84 m above mean sea level".
    result = {
        "camera": {"name": "DJI Phantom 4 RTK"}, "gsd_cm": 2.29, "gsd_along_cm": 2.29, "altitude_agl_m": 84.0, "max_altitude_agl_m": 120.0,
        "footprint_across_m": 125.0, "footprint_along_m": 83.6, "line_spacing_m": 37.6, "trigger_distance_m": 16.7, "trigger_interval_s": 1.7,
        "direction_deg": 0.0, "line_count": 29, "segment_count": 29, "line_km": 26.6, "photos": 1943, "flight_minutes": 59.0, "batteries": 3,
        "total_minutes": 68.0, "area_ha": 120.0, "ha_per_hour": 104.0, "blocks": [], "issues": [], "layers": {},
        "height_reference": "amsl", "flight_level_amsl_m": 1000.0,
        "takeoff": {"lon": 77.605, "lat": 12.955, "elevation_m": 916.0, "source": "centroid", "elevation_source": "dem"},
    }
    params = {"geometry": SQUARE, "height_reference": "amsl", "altitude_agl_m": 1000}
    height = _facts(build_brief([_source("survey", params, result)]), "survey")["Height"]
    assert height.text == f"84{NNBSP}m"
    assert height.note == f"Above the take-off ground; flight level 1,000{NNBSP}m AMSL"
    takeoff = dict(result, height_reference="takeoff", altitude_agl_m=100.0, flight_level_amsl_m=1016.0)
    height = _facts(build_brief([_source("survey", {**params, "height_reference": "takeoff"}, takeoff)]), "survey")["Height"]
    assert height.note == "Above take-off"


def test_brief_shows_the_amsl_cruise_level_of_an_evtol_corridor() -> None:
    result = {
        "route": {"type": "LineString", "coordinates": [[77.6, 12.95], [77.7, 12.9]]}, "length_km": 16.9, "straight_km": 16.3,
        "detour_pct": 3.5, "flight_time_min": 6.8, "energy_kwh": 16.9, "cruise_altitude_agl_m": 300.0,
        "required_cruise_amsl_m": 1209.0, "max_terrain_m": 909.0, "profile": [], "obstacles": [], "zones": [],
        "noise": {"threshold_db": 55.0, "exposed_population": 1000.0, "footprint_half_width_m": 900.0, "cells": []},
        "disclaimer": "Planning evidence.", "issues": [], "layers": {}, "cruise_reference": "amsl", "cruise_altitude_amsl_m": 1200.0,
    }
    params = {"origin": [77.6, 12.95], "destination": [77.7, 12.9], "cruise_reference": "amsl", "cruise_altitude_amsl_m": 1200}
    facts = _facts(build_brief([_source("evtol_corridor", params, result)]), "evtol")
    assert facts["Cruise height"].text == f"1,200{NNBSP}m"
    assert facts["Cruise height"].note == "Above mean sea level"
    agl = dict(result, cruise_reference="agl", cruise_altitude_amsl_m=None)
    facts = _facts(build_brief([_source("evtol_corridor", {**params, "cruise_reference": "agl"}, agl)]), "evtol")
    assert facts["Cruise height"].text == f"300{NNBSP}m" and facts["Cruise height"].note == "Above the highest ground"


def test_not_assessed_banners_do_not_repeat_the_issue_heading() -> None:
    gap = "Airspace not assessed: no data pack covers this area — aerodrome zones were not checked. Check Digital Sky."
    result = _airspace_result(assessment_complete=False, verdict="caution",
                              issues=[{"code": "not_assessed", "message": gap, "severity": "warning"}])
    part = next(s for s in build_brief([_source("airspace", {"geometry": SQUARE}, result)]).sections if s.key == "airspace").parts[0]
    assert part.banners == [gap]
    partly = "Airspace only partly assessed: 1 of 3 track points lie outside data pack 'demo' — aerodrome zones were not checked there."
    audit = {
        "verdict": "caution", "flight": {"points": 3, "source": "track"}, "altitude_reference": "terrain_calibrated", "distance_m": 300.0,
        "max_agl_m": 50.0, "max_height_above_takeoff_m": 50.0, "ceiling_agl_m": 120.0, "profile": [], "exceedances": [], "zones": [],
        "disclaimer": "Audit evidence.", "issues": [{"code": "not_assessed", "message": partly, "severity": "warning"}], "layers": {},
        "assessment_complete": False,
    }
    params = {"track": {"coordinates": [[77.6, 12.95, 900], [77.61, 12.96, 950]]}}
    part = next(s for s in build_brief([_source("flight_audit", params, audit)]).sections if s.key == "flight_audit").parts[0]
    assert part.banners == [partly]
    # An issue without the heading still gets it.
    plain = _airspace_result(assessment_complete=False, issues=[{"code": "not_assessed", "message": "No pack here.", "severity": "warning"}])
    part = next(s for s in build_brief([_source("airspace", {"geometry": SQUARE}, plain)]).sections if s.key == "airspace").parts[0]
    assert part.banners == ["Airspace not assessed: No pack here."]


def _environment_result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "point": [77.6, 12.95], "geometry_type": "Polygon", "date": "2026-06-21", "utc_offset_min": 330, "utc_offset_label": "UTC+05:30",
        "height_agl_m": 120.0,
        "sun": {"sunrise": {"utc": "", "local": "05:55"}, "sunset": {"utc": "", "local": "18:48"},
                "solar_noon": {"utc": "", "local": "12:21"}, "max_elevation_deg": 79.5, "daylight_minutes": 773.0},
        "daylight": {"profile": "india_daylight", "profile_label": "Daylight only", "rule": "", "statement": "No planned window."},
        "magnetic": {"declination_deg": -1.2, "inclination_deg": 10.9, "model": "WMM2025", "blackout": False, "caution": False,
                     "within_validity": True},
        "wind": None, "emi": [], "emi_assessed": True, "emi_radius_m": 300.0, "sun_window": {}, "issues": [], "method": "NOAA",
        "disclaimer": "Evidence.",
    }
    result.update(overrides)
    return result


def test_environment_without_interference_data_is_a_partial_section_with_a_banner() -> None:
    message = "No data pack covers this area: power lines, substations and masts were not checked."
    result = _environment_result(emi_assessed=False, issues=[{"code": "emi_not_assessed", "message": message, "severity": "warning"}])
    brief = build_brief([_source("environment", {"geometry": SQUARE, "date": "2026-06-21"}, result)])
    section = next(s for s in brief.sections if s.key == "environment")
    assert section.status == "partial"
    assert section.parts[0].banners == [f"Interference not assessed: {message}"]
    # A pack without a power layer: only masts were checked.
    no_power = "Data pack 'demo' has no power layer: power lines and substations were not checked."
    result = _environment_result(issues=[{"code": "power_not_assessed", "message": no_power, "severity": "warning"}])
    section = next(s for s in build_brief([_source("environment", {"geometry": SQUARE, "date": "2026-06-21"}, result)]).sections
                   if s.key == "environment")
    assert section.status == "partial" and section.parts[0].banners == [f"Interference only partly assessed: {no_power}"]
    # EMI switched off on purpose is not a data gap; a checked area is assessed.
    for checked in (_environment_result(emi_assessed=False, emi_radius_m=None), _environment_result()):
        section = next(s for s in build_brief([_source("environment", {"geometry": SQUARE, "date": "2026-06-21"}, checked)]).sections
                       if s.key == "environment")
        assert section.status == "assessed" and section.parts[0].banners == []


def test_environment_wind_note_names_a_gust_above_the_rated_limit() -> None:
    wind = {"surface_wind_ms": 5.0, "gust_ms": 9.0, "reference_height_m": 10.0, "height_agl_m": 120.0, "shear_exponent": 0.142857,
            "factor": 1.42, "wind_at_height_ms": 7.1, "gust_at_height_ms": 12.8, "wind_limit_ms": 10.0, "margin_ms": 2.9,
            "gust_margin_ms": -2.8, "statement": ""}
    brief = build_brief([_source("environment", {"geometry": SQUARE, "date": "2026-06-21"}, _environment_result(wind=wind))])
    fact = _facts(brief, "environment")[f"Wind at 120{NNBSP}m"]
    assert fact.tone == "warn" and fact.tone_label == "Gusts above the rated limit"
    assert "gusts 12.8" in fact.note and "above the 10" in fact.note


def test_airspace_partly_outside_the_pack_is_not_a_pass(synthetic_pack: DataPack) -> None:
    # The flight audit's rule: aerodrome zones outside the pack were not checked there.
    edge = box(77.90, 13.25, 78.00, 13.35)
    result = airspace_run(AirspaceParams(geometry=mapping(edge), altitude_agl_m=30.0, grid=False), AnalysisContext(datapack=synthetic_pack))
    assert result.assessment_complete is False
    assert result.verdict != Verdict.PASS
    gap = next(i for i in result.issues if i.code == "not_assessed")
    assert gap.message.startswith("Airspace only partly assessed: the geometry extends beyond data pack 'demo'")
    assert result.summary.startswith("Airspace only partly assessed")
    assert "user zones and the default rules only" not in result.summary


def test_ground_risk_footprint_partly_outside_the_pack_is_a_partial_section() -> None:
    # Densities outside the pack are unknown: the highest density (and so the iGRC) may be higher.
    partial = "Part of the footprint lies outside data pack 'demo'; densities there are unknown."
    result = {
        "igrc": 3, "in_scope": True, "small_uas_rule": False, "column": "≤ 1 m", "density_band": "< 500", "density_statistic": "max",
        "max_density_per_km2": 400.0, "p95_density_per_km2": 300.0, "mean_density_per_km2": 200.0, "population_in_footprint": 90.0,
        "footprint_area_km2": 0.5, "buffer_m": 120.0, "hotspots": [], "cells": [], "table_reference": "SORA 2.5 Table 2", "method": "",
        "disclaimer": "Evidence.", "issues": [{"code": "partial_coverage", "message": partial, "severity": "warning"}], "layers": {},
    }
    section = next(s for s in build_brief([_source("ground_risk", {"geometry": SQUARE}, result)]).sections if s.key == "ground_risk")
    assert section.status == "partial"
    assert section.parts[0].banners == [f"Population only partly assessed: {partial}"]
    covered = dict(result, issues=[])
    section = next(s for s in build_brief([_source("ground_risk", {"geometry": SQUARE}, covered)]).sections if s.key == "ground_risk")
    assert section.status == "assessed" and section.parts[0].banners == []


def test_brief_texts_for_unnamed_tracks_flat_heights_and_missing_assemblies_layer() -> None:
    audit = {
        "verdict": "pass", "flight": {"points": 3, "source": "track", "track_format": "other"}, "altitude_reference": "takeoff_relative",
        "distance_m": 300.0, "max_agl_m": 50.0, "max_height_above_takeoff_m": 50.0, "ceiling_agl_m": 120.0, "profile": [],
        "exceedances": [], "zones": [], "disclaimer": "Audit evidence.", "issues": [], "layers": {},
    }
    facts = _facts(build_brief([_source("flight_audit", {"track": {"coordinates": [[77.6, 12.95], [77.61, 12.96]]}}, audit)]),
                   "flight_audit")
    assert facts["Flight"].text == "Position track"  # not "Position track · Position track"
    # An above-take-off plan without terrain: the height above ground is one value.
    flat = _airspace_result(height_reference="takeoff", agl_min_m=120.0, agl_max_m=120.0, below_min_agl=[], above_ceiling=[])
    facts = _facts(build_brief([_source("airspace", {"geometry": SQUARE, "height_reference": "takeoff"}, flat)]), "heights")
    assert facts["Height above ground"].text == f"120{NNBSP}m"
    # Ground risk with a pack that has no assemblies layer: "0" says only entered ones were checked.
    result = {
        "igrc": 3, "in_scope": True, "small_uas_rule": False, "column": "≤ 1 m", "density_band": "< 500", "density_statistic": "max",
        "max_density_per_km2": 400.0, "p95_density_per_km2": 300.0, "mean_density_per_km2": 200.0, "population_in_footprint": 90.0,
        "footprint_area_km2": 0.5, "buffer_m": 150.0, "table_reference": "SORA 2.5 Table 2", "method": "", "disclaimer": "Evidence.",
        "layers": {}, "assemblies": [],
        "volume": {"aircraft": "multirotor", "speed_ms": 15.0, "fg_height_m": 120.0, "s_cv_m": 30.0, "h_cv_m": 140.0, "grb_m": 120.0,
                   "grb_rule": "one_to_one", "adjacent_outer_km": 5.0, "adjacent_distance_km": 2.7, "grid_m": 200.0,
                   "grid_source": "auto", "terms": [], "reference": "SORA 2.5"},
        "issues": [{"code": "assemblies_layer_missing", "message": "No assemblies layer.", "severity": "info"}],
    }
    facts = _facts(build_brief([_source("ground_risk", {"geometry": SQUARE, "volume": {}}, result)]), "ground_risk")
    assert facts["Assemblies within 1 km"].note == "Only those entered: the data pack has no assemblies layer"


def test_line_of_sight_with_dem_gaps_is_a_partial_section() -> None:
    gap = "120 of 4,000 grid cells have no DEM data (80 of them in the operation area); they are left out of the percentages."
    result = {
        "geometry_type": "Polygon", "target_height_agl_m": 120.0, "combined_visible_pct": 87.5, "observers": [],
        "grid": {"grid_m": 30.0, "requested_grid_m": 30.0, "cols": 80, "rows": 50, "area_cells": 1000, "nodata_cells": 120,
                 "dem_source": "GLO-30"},
        "vlos_guidance_m": 500.0, "vlos_guidance": {"guidance_m": 500.0}, "method": "", "disclaimer": "Geometric evidence.",
        "issues": [{"code": "dem_nodata", "message": gap, "severity": "warning"}], "layers": {},
    }
    section = next(s for s in build_brief([_source("line_of_sight", {"geometry": SQUARE}, result)]).sections if s.key == "line_of_sight")
    assert section.status == "partial" and section.parts[0].banners == [f"Line of sight only partly assessed: {gap}"]
    full = dict(result, issues=[], grid={**result["grid"], "nodata_cells": 0})
    section = next(s for s in build_brief([_source("line_of_sight", {"geometry": SQUARE}, full)]).sections if s.key == "line_of_sight")
    assert section.status == "assessed" and section.parts[0].banners == []


def test_environment_point_of_an_area_across_the_antimeridian() -> None:
    # A 2 km area across 180°: the sun and the magnetic field are evaluated there, not at 0°
    # (the plain lon/lat centroid), which would put sunrise 12 hours off.
    area = {"type": "Polygon", "coordinates": [[[179.99, -16.5], [-179.99, -16.5], [-179.99, -16.49], [179.99, -16.49],
                                                [179.99, -16.5]]]}
    result = environment.run(EnvironmentParams(geometry=area, date="2026-06-21", utc_offset_min=720), AnalysisContext())
    assert abs(result.point[0]) > 179.98 and result.point[1] == pytest.approx(-16.495, abs=1e-6)
    sunrise = result.sun.sunrise
    assert sunrise is not None and "06:00" <= sunrise.local <= "07:30"  # Fiji time (UTC+12) on the June solstice


def test_default_take_off_and_assumed_pilot_of_an_area_across_the_antimeridian() -> None:
    area = shape({"type": "Polygon", "coordinates": [[[179.99, -16.5], [-179.99, -16.5], [-179.99, -16.49], [179.99, -16.49],
                                                      [179.99, -16.5]]]})
    lon, lat, source = heights.default_takeoff(area)
    assert abs(lon) > 179.98 and lat == pytest.approx(-16.495) and source == "centroid"
    lon, lat, _where = line_of_sight._assumed_position(area)
    assert abs(lon) > 179.98 and lat == pytest.approx(-16.495)
    # An ordinary area keeps its plain centroid.
    assert heights.default_takeoff(shape(SQUARE))[:2] == pytest.approx((77.605, 12.955))


def test_environment_dates_before_1970_and_out_of_range() -> None:
    # Times before 1970 are negative Unix times, which datetime.fromtimestamp refuses on Windows
    # (an internal error before the fix). Dates outside the solar equations' range are a 422.
    for day in ("1969-12-31", "1901-01-01", "2099-12-31"):
        result = environment.run(EnvironmentParams(geometry=SQUARE, date=day, window_start="05:00", window_end="07:00",
                                                   utc_offset_min=330), AnalysisContext())
        assert result.sun.sunrise is not None and result.sun.sunrise.utc.startswith(day[:4])
    for day in ("1900-12-31", "2100-01-01", "9999-12-31", "0001-01-01"):
        with pytest.raises(ValidationError):
            EnvironmentParams(geometry=SQUARE, date=day)


def test_airspace_amsl_plan_without_terrain_never_claims_a_height_above_ground() -> None:
    # No pack, no DEM, no take-off elevation: 1,050 m AMSL says nothing about the height above
    # ground, so the summary must not claim "reaches 1,050 m above ground".
    far = box(10.0, 50.0, 10.01, 50.01)
    params = AirspaceParams(geometry=mapping(far), altitude_agl_m=1050.0, height_reference="amsl", grid=False)
    result = airspace_run(params, AnalysisContext())
    assert result.heights_assessed is False and result.agl_max_m is None
    assert "above ground at" not in result.summary
    assert "was checked at 1,050 m AMSL" in result.summary
    assert result.verdict == Verdict.CAUTION
    assert result.max_altitude_amsl_m == 1050.0


def test_registry_ignores_the_folders_of_a_pack_swap(tmp_path: Path, synthetic_pack_dir: Path) -> None:
    # seed.datapack builds in ".<name>.partial" and keeps the old pack as ".<name>.previous" until
    # it can delete it: neither may shadow the real pack ('.' sorts before letters).
    shutil.copytree(synthetic_pack_dir / "demo", tmp_path / "demo")
    for stale in (".demo.previous", ".demo.partial"):
        shutil.copytree(synthetic_pack_dir / "demo", tmp_path / stale)
        manifest_path = tmp_path / stale / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = "stale"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    registry = DataPackRegistry(tmp_path)
    packs = registry.list()
    assert [p.name for p in packs] == ["demo"]
    assert packs[0].root == tmp_path / "demo" and packs[0].manifest.version != "stale"


def test_corridor_survey_with_millions_of_line_pieces_is_refused_quickly() -> None:
    # 80 km × 5 km at 0.5 cm/px: about 600 lines cut into 2,000 battery pieces (1.2 million line
    # pieces, gigabytes of geometry). Refused up front instead of running for many minutes.
    line = {"type": "LineString", "coordinates": [[77.35, 12.75], [77.9, 13.25]]}
    params = SurveyParams(geometry=line, coverage="corridor", corridor_width_m=5000, target_gsd_cm=0.5)
    started = time.perf_counter()
    with pytest.raises(AnalysisError) as caught:
        survey.run(params, AnalysisContext())
    assert caught.value.code == "area_too_large"
    assert time.perf_counter() - started < 30.0


def test_area_survey_with_thousands_of_battery_blocks_is_refused_quickly(synthetic_pack: DataPack) -> None:
    # About 6,700 lines of 5.5 km at 0.3 cm/px, each longer than a battery at 1 m/s: one block per
    # line, each with its own polygon and DEM statistics (many minutes). Refused before the blocks
    # are built.
    side = 0.05
    poly = {"type": "Polygon", "coordinates": [[[77.4, 12.8], [77.4 + side, 12.8], [77.4 + side, 12.8 + side],
                                                [77.4, 12.8 + side], [77.4, 12.8]]]}
    params = SurveyParams(geometry=poly, target_gsd_cm=0.3, side_overlap_pct=95.0, speed_ms=1.0)
    started = time.perf_counter()
    with pytest.raises(AnalysisError) as caught:
        survey.run(params, AnalysisContext(datapack=synthetic_pack))
    assert caught.value.code == "area_too_large"
    assert caught.value.details["max_blocks"] == survey.MAX_SURVEY_BLOCKS
    assert time.perf_counter() - started < 30.0


@pytest.mark.parametrize("mode", ["mapping", "spraying"])
def test_survey_terrain_per_block_honours_cancellation(synthetic_pack: DataPack, mode: str) -> None:
    # The DEM statistics of every block (up to MAX_SURVEY_BLOCKS of them) is the last long loop
    # of a survey: a cancel asked for during it must stop the run.
    event = threading.Event()

    def progress(stage: str, _fraction: float) -> None:
        if stage == "Heights over the ground":
            event.set()

    ctx = AnalysisContext(datapack=synthetic_pack, progress=progress, cancel_event=event)
    with pytest.raises(AnalysisCancelled):
        survey.run(SurveyParams(geometry=SQUARE, mode=mode), ctx)


def test_line_of_sight_extent_message_counts_the_grid_margin() -> None:
    # 59.8 km plus the 150 m margin on each side is over the 60 km limit: say 60.1 km, not 59.8.
    with pytest.raises(AnalysisError) as caught:
        line_of_sight._grid_layout((0.0, 0.0, 59_800.0, 100.0), 30.0)
    assert caught.value.code == "area_too_large"
    assert "60.1 km" in caught.value.message
    assert caught.value.details["extent_m"] > line_of_sight.MAX_EXTENT_M
