"""Terrain-true heights (airbrief_core.analyses.heights) and their use by the airspace module.

Hand-made DEMs are linear in longitude between pixel centres, so bilinear sampling reproduces
them exactly and every expected number below is worked out by hand from the ramp equations.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box, mapping

from airbrief_core.analyses.airspace import run as run_airspace
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.heights import (
    default_takeoff,
    dem_info,
    flight_level,
    height_segments,
    heights_from_ground,
    index_runs,
    resolve_heights,
    segments,
    takeoff_point,
)
from airbrief_core.data import DataPack
from airbrief_core.data.datapack import DataPackManifest, LayerEntry, seal_manifest, write_manifest
from airbrief_core.geo.geodesic import geodesic_length
from airbrief_core.models.analyses import AirspaceParams
from airbrief_core.models.common import Severity, Verdict

RES = 0.0005  # DEM pixel, degrees
LAT0 = 12.0
J0 = 20  # column of the route start; the route ends N columns further east
N = 60
COLS = N + 2 * J0
ROWS = 21
WEST = 77.0 - COLS * RES / 2.0
NORTH = LAT0 + ROWS * RES / 2.0


def _col_lon(j: float) -> float:
    return WEST + (j + 0.5) * RES


ROUTE = LineString([(_col_lon(J0), LAT0), (_col_lon(J0 + N), LAT0)])
ROUTE_LENGTH = geodesic_length(ROUTE)


def _pack(root: Path, z_of_col: Any, obstacles: list[dict[str, Any]] | None = None, source: str = "test-dem") -> DataPack:
    """A data pack whose DEM depends on the column only: ``z_of_col(j)`` at column centre j."""
    root.mkdir(parents=True, exist_ok=True)
    row = np.array([z_of_col(j) for j in range(COLS)], dtype=np.float32)
    array = np.tile(row, (ROWS, 1))
    with rasterio.open(
        root / "dem.tif", "w", driver="GTiff", width=COLS, height=ROWS, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(WEST, NORTH, RES, RES), nodata=-9999.0,
    ) as dst:
        dst.write(array, 1)
    layers = {"dem": LayerEntry(file="dem.tif", source=source)}
    if obstacles is not None:
        (root / "obstacles.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": obstacles}))
        layers["obstacles"] = LayerEntry(file="obstacles.geojson", source="test-obstacles")
    write_manifest(root, DataPackManifest(
        name=root.name, bbox=[WEST, NORTH - ROWS * RES, WEST + COLS * RES, NORTH],
        created_utc="2026-01-01T00:00:00Z", layers=layers,
    ))
    return DataPack(root)


def _ramp(top: float) -> Any:
    """Ground rising linearly from 900 m at the route start to ``top`` at its end."""
    return lambda j: 900.0 + (top - 900.0) * (j - J0) / N


@pytest.fixture
def ramp_pack(tmp_path: Path) -> DataPack:
    obstacle = {
        "type": "Feature", "id": "mast-1",
        "geometry": {"type": "Point", "coordinates": [_col_lon(J0 + 40), LAT0]},
        "properties": {"kind": "mast", "height_m": 25.0, "height_source": "default"},
    }
    return _pack(tmp_path / "ramp", _ramp(990.0), obstacles=[obstacle])


def _airspace(pack: DataPack | None, **params: Any) -> Any:
    params.setdefault("geometry", mapping(ROUTE))
    params.setdefault("grid", False)
    params.setdefault("obstacle_search_m", 0.0)
    # the heights were specified against the rules in force before zone profiles
    params.setdefault("zone_profile", "conservative_illustrative")
    return run_airspace(AirspaceParams(**params), AnalysisContext(datapack=pack))


def test_index_runs_matches_the_flight_audit_rule() -> None:
    assert index_runs([False, True, True, False, True]) == [(1, 2), (4, 4)]
    assert index_runs([True, True]) == [(0, 1)]
    assert index_runs([]) == []


def test_segments_interpolate_the_crossings() -> None:
    d = [0.0, 10.0, 20.0, 30.0, 40.0]
    excess = [-2.0, 2.0, 6.0, -2.0, -1.0]  # crosses 0 at 5 m (up) and 25 m (down: 6 → −2 at 3/4)
    mask = np.asarray(excess) > 0
    assert segments(mask, d) == [(10.0, 20.0)]
    assert segments(mask, d, excess) == [(5.0, 27.5)]
    # A run touching the start of the route starts there.
    assert segments(np.array([True, False]), [0.0, 10.0], [1.0, -1.0]) == [(0.0, 5.0)]


def test_height_segments_peak_and_limit() -> None:
    d = np.array([0.0, 100.0, 200.0, 300.0])
    agl = np.array([120.0, 60.0, 20.0, 60.0])
    below = height_segments(d, agl, 30.0, "below")
    assert len(below) == 1
    # 60 → 20 crosses 30 at 3/4 of the step (175 m); 20 → 60 at 1/4 (225 m).
    assert below[0].start_m == pytest.approx(175.0)
    assert below[0].end_m == pytest.approx(225.0)
    assert below[0].length_m == pytest.approx(50.0)
    assert below[0].peak_agl_m == pytest.approx(20.0)
    assert below[0].limit_m == pytest.approx(30.0)
    limits = np.array([100.0, 100.0, np.nan, 100.0])  # NaN: no free-flight ceiling (permission)
    above = height_segments(d, agl, limits, "above")
    # 120 → 60 crosses 100 a third of the way (33.33 m, rounded to the centimetre).
    assert [(s.start_m, s.end_m, s.peak_agl_m) for s in above] == [(0.0, pytest.approx(100 / 3, abs=0.005), 120.0)]


def test_heights_from_ground_three_references() -> None:
    ground = np.array([900.0, 950.0, np.nan])
    agl, amsl = heights_from_ground(ground, "agl", 100.0, None)
    assert agl.tolist() == [100.0, 100.0, 100.0]
    assert amsl[:2].tolist() == [1000.0, 1050.0] and math.isnan(amsl[2])
    # Above a take-off at 900 m: level 1020 m, AGL 120 / 70, flat ground (120) where unknown.
    agl, amsl = heights_from_ground(ground, "takeoff", 120.0, 900.0)
    assert agl.tolist() == [120.0, 70.0, 120.0]
    assert amsl.tolist() == [1020.0, 1020.0, 1020.0]
    # AMSL 1020 m: the same heights; unknown ground → flat at the take-off, or unknown without it.
    agl, amsl = heights_from_ground(ground, "amsl", 1020.0, 900.0)
    assert agl.tolist() == [120.0, 70.0, 120.0]
    agl, _ = heights_from_ground(ground, "amsl", 1020.0, None)
    assert agl[:2].tolist() == [120.0, 70.0] and math.isnan(agl[2])
    assert flight_level("takeoff", 120.0, 900.0) == 1020.0
    assert flight_level("amsl", 1020.0, None) == 1020.0
    assert flight_level("agl", 120.0, 900.0) is None


def test_takeoff_defaults_and_override(ramp_pack: DataPack) -> None:
    dem = ramp_pack.dem()
    assert default_takeoff(ROUTE)[2] == "first_vertex"
    area = box(_col_lon(J0), LAT0 - 0.001, _col_lon(J0 + 20), LAT0 + 0.001)
    lon, lat, source = default_takeoff(area)
    assert source == "centroid" and (lon, lat) == pytest.approx((area.centroid.x, area.centroid.y))
    assert default_takeoff(Point(77.0, 12.0)) == (77.0, 12.0, "point")
    t = takeoff_point(ROUTE, None, dem, None)
    assert t.source == "first_vertex" and t.elevation_source == "dem"
    assert t.elevation_m == pytest.approx(900.0, abs=1e-3)
    t = takeoff_point(ROUTE, (_col_lon(J0 + 30), LAT0), dem, None)
    assert t.source == "given" and t.elevation_m == pytest.approx(945.0, abs=1e-3)
    t = takeoff_point(ROUTE, None, dem, 880.0)
    assert t.elevation_source == "override" and t.elevation_m == 880.0


def test_resolve_heights_samples_the_dem(ramp_pack: DataPack) -> None:
    lons = np.array([_col_lon(J0), _col_lon(J0 + 30), _col_lon(J0 + 60)])
    lats = np.full(3, LAT0)
    agl, amsl, takeoff, issues = resolve_heights(lons, lats, ramp_pack.dem(), "takeoff", 120.0, (_col_lon(J0), LAT0))
    assert takeoff == pytest.approx(900.0, abs=1e-3)
    assert agl == pytest.approx([120.0, 75.0, 30.0], abs=1e-3)
    assert amsl == pytest.approx([1020.0] * 3, abs=1e-3)
    assert issues == []
    _, _, _, amsl_issues = resolve_heights(lons, lats, ramp_pack.dem(), "amsl", 1020.0)
    assert [i.code for i in amsl_issues] == ["vertical_reference_approximated"]
    agl, _, takeoff, issues = resolve_heights(lons, lats, None, "takeoff", 120.0, (0.0, 0.0))
    assert takeoff is None and agl.tolist() == [120.0] * 3
    assert [i.code for i in issues] == ["heights_not_assessed"]


def test_rising_ground_gives_30m_agl_and_a_below_minimum_stretch(ramp_pack: DataPack) -> None:
    # Take-off at 900 m (the first vertex), 120 m above take-off: level 1020 m. The ground rises
    # linearly to 990 m, so AGL(d) = 120 − 90·d/L: 30 m at the end. With a 40 m minimum the
    # stretch below starts where 90·d/L = 80, at d = 8L/9, and runs to the end.
    result = _airspace(ramp_pack, altitude_agl_m=120.0, height_reference="takeoff", min_agl_m=40.0,
                       profile_spacing_m=30.0)
    assert result.takeoff is not None and result.takeoff.source == "first_vertex"
    assert result.takeoff.elevation_m == pytest.approx(900.0, abs=0.01)
    assert result.flight_level_amsl_m == pytest.approx(1020.0, abs=0.01)
    assert result.agl_max_m == pytest.approx(120.0, abs=0.05)
    assert result.agl_min_m == pytest.approx(30.0, abs=0.05)
    assert len(result.below_min_agl) == 1
    stretch = result.below_min_agl[0]
    assert stretch.start_m == pytest.approx(8 * ROUTE_LENGTH / 9, abs=0.5)
    assert stretch.end_m == pytest.approx(ROUTE_LENGTH, abs=0.05)
    assert stretch.peak_agl_m == pytest.approx(30.0, abs=0.05)
    assert stretch.limit_m == 40.0
    for p in result.profile:
        assert p.altitude_amsl_m == pytest.approx(1020.0, abs=0.01)
        assert p.agl_m == pytest.approx(120.0 - 90.0 * p.distance_m / ROUTE_LENGTH, abs=0.05)
    assert result.heights_assessed is True
    assert result.verdict == Verdict.CAUTION
    assert "Below the 40 m minimum above ground" in result.summary
    assert "Flight level 1,020 m AMSL (take-off ground 900 m AMSL): from 30 m to 120 m above ground." in result.summary
    assert result.max_altitude_amsl_m == pytest.approx(1020.0)
    stretches = result.layers["heights"]["features"]
    assert [f["properties"]["kind"] for f in stretches] == ["below_min_agl"]
    assert result.layers["takeoff"]["features"][0]["properties"]["elevation_m"] == pytest.approx(900.0, abs=0.01)

    # At the default 30 m minimum the end is exactly at the minimum: no stretch below.
    at_minimum = _airspace(ramp_pack, altitude_agl_m=120.0, height_reference="takeoff", profile_spacing_m=30.0)
    assert at_minimum.below_min_agl == []
    assert at_minimum.agl_min_m == pytest.approx(30.0, abs=0.05)


def test_amsl_mode_matches_the_same_level_above_take_off(ramp_pack: DataPack) -> None:
    takeoff = _airspace(ramp_pack, altitude_agl_m=120.0, height_reference="takeoff", min_agl_m=40.0)
    amsl = _airspace(ramp_pack, altitude_agl_m=1020.0, height_reference="amsl", min_agl_m=40.0)
    assert amsl.flight_level_amsl_m == pytest.approx(1020.0)
    assert [p.agl_m for p in amsl.profile] == pytest.approx([p.agl_m for p in takeoff.profile], abs=0.02)
    assert amsl.below_min_agl[0].start_m == pytest.approx(takeoff.below_min_agl[0].start_m, abs=0.05)
    assert any(i.code == "vertical_reference_approximated" for i in amsl.issues)
    assert "1,020 m AMSL" in amsl.summary


def test_valley_exceeds_the_ceiling(tmp_path: Path) -> None:
    # Ground 905 m, a valley down to 845 m between the thirds of the route (kinks at pixel centres).
    # Level 900 + 120 = 1020 m (take-off elevation typed): AGL = 1020 − z exceeds the 120 m
    # ceiling where z < 900, i.e. 5/60 of the way down each slope of length L/6:
    # from L/3 + L/72 to 2L/3 − L/72, peaking at 1020 − 845 = 175 m.
    def valley(j: float) -> float:
        k = j - J0
        if k <= N / 3 or k >= 2 * N / 3:
            return 905.0
        return 905.0 - 60.0 * (1.0 - abs(k - N / 2) / (N / 6))

    pack = _pack(tmp_path / "valley", valley)
    result = _airspace(pack, altitude_agl_m=120.0, height_reference="takeoff", takeoff_elevation_m=900.0,
                       profile_spacing_m=20.0)
    assert result.takeoff is not None and result.takeoff.elevation_source == "override"
    assert len(result.above_ceiling) == 1
    s = result.above_ceiling[0]
    assert s.start_m == pytest.approx(ROUTE_LENGTH / 3 + ROUTE_LENGTH / 72, abs=0.5)
    assert s.end_m == pytest.approx(2 * ROUTE_LENGTH / 3 - ROUTE_LENGTH / 72, abs=0.5)
    assert s.peak_agl_m == pytest.approx(175.0, abs=0.5)
    assert s.limit_m == 120.0
    assert result.agl_max_m == pytest.approx(175.0, abs=0.5)
    assert result.agl_min_m == pytest.approx(115.0, abs=0.05)
    assert result.verdict == Verdict.CAUTION
    assert "above the 120 m AGL free-flight ceiling" in result.summary
    assert "Above the free-flight ceiling for" in result.summary


def test_ground_above_the_flight_level_fails(ramp_pack: DataPack) -> None:
    result = _airspace(ramp_pack, altitude_agl_m=50.0, height_reference="takeoff")
    assert result.agl_min_m == pytest.approx(-40.0, abs=0.05)  # 950 m level over 990 m ground
    assert result.verdict == Verdict.FAIL
    issue = next(i for i in result.issues if i.code == "terrain_above_flight_level")
    assert issue.severity == Severity.CRITICAL
    assert "The ground rises above the flight level." in result.summary


def test_area_uses_the_terrain_under_it(ramp_pack: DataPack) -> None:
    area = box(_col_lon(J0), LAT0 - 0.002, _col_lon(J0 + N), LAT0 + 0.002)
    result = _airspace(ramp_pack, geometry=mapping(area), altitude_agl_m=120.0, height_reference="takeoff",
                       takeoff=(_col_lon(J0), LAT0), min_agl_m=40.0, grid=True, h3_resolution=10)
    assert result.below_min_agl == []  # stretches are for routes; areas count grid cells
    assert result.cells_below_min_agl is not None and result.cells_below_min_agl > 0
    assert all(c.agl_m is not None for c in result.grid)
    for c in result.grid:
        assert c.terrain_m is not None
        assert c.agl_m == pytest.approx(1020.0 - c.terrain_m, abs=0.02)
    assert result.agl_min_m is not None and result.agl_min_m < 40.0
    assert result.verdict == Verdict.CAUTION


def test_obstacle_clearance_at_a_constant_level(ramp_pack: DataPack) -> None:
    # The mast stands on ground at 960 m (column J0 + 40): level 1020 m → 60 m above its ground,
    # 60 − 25 = 35 m above its top.
    level = _airspace(ramp_pack, altitude_agl_m=120.0, height_reference="takeoff", obstacle_search_m=10.0,
                      vertical_clearance_m=30.0)
    mast = next(o for o in level.obstacles if o.id == "mast-1")
    assert mast.base_m == pytest.approx(960.0, abs=0.01)
    assert mast.clearance_m == pytest.approx(35.0, abs=0.02)
    assert mast.conflict is False
    assert mast.height_assumed is True and mast.height_source == "default"
    strict = _airspace(ramp_pack, altitude_agl_m=120.0, height_reference="takeoff", obstacle_search_m=10.0,
                       vertical_clearance_m=40.0)
    assert next(o for o in strict.obstacles if o.id == "mast-1").conflict is True
    # The same plan read as 120 m above ground leaves 95 m.
    agl = _airspace(ramp_pack, altitude_agl_m=120.0, obstacle_search_m=10.0)
    assert next(o for o in agl.obstacles if o.id == "mast-1").clearance_m == pytest.approx(95.0)


def test_no_dem_reports_heights_not_assessed() -> None:
    result = _airspace(None, altitude_agl_m=120.0, height_reference="takeoff")
    assert result.heights_assessed is False
    assert any(i.code == "heights_not_assessed" for i in result.issues)
    assert result.agl_min_m == pytest.approx(120.0) and result.agl_max_m == pytest.approx(120.0)
    assert result.verdict != Verdict.PASS
    assert "Heights above ground were not assessed" in result.summary

    amsl = _airspace(None, altitude_agl_m=1020.0, height_reference="amsl")
    assert amsl.heights_assessed is False and amsl.agl_min_m is None
    assert all(p.agl_m is None for p in amsl.profile)
    flat = _airspace(None, altitude_agl_m=1020.0, height_reference="amsl", takeoff_elevation_m=900.0)
    assert flat.agl_min_m == pytest.approx(120.0) and flat.heights_assessed is False


def test_agl_plan_is_not_checked_against_a_minimum(ramp_pack: DataPack) -> None:
    """An AGL plan's verdict ignores the minimum, so the result reports no stretch below it."""
    low = _airspace(ramp_pack, altitude_agl_m=20.0, grid=True, h3_resolution=10)  # under the default 30 m
    assert low.below_min_agl == [] and low.cells_below_min_agl is None and low.min_agl_m is None
    same = _airspace(ramp_pack, altitude_agl_m=20.0, grid=True, h3_resolution=10, min_agl_m=0.0)
    assert (low.verdict, low.summary) == (same.verdict, same.summary)
    # The ceiling stretches still restate the planned value against the zone ceilings (120 m).
    high = _airspace(ramp_pack, altitude_agl_m=150.0)
    assert [(s.start_m, s.end_m) for s in high.above_ceiling] == [(0.0, pytest.approx(ROUTE_LENGTH, abs=0.05))]
    assert high.above_ceiling[0].peak_agl_m == 150.0 and high.above_ceiling[0].limit_m == 120.0
    # Above take-off, the same minimum is checked.
    takeoff = _airspace(ramp_pack, altitude_agl_m=20.0, height_reference="takeoff")
    assert takeoff.min_agl_m == 30.0 and takeoff.below_min_agl


def test_agl_height_limit_depends_on_the_reference() -> None:
    with pytest.raises(ValueError, match="at most 1500"):
        AirspaceParams(geometry=mapping(ROUTE), altitude_agl_m=2000.0)
    assert AirspaceParams(geometry=mapping(ROUTE), altitude_agl_m=2000.0, height_reference="amsl").altitude_agl_m == 2000.0


_NEW_RESULT_KEYS = {
    "height_reference", "min_agl_m", "takeoff", "flight_level_amsl_m", "agl_min_m", "agl_max_m",
    "below_min_agl", "above_ceiling", "cells_below_min_agl", "cells_above_ceiling", "heights_assessed", "dem",
    "zone_profile", "band_evidence", "inactive_zones", "assessment_banner",
}
# fields on zone hits, nearest aerodromes and the zones layer that the snapshots predate
_NEW_ZONE_KEYS = {"active", "valid_from", "valid_to", "agl_margin_m", "basis", "distance_from", "inactive_reason"}
_NEW_AERODROME_KEYS = {"operational", "scheduled_service"}
_NEW_LAYER_KEYS = {"basis", "distance_from", "ceiling_exceeded_zone", "active", "valid_from", "valid_to",
                   "lower_limit_m", "upper_limit_m", "vertical_reference", "schedule", "restriction", "authority",
                   "reason", "source_format"}


def _without_new_zone_fields(result: dict[str, Any]) -> None:
    result["zones"] = [{k: v for k, v in z.items() if k not in _NEW_ZONE_KEYS} for z in result["zones"]]
    if "nearest_aerodromes" in result:
        result["nearest_aerodromes"] = [{k: v for k, v in a.items() if k not in _NEW_AERODROME_KEYS}
                                        for a in result["nearest_aerodromes"]]
    for feature in result.get("layers", {}).get("zones", {}).get("features", []):
        for key in _NEW_LAYER_KEYS:
            feature["properties"].pop(key, None)


def _without_new_fields(result: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in result.items() if k not in _NEW_RESULT_KEYS}
    out["grid"] = [{k: v for k, v in c.items() if k != "agl_m"} for c in out["grid"]]
    out["obstacles"] = [{k: v for k, v in o.items() if k not in ("height_source", "height_assumed")} for o in out["obstacles"]]
    for feature in out["layers"]["obstacles"]["features"]:
        for key in ("height_source", "height_assumed"):
            feature["properties"].pop(key, None)
    _without_new_zone_fields(out)
    return out


def test_agl_mode_is_unchanged(synthetic_pack: DataPack) -> None:
    """Every field of an AGL result is the same as before the height reference existed.

    The snapshot was computed by the module before this change on the same synthetic pack; only
    the fields added since (AGL range, stretches, take-off, DEM label, obstacle height source) differ.
    """
    snapshot = json.loads((Path(__file__).parent / "snapshots" / "airspace_agl_before_heights.json").read_text(encoding="utf-8"))
    assert len(snapshot) == 6
    for name, case in snapshot.items():
        # computed with the rules in force before zone profiles
        params = AirspaceParams(**case["params"], zone_profile="conservative_illustrative")
        result = run_airspace(params, AnalysisContext(datapack=synthetic_pack if case["with_pack"] else None))
        now = json.loads(json.dumps(result.model_dump(mode="json")))
        assert _without_new_fields(now) == case["result"], name
        assert now["height_reference"] == "agl" and now["takeoff"] is None
        assert now["agl_min_m"] == now["agl_max_m"] == case["params"].get("altitude_agl_m", 120.0)


def test_results_stored_before_the_heights_still_validate() -> None:
    from airbrief_core.models.analyses import AirspaceResult, EvtolCorridorResult, SurveyResult

    snapshots = Path(__file__).parent / "snapshots"
    airspace = json.loads((snapshots / "airspace_agl_before_heights.json").read_text(encoding="utf-8"))
    for case in airspace.values():
        old = AirspaceResult.model_validate(case["result"])
        assert old.height_reference == "agl" and old.takeoff is None and old.heights_assessed is True
        assert old.below_min_agl == [] and old.above_ceiling == [] and old.dem is None
    survey = json.loads((snapshots / "survey_before_upgrades.json").read_text(encoding="utf-8"))
    for case in survey.values():
        old_survey = SurveyResult.model_validate(case["result"])
        assert old_survey.mode == "mapping" and old_survey.coverage == "area" and old_survey.camera is not None
        assert old_survey.camera.kind == "rgb" and old_survey.spray is None and old_survey.height_reference == "agl"
    corridor = json.loads((snapshots / "evtol_corridor_agl_before_heights.json").read_text(encoding="utf-8"))
    old_corridor = EvtolCorridorResult.model_validate(corridor["result"])
    assert old_corridor.cruise_reference == "agl" and old_corridor.cruise_altitude_amsl_m is None


def test_dem_labels_come_from_the_manifest(tmp_path: Path, synthetic_pack: DataPack) -> None:
    glo30 = dem_info(_pack(tmp_path / "glo30", _ramp(990.0), source="copernicus_glo30"))
    assert glo30 is not None and glo30.surface_model is True and glo30.synthetic is False
    assert "Copernicus GLO-30 surface model — includes roofs and canopy" in glo30.caveat
    assert glo30.vertical_datum == "EGM2008 (≈ AMSL)"
    demo = dem_info(synthetic_pack)
    assert demo is not None and demo.synthetic is True and "not real terrain" in demo.caveat
    other = dem_info(_pack(tmp_path / "other", _ramp(990.0)))
    assert other is not None and other.surface_model is None and "test-dem" in other.caveat
    assert dem_info(None) is None
    route = _airspace(_pack(tmp_path / "labelled", _ramp(990.0), source="copernicus_glo30"))
    assert route.dem is not None and route.dem.source == "copernicus_glo30"


CORRIDOR: dict[str, Any] = {
    "origin": [77.5700, 12.9795],
    "destination": [77.6120, 12.9795],
    "origin_name": "West pad",
    "destination_name": "East pad",
    "study_margin_km": 3.0,
    "h3_resolution": 8,
    "profile_spacing_m": 250.0,
}


def test_corridor_agl_mode_is_unchanged(synthetic_pack: DataPack) -> None:
    from airbrief_core.analyses.evtol import run_corridor
    from airbrief_core.models.analyses import EvtolCorridorParams

    path = Path(__file__).parent / "snapshots" / "evtol_corridor_agl_before_heights.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    # the snapshot predates zone profiles too
    params = EvtolCorridorParams(**snapshot["params"], zone_profile="conservative_illustrative")
    result = run_corridor(params, AnalysisContext(datapack=synthetic_pack))
    now = json.loads(result.model_dump_json())
    new = {"cruise_reference", "cruise_altitude_amsl_m", "min_agl_m", "ceiling_agl_m", "takeoff", "agl_min_m",
           "agl_max_m", "below_min_agl", "above_ceiling", "heights_assessed", "dem", "zone_profile", "unverified_band"}
    trimmed = {k: v for k, v in now.items() if k not in new}
    trimmed["obstacles"] = [{k: v for k, v in o.items() if k not in ("height_source", "height_assumed")}
                            for o in trimmed["obstacles"]]
    _without_new_zone_fields(trimmed)
    assert trimmed == snapshot["result"]
    assert now["cruise_reference"] == "agl" and now["takeoff"]["source"] == "origin"
    # The AGL range along the route is the required level minus the terrain.
    agls = [p["agl_m"] for p in now["profile"] if p["agl_m"] is not None]
    assert now["agl_min_m"] == pytest.approx(min(agls)) and now["agl_max_m"] == pytest.approx(max(agls))


def test_corridor_at_a_fixed_amsl_level(synthetic_pack: DataPack) -> None:
    from airbrief_core.analyses.evtol import noise_half_width_m, run_corridor
    from airbrief_core.models.analyses import EvtolCorridorParams

    reference = run_corridor(EvtolCorridorParams(**CORRIDOR), AnalysisContext(datapack=synthetic_pack))
    assert reference.max_terrain_m is not None
    level = round(reference.max_terrain_m + 60.0, 1)  # 60 m over the highest ground
    result = run_corridor(
        EvtolCorridorParams(**CORRIDOR, cruise_reference="amsl", cruise_altitude_amsl_m=level, min_agl_m=80.0),
        AnalysisContext(datapack=synthetic_pack),
    )
    assert result.cruise_reference == "amsl" and result.cruise_altitude_amsl_m == level
    terrain_points = [p for p in result.profile if p.terrain_m is not None]
    assert terrain_points
    for p in terrain_points:
        assert p.altitude_amsl_m == pytest.approx(level, abs=0.05)
        assert p.agl_m == pytest.approx(level - p.terrain_m, abs=0.06)
    agls = [p.agl_m for p in terrain_points if p.agl_m is not None]
    assert result.agl_min_m == pytest.approx(min(agls)) and result.agl_max_m == pytest.approx(max(agls))
    # Below the 80 m minimum wherever the ground is within 80 m of the level.
    assert result.below_min_agl
    for stretch in result.below_min_agl:
        inside = [p for p in terrain_points if stretch.start_m < p.distance_m < stretch.end_m]
        assert all(p.agl_m is not None and p.agl_m < 80.0 for p in inside)
        assert stretch.limit_m == 80.0
    codes = {i.code for i in result.issues}
    assert {"below_min_agl", "vertical_reference_approximated"} <= codes
    # The noise footprint is computed at the lowest height above ground along the route.
    expected = noise_half_width_m(75.0, 100.0, 55.0, max(result.agl_min_m or 0.0, 0.0))
    assert result.noise.footprint_half_width_m == pytest.approx(expected, abs=0.1)
    assert result.takeoff is not None and result.takeoff.source == "origin"
    assert result.layers["heights"]["features"]

    low = run_corridor(
        EvtolCorridorParams(**CORRIDOR, cruise_reference="amsl", cruise_altitude_amsl_m=round(min(
            p.terrain_m for p in terrain_points if p.terrain_m is not None) - 10.0, 1)),
        AnalysisContext(datapack=synthetic_pack),
    )
    issue = next(i for i in low.issues if i.code == "terrain_above_flight_level")
    assert issue.severity == Severity.CRITICAL


def test_corridor_amsl_without_a_dem_is_not_assessed(tmp_path: Path, synthetic_pack: DataPack) -> None:
    import shutil

    from airbrief_core.analyses.evtol import run_corridor
    from airbrief_core.models.analyses import EvtolCorridorParams

    # The synthetic pack without its DEM layer.
    root = tmp_path / "no-dem"
    shutil.copytree(synthetic_pack.root, root)
    layers = {k: v for k, v in synthetic_pack.manifest.layers.items() if k != "dem"}
    # A layer dropped from the manifest changes the pack's content hash: record the new one.
    write_manifest(root, seal_manifest(root, synthetic_pack.manifest.model_copy(update={"layers": layers})))
    params = EvtolCorridorParams(**CORRIDOR, cruise_reference="amsl", cruise_altitude_amsl_m=1000.0, origin_elevation_m=900.0)
    result = run_corridor(params, AnalysisContext(datapack=DataPack(root)))
    assert result.heights_assessed is False and result.agl_min_m is None and result.below_min_agl == []
    notes = [i for i in result.issues if i.code == "heights_not_assessed"]
    assert len(notes) == 1 and "1,000 m AMSL were not assessed" in notes[0].message
    assert "flat" not in notes[0].message  # no flat-ground heights are derived without a profile
    assert any(i.code == "vertical_reference_approximated" for i in result.issues)


def test_corridor_amsl_needs_a_level() -> None:
    from airbrief_core.models.analyses import EvtolCorridorParams

    with pytest.raises(ValueError, match="cruise_altitude_amsl_m"):
        EvtolCorridorParams(**CORRIDOR, cruise_reference="amsl")
