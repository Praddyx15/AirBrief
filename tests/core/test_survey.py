"""Survey effort estimator: camera maths, line layout, batteries and blocks."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError
from shapely.geometry import box, shape
from shapely.ops import unary_union

from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.survey import altitude_for_gsd, camera_math, run
from airbrief_core.errors import AnalysisError
from airbrief_core.geo.frame import LocalFrame
from airbrief_core.geo.geodesic import geodesic_area_perimeter
from airbrief_core.geo.transform import geometry_to_geojson
from airbrief_core.models.analyses import SurveyParams
from airbrief_core.models.presets import CAMERA_PRESETS, CameraSpec

P4RTK = CAMERA_PRESETS["p4rtk"]
ORIGIN = (77.49, 12.905)


def _rect(width_m: float, height_m: float) -> dict:
    """A width (east-west) × height (north-south) rectangle centred on ORIGIN, as GeoJSON."""
    frame = LocalFrame(*ORIGIN)
    local = box(-width_m / 2, -height_m / 2, width_m / 2, height_m / 2)
    return geometry_to_geojson(frame.to_wgs84(local))


def test_p4rtk_worked_example() -> None:
    cam = camera_math(P4RTK, 100.0)
    assert cam["gsd_cm"] == pytest.approx(2.74, abs=0.005)
    assert cam["footprint_across_m"] == pytest.approx(150.0)
    assert cam["footprint_along_m"] == pytest.approx(100.0)
    assert cam["line_spacing_m"] == pytest.approx(45.0)
    assert cam["trigger_distance_m"] == pytest.approx(20.0)
    assert cam["square_pixels"] is True


def test_portrait_swaps_axes() -> None:
    cam = camera_math(P4RTK, 100.0, orientation="portrait")
    assert cam["footprint_across_m"] == pytest.approx(100.0)
    assert cam["footprint_along_m"] == pytest.approx(150.0)
    assert cam["line_spacing_m"] == pytest.approx(30.0)
    assert cam["trigger_distance_m"] == pytest.approx(30.0)
    assert cam["gsd_cm"] == pytest.approx(2.74, abs=0.01)


def test_target_gsd_inverse() -> None:
    altitude = altitude_for_gsd(P4RTK, 2.0)
    assert altitude == pytest.approx(2.0 / 100.0 * 8.8 * 5472 / 13.2)
    assert camera_math(P4RTK, altitude)["gsd_cm"] == pytest.approx(2.0)

    result = run(SurveyParams(geometry=_rect(600, 300), target_gsd_cm=2.0), AnalysisContext())
    assert result.altitude_agl_m == pytest.approx(altitude, abs=1e-3)
    assert result.gsd_cm == pytest.approx(2.0, abs=1e-3)
    assert any(i.code == "altitude_from_gsd" for i in result.issues)


def test_both_height_modes_rejected() -> None:
    with pytest.raises(ValidationError):
        SurveyParams(geometry=_rect(100, 100), altitude_agl_m=100.0, target_gsd_cm=2.0)


def test_default_altitude_is_100m() -> None:
    params = SurveyParams(geometry=_rect(100, 100))
    assert params.altitude_agl_m == 100.0


def test_rectangle_layout_known_answer() -> None:
    result = run(SurveyParams(geometry=_rect(900, 450), camera="p4rtk", altitude_agl_m=100.0), AnalysisContext())
    assert result.direction_deg == pytest.approx(90.0, abs=0.5)
    assert result.line_spacing_m == pytest.approx(45.0)
    assert result.trigger_distance_m == pytest.approx(20.0)
    assert result.trigger_interval_s == pytest.approx(2.0)
    assert result.line_count == 10
    assert result.segment_count == 10
    assert result.line_km == pytest.approx(9.0, rel=2e-3)
    # 9 km at 10 m/s + 9 connectors of 45 m + 9 turns of 8 s.
    expected_s = 9000 / 10 + 9 * 45 / 10 + 9 * 8
    assert result.flight_minutes == pytest.approx(expected_s / 60.0, rel=2e-3)
    assert result.batteries == 1
    assert result.total_minutes == pytest.approx(result.flight_minutes, rel=1e-6)
    assert 440 <= result.photos <= 460
    assert result.area_ha == pytest.approx(40.5, rel=2e-3)
    assert result.ha_per_hour == pytest.approx(result.area_ha / (result.total_minutes / 60.0), rel=1e-3)
    assert set(result.layers) == {"input", "blocks", "lines"}


def test_user_direction() -> None:
    result = run(SurveyParams(geometry=_rect(900, 450), direction_deg=0.0), AnalysisContext())
    assert result.direction_deg == pytest.approx(0.0)
    # North-south lines across a 900 m wide area: 900 / 45 = 20 lines of 450 m.
    assert result.line_count == 20
    assert result.line_km == pytest.approx(9.0, rel=2e-3)


def test_batteries_and_blocks_cover_area() -> None:
    params = SurveyParams(geometry=_rect(2000, 1500), battery_minutes=10.0, battery_swap_minutes=4.0)
    result = run(params, AnalysisContext())
    assert result.batteries >= math.ceil(result.flight_minutes / 10.0 - 1e-6)
    assert result.batteries > 1
    # No line outlasts a battery here: one battery per block, one swap between blocks.
    assert result.batteries == len(result.blocks)
    assert result.total_minutes == pytest.approx(result.flight_minutes + (result.batteries - 1) * 4.0, rel=1e-4)
    assert [b.index for b in result.blocks] == list(range(1, len(result.blocks) + 1))
    for block in result.blocks:
        assert block.minutes <= 10.0 + 1e-6
    assert sum(b.minutes for b in result.blocks) == pytest.approx(result.flight_minutes, rel=1e-3)
    assert sum(b.photos for b in result.blocks) == result.photos

    union = unary_union([shape(b.polygon) for b in result.blocks])
    area_m2, _ = geodesic_area_perimeter(union)
    assert area_m2 / 1e4 == pytest.approx(result.area_ha, rel=5e-3)
    assert sum(b.area_ha for b in result.blocks) == pytest.approx(result.area_ha, rel=5e-3)
    assert len(result.layers["blocks"]["features"]) == len(result.blocks)


def test_whole_line_packing_sets_the_battery_count() -> None:
    # Three 3 km east-west lines of about 0.6 battery each: ceil(flight/battery) would say 2, but
    # two whole lines never fit in one battery, so three blocks (and batteries) are flown.
    params = SurveyParams(
        geometry=_rect(3000, 135), direction_deg=90.0, battery_minutes=500.0 / 60.0, battery_swap_minutes=5.0
    )
    result = run(params, AnalysisContext())
    assert result.line_count == 3
    assert math.ceil(result.flight_minutes / params.battery_minutes - 1e-9) == 2
    assert len(result.blocks) == 3
    assert result.batteries == 3
    assert [b.index for b in result.blocks] == [1, 2, 3]
    assert result.total_minutes == pytest.approx(result.flight_minutes + 2 * 5.0, rel=1e-6)
    assert result.ha_per_hour == pytest.approx(result.area_ha / (result.total_minutes / 60.0), rel=1e-3)
    assert [f["properties"]["block"] for f in result.layers["blocks"]["features"]] == [1, 2, 3]
    line_blocks = {f["properties"]["block"] for f in result.layers["lines"]["features"] if f["properties"]["kind"] == "line"}
    assert line_blocks == {1, 2, 3}


def test_line_longer_than_a_battery_counts_every_battery() -> None:
    # Each 3 km line takes about 5 min at 10 m/s: with 4-minute batteries every block needs two.
    params = SurveyParams(geometry=_rect(3000, 135), direction_deg=90.0, battery_minutes=4.0, battery_swap_minutes=5.0)
    result = run(params, AnalysisContext())
    assert len(result.blocks) == 3
    assert result.batteries == 6
    assert result.total_minutes == pytest.approx(result.flight_minutes + 5 * 5.0, rel=1e-6)
    assert "line_exceeds_battery" in {i.code for i in result.issues}


def test_height_limit_is_reported_with_the_result() -> None:
    result = run(SurveyParams(geometry=_rect(600, 300), altitude_agl_m=150.0, max_altitude_agl_m=140.0), AnalysisContext())
    assert result.max_altitude_agl_m == pytest.approx(140.0)
    assert "altitude_above_limit" in {i.code for i in result.issues}


def test_lines_are_visual_only_and_no_waypoints() -> None:
    result = run(SurveyParams(geometry=_rect(600, 300)), AnalysisContext())
    features = result.layers["lines"]["features"]
    assert features
    assert all(f["properties"]["visual_only"] is True for f in features)
    kinds = {f["properties"]["kind"] for f in features}
    assert kinds <= {"line", "connector"}
    dumped = result.model_dump_json().lower()
    assert "waypoint" not in dumped


def test_concave_area_gives_multiple_segments() -> None:
    frame = LocalFrame(*ORIGIN)
    u_shape = box(-300, -300, 300, 300).difference(box(-100, -50, 100, 400))
    geom = geometry_to_geojson(frame.to_wgs84(u_shape))
    result = run(SurveyParams(geometry=geom, direction_deg=90.0), AnalysisContext())
    assert result.segment_count > result.line_count


def test_issues_altitude_trigger_non_square() -> None:
    result = run(SurveyParams(geometry=_rect(600, 300), altitude_agl_m=150.0, speed_ms=15.0), AnalysisContext())
    codes = {i.code for i in result.issues}
    assert "altitude_above_limit" in codes
    # Trigger 30 m at 15 m/s = 2.0 s: not below the minimum.
    assert "trigger_interval_too_short" not in codes

    fast = run(SurveyParams(geometry=_rect(600, 300), speed_ms=15.0), AnalysisContext())
    trigger = next(i for i in fast.issues if i.code == "trigger_interval_too_short")
    assert "10.0 m/s" in trigger.message

    odd = CameraSpec(
        name="Odd pixels",
        sensor_width_mm=13.2,
        sensor_height_mm=8.8,
        focal_length_mm=8.8,
        image_width_px=5472,
        image_height_px=3000,
    )
    result = run(SurveyParams(geometry=_rect(600, 300), camera=odd), AnalysisContext())
    assert "non_square_pixels" in {i.code for i in result.issues}
    assert result.gsd_along_cm > result.gsd_cm


def test_non_polygon_rejected() -> None:
    params = SurveyParams(geometry={"type": "LineString", "coordinates": [[77.49, 12.90], [77.50, 12.91]]})
    with pytest.raises(AnalysisError) as exc:
        run(params, AnalysisContext())
    assert exc.value.code == "geometry_type_not_supported"


def test_unknown_camera_rejected() -> None:
    with pytest.raises(ValidationError):
        SurveyParams(geometry=_rect(100, 100), camera="no-such-camera")
