"""Tests for airspace zone classification (airbrief_core.analyses.zones)."""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import shapely
from shapely.errors import GEOSException
from shapely.geometry import LineString, Point, box, mapping, shape

from airbrief_core.analyses.airspace import run as airspace_run
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.zones import (
    ZoneState,
    band_evidence,
    classify_points,
    default_state,
    most_restrictive,
    nearest_aerodromes,
    ring_polygons,
    zone_active,
    zone_hits_by_activity,
    zones_for_geometry,
)
from airbrief_core.data import DataPack
from airbrief_core.data.datapack import DataPackManifest, LayerEntry, write_manifest
from airbrief_core.errors import AnalysisError
from airbrief_core.geo.geodesic import GEOD, geodesic_area_perimeter
from airbrief_core.models.analyses import AirspaceParams
from airbrief_core.models.common import BBox, UserZone, Verdict, ZoneSchedule
from airbrief_core.models.presets import DEFAULT_ZONE_RULES, DGCA_2021_ZONE_RULES

RULES = DEFAULT_ZONE_RULES
VOBL = (77.7066, 13.1986)
VOBG = (77.6682, 12.9499)
RED_ZONE = box(77.5840, 12.9735, 77.5975, 12.9855)
SURVEY = box(77.48, 12.90, 77.50, 12.91)


def _east_of(origin: tuple[float, float], distance_m: float) -> tuple[float, float]:
    lon, lat, _ = GEOD.fwd(origin[0], origin[1], 90.0, distance_m)
    return float(lon), float(lat)


def _state(zone: str, ceiling: float | None, permission: bool, name: str) -> ZoneState:
    return ZoneState(zone=zone, ceiling_agl_m=ceiling, permission_required=permission,  # type: ignore[arg-type]
                     label=name, source="user", name=name)


def test_most_restrictive_precedence() -> None:
    red = _state("red", None, True, "red")
    permit = _state("yellow", None, True, "permit")
    yellow60 = _state("yellow", 60.0, False, "yellow60")
    green120 = _state("green", 120.0, False, "green120")
    green50 = _state("green", 50.0, False, "green50")
    green60 = _state("green", 60.0, False, "green60")

    assert most_restrictive([green120, yellow60, permit, red]).name == "red"
    assert most_restrictive([green120, yellow60, permit]).name == "permit"
    # A lower ceiling beats the colour of the zone.
    assert most_restrictive([green120, yellow60, green50]).name == "green50"
    # Equal ceilings: yellow wins over green.
    assert most_restrictive([green60, yellow60]).name == "yellow60"
    assert most_restrictive([green120]).name == "green120"
    with pytest.raises(AnalysisError) as exc:
        most_restrictive([])
    assert exc.value.code == "invalid_params"


def test_default_state_uses_rules() -> None:
    state = default_state(RULES)
    assert state.zone == "green"
    assert state.ceiling_agl_m == pytest.approx(120.0)
    assert state.permission_required is False
    assert state.source == "default"


def test_distance_to_perimeter_rings_large_airport(synthetic_pack: DataPack) -> None:
    # VOBL is a large airport: assumed radius 2 km, rings 0–8 km (permission) and 8–12 km (60 m).
    distances = [0.0, 1_000.0, 9_900.0, 10_100.0, 13_900.0, 14_100.0]
    pts = [_east_of(VOBL, d) for d in distances]
    lons = np.array([p[0] for p in pts])
    lats = np.array([p[1] for p in pts])
    states = classify_points(lons, lats, pack=synthetic_pack, rules=RULES)
    assert len(states) == len(distances)

    for state in states[:3]:  # perimeter distance 0, 0 (inside assumed radius), 7.9 km
        assert state.source == "aerodrome"
        assert state.ident == "VOBL"
        assert state.zone == "yellow"
        assert state.permission_required is True
        assert state.ceiling_agl_m is None
    for state in states[3:5]:  # perimeter 8.1 km and 11.9 km
        assert state.ident == "VOBL"
        assert state.permission_required is False
        assert state.ceiling_agl_m == pytest.approx(60.0)
    outside = states[5]  # perimeter 12.1 km
    assert outside.source == "default"
    assert outside.zone == "green"
    assert outside.ceiling_agl_m == pytest.approx(120.0)


def test_distance_to_perimeter_medium_airport(synthetic_pack: DataPack) -> None:
    # VOBG is medium: assumed radius 1 km, so 8.9 km from the reference point is 7.9 km from the perimeter.
    inner = _east_of(VOBG, 8_900.0)
    outer = _east_of(VOBG, 9_100.0)
    states = classify_points(np.array([inner[0], outer[0]]), np.array([inner[1], outer[1]]),
                             pack=synthetic_pack, rules=RULES)
    assert states[0].ident == "VOBG" and states[0].permission_required
    assert states[1].ident == "VOBG" and states[1].ceiling_agl_m == pytest.approx(60.0)


def test_classify_without_pack_and_non_finite() -> None:
    states = classify_points(np.array([77.5, np.nan]), np.array([12.9, 12.9]), pack=None, rules=RULES)
    assert [s.source for s in states] == ["default", "default"]
    assert classify_points(np.array([]), np.array([]), pack=None, rules=RULES) == []
    with pytest.raises(AnalysisError) as exc:
        classify_points(np.array([1.0, 2.0]), np.array([1.0]), pack=None, rules=RULES)
    assert exc.value.code == "invalid_params"


def test_datapack_red_zone_contains_point(synthetic_pack: DataPack) -> None:
    lons = np.array([77.590, 77.5840])  # inside, and exactly on the west edge
    lats = np.array([12.980, 12.9800])
    states = classify_points(lons, lats, pack=synthetic_pack, rules=RULES)
    assert all(s.zone == "red" and s.source == "datapack" for s in states)
    without = classify_points(lons, lats, pack=synthetic_pack, rules=RULES, include_datapack_zones=False)
    assert all(s.zone != "red" for s in without)


def test_user_zones_override_default(synthetic_pack: DataPack) -> None:
    centre = SURVEY.centroid
    lons, lats = np.array([centre.x]), np.array([centre.y])
    assert classify_points(lons, lats, pack=synthetic_pack, rules=RULES)[0].source == "default"

    yellow = UserZone(name="Low ceiling", zone="yellow", geometry=mapping(SURVEY), ceiling_agl_m=40.0)
    state = classify_points(lons, lats, pack=synthetic_pack, rules=RULES, user_zones=[yellow])[0]
    assert state.source == "user" and state.ceiling_agl_m == pytest.approx(40.0)

    # A green zone with a higher ceiling replaces the default inside it.
    green = UserZone(name="High ceiling", zone="green", geometry=mapping(SURVEY), ceiling_agl_m=200.0)
    state = classify_points(lons, lats, pack=synthetic_pack, rules=RULES, user_zones=[green])[0]
    assert state.ceiling_agl_m == pytest.approx(200.0)

    # Overlapping zones: the most restrictive wins.
    both = classify_points(lons, lats, pack=synthetic_pack, rules=RULES, user_zones=[green, yellow])[0]
    assert both.name == "Low ceiling"

    red = UserZone(name="No fly", zone="red", geometry=mapping(SURVEY))
    state = classify_points(lons, lats, pack=None, rules=RULES, user_zones=[green, red])[0]
    assert state.zone == "red" and state.permission_required


def test_user_zone_must_be_polygon() -> None:
    bad = UserZone(name="Line", zone="red", geometry={"type": "LineString", "coordinates": [[77.0, 12.0], [77.1, 12.1]]})
    with pytest.raises(AnalysisError) as exc:
        classify_points(np.array([77.0]), np.array([12.0]), pack=None, rules=RULES, user_zones=[bad])
    assert exc.value.code == "invalid_geometry"


def test_zones_for_geometry_red_zone_overlap(synthetic_pack: DataPack) -> None:
    area = box(77.590, 12.975, 77.600, 12.985)  # east part overlaps the demo red zone
    hits = zones_for_geometry(area, pack=synthetic_pack, rules=RULES)
    assert hits, "expected at least the red zone"
    first = hits[0]
    assert first.zone == "red"
    assert first.source == "datapack"
    expected_area, _ = geodesic_area_perimeter(area.intersection(RED_ZONE))
    assert first.overlap_area_m2 == pytest.approx(expected_area, rel=1e-3)
    assert first.overlap_length_m is None


def test_zones_for_geometry_line_through_vobl_rings(synthetic_pack: DataPack) -> None:
    end = _east_of(VOBL, 20_000.0)
    line = LineString([VOBL, end])
    hits = [h for h in zones_for_geometry(line, pack=synthetic_pack, rules=RULES) if h.ident == "VOBL"]
    assert len(hits) == 2
    permit, limited = hits  # sorted most restrictive first
    assert permit.permission_required and permit.ceiling_agl_m is None
    assert limited.ceiling_agl_m == pytest.approx(60.0)
    # Radii from the reference point: 2 + 8 = 10 km and 2 + 12 = 14 km.
    assert permit.overlap_length_m == pytest.approx(10_000.0, abs=50.0)
    assert limited.overlap_length_m == pytest.approx(4_000.0, abs=50.0)
    assert permit.distance_km == pytest.approx(0.0)
    assert permit.first_index == 0
    assert permit.ring is not None and permit.ring.startswith("0–8 km")


def test_zones_for_geometry_far_away_is_empty(synthetic_pack: DataPack) -> None:
    assert zones_for_geometry(box(10.0, 10.0, 10.01, 10.01), pack=synthetic_pack, rules=RULES) == []
    assert zones_for_geometry(SURVEY, pack=None, rules=RULES) == []


def test_ring_polygons_feature_collection(synthetic_pack: DataPack) -> None:
    fc = ring_polygons(synthetic_pack, RULES)
    assert fc["type"] == "FeatureCollection"
    # VOBL 2 rings, VOBG 2, VOYK 2, VOJK (small) 1.
    assert len(fc["features"]) == 7
    idents = {f["properties"]["ident"] for f in fc["features"]}
    assert idents == {"VOBL", "VOBG", "VOYK", "VOJK"}
    for feature in fc["features"]:
        props = feature["properties"]
        for key in ("name", "ident", "zone", "ring", "label", "ceiling_agl_m", "permission_required"):
            assert key in props
        assert feature["geometry"]["type"] in ("Polygon", "MultiPolygon")
    far = ring_polygons(synthetic_pack, RULES, BBox(min_lon=0.0, min_lat=0.0, max_lon=1.0, max_lat=1.0))
    assert far["features"] == []
    assert ring_polygons(None, RULES)["features"] == []


def test_nearest_aerodromes(synthetic_pack: DataPack) -> None:
    near = nearest_aerodromes(Point(VOBG), synthetic_pack, k=2)
    assert len(near) == 2
    assert near[0].ident == "VOBG"
    assert near[0].distance_km == pytest.approx(0.0, abs=1e-3)
    assert near[1].distance_km > near[0].distance_km
    assert 0.0 <= near[1].bearing_deg < 360.0
    assert nearest_aerodromes(Point(VOBG), None) == []


# The synthetic VOBL outline is 4.8 km north-south (2.4 km each side of the reference point)
# with runways ending 2.0 km north and south of it.

DGCA = DGCA_2021_ZONE_RULES


def _north_of(origin: tuple[float, float], distance_m: float) -> tuple[float, float]:
    lon, lat, _ = GEOD.fwd(origin[0], origin[1], 0.0, distance_m)
    return float(lon), float(lat)


def _at(point: tuple[float, float], **kwargs: Any) -> ZoneState:
    return classify_points(np.array([point[0]]), np.array([point[1]]), **kwargs)[0]


def test_bands_are_measured_from_the_perimeter(synthetic_pack: DataPack) -> None:
    # 11 km north of the reference point is 9 km past the runway end and 8.6 km past the boundary.
    point = _north_of(VOBL, 11_000.0)
    state = _at(point, pack=synthetic_pack, rules=DGCA, agl=50.0)
    assert state.source == "aerodrome" and state.ident == "VOBL"
    assert state.label.startswith("8–12 km") and state.distance_from == "perimeter"
    (hit,) = [h for h in zones_for_geometry(Point(point), pack=synthetic_pack, rules=DGCA) if h.ident == "VOBL"]
    assert hit.distance_km == pytest.approx(8.6, abs=0.01)
    assert hit.distance_from == "perimeter"

    # 14.2 km out: 11.8 km from the boundary is still in the band, but 12.2 km from the reference
    # point minus its assumed 2 km radius is not.
    far = _north_of(VOBL, 14_200.0)
    assert _at(far, pack=synthetic_pack, rules=DGCA, agl=50.0).label.startswith("8–12 km")
    assert _at(far, pack=synthetic_pack, rules=RULES).source == "default"


def test_five_to_eight_km_is_yellow_from_the_ground(synthetic_pack: DataPack) -> None:
    state = _at(_north_of(VOBL, 9_900.0), pack=synthetic_pack, rules=DGCA, agl=10.0)  # 7.5 km out
    assert state.zone == "yellow" and state.permission_required and state.ceiling_agl_m is None
    assert state.label.startswith("5–8 km") and state.basis == "rule"


def test_eight_to_twelve_km_green_to_60_m_yellow_above(synthetic_pack: DataPack) -> None:
    point = _north_of(VOBL, 12_400.0)  # 10 km from the boundary
    low = _at(point, pack=synthetic_pack, rules=DGCA, agl=59.0)
    assert low.zone == "green" and low.ceiling_agl_m == pytest.approx(60.0) and not low.permission_required
    high = _at(point, pack=synthetic_pack, rules=DGCA, agl=61.0)
    assert high.zone == "yellow" and high.permission_required and high.ceiling_agl_m is None
    assert high.rule_ceiling_agl_m == pytest.approx(60.0)

    (hit,) = zones_for_geometry(Point(point), pack=synthetic_pack, rules=DGCA, altitude_agl_m=61.0)
    assert hit.zone == "yellow" and hit.permission_required and hit.agl_margin_m == pytest.approx(-1.0)
    (hit,) = zones_for_geometry(Point(point), pack=synthetic_pack, rules=DGCA, altitude_agl_m=59.0)
    assert hit.zone == "green" and hit.agl_margin_m == pytest.approx(1.0)


def test_zero_to_five_km_needs_verification_and_permission(synthetic_pack: DataPack) -> None:
    state = _at(_north_of(VOBL, 3_000.0), pack=synthetic_pack, rules=DGCA, agl=10.0)
    assert state.basis == "verify_official_map" and state.permission_required
    params = AirspaceParams(geometry=mapping(Point(_north_of(VOBL, 3_000.0))), altitude_agl_m=10.0, grid=False)
    result = airspace_run(params, AnalysisContext(datapack=synthetic_pack))
    assert result.verdict == Verdict.CAUTION
    assert result.zones[0].basis == "verify_official_map" and result.zones[0].permission_required
    assert any(b.basis == "verify_official_map" for b in result.band_evidence)
    assert "verify on Digital Sky" in result.summary
    assert result.assessment_banner is not None and "not published as open data" in result.assessment_banner


def test_default_rules_above_120_m(synthetic_pack: DataPack) -> None:
    centre = (SURVEY.centroid.x, SURVEY.centroid.y)
    assert _at(centre, pack=synthetic_pack, rules=DGCA, agl=120.0).ceiling_agl_m == pytest.approx(120.0)
    above = _at(centre, pack=synthetic_pack, rules=DGCA, agl=150.0)
    assert above.zone == "yellow" and above.permission_required
    # the conservative rules keep a plain 120 m ceiling
    assert _at(centre, pack=synthetic_pack, rules=RULES, agl=150.0).ceiling_agl_m == pytest.approx(120.0)


def test_yellow_above_120_m_is_for_open_airspace_only(synthetic_pack: DataPack) -> None:
    green = UserZone(name="High ceiling", zone="green", geometry=mapping(SURVEY), ceiling_agl_m=200.0)
    inside, outside = classify_points(np.array([SURVEY.centroid.x, 77.51]), np.array([SURVEY.centroid.y, 12.905]),
                                      pack=synthetic_pack, rules=DGCA, user_zones=[green], agl=150.0)
    assert inside.name == "High ceiling" and inside.zone == "green" and not inside.permission_required
    assert inside.ceiling_agl_m == pytest.approx(200.0)
    assert outside.source == "default" and outside.zone == "yellow" and outside.permission_required

    params = AirspaceParams(geometry=mapping(SURVEY.centroid), altitude_agl_m=150.0, grid=False, user_zones=[green])
    result = airspace_run(params, AnalysisContext(datapack=synthetic_pack))
    assert not result.permission_required and result.effective_ceiling_agl_m == pytest.approx(200.0)
    assert result.verdict == Verdict.PASS


def test_a_zone_without_its_own_ceiling_is_yellow_above_120_m(synthetic_pack: DataPack) -> None:
    # it takes the open-airspace ceiling, so it takes the yellow above it too
    green = UserZone(name="Plain green", zone="green", geometry=mapping(SURVEY))
    state = _at((SURVEY.centroid.x, SURVEY.centroid.y), pack=synthetic_pack, rules=DGCA, user_zones=[green], agl=150.0)
    assert state.zone == "yellow" and state.permission_required and state.source == "default"
    low = _at((SURVEY.centroid.x, SURVEY.centroid.y), pack=synthetic_pack, rules=DGCA, user_zones=[green], agl=100.0)
    assert low.name == "Plain green" and low.ceiling_agl_m == pytest.approx(120.0)


def test_non_operational_aerodrome_has_no_bands(synthetic_pack: DataPack) -> None:
    point = _north_of(VOBL, 9_900.0)
    assert _at(point, pack=synthetic_pack, rules=DGCA).ident == "VOBL"
    off = {"vobl": False}
    assert _at(point, pack=synthetic_pack, rules=DGCA, operational=off).source == "default"
    rings = ring_polygons(synthetic_pack, DGCA, operational=off)["features"]
    assert "VOBL" not in {f["properties"]["ident"] for f in rings}
    near = nearest_aerodromes(Point(point), synthetic_pack, k=4, rules=DGCA, operational=off)
    assert {a.ident: a.operational for a in near}["VOBL"] is False


def test_small_airports_need_scheduled_service_unless_set(synthetic_pack: DataPack) -> None:
    vojk = Point(_north_of((77.5975, 13.0781), 600.0))  # small, no scheduled service, a runway but no boundary
    assert "VOJK" not in {h.ident for h in zones_for_geometry(vojk, pack=synthetic_pack, rules=DGCA)}
    hits = zones_for_geometry(vojk, pack=synthetic_pack, rules=DGCA, operational={"VOJK": True})
    (hit,) = [h for h in hits if h.ident == "VOJK"]
    assert hit.distance_from == "runway_hull" and hit.basis == "verify_official_map"
    # beside the 800 m runway on 060°: 600 m · sin 60° from its centre line, less half its 18 m width
    assert hit.distance_km == pytest.approx((600.0 * math.sin(math.radians(60.0)) - 9.0) / 1000.0, abs=0.002)


def test_bands_hug_the_runways(synthetic_pack: DataPack) -> None:
    rings = {f["properties"]["ring"]: f for f in ring_polygons(synthetic_pack, DGCA)["features"]
             if f["properties"]["ident"] == "VOBL"}
    outer = shape(rings["8–12 km: green up to 60 m (200 ft) AGL, yellow above"]["geometry"])
    assert outer.contains(Point(_north_of(VOBL, 14_000.0)))  # along the runways: 11.6 km out
    assert not outer.contains(Point(_east_of(VOBL, 14_000.0)))  # across them: 12.7 km out
    assert all(f["properties"]["distance_from"] == "perimeter" for f in rings.values())


def test_no_perimeter_falls_back_to_the_reference_point(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    (root / "aerodromes.csv").write_text(
        "ident,type,name,latitude_deg,longitude_deg\nVOBL,large_airport,Kempegowda,13.1986,77.7066\n", encoding="utf-8")
    write_manifest(root, DataPackManifest(name="bare", bbox=[77.3, 12.7, 78.0, 13.5], layers={
        "aerodromes": LayerEntry(file="aerodromes.csv")}))
    pack = DataPack(root)
    point = _north_of(VOBL, 11_000.0)  # 9 km from the reference point minus the assumed 2 km
    assert _at(point, pack=pack, rules=DGCA, agl=50.0).distance_from == "reference_point"
    result = airspace_run(AirspaceParams(geometry=mapping(Point(point)), altitude_agl_m=50.0, grid=False),
                          AnalysisContext(datapack=pack))
    assert result.zones[0].distance_from == "reference_point"
    assert result.zones[0].distance_km == pytest.approx(9.0, abs=0.01)
    assert any(i.code == "perimeter_approximated" and "VOBL" in i.message for i in result.issues)


_SQUARE = mapping(box(77.48, 12.90, 77.50, 12.91))
_CENTRE = (77.49, 12.905)


def _timed(start: str, end: str, **extra: Any) -> UserZone:
    return UserZone(name="Event", zone="red", geometry=_SQUARE, valid_from=datetime.fromisoformat(start),
                    valid_to=datetime.fromisoformat(end), **extra)


@pytest.mark.parametrize(("window", "active"), [
    (("2026-10-20T10:00:00+00:00", "2026-10-20T11:00:00+00:00"), False),  # starts as the zone ends
    (("2026-10-20T07:00:00+00:00", "2026-10-20T08:00:00+00:00"), False),  # ends as the zone starts
    (("2026-10-20T07:00:00+00:00", "2026-10-20T08:00:01+00:00"), True),
    (("2026-10-20T09:59:59+00:00", "2026-10-20T11:00:00+00:00"), True),
    (("2026-10-20T13:00:00+05:30", "2026-10-20T14:00:00+05:30"), True),  # 07:30-08:30 UTC
])
def test_time_windows_at_the_boundaries(window: tuple[str, str], active: bool) -> None:
    zone = _timed("2026-10-20T08:00:00Z", "2026-10-20T10:00:00Z")
    span = (datetime.fromisoformat(window[0]), datetime.fromisoformat(window[1]))
    assert zone_active(zone, span) is active
    state = _at(_CENTRE, pack=None, rules=DGCA, user_zones=[zone], window=span)
    assert (state.zone == "red") is active
    hits, inactive = zone_hits_by_activity(Point(_CENTRE), pack=None, rules=DGCA, user_zones=[zone], window=span)
    assert bool(hits) is active and bool(inactive) is not active
    if not active:
        assert inactive[0].active is False and "planned window" in (inactive[0].inactive_reason or "")


def test_daily_schedules() -> None:
    # Mondays 06:00-07:00 UTC from 19 October 2026 (a Monday) for two weeks
    zone = _timed("2026-10-19T00:00:00Z", "2026-11-02T00:00:00Z",
                  schedule=[ZoneSchedule(days=["MON"], start="06:00", end="07:00")])
    monday = datetime(2026, 10, 26, tzinfo=UTC)
    assert zone_active(zone, (monday.replace(hour=6, minute=30), monday.replace(hour=8))) is True
    assert zone_active(zone, (monday.replace(hour=7), monday.replace(hour=8))) is False
    assert zone_active(zone, (monday.replace(day=27, hour=6), monday.replace(day=27, hour=7))) is False
    night = _timed("2026-10-19T00:00:00Z", "2026-11-02T00:00:00Z",
                   schedule=[ZoneSchedule(days=["ANY"], start="22:00", end="02:00")])
    assert zone_active(night, (monday.replace(hour=1), monday.replace(hour=1, minute=30))) is True
    assert zone_active(night, (monday.replace(hour=3), monday.replace(hour=21))) is False
    assert zone_active(zone, None) is None


def test_amsl_limits_use_the_ground_from_the_dem(synthetic_pack: DataPack) -> None:
    dem = synthetic_pack.dem()
    assert dem is not None
    ground = float(dem.sample(np.array([_CENTRE[0]]), np.array([_CENTRE[1]]))[0])
    zone = UserZone(name="Above 1,000 m", zone="red", geometry=_SQUARE, lower_limit_m=1000.0, vertical_reference="AMSL")
    start_agl = 1000.0 - ground  # about 82 m above this ground
    below = _at(_CENTRE, pack=synthetic_pack, rules=RULES, user_zones=[zone], agl=start_agl - 5.0)
    assert below.zone != "red"
    above = _at(_CENTRE, pack=synthetic_pack, rules=RULES, user_zones=[zone], agl=start_agl + 5.0)
    assert above.zone == "red"
    # without a height the zone may apply, so it does
    assert _at(_CENTRE, pack=synthetic_pack, rules=RULES, user_zones=[zone]).zone == "red"

    hits, inactive = zone_hits_by_activity(Point(_CENTRE), pack=synthetic_pack, rules=RULES, user_zones=[zone],
                                           altitude_agl_m=start_agl - 5.0)
    assert not [h for h in hits if h.source == "user"]
    assert inactive[0].inactive_reason is not None and inactive[0].inactive_reason.startswith("Above the planned height")


def test_agl_band_and_unknown_ground() -> None:
    zone = UserZone(name="High", zone="red", geometry=_SQUARE, lower_limit_m=150.0, upper_limit_m=500.0)
    assert _at(_CENTRE, pack=None, rules=RULES, user_zones=[zone], agl=120.0).zone != "red"
    assert _at(_CENTRE, pack=None, rules=RULES, user_zones=[zone], agl=160.0).zone == "red"
    amsl = UserZone(name="Low", zone="red", geometry=_SQUARE, upper_limit_m=500.0, vertical_reference="AMSL")
    # no DEM: the ground is unknown and the zone applies
    assert _at(_CENTRE, pack=None, rules=RULES, user_zones=[amsl], agl=120.0).zone == "red"


def test_band_evidence_for_a_route(synthetic_pack: DataPack) -> None:
    # A route from 10 km to 14 km north of VOBL's boundary at 75 m: the 8-12 km band is 15 m over.
    route = LineString([_north_of(VOBL, 12_400.0), _north_of(VOBL, 16_400.0)])
    params = AirspaceParams(geometry=mapping(route), altitude_agl_m=75.0, grid=False, profile_spacing_m=100.0)
    result = airspace_run(params, AnalysisContext(datapack=synthetic_pack))
    (band,) = [b for b in result.band_evidence if b.ident == "VOBL"]
    assert band.band.startswith("8–12 km") and band.sample_kind == "profile"
    assert band.worst_agl_m == pytest.approx(75.0) and band.agl_margin_m == pytest.approx(-15.0)
    assert band.zone == "yellow" and band.permission_required
    elsewhere = [b for b in result.band_evidence if b.ident is None]
    assert elsewhere and elsewhere[0].agl_margin_m == pytest.approx(45.0)
    assert "15 m above its 60 m green ceiling" in result.summary
    assert result.zone_profile == "dgca_2021"
    assert result.above_ceiling and result.above_ceiling[0].limit_m == pytest.approx(60.0)


def test_band_evidence_counts_samples(synthetic_pack: DataPack) -> None:
    lons, lats = zip(*[_north_of(VOBL, d) for d in (3_000.0, 9_900.0, 12_400.0, 20_000.0)], strict=True)
    bands = band_evidence(np.array(lons), np.array(lats), 100.0, pack=synthetic_pack, rules=DGCA, sample_kind="vertices")
    assert [b.band.split(":")[0] for b in bands] == ["0–5 km", "5–8 km", "8–12 km", "Elsewhere"]
    assert [b.samples for b in bands] == [1, 1, 1, 1]
    assert bands[-1].zone == "green" and bands[-1].agl_margin_m == pytest.approx(20.0)


def test_many_dataset_zones_are_prefiltered(synthetic_pack: DataPack) -> None:
    # 5,000 small zones far from the point and one over it: the bounding-box filter keeps it quick.
    far = [UserZone(name=f"Far {i}", zone="red", geometry=mapping(box(70.0 + i * 1e-3, 20.0, 70.0005 + i * 1e-3, 20.0005)))
           for i in range(5_000)]
    near = UserZone(name="Near", zone="yellow", geometry=_SQUARE)
    started = time.perf_counter()
    state = _at(_CENTRE, pack=synthetic_pack, rules=RULES, user_zones=[*far, near])
    assert state.name == "Near" and state.permission_required
    assert time.perf_counter() - started < 10.0


def test_a_geos_error_on_a_zone_retries_with_its_repaired_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    lons, lats = np.array([77.47, 77.49, 77.51]), np.full(3, 12.905)
    fixes = (lons, lats, np.array([50.0, 160.0, 50.0]))
    line = LineString(np.column_stack([lons, lats]))
    zone = UserZone(name="High", zone="red", geometry=_SQUARE, lower_limit_m=150.0)
    expected = zone_hits_by_activity(line, pack=None, rules=RULES, user_zones=[zone], fixes=fixes)
    assert expected[0][0].name == "High"

    real = shapely.intersects
    calls: list[int] = []

    def flaky(a: Any, b: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise GEOSException("TopologyException: side location conflict")
        return real(a, b, **kwargs)

    monkeypatch.setattr(shapely, "intersects", flaky)
    assert zone_hits_by_activity(line, pack=None, rules=RULES, user_zones=[zone], fixes=fixes) == expected
    assert len(calls) > 1


def test_an_audit_track_is_tested_against_each_zone_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # the zone's height and its first_index share one query: the path is the fixes
    lons, lats = np.array([77.47, 77.49, 77.51]), np.full(3, 12.905)
    zone = UserZone(name="High", zone="red", geometry=_SQUARE, lower_limit_m=150.0)
    real = shapely.intersects
    calls: list[int] = []

    def counted(a: Any, b: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real(a, b, **kwargs)

    monkeypatch.setattr(shapely, "intersects", counted)
    (hit,), _ = zone_hits_by_activity(LineString(np.column_stack([lons, lats])), pack=None, rules=RULES,
                                      user_zones=[zone], fixes=(lons, lats, np.array([50.0, 160.0, 50.0])))
    assert hit.first_index == 0 and hit.agl_margin_m == pytest.approx(-10.0)
    assert len(calls) == 1


def test_a_long_track_through_many_zones_is_quick() -> None:
    # 50,000 fixes zig-zagging through 500 zones from 100 m up: about 2 s on a laptop
    n = 50_000
    lons = np.linspace(77.30, 77.80, n)
    lats = 12.80 + 0.002 * np.sin(np.arange(n) / 50.0)
    agl = 50.0 + 100.0 * (np.arange(n) % 1000) / 1000.0
    zones = [UserZone(name=f"Z{i}", zone="yellow", lower_limit_m=100.0,
                      geometry=mapping(box(77.30 + i * 0.001, 12.797, 77.3006 + i * 0.001, 12.803))) for i in range(500)]
    started = time.perf_counter()
    hits, inactive = zone_hits_by_activity(LineString(np.column_stack([lons, lats])), pack=None, rules=DGCA,
                                           user_zones=zones, fixes=(lons, lats, agl))
    assert time.perf_counter() - started < 10.0
    assert len(hits) + len(inactive) == 500 and hits and inactive
    assert all(h.first_index is not None and h.agl_margin_m is not None and h.agl_margin_m <= 0.0 for h in hits)


def test_five_thousand_zones_inside_the_area(synthetic_pack: DataPack) -> None:
    # the server's cap of dataset zones, all inside the checked area: well inside the job time limit
    zones = [UserZone(name=f"Z{i}-{j}", zone="yellow", ceiling_agl_m=50.0,
                      geometry=mapping(box(77.55 + i * 0.0018, 12.92 + j * 0.0009, 77.551 + i * 0.0018, 12.9205 + j * 0.0009)))
             for i in range(50) for j in range(100)]
    params = AirspaceParams(geometry=mapping(box(77.55, 12.92, 77.64, 13.01)), dataset_zones=zones, h3_resolution=9,
                            altitude_agl_m=40.0)
    started = time.perf_counter()
    result = airspace_run(params, AnalysisContext(datapack=synthetic_pack))
    assert time.perf_counter() - started < 120.0
    assert sum(1 for z in result.zones if z.source == "user") == 5_000
    assert any(c.ceiling_agl_m == pytest.approx(50.0) for c in result.grid)
