"""Tests for eVTOL vertiport siting and corridor routing on the synthetic data pack."""

from __future__ import annotations

import itertools
import math
from typing import Any

import h3
import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, Point, box, shape

from airbrief_core.analyses import evtol
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.evtol import (
    _simplify_route,
    noise_half_width_m,
    noise_level_db,
    run_corridor,
    run_siting,
)
from airbrief_core.data import DataPack
from airbrief_core.errors import AnalysisError
from airbrief_core.geo.frame import LocalFrame
from airbrief_core.geo.geodesic import distance_m
from airbrief_core.geo.h3grid import cell_polygon
from airbrief_core.models.analyses import (
    EvtolCorridorParams,
    EvtolSitingParams,
    SitingWeights,
)

# The synthetic pack's demo red zone (seed/datapack.py::_synthetic_zones).
RED_ZONE = box(77.5840, 12.9735, 77.5975, 12.9855)

# Area around the city centre containing the red zone and the synthetic Ulsoor lake.
SITING_AREA = {
    "type": "Polygon",
    "coordinates": [[[77.56, 12.94], [77.64, 12.94], [77.64, 13.00], [77.56, 13.00], [77.56, 12.94]]],
}


@pytest.fixture(scope="module")
def siting_result(synthetic_pack: DataPack):  # type: ignore[no-untyped-def]
    params = EvtolSitingParams(
        geometry=SITING_AREA, h3_resolution=9, top_n=5, min_separation_km=2.0
    )
    return run_siting(params, AnalysisContext(datapack=synthetic_pack))


def test_siting_top_n_and_separation(siting_result) -> None:  # type: ignore[no-untyped-def]
    candidates = siting_result.candidates
    assert 1 <= len(candidates) <= 5
    assert [c.rank for c in candidates] == list(range(1, len(candidates) + 1))
    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True)
    assert all(0.0 <= s <= 100.0 for s in scores)
    for a, b in itertools.combinations(candidates, 2):
        assert float(distance_m(a.lon, a.lat, b.lon, b.lat)) >= 2000.0
    for c in candidates:
        assert set(c.breakdown) == {"demand", "access", "airspace", "obstacles", "slope"}
        assert all(0.0 <= v <= 1.0 for v in c.breakdown.values())


def test_siting_excludes_water_and_red(siting_result, synthetic_pack: DataPack) -> None:  # type: ignore[no-untyped-def]
    water = synthetic_pack.water()
    for c in siting_result.candidates:
        assert c.zone != "red"
        assert not bool(water.contains_xy(c.lon, c.lat))
        assert not RED_ZONE.covers(Point(c.lon, c.lat))
    reasons = {cell.reason for cell in siting_result.cells if cell.excluded}
    assert "red_zone" in reasons
    assert "water" in reasons
    excluded = [cell for cell in siting_result.cells if cell.excluded]
    assert siting_result.cells_excluded == len(excluded)
    assert all(cell.score is None for cell in excluded)
    assert siting_result.cells_evaluated == len(siting_result.cells)
    chosen = {c.h3 for c in siting_result.candidates}
    assert not chosen & {cell.h3 for cell in excluded}


def test_siting_layers_and_metadata(siting_result) -> None:  # type: ignore[no-untyped-def]
    assert set(siting_result.layers) == {"input", "candidates"}
    feats = siting_result.layers["candidates"]["features"]
    assert len(feats) == len(siting_result.candidates)
    assert all(f["geometry"]["type"] == "Point" for f in feats)
    assert [f["properties"]["rank"] for f in feats] == [c.rank for c in siting_result.candidates]
    assert set(siting_result.criteria) == {"demand", "access", "airspace", "obstacles", "slope"}
    assert siting_result.disclaimer


def test_siting_all_zero_weights_rejected() -> None:
    with pytest.raises(ValidationError):
        SitingWeights(demand=0, access=0, airspace=0, obstacles=0, slope=0)


def test_siting_without_datapack_is_datapack_missing() -> None:
    params = EvtolSitingParams(geometry=SITING_AREA)
    with pytest.raises(AnalysisError) as info:
        run_siting(params, AnalysisContext(datapack=None))
    assert info.value.code == "datapack_missing"


ORIGIN = (77.5700, 12.9795)  # west of the demo red zone
DESTINATION = (77.6120, 12.9795)  # east of it; the straight line crosses the zone


@pytest.fixture(scope="module")
def corridor_result(synthetic_pack: DataPack):  # type: ignore[no-untyped-def]
    params = EvtolCorridorParams(
        origin=ORIGIN,
        destination=DESTINATION,
        origin_name="West pad",
        destination_name="East pad",
        study_margin_km=2.0,
        h3_resolution=9,
    )
    return run_corridor(params, AnalysisContext(datapack=synthetic_pack))


def test_corridor_avoids_red_zone(corridor_result) -> None:  # type: ignore[no-untyped-def]
    straight = shape({"type": "LineString", "coordinates": [list(ORIGIN), list(DESTINATION)]})
    assert straight.intersects(RED_ZONE)  # sanity: the direct line would cross it
    route = shape(corridor_result.route)
    assert route.geom_type == "LineString"
    assert not route.intersects(RED_ZONE)
    coords = list(route.coords)
    assert coords[0] == pytest.approx(ORIGIN, abs=1e-7)
    assert coords[-1] == pytest.approx(DESTINATION, abs=1e-7)


def test_corridor_metrics(corridor_result) -> None:  # type: ignore[no-untyped-def]
    r = corridor_result
    assert r.detour_pct >= 0.0
    assert r.length_km >= r.straight_km - 1e-3
    assert r.straight_km == pytest.approx(float(distance_m(*ORIGIN, *DESTINATION)) / 1000.0, abs=1e-3)
    assert r.flight_time_min == pytest.approx(r.length_km / 150.0 * 60.0, rel=1e-2)
    assert r.energy_kwh == pytest.approx(r.length_km * 1000.0 / 1000.0, rel=1e-2)
    assert r.cells_considered > 0
    assert set(r.layers) == {"route", "corridor", "endpoints", "obstacles"}
    roles = [f["properties"]["role"] for f in r.layers["endpoints"]["features"]]
    assert roles == ["origin", "destination"]
    assert shape(r.corridor).contains(shape(r.route).interpolate(0.5, normalized=True))
    assert r.profile
    assert r.max_terrain_m is not None
    assert r.required_cruise_amsl_m is not None
    assert r.required_cruise_amsl_m >= r.max_terrain_m + r.cruise_altitude_agl_m - 0.1
    assert r.disclaimer


def test_corridor_noise_half_width(corridor_result) -> None:  # type: ignore[no-untyped-def]
    # Defaults: 75 dB at 100 m, threshold 55 dB, cruise 300 m AGL -> r_t = 1000 m.
    expected = math.sqrt(1000.0**2 - 300.0**2)
    assert corridor_result.noise.footprint_half_width_m == pytest.approx(expected, abs=0.1)
    assert corridor_result.noise.threshold_db == 55.0
    assert corridor_result.noise.cells
    for cell in corridor_result.noise.cells:
        assert cell.level_db >= 55.0 - 0.05
        assert cell.population >= 0.0
    assert corridor_result.noise.exposed_population > 0.0


def test_noise_formulas() -> None:
    assert noise_half_width_m(75.0, 100.0, 55.0, 300.0) == pytest.approx(math.sqrt(1000.0**2 - 300.0**2))
    assert noise_half_width_m(75.0, 100.0, 55.0, 1000.0) == 0.0
    assert noise_half_width_m(75.0, 100.0, 55.0, 2000.0) == 0.0
    assert float(noise_level_db(75.0, 100.0, 1000.0)) == pytest.approx(55.0)
    assert float(noise_level_db(75.0, 100.0, 100.0)) == pytest.approx(75.0)


def test_corridor_without_datapack_is_datapack_missing() -> None:
    params = EvtolCorridorParams(origin=ORIGIN, destination=DESTINATION)
    with pytest.raises(AnalysisError) as info:
        run_corridor(params, AnalysisContext(datapack=None))
    assert info.value.code == "datapack_missing"


def test_corridor_identical_endpoints_rejected(synthetic_pack: DataPack) -> None:
    params = EvtolCorridorParams(origin=ORIGIN, destination=ORIGIN)
    with pytest.raises(AnalysisError) as info:
        run_corridor(params, AnalysisContext(datapack=synthetic_pack))
    assert info.value.code == "invalid_params"


def _local_line(frame: LocalFrame, xy: list[tuple[float, float]]) -> LineString:
    return frame.to_wgs84(LineString(xy))


def _detour(frame: LocalFrame) -> tuple[LineString, tuple[float, ...], tuple[float, ...]]:
    # local metres: the cell route leaves the origin's cell at an angle and bends 15 m round
    # (200, 0); the 20 m smoothing straightens both
    raw = _local_line(frame, [(0, 0), (30, 15), (100, 0), (200, 15), (300, 0), (400, 0)])
    return raw, raw.coords[0], raw.coords[-1]


def test_smoothing_keeps_clear_of_impassable_cells() -> None:
    frame = LocalFrame(77.60, 12.90)
    raw, origin, destination = _detour(frame)
    # nothing in the way: the straight line, though it leaves the origin away from the cell route
    assert len(_simplify_route(raw, frame, origin, destination).coords) == 2

    tall = frame.to_wgs84(box(190, -8, 210, 8))  # blocked by a tall obstacle, under the shortcut
    route = _simplify_route(raw, frame, origin, destination, cells=[tall])
    assert route.equals(raw) and not route.intersects(tall)
    beside = frame.to_wgs84(box(50, -8, 70, 8))  # an excluded band cell next to the origin's
    assert _simplify_route(raw, frame, origin, destination, cells=[beside]).equals(raw)


def test_smoothing_from_the_edge_of_a_red_zone_keeps_out_of_its_middle() -> None:
    # one red zone: the origin's own cell (allowed, so not passed), a strip south of the route and
    # an arm up under the bend at 200 m
    frame = LocalFrame(77.60, 12.90)
    raw, origin, destination = _detour(frame)
    strip = frame.to_wgs84(box(50, -50, 210, -40))
    arm = frame.to_wgs84(box(190, -40, 210, 8))
    route = _simplify_route(raw, frame, origin, destination, cells=[strip, arm])
    assert route.equals(raw) and not route.intersects(arm)


def test_the_corridor_smoothing_checks_obstacle_cells(synthetic_pack: DataPack, monkeypatch: pytest.MonkeyPatch) -> None:
    # 10 m under the cruise height every obstacle blocks its cell; fly straight at the tallest one
    tall = max(synthetic_pack.obstacles().in_bbox((77.45, 12.85, 77.80, 13.15)), key=lambda f: float(f.get("height_m") or 0.0))
    seen: list[Any] = []
    real = evtol._simplify_route

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.extend(kwargs.get("cells", ()))
        return real(*args, **kwargs)

    monkeypatch.setattr(evtol, "_simplify_route", spy)
    params = EvtolCorridorParams(origin=(tall.lon - 0.01, tall.lat), destination=(tall.lon + 0.01, tall.lat),
                                 h3_resolution=9, study_margin_km=1.0, cruise_altitude_agl_m=60.0,
                                 obstacle_clearance_m=50.0)
    result = run_corridor(params, AnalysisContext(datapack=synthetic_pack))

    blocked = cell_polygon(h3.latlng_to_cell(tall.lat, tall.lon, 9))
    assert any(p.equals(blocked) for p in seen)
    assert not shape(result.route).intersects(blocked)


def test_the_corridor_projects_its_cell_route_once(synthetic_pack: DataPack, monkeypatch: pytest.MonkeyPatch) -> None:
    projected: list[Any] = []
    counts: list[int] = []
    to_local = LocalFrame.to_local
    real = evtol._simplify_route

    def counted(self: LocalFrame, geom: Any) -> Any:
        projected.append(geom)
        return to_local(self, geom)

    def spy(*args: Any, **kwargs: Any) -> Any:
        route = real(*args, **kwargs)
        counts.append(sum(g is args[0] for g in projected))
        return route

    monkeypatch.setattr(LocalFrame, "to_local", counted)
    monkeypatch.setattr(evtol, "_simplify_route", spy)
    run_corridor(EvtolCorridorParams(origin=ORIGIN, destination=DESTINATION, study_margin_km=2.0, h3_resolution=9),
                 AnalysisContext(datapack=synthetic_pack))
    assert counts == [1]
