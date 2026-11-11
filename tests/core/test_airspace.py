"""Tests for the airspace & terrain module (airbrief_core.analyses.airspace)."""

from __future__ import annotations

import math
import threading
from pathlib import Path

import pytest
from shapely.geometry import LineString, box, mapping

from airbrief_core.analyses.airspace import NOT_ASSESSED_SUMMARY, run
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.terrain import obstacle_height
from airbrief_core.data import DataPack
from airbrief_core.data.datapack import DataPackManifest, LayerEntry, write_manifest
from airbrief_core.errors import AnalysisCancelled, AnalysisError
from airbrief_core.geo.geodesic import geodesic_length
from airbrief_core.models.analyses import AirspaceParams, AirspaceResult
from airbrief_core.models.common import Severity, UserZone, Verdict
from airbrief_core.models.presets import DEFAULT_ZONE_RULES, DIGITAL_SKY_URL, DISCLAIMER_AIRSPACE

SURVEY = box(77.48, 12.90, 77.50, 12.91)
RED_CORE = box(77.588, 12.977, 77.594, 12.982)  # inside the demo red zone
NEAR_VOBL = {"type": "Point", "coordinates": [77.7066, 13.16]}  # 4.3 km from the reference point
ROUTE = LineString([(77.47, 12.90), (77.51, 12.92)])


def _ctx(pack: DataPack | None) -> AnalysisContext:
    return AnalysisContext(datapack=pack)


def _survey_conflicts(pack: DataPack, altitude: float, clearance: float) -> set[str]:
    """Obstacles inside the survey area that should conflict, computed from the pack itself."""
    out: set[str] = set()
    for feat in pack.obstacles().in_polygon(SURVEY):
        height = obstacle_height(feat.props)
        if height is not None and height + clearance > altitude:
            out.add(feat.id)
    return out


@pytest.mark.parametrize("altitude", [120.0, 60.0])
def test_survey_area_obstacle_conflicts(synthetic_pack: DataPack, altitude: float) -> None:
    params = AirspaceParams(geometry=mapping(SURVEY), altitude_agl_m=altitude, obstacle_search_m=0.0,
                            vertical_clearance_m=30.0)
    result = run(params, _ctx(synthetic_pack))
    expected = _survey_conflicts(synthetic_pack, altitude, 30.0)
    got = {o.id for o in result.obstacles if o.conflict}
    assert got == expected
    ids = {o.id for o in result.obstacles}
    assert {"obs-0001", "obs-0002", "obs-0003"} <= ids
    if altitude == 60.0:
        # All three fixed obstacles (65, 42, 48 m) + 30 m clearance exceed 60 m.
        assert {"obs-0001", "obs-0002", "obs-0003"} <= got
    else:
        assert not {"obs-0001", "obs-0002", "obs-0003"} & got

    # Outside every aerodrome ring: default green, 120 m.
    assert result.permission_required is False
    assert result.effective_ceiling_agl_m == pytest.approx(120.0)
    assert not [z for z in result.zones if z.source == "aerodrome"]
    assert result.verdict == (Verdict.CAUTION if expected else Verdict.PASS)
    assert result.geometry_type == "Polygon"
    assert result.area_m2 is not None and result.area_m2 > 1e6
    assert result.terrain is not None
    assert result.max_terrain_m == pytest.approx(result.terrain.max_m)
    assert result.max_altitude_amsl_m == pytest.approx(result.terrain.max_m + altitude)
    assert result.disclaimer == DISCLAIMER_AIRSPACE
    assert set(result.layers) == {"input", "zones", "obstacles"}
    assert len(result.layers["obstacles"]["features"]) == len(result.obstacles)
    assert result.nearest_aerodromes and result.nearest_aerodromes[0].distance_km > 10.0


def test_flyability_grid_statuses(synthetic_pack: DataPack) -> None:
    params = AirspaceParams(geometry=mapping(SURVEY), altitude_agl_m=60.0, obstacle_search_m=0.0,
                            vertical_clearance_m=30.0, h3_resolution=10)
    result = run(params, _ctx(synthetic_pack))
    assert result.grid
    assert result.grid_resolution == 10
    for cell in result.grid:
        assert cell.zone == "green"
        assert cell.terrain_m is not None
        if cell.obstacle_max_height_m is not None and cell.obstacle_max_height_m + 30.0 > 60.0:
            assert cell.status == "limited"
        else:
            assert cell.status == "free"
    assert any(c.obstacle_max_height_m == pytest.approx(65.0) for c in result.grid)
    assert any(c.status == "free" for c in result.grid)

    # Above the 120 m default ceiling every cell is limited and the verdict is caution (the
    # conservative rules; DGCA 2021 makes it yellow above 120 m, see test_zones.py).
    high = run(AirspaceParams(geometry=mapping(SURVEY), altitude_agl_m=150.0, h3_resolution=9,
                              zone_profile="conservative_illustrative"), _ctx(synthetic_pack))
    assert high.grid and all(c.status == "limited" for c in high.grid)
    assert high.effective_ceiling_agl_m == pytest.approx(120.0)
    assert high.verdict == Verdict.CAUTION


def test_grid_disabled(synthetic_pack: DataPack) -> None:
    result = run(AirspaceParams(geometry=mapping(SURVEY), grid=False), _ctx(synthetic_pack))
    assert result.grid == []
    assert result.grid_resolution is None


def test_red_zone_fails(synthetic_pack: DataPack) -> None:
    result = run(AirspaceParams(geometry=mapping(RED_CORE), altitude_agl_m=50.0, h3_resolution=10),
                 _ctx(synthetic_pack))
    assert result.verdict == Verdict.FAIL
    assert result.zones[0].zone == "red"
    assert result.zones[0].source == "datapack"
    assert result.effective_ceiling_agl_m is None
    assert result.grid and all(c.status == "restricted" for c in result.grid)
    assert "red zone" in result.summary
    names = {f["properties"]["name"] for f in result.layers["zones"]["features"]}
    assert "Demo restricted zone (synthetic)" in names


def test_red_zone_ignored_when_datapack_zones_excluded(synthetic_pack: DataPack) -> None:
    result = run(AirspaceParams(geometry=mapping(RED_CORE), include_datapack_zones=False),
                 _ctx(synthetic_pack))
    assert result.verdict != Verdict.FAIL
    assert all(z.zone != "red" for z in result.zones)


def test_point_near_vobl_requires_permission(synthetic_pack: DataPack) -> None:
    result = run(AirspaceParams(geometry=NEAR_VOBL, altitude_agl_m=30.0, zone_profile="conservative_illustrative"),
                 _ctx(synthetic_pack))
    assert result.permission_required is True
    assert result.effective_ceiling_agl_m is None
    assert result.verdict == Verdict.CAUTION
    vobl = [z for z in result.zones if z.ident == "VOBL"]
    assert vobl and vobl[0].permission_required and vobl[0].ring is not None and vobl[0].ring.startswith("0–8 km")
    assert vobl[0].distance_km == pytest.approx(4.3 - 2.0, abs=0.1)
    assert result.nearest_aerodromes[0].ident == "VOBL"
    assert result.geometry_type == "Point"
    assert result.grid and all(c.status == "permission" for c in result.grid)
    assert "permission" in result.summary


def test_user_red_zone_without_datapack() -> None:
    zone = UserZone(name="Stadium TFR", zone="red", geometry=mapping(box(77.485, 12.902, 77.495, 12.908)))
    params = AirspaceParams(geometry=mapping(SURVEY), user_zones=[zone], grid=False)
    result = run(params, _ctx(None))
    assert result.verdict == Verdict.FAIL
    assert result.zones[0].source == "user"
    assert result.zones[0].name == "Stadium TFR"
    assert result.zones[0].overlap_area_m2 is not None and result.zones[0].overlap_area_m2 > 0
    zone_names = {f["properties"]["name"] for f in result.layers["zones"]["features"]}
    assert zone_names == {"Stadium TFR"}


def test_no_datapack_still_produces_result() -> None:
    result = run(AirspaceParams(geometry=mapping(SURVEY), altitude_agl_m=100.0), _ctx(None))
    codes = {i.code for i in result.issues}
    assert "datapack_missing" in codes
    assert result.nearest_aerodromes == []
    assert result.terrain is None
    assert result.max_terrain_m is None
    assert result.obstacles == []
    assert result.zones == []
    assert result.effective_ceiling_agl_m == pytest.approx(120.0)
    assert result.grid and all(c.terrain_m is None for c in result.grid)
    assert "No data pack" in result.summary
    assert result.layers["zones"]["features"] == []


def test_no_datapack_is_never_a_pass() -> None:
    # Unchecked aerodrome zones are never an all-clear: caution + not_assessed, never "pass".
    result = run(AirspaceParams(geometry=mapping(SURVEY), altitude_agl_m=100.0), _ctx(None))
    assert result.verdict == Verdict.CAUTION
    assert result.assessment_complete is False
    gaps = [i for i in result.issues if i.code == "not_assessed"]
    assert len(gaps) == 1 and gaps[0].severity == Severity.WARNING
    assert gaps[0].message.startswith(NOT_ASSESSED_SUMMARY)
    assert DIGITAL_SKY_URL in gaps[0].message
    assert result.summary.startswith(
        "Airspace not assessed: no data pack covers this area — aerodrome zones were not checked."
    )
    assert "without permission" not in result.summary  # never worded as allowed
    # A red user zone still fails: the gap only ever lowers a pass.
    zone = UserZone(name="Stadium TFR", zone="red", geometry=mapping(box(77.485, 12.902, 77.495, 12.908)))
    red = run(AirspaceParams(geometry=mapping(SURVEY), user_zones=[zone], grid=False), _ctx(None))
    assert red.verdict == Verdict.FAIL and red.assessment_complete is False


def test_pack_without_aerodromes_is_not_assessed(tmp_path: Path) -> None:
    root = tmp_path / "noaero"
    root.mkdir()
    (root / "zones.geojson").write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")
    write_manifest(root, DataPackManifest(name="noaero", bbox=[77.0, 12.5, 78.0, 13.5],
                                          layers={"zones": LayerEntry(file="zones.geojson", source="test")}))
    result = run(AirspaceParams(geometry=mapping(SURVEY), grid=False), _ctx(DataPack(root)))
    assert result.verdict == Verdict.CAUTION and result.assessment_complete is False
    gap = next(i for i in result.issues if i.code == "not_assessed")
    assert "has no aerodrome layer" in gap.message
    assert result.summary.startswith("Airspace not assessed")


def test_geometry_outside_the_pack_is_not_assessed(synthetic_pack: DataPack) -> None:
    far = box(10.0, 45.0, 10.01, 45.01)
    result = run(AirspaceParams(geometry=mapping(far), grid=False), _ctx(synthetic_pack))
    assert result.verdict == Verdict.CAUTION and result.assessment_complete is False
    assert any(i.code == "not_assessed" and "outside data pack" in i.message for i in result.issues)


def test_covered_area_is_assessed(synthetic_pack: DataPack) -> None:
    result = run(AirspaceParams(geometry=NEAR_VOBL, altitude_agl_m=30.0, grid=False), _ctx(synthetic_pack))
    assert result.assessment_complete is True
    assert not any(i.code == "not_assessed" for i in result.issues)
    # Stored results from before the flag validate as complete.
    stored = result.model_dump(mode="json")
    del stored["assessment_complete"]
    assert AirspaceResult.model_validate(stored).assessment_complete is True


def test_interim_dgca_wording() -> None:
    rings = {kind: rule.rings for rule in DEFAULT_ZONE_RULES.rules for kind in rule.aerodrome_types}
    for kind in ("large_airport", "medium_airport"):
        assert rings[kind][0].label == "0–8 km: permission required — verify on Digital Sky"
    for kind in ("small_airport", "heliport"):
        assert rings[kind][0].label.endswith("conservative assumption, not a DGCA rule")
    assert DIGITAL_SKY_URL == "https://digitalsky.dgca.gov.in"
    assert DIGITAL_SKY_URL in DEFAULT_ZONE_RULES.reference


def test_route_profile(synthetic_pack: DataPack) -> None:
    params = AirspaceParams(geometry=mapping(ROUTE), altitude_agl_m=100.0, profile_spacing_m=50.0, grid=False)
    result = run(params, _ctx(synthetic_pack))
    length = geodesic_length(ROUTE)
    assert result.geometry_type == "LineString"
    assert result.length_m == pytest.approx(length, abs=0.01)
    assert len(result.profile) == int(math.ceil(length / 50.0 * (1.0 - 1e-6))) + 1
    first, last = result.profile[0], result.profile[-1]
    assert (first.lon, first.lat) == pytest.approx((77.47, 12.90))
    assert (last.lon, last.lat) == pytest.approx((77.51, 12.92))
    assert last.distance_m == pytest.approx(length, abs=0.05)
    for p in result.profile:
        assert p.terrain_m is not None
        assert p.agl_m == pytest.approx(100.0)
        assert p.altitude_amsl_m == pytest.approx(p.terrain_m + 100.0, abs=0.02)
        assert p.limit_agl_m == pytest.approx(120.0)
    assert result.max_terrain_m is not None


def test_route_through_red_zone_profile_limits(synthetic_pack: DataPack) -> None:
    route = LineString([(77.575, 12.980), (77.605, 12.980)])  # crosses the demo red zone west to east
    result = run(AirspaceParams(geometry=mapping(route), profile_spacing_m=100.0, grid=False), _ctx(synthetic_pack))
    assert result.verdict == Verdict.FAIL
    limits = [p.limit_agl_m for p in result.profile]
    assert 0.0 in limits
    inside = [p for p in result.profile if 77.5840 < p.lon < 77.5975]
    assert inside and all(p.limit_agl_m == 0.0 for p in inside)
    red = [z for z in result.zones if z.zone == "red"]
    assert red and red[0].overlap_length_m == pytest.approx(geodesic_length(
        LineString([(77.5840, 12.980), (77.5975, 12.980)])), rel=1e-3)
    assert red[0].first_index == 0


def test_invalid_geometry_raises_analysis_error() -> None:
    with pytest.raises(AnalysisError) as exc:
        run(AirspaceParams(geometry={"type": "Polygon", "coordinates": "nope"}), _ctx(None))
    assert exc.value.code == "invalid_geometry"


def test_cancellation() -> None:
    event = threading.Event()
    event.set()
    ctx = AnalysisContext(datapack=None, cancel_event=event)
    with pytest.raises(AnalysisCancelled):
        run(AirspaceParams(geometry=mapping(SURVEY)), ctx)


def test_progress_reported(synthetic_pack: DataPack) -> None:
    seen: list[tuple[str, float]] = []
    ctx = AnalysisContext(datapack=synthetic_pack, progress=lambda stage, frac: seen.append((stage, frac)))
    run(AirspaceParams(geometry=NEAR_VOBL, grid=False), ctx)
    fractions = [f for _, f in seen]
    assert fractions == sorted(fractions)
    assert fractions[-1] == pytest.approx(1.0)
