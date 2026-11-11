"""Survey upgrades: heights over relief, corridor mapping, battery reserve and transit legs,
GCP and storage estimates, sensor presets and the spraying effort mode.

Every expected number is worked out by hand in the comments (P4 RTK at 100 m: footprint
150 × 100 m, line spacing 45 m at 70 % side overlap, trigger 20 m at 80 % front overlap).
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
from shapely.geometry import LineString, box

from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.survey import (
    camera_math,
    gcp_estimate,
    run,
    spray_flow_l_per_min,
    worst_overlap_pct,
)
from airbrief_core.data import DataPack
from airbrief_core.data.datapack import DataPackManifest, LayerEntry, write_manifest
from airbrief_core.errors import AnalysisError
from airbrief_core.geo.frame import LocalFrame
from airbrief_core.geo.transform import geometry_to_geojson
from airbrief_core.models.analyses import SurveyParams
from airbrief_core.models.common import Severity
from airbrief_core.models.presets import CAMERA_PRESETS
from airbrief_core.models.survey import SprayParams

P4RTK = CAMERA_PRESETS["p4rtk"]
ORIGIN = (77.49, 12.905)
FRAME = LocalFrame(*ORIGIN)


def _rect(width_m: float, height_m: float, centre: tuple[float, float] = (0.0, 0.0)) -> dict[str, Any]:
    cx, cy = centre
    return geometry_to_geojson(FRAME.to_wgs84(box(cx - width_m / 2, cy - height_m / 2, cx + width_m / 2, cy + height_m / 2)))


def _lonlat(x: float, y: float) -> tuple[float, float]:
    lons, lats = FRAME.lonlat(np.array([x]), np.array([y]))
    return float(lons[0]), float(lats[0])


RES = 0.0002  # about 22 m


def _ramp_pack(root: Path, slope_m_per_m: float = 0.1, west_x: float = -500.0) -> DataPack:
    """Ground z = 900 + slope·(x − west_x), x in metres east of ORIGIN, over ±3 km."""
    lon_w, lat_s = _lonlat(-3000.0, -3000.0)
    lon_e, lat_n = _lonlat(3000.0, 3000.0)
    cols = int(math.ceil((lon_e - lon_w) / RES))
    rows = int(math.ceil((lat_n - lat_s) / RES))
    lons = lon_w + (np.arange(cols) + 0.5) * RES
    xs = FRAME.xy(lons, np.full(cols, ORIGIN[1]))[0]
    row = (900.0 + slope_m_per_m * (xs - west_x)).astype(np.float32)
    root.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        root / "dem.tif", "w", driver="GTiff", width=cols, height=rows, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(lon_w, lat_n, RES, RES), nodata=-9999.0,
    ) as dst:
        dst.write(np.tile(row, (rows, 1)), 1)
    write_manifest(root, DataPackManifest(
        name=root.name, bbox=[lon_w, lat_s, lon_w + cols * RES, lat_n], created_utc="2026-01-01T00:00:00Z",
        layers={"dem": LayerEntry(file="dem.tif", source="copernicus_glo30")},
    ))
    return DataPack(root)


@pytest.fixture
def ramp(tmp_path: Path) -> DataPack:
    return _ramp_pack(tmp_path / "ramp")


_NEW_RESULT = {
    "mode", "coverage", "corridor_width_m", "centreline_km", "height_reference", "takeoff", "flight_level_amsl_m",
    "heights_assessed", "dem", "terrain_min_m", "terrain_max_m", "relief_m", "agl_min_m", "agl_max_m",
    "gsd_min_cm", "gsd_max_cm", "worst_side_overlap_pct", "worst_front_overlap_pct", "heights_note",
    "battery_reserve_pct", "usable_battery_minutes", "transit_km", "transit_minutes", "gcp", "storage", "spray",
    "method",
}
_NEW_BLOCK = {
    "transit_in_m", "transit_out_m", "terrain_min_m", "terrain_max_m", "agl_min_m", "agl_max_m", "gsd_min_cm",
    "gsd_max_cm", "litres",
}
_NEW_CAMERA = {"kind", "images_per_capture", "photo_mb", "reference"}


def test_default_survey_is_unchanged() -> None:
    """Each existing field equals the result computed before the upgrades (snapshot)."""
    path = Path(__file__).parent / "snapshots" / "survey_before_upgrades.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    assert len(snapshot) == 5
    for name, case in snapshot.items():
        now = json.loads(run(SurveyParams(**case["params"]), AnalysisContext()).model_dump_json())
        trimmed = {k: v for k, v in now.items() if k not in _NEW_RESULT}
        trimmed["blocks"] = [{k: v for k, v in b.items() if k not in _NEW_BLOCK} for b in trimmed["blocks"]]
        trimmed["camera"] = {k: v for k, v in trimmed["camera"].items() if k not in _NEW_CAMERA}
        assert trimmed == case["result"], name
        assert now["mode"] == "mapping" and now["height_reference"] == "agl" and now["takeoff"] is None
        assert now["gsd_min_cm"] == now["gsd_max_cm"] == now["gsd_cm"]


def test_agl_mode_with_a_dem_only_adds_the_relief(ramp: DataPack) -> None:
    flat = run(SurveyParams(geometry=_rect(900, 450)), AnalysisContext())
    hilly = run(SurveyParams(geometry=_rect(900, 450)), AnalysisContext(datapack=ramp))
    for key in ("gsd_cm", "photos", "flight_minutes", "batteries", "line_count", "issues"):
        assert getattr(hilly, key) == getattr(flat, key)
    assert hilly.relief_m == pytest.approx(90.0, abs=3.0)  # 0.1 m/m over 900 m, sampled on a grid
    assert hilly.gsd_min_cm == hilly.gsd_max_cm == hilly.gsd_cm  # terrain following
    assert hilly.heights_note is not None and "Terrain following" in hilly.heights_note
    assert hilly.dem is not None and hilly.dem.surface_model is True


def test_worst_overlap_formula() -> None:
    # 70 % side overlap laid out at 100 m; at 50 m above the highest ground the footprint halves:
    # 1 − 0.3 × 100 / 50 = 40 %. At 200 m (over lower ground) it improves: 1 − 0.3 × 0.5 = 85 %.
    assert worst_overlap_pct(70.0, 100.0, 50.0) == pytest.approx(40.0)
    assert worst_overlap_pct(70.0, 100.0, 200.0) == pytest.approx(85.0)
    assert worst_overlap_pct(80.0, 100.0, 25.0) == pytest.approx(20.0)  # 1 − 0.2 × 4
    assert worst_overlap_pct(70.0, 100.0, 0.0) is None


def test_gsd_range_is_camera_math_at_the_heights_over_the_ground(ramp: DataPack) -> None:
    # Take-off on the lowest ground (the west edge, 900 m), 100 m above take-off: level 1000 m.
    # Over the east edge (≈ 990 m) the height above ground drops to ≈ 10 m.
    takeoff = _lonlat(-450.0, 0.0)
    result = run(
        SurveyParams(geometry=_rect(900, 450), height_reference="takeoff", takeoff=takeoff, altitude_agl_m=100.0),
        AnalysisContext(datapack=ramp),
    )
    assert result.takeoff is not None and result.takeoff.elevation_m == pytest.approx(905.0, abs=0.5)
    level = result.takeoff.elevation_m + 100.0
    assert result.flight_level_amsl_m == pytest.approx(level, abs=0.01)
    assert result.altitude_agl_m == pytest.approx(100.0)  # the layout height above the take-off ground
    assert result.relief_m == pytest.approx(90.0, abs=3.0)
    assert result.agl_min_m == pytest.approx(level - result.terrain_max_m, abs=0.01)
    assert result.agl_max_m == pytest.approx(level - result.terrain_min_m, abs=0.01)
    assert result.gsd_min_cm == pytest.approx(camera_math(P4RTK, result.agl_min_m)["gsd_cm"], abs=1e-3)
    assert result.gsd_max_cm == pytest.approx(camera_math(P4RTK, result.agl_max_m)["gsd_cm"], abs=1e-3)
    assert result.worst_side_overlap_pct == pytest.approx(worst_overlap_pct(70.0, 100.0, result.agl_min_m), abs=0.05)
    assert result.worst_front_overlap_pct == pytest.approx(worst_overlap_pct(80.0, 100.0, result.agl_min_m), abs=0.05)
    assert result.heights_assessed is True
    assert result.heights_note is not None and "Flight level" in result.heights_note
    for block in result.blocks:
        assert block.terrain_min_m is not None and block.terrain_max_m is not None
        assert block.agl_min_m == pytest.approx(level - block.terrain_max_m, abs=0.01)


def test_take_off_at_the_middle_gives_h_plus_or_minus_half_the_relief(ramp: DataPack) -> None:
    # Take-off on the middle of the ramp (≈ 945 m): H_eff = 100 ± 45 m.
    result = run(
        SurveyParams(geometry=_rect(900, 450), height_reference="takeoff", takeoff=ORIGIN, altitude_agl_m=100.0),
        AnalysisContext(datapack=ramp),
    )
    assert result.takeoff is not None and result.takeoff.elevation_m == pytest.approx(950.0, abs=0.5)
    assert result.agl_min_m == pytest.approx(100.0 - 45.0, abs=2.0)
    assert result.agl_max_m == pytest.approx(100.0 + 45.0, abs=2.0)
    assert result.gsd_max_cm == pytest.approx(camera_math(P4RTK, result.agl_max_m)["gsd_cm"], abs=1e-3)
    assert "altitude_above_limit_over_low_ground" in {i.code for i in result.issues}  # 145 m > 120 m


def test_amsl_reads_the_level_and_needs_a_ground_reference(ramp: DataPack) -> None:
    at_level = run(
        SurveyParams(geometry=_rect(900, 450), height_reference="amsl", takeoff=ORIGIN, altitude_agl_m=1050.0),
        AnalysisContext(datapack=ramp),
    )
    assert at_level.flight_level_amsl_m == pytest.approx(1050.0)
    assert at_level.altitude_agl_m == pytest.approx(1050.0 - at_level.takeoff.elevation_m, abs=0.01)  # type: ignore[union-attr]
    assert any(i.code == "vertical_reference_approximated" for i in at_level.issues)
    with pytest.raises(AnalysisError) as exc:
        run(SurveyParams(geometry=_rect(900, 450), height_reference="amsl", altitude_agl_m=1050.0), AnalysisContext())
    assert exc.value.code == "heights_not_assessed"
    typed = run(
        SurveyParams(geometry=_rect(900, 450), height_reference="amsl", altitude_agl_m=1050.0, takeoff_elevation_m=950.0),
        AnalysisContext(),
    )
    assert typed.altitude_agl_m == pytest.approx(100.0)
    assert typed.heights_assessed is False
    assert any(i.code == "heights_not_assessed" for i in typed.issues)


def test_no_dem_takeoff_mode_is_flat_and_flagged() -> None:
    result = run(SurveyParams(geometry=_rect(900, 450), height_reference="takeoff"), AnalysisContext())
    assert result.heights_assessed is False
    assert result.gsd_min_cm == result.gsd_max_cm == result.gsd_cm
    issue = next(i for i in result.issues if i.code == "heights_not_assessed")
    assert issue.severity == Severity.WARNING


def test_ground_above_the_level_is_critical(ramp: DataPack) -> None:
    takeoff = _lonlat(-450.0, 0.0)
    result = run(
        SurveyParams(geometry=_rect(900, 450), height_reference="takeoff", takeoff=takeoff, altitude_agl_m=50.0),
        AnalysisContext(datapack=ramp),
    )
    issue = next(i for i in result.issues if i.code == "terrain_above_flight_level")
    assert issue.severity == Severity.CRITICAL
    assert result.worst_side_overlap_pct is None


def test_target_gsd_above_take_off() -> None:
    result = run(SurveyParams(geometry=_rect(600, 300), height_reference="takeoff", target_gsd_cm=2.0), AnalysisContext())
    message = next(i.message for i in result.issues if i.code == "altitude_from_gsd")
    assert "above the take-off ground" in message


def _centreline(length_m: float) -> dict[str, Any]:
    return geometry_to_geojson(FRAME.to_wgs84(LineString([(-length_m / 2, 0.0), (length_m / 2, 0.0)])))


def test_straight_corridor_known_answer() -> None:
    # 1000 m long, 100 m wide; spacing 45 m → n = ceil(100 / 45) = 3 lines at −45, 0, +45 m.
    # Time = 3 × 1000 / 10 + 2 connectors × 45 / 10 + 2 turns × 8 = 300 + 9 + 16 = 325 s;
    # photos = 3 × (floor(1000 / 20) + 1) = 153; area = 1000 × 100 m = 10 ha.
    result = run(SurveyParams(geometry=_centreline(1000.0), coverage="corridor", corridor_width_m=100.0),
                 AnalysisContext())
    assert result.coverage == "corridor"
    assert result.line_count == 3 and result.segment_count == 3
    assert result.centreline_km == pytest.approx(1.0, rel=1e-3)
    assert result.line_km == pytest.approx(3.0, rel=1e-3)
    assert result.flight_minutes == pytest.approx(325.0 / 60.0, rel=1e-3)
    assert result.photos == 153
    assert result.area_ha == pytest.approx(10.0, rel=2e-3)
    assert result.direction_deg == pytest.approx(90.0, abs=0.5)
    assert len(result.blocks) == 1 and result.batteries == 1
    assert result.gcp is not None and result.gcp.basis == "corridor"
    assert result.gcp.count == 5  # max(5, floor(1000 / 300) + 1 = 4)
    roles = [f["properties"]["role"] for f in result.layers["input"]["features"]]
    assert roles == ["input", "centreline"]
    assert all(f["properties"]["visual_only"] is True for f in result.layers["lines"]["features"])


def test_corridor_is_cut_into_battery_pieces() -> None:
    # Battery 2 min = 120 s. One piece: 325 s. Three pieces of 333 m: 100 + 9 + 16 = 125 s > 120.
    # Four pieces of 250 m: 3 × 25 s + 9 s + 16 s = 100 s each; the pieces after the first also fly
    # the 90 m connector from the +45 m line back to the −45 m line (9 s) and a turn (8 s): 117 s.
    result = run(
        SurveyParams(geometry=_centreline(1000.0), coverage="corridor", corridor_width_m=100.0, battery_minutes=2.0),
        AnalysisContext(),
    )
    assert len(result.blocks) == 4 and result.batteries == 4
    assert [b.minutes * 60.0 for b in result.blocks] == pytest.approx([100.0, 117.0, 117.0, 117.0], abs=0.3)
    assert result.flight_minutes * 60.0 == pytest.approx(451.0, abs=1.0)
    assert result.total_minutes == pytest.approx(result.flight_minutes + 3 * 5.0, rel=1e-6)
    assert result.photos == 3 * 4 * (12 + 1)  # floor(250 / 20) + 1 per line and piece


def test_corridor_pieces_fit_with_the_connector_into_them() -> None:
    # Battery 1.75 min = 105 s. Four pieces of 250 m fly 100 s, but the pieces after the first also
    # fly the 90 m connector back (9 s) and a turn (8 s): 117 s > 105 s. Five pieces of 200 m:
    # 3 × 20 s + 9 s + 16 s = 85 s, then 85 + 17 = 102 s each — one battery per piece.
    result = run(
        SurveyParams(geometry=_centreline(1000.0), coverage="corridor", corridor_width_m=100.0, battery_minutes=1.75),
        AnalysisContext(),
    )
    assert len(result.blocks) == 5 and result.batteries == 5
    assert [b.minutes * 60.0 for b in result.blocks] == pytest.approx([85.0, 102.0, 102.0, 102.0, 102.0], abs=0.3)
    assert "line_exceeds_battery" not in {i.code for i in result.issues}


def test_corridor_spraying_shares_the_mix_by_line_length() -> None:
    # An L-shaped centreline (500 m east, 500 m north), 20 m wide, 5 m swath: 4 lines whose offsets
    # are longer outside the bend. About 2 ha × 20 L/ha = 40 L in a 5 L tank: the pieces are cut by
    # the tank, each carries its share of the mix by line length, and none needs more than one tank.
    centre = geometry_to_geojson(FRAME.to_wgs84(LineString([(0.0, 0.0), (500.0, 0.0), (500.0, 500.0)])))
    spray = SprayParams(swath_m=5.0, rate_l_per_ha=20.0, tank_l=5.0, flow_l_per_min=10.0, speed_ms=5.0)
    result = run(SurveyParams(geometry=centre, coverage="corridor", corridor_width_m=20.0, mode="spraying", spray=spray),
                 AnalysisContext())
    s = result.spray
    assert s is not None and result.line_count == 4
    assert s.litres == pytest.approx(result.area_ha * 20.0, abs=0.01)
    assert sum(b.litres or 0.0 for b in result.blocks) == pytest.approx(s.litres, abs=0.05)
    assert all((b.litres or 0.0) <= 5.0 * 1.001 for b in result.blocks)
    assert s.sorties == len(result.blocks) >= s.tank_loads


def test_corridor_needs_a_line() -> None:
    with pytest.raises(AnalysisError) as exc:
        run(SurveyParams(geometry=_rect(100, 100), coverage="corridor", corridor_width_m=50.0), AnalysisContext())
    assert exc.value.code == "geometry_type_not_supported"
    with pytest.raises(ValueError, match="corridor_width_m"):
        SurveyParams(geometry=_centreline(100.0), coverage="corridor")


def test_transit_legs_and_battery_reserve() -> None:
    # 900 × 450 m, lines east-west at y = −202.5 + 45k (k = 0..9), 900 m each, the first one
    # flown eastwards from (−450, −202.5). Take-off 1000 m south of the centre, at (0, −1000).
    takeoff = _lonlat(0.0, -1000.0)
    one = run(SurveyParams(geometry=_rect(900, 450), direction_deg=90.0, transit_legs=True, takeoff=takeoff),
              AnalysisContext())
    # In: hypot(450, 797.5) = 915.69 m; out from (−450, 202.5): hypot(450, 1202.5) = 1283.94 m.
    assert len(one.blocks) == 1
    assert one.blocks[0].transit_in_m == pytest.approx(915.7, abs=0.5)
    assert one.blocks[0].transit_out_m == pytest.approx(1283.9, abs=0.5)
    assert one.transit_km == pytest.approx(2.1996, abs=1e-3)
    # Lines 1012.5 s (900 + 40.5 + 72) + transit 219.96 s.
    assert one.flight_minutes * 60.0 == pytest.approx(1012.5 + 219.96, abs=1.0)
    assert one.transit_minutes == pytest.approx(219.96 / 60.0, abs=0.01)
    assert one.takeoff is not None and one.takeoff.source == "given"

    # 30 % reserve: 25 min × 0.7 = 1050 s usable. Each line 90 s, each later line +12.5 s (45 m
    # connector + 8 s turn). Lines 0–7: 91.57 + 90 + 7 × 102.5 + out(−450, 112.5)/10 = 120.01 →
    # 1019.07 s; adding line 8 would need 1125.8 s. Block 2: lines 8–9, in from (−450, 157.5)
    # 124.19 + 90 + 102.5 + out 128.39 = 445.08 s.
    reserve = run(
        SurveyParams(geometry=_rect(900, 450), direction_deg=90.0, transit_legs=True, takeoff=takeoff,
                     battery_reserve_pct=30.0),
        AnalysisContext(),
    )
    assert reserve.usable_battery_minutes == pytest.approx(17.5)
    assert [len(b.photos and [1] or []) for b in reserve.blocks]  # blocks exist
    assert [b.minutes * 60.0 for b in reserve.blocks] == pytest.approx([1019.07, 445.08], abs=1.0)
    assert reserve.batteries == 2
    assert reserve.total_minutes == pytest.approx(reserve.flight_minutes + 5.0, rel=1e-6)


def test_transit_that_uses_a_battery_is_flagged() -> None:
    far = _lonlat(0.0, -8000.0)
    result = run(
        SurveyParams(geometry=_rect(900, 450), transit_legs=True, takeoff=far, battery_minutes=20.0),
        AnalysisContext(),
    )
    assert "transit_exceeds_battery" in {i.code for i in result.issues}


def test_gcp_rule_of_thumb() -> None:
    assert gcp_estimate(area_ha=40.5, centreline_m=None, gcp_min=5, ha_per_extra=10.0, corridor_spacing_m=300.0).count == 9
    assert gcp_estimate(area_ha=9.9, centreline_m=None, gcp_min=5, ha_per_extra=10.0, corridor_spacing_m=300.0).count == 5
    corridor = gcp_estimate(area_ha=10.0, centreline_m=2500.0, gcp_min=5, ha_per_extra=10.0, corridor_spacing_m=300.0)
    assert corridor.count == 9 and corridor.basis == "corridor"  # floor(2500 / 300) + 1
    result = run(SurveyParams(geometry=_rect(900, 450), gcp_min=4, gcp_ha_per_extra=5.0), AnalysisContext())
    assert result.gcp is not None and result.gcp.count == 4 + 8  # 40.5 ha / 5 ha
    assert result.gcp.rule == "4 + 1 per 5 ha"


def test_storage_is_images_times_size() -> None:
    result = run(SurveyParams(geometry=_rect(900, 450), photo_mb=8.0), AnalysisContext())
    assert result.storage is not None
    assert result.storage.images == result.photos
    assert result.storage.total_gb == pytest.approx(result.photos * 8.0 / 1000.0, abs=1e-3)
    assert result.storage.source == "params"


def test_spray_flow_formula() -> None:
    # 20 L/ha over 5 m at 5 m/s (18 km/h): 20 × 18 × 5 / 600 = 3 L/min.
    assert spray_flow_l_per_min(20.0, 5.0, 5.0) == pytest.approx(3.0)
    assert spray_flow_l_per_min(20.0, 5.0, 5.0) == pytest.approx(20.0 * 18.0 * 5.0 / 600.0)


def test_spraying_effort_known_answer() -> None:
    # 200 × 100 m = 2 ha; swath 5 m → 20 lines of 200 m (4 km), 19 connectors of 5 m, 19 turns.
    # Spray time 4000 / 5 = 800 s; flight = 800 + 19 × 5 s + 95 m / 5 = 914 s.
    # Litres = 2 ha × 20 L/ha = 40 L → 4 tank loads of 10 L; 2 L per line → 5 lines per sortie.
    # Refills 3, each 5 min (the battery swap outlasts the 3 min refill): total 914 + 900 = 1814 s.
    # Field capacity 5 m × 5 m/s × 0.36 = 9 ha/h; effective 2 / (1814 / 3600) = 3.969 ha/h.
    spray = SprayParams(swath_m=5.0, rate_l_per_ha=20.0, tank_l=10.0, flow_l_per_min=4.0, speed_ms=5.0,
                        turn_seconds=5.0, refill_minutes=3.0, height_m=3.0)
    result = run(SurveyParams(geometry=_rect(200, 100), mode="spraying", spray=spray, battery_minutes=10.0),
                 AnalysisContext())
    s = result.spray
    assert s is not None and result.mode == "spraying"
    assert result.camera is None and result.gsd_cm is None and result.photos == 0
    assert result.line_count == 20 and result.line_km == pytest.approx(4.0, rel=2e-3)
    assert result.flight_minutes * 60.0 == pytest.approx(914.0, abs=1.0)
    assert s.litres == pytest.approx(40.0, rel=2e-3)
    assert s.tank_loads == 4 and s.sorties == 4 and s.refills == 3
    assert [b.litres for b in result.blocks] == pytest.approx([10.0] * 4, abs=0.05)
    assert result.total_minutes * 60.0 == pytest.approx(1814.0, abs=1.0)
    assert s.flow_l_per_min == pytest.approx(3.0) and s.flow_limited is False
    assert s.theoretical_ha_per_hour == pytest.approx(9.0)
    assert s.effective_ha_per_hour == pytest.approx(2.0 / (1814.0 / 3600.0), rel=2e-3)
    assert result.ha_per_hour == pytest.approx(s.effective_ha_per_hour, rel=1e-6)
    assert s.spray_minutes == pytest.approx(800.0 / 60.0, rel=2e-3)
    assert all(f["properties"]["visual_only"] is True for f in result.layers["lines"]["features"])
    assert "waypoint" not in result.model_dump_json().lower()


def test_spraying_slows_down_to_the_pump_limit() -> None:
    # 2 L/min at 20 L/ha over 5 m allows 2 × 10 000 / (20 × 5 × 60) = 3.33 m/s.
    spray = SprayParams(swath_m=5.0, rate_l_per_ha=20.0, flow_l_per_min=2.0, speed_ms=5.0, height_m=8.0)
    result = run(SurveyParams(geometry=_rect(200, 100), mode="spraying", spray=spray), AnalysisContext())
    assert result.spray is not None and result.spray.flow_limited is True
    assert result.spray.speed_ms == pytest.approx(10.0 / 3.0, abs=1e-3)
    codes = {i.code for i in result.issues}
    assert {"spray_flow_limited", "spray_height_outside_band"} <= codes


def test_spraying_resumes_where_the_tank_runs_out() -> None:
    # 1000 × 100 m, east-west lines: 20 lines of 1000 m (5 m swath), 0.5 ha each. At 30 L/ha a
    # line needs 15 L but the tank holds 10 L: a sortie stops mid-line and the next resumes there
    # (breakpoint resume), so the sorties are the 30 tank loads (300 L / 10 L), not 2 per line.
    # Flight = 20 × 200 s + 19 × (5 m / 5 m/s + 5 s) = 4114 s; 29 stops of max(3, 5) min = 8700 s.
    spray = SprayParams(swath_m=5.0, rate_l_per_ha=30.0, tank_l=10.0, flow_l_per_min=10.0, speed_ms=5.0,
                        turn_seconds=5.0, refill_minutes=3.0)
    result = run(SurveyParams(geometry=_rect(1000, 100), mode="spraying", spray=spray, direction_deg=90.0),
                 AnalysisContext())
    s = result.spray
    assert s is not None and s.litres == pytest.approx(300.0, rel=2e-3)
    assert s.tank_loads == 30 and s.sorties == 30 and s.refills == 29
    assert result.batteries == 30 and len(result.blocks) == 30
    assert all(b.litres == pytest.approx(10.0, abs=0.05) for b in result.blocks)
    assert result.flight_minutes * 60.0 == pytest.approx(4114.0, abs=1.0)
    assert result.total_minutes * 60.0 == pytest.approx(4114.0 + 29 * 300.0, abs=1.0)
    # The sorties' areas tile the field: partial lines end at the break points.
    assert sum(b.area_ha for b in result.blocks) == pytest.approx(result.area_ha, rel=2e-3)
    issue = next(i for i in result.issues if i.code == "spray_line_resumed")
    assert issue.message.startswith("20 spray line(s)")


def test_spraying_resumes_where_the_battery_runs_out() -> None:
    # The same field with a 2-minute battery and a large tank: every sortie flies its 120 s (lines
    # are cut where the battery runs out) except the last, so ceil(4114 / 120) = 35 sorties.
    spray = SprayParams(swath_m=5.0, rate_l_per_ha=20.0, tank_l=500.0, flow_l_per_min=10.0, speed_ms=5.0,
                        turn_seconds=5.0)
    result = run(SurveyParams(geometry=_rect(1000, 100), mode="spraying", spray=spray, direction_deg=90.0,
                              battery_minutes=2.0), AnalysisContext())
    assert result.spray is not None and result.spray.sorties == 35
    assert max(b.minutes for b in result.blocks) * 60.0 == pytest.approx(120.0, abs=0.01)
    assert result.flight_minutes * 60.0 == pytest.approx(4114.0, abs=1.0)
    assert sum(b.area_ha for b in result.blocks) == pytest.approx(result.area_ha, rel=2e-3)


def test_spraying_is_planned_above_the_crop() -> None:
    with pytest.raises(ValueError, match="height_reference 'agl'"):
        SurveyParams(geometry=_rect(200, 100), mode="spraying", height_reference="takeoff")


def test_thermal_presets_follow_pixel_pitch_and_focal_length() -> None:
    # H20T thermal: 640 × 512 at 12 µm behind 13.5 mm. GSD = H × pitch / f = 100 m × 12 µm / 13.5 mm
    # = 8.889 cm; footprint 640 × 8.889 cm = 56.9 m across. The DFOV from these numbers, 40.0°,
    # matches DJI's published 40.6°.
    h20t = CAMERA_PRESETS["h20t_thermal"]
    assert h20t.kind == "thermal" and (h20t.image_width_px, h20t.image_height_px) == (640, 512)
    assert h20t.sensor_width_mm == pytest.approx(640 * 0.012)
    cam = camera_math(h20t, 100.0)
    assert cam["gsd_cm"] == pytest.approx(100.0 * 0.012 / 13.5 * 100.0)
    assert cam["footprint_across_m"] == pytest.approx(640 * cam["gsd_cm"] / 100.0)
    dfov = 2.0 * math.degrees(math.atan(math.hypot(h20t.sensor_width_mm, h20t.sensor_height_mm) / 2.0 / h20t.focal_length_mm))
    assert dfov == pytest.approx(40.6, abs=1.0)
    # XT2 640 13 mm: 17 µm pitch → 100 × 17 / 13 = 13.08 cm.
    assert camera_math(CAMERA_PRESETS["xt2_640_13"], 100.0)["gsd_cm"] == pytest.approx(100.0 * 0.017 / 13.0 * 100.0)


def test_multispectral_presets_match_the_published_gsd() -> None:
    # DJI publishes GSD = H/18.9 cm/px (P4 Multispectral) and H/21.6 cm/px (Mavic 3M band camera).
    assert camera_math(CAMERA_PRESETS["p4m"], 100.0)["gsd_cm"] == pytest.approx(100.0 / 18.9, rel=2e-4)
    assert camera_math(CAMERA_PRESETS["m3m"], 100.0)["gsd_cm"] == pytest.approx(100.0 / 21.6, rel=2e-4)
    assert CAMERA_PRESETS["p4m"].images_per_capture == 6 and CAMERA_PRESETS["m3m"].images_per_capture == 5
    for key, spec in CAMERA_PRESETS.items():
        assert spec.photo_mb is not None and spec.reference, key


def test_storage_counts_every_band_of_a_multispectral_capture() -> None:
    result = run(SurveyParams(geometry=_rect(600, 300), camera="p4m", altitude_agl_m=60.0), AnalysisContext())
    assert result.storage is not None and result.storage.source == "camera"
    assert result.storage.images == result.photos * 6
    assert result.storage.total_gb == pytest.approx(result.photos * 6 * 4.2 / 1000.0, abs=1e-3)
    # The default RGB preset gives an estimate from its typical JPEG size.
    rgb = run(SurveyParams(geometry=_rect(600, 300)), AnalysisContext())
    assert rgb.storage is not None and rgb.storage.mb_per_image == 8.0
    assert rgb.storage.total_gb == pytest.approx(rgb.photos * 8.0 / 1000.0, abs=1e-3)
