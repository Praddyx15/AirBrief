"""Line of sight: formulas, viewshed geometry against analytic answers, radio paths, full runs."""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import LineString, box, mapping

from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.line_of_sight import (
    MAX_GRID_SIDE,
    curvature_drop_m,
    fresnel_profile,
    fresnel_radius_m,
    fspl_db,
    grid_bilinear,
    run,
    viewshed,
    vlos_guidance,
)
from airbrief_core.data import DataPack
from airbrief_core.data.datapack import DataPackManifest, LayerEntry, write_manifest
from airbrief_core.errors import AnalysisCancelled, AnalysisError
from airbrief_core.geo.frame import LocalFrame
from airbrief_core.models.line_of_sight import (
    EARTH_RADIUS_M,
    K_RADIO,
    K_VISIBLE,
    LineOfSightParams,
    LineOfSightResult,
)
from airbrief_core.orchestrator import run_analysis_sync

LON0, LAT0 = 77.0, 12.0  # centre of the hand-made test packs


def _make_pack(
    root: Path,
    array: np.ndarray,
    *,
    res: float = 0.0005,
    obstacles: list[dict[str, Any]] | None = None,
) -> DataPack:
    """A data pack whose DEM is ``array`` centred on (LON0, LAT0) (row 0 = north)."""
    root.mkdir(parents=True, exist_ok=True)
    rows, cols = array.shape
    west = LON0 - cols * res / 2.0
    north = LAT0 + rows * res / 2.0
    with rasterio.open(
        root / "dem.tif", "w", driver="GTiff", width=cols, height=rows, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(west, north, res, res), nodata=-9999.0,
    ) as dst:
        dst.write(array.astype(np.float32), 1)
    layers = {"dem": LayerEntry(file="dem.tif", source="test-dem")}
    if obstacles is not None:
        (root / "obstacles.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": obstacles}))
        layers["obstacles"] = LayerEntry(file="obstacles.geojson", source="test-obstacles")
    write_manifest(root, DataPackManifest(
        name=root.name,
        bbox=[west, north - rows * res, west + cols * res, north],
        created_utc="2026-01-01T00:00:00Z",
        layers=layers,
    ))
    return DataPack(root)


def _offset(east_m: float, north_m: float) -> tuple[float, float]:
    frame = LocalFrame(LON0, LAT0)
    lons, lats = frame.lonlat(np.array([east_m]), np.array([north_m]))
    return float(lons[0]), float(lats[0])


def _square(half_m: float, centre: tuple[float, float] = (0.0, 0.0)) -> dict[str, Any]:
    frame = LocalFrame(LON0, LAT0)
    cx, cy = centre
    ring = [(cx - half_m, cy - half_m), (cx + half_m, cy - half_m), (cx + half_m, cy + half_m), (cx - half_m, cy + half_m)]
    lons, lats = frame.lonlat(np.array([p[0] for p in ring]), np.array([p[1] for p in ring]))
    coords = [[float(lo), float(la)] for lo, la in zip(lons, lats, strict=True)]
    return {"type": "Polygon", "coordinates": [[*coords, coords[0]]]}


def _observer(name: str, east_m: float, north_m: float, role: str = "pilot", height: float | None = None) -> dict[str, Any]:
    lon, lat = _offset(east_m, north_m)
    out: dict[str, Any] = {"name": name, "role": role, "lon": lon, "lat": lat}
    if height is not None:
        out["height_agl_m"] = height
    return out


def _run(pack: DataPack, **params: Any) -> LineOfSightResult:
    return run(LineOfSightParams.model_validate(params), AnalysisContext(datapack=pack))


@pytest.fixture(scope="module")
def flat_pack(tmp_path_factory: pytest.TempPathFactory) -> DataPack:
    """Level ground at 900 m over about 22 × 22 km."""
    return _make_pack(tmp_path_factory.mktemp("los") / "flat", np.full((400, 400), 900.0))


@pytest.fixture(scope="module")
def hilly_pack(tmp_path_factory: pytest.TempPathFactory) -> DataPack:
    """Rolling hills (±40 m, about 1.2 km wavelength) so one observer sees only part of an area."""
    rows, cols = 400, 400
    r, c = np.mgrid[0:rows, 0:cols].astype(np.float64)
    z = 900.0 + 40.0 * np.sin(c / 11.0) * np.cos(r / 9.0)
    return _make_pack(tmp_path_factory.mktemp("los") / "hilly", z)


def test_fresnel_radius_at_midpoint_of_1_km_at_2_4_ghz() -> None:
    # r1 = 17.32·√(0.5·0.5/(2.4·1)) = 5.590 m
    assert fresnel_radius_m(500.0, 500.0, 2400.0) == pytest.approx(5.59, abs=0.005)
    assert fresnel_radius_m(250.0, 750.0, 2400.0) == pytest.approx(fresnel_radius_m(750.0, 250.0, 2400.0))
    assert fresnel_radius_m(0.0, 1000.0, 2400.0) == 0.0
    # r1 scales with 1/√f: 433 MHz is √(2400/433) times wider.
    assert fresnel_radius_m(500.0, 500.0, 433.0) == pytest.approx(5.59 * math.sqrt(2400.0 / 433.0), rel=1e-3)


def test_free_space_path_loss_at_2_4_ghz_over_1_km() -> None:
    # 20·log10(1) + 20·log10(2400) + 32.44 = 100.04 dB
    assert fspl_db(1000.0, 2400.0) == pytest.approx(100.0, abs=0.05)
    assert fspl_db(2000.0, 2400.0) - fspl_db(1000.0, 2400.0) == pytest.approx(20.0 * math.log10(2.0))
    with pytest.raises(AnalysisError):
        fspl_db(0.0, 2400.0)


def test_curvature_drop_at_10_km_is_5_9_m_for_radio() -> None:
    assert curvature_drop_m(10_000.0, K_RADIO) == pytest.approx(5.886, abs=0.001)
    assert curvature_drop_m(10_000.0, K_VISIBLE) == pytest.approx(1e8 / (2.0 * (7.0 / 6.0) * EARTH_RADIUS_M))
    assert curvature_drop_m(10_000.0, math.inf) == 0.0
    assert K_VISIBLE == pytest.approx(1.0 / (1.0 - 1.0 / 7.0))  # GDAL coefficient 0.85714


def test_fresnel_profile_over_level_ground() -> None:
    d = np.linspace(0.0, 1000.0, 101)
    los, r1, clearance = fresnel_profile(d, np.zeros_like(d), 10.0, 10.0, 2400.0, math.inf)
    assert los == pytest.approx(np.full_like(d, 10.0))
    assert r1[50] == pytest.approx(5.59, abs=0.005)
    assert math.isnan(clearance[0]) and math.isnan(clearance[-1])
    assert clearance[50] == pytest.approx(10.0 / r1[50] * 100.0)
    # With curvature the line sags by the bulge d1·d2/(2kR) (1 km, k = 4/3: 1.47 cm at the midpoint).
    los_k, _, _ = fresnel_profile(d, np.zeros_like(d), 10.0, 10.0, 2400.0, K_RADIO)
    assert 10.0 - los_k[50] == pytest.approx(500.0 * 500.0 / (2.0 * K_RADIO * EARTH_RADIUS_M))


@pytest.mark.parametrize(
    ("aircraft", "cd", "visibility", "expected", "basis"),
    [
        ("multirotor", 0.35, 5000.0, 327.0 * 0.35 + 20.0, "easa_alos_dlos"),  # ALOS 134.45 m < DLOS 1,500 m
        ("fixed_wing", 2.0, 5000.0, 490.0 * 2.0 + 30.0, "easa_alos_dlos"),  # ALOS 1,010 m
        ("fixed_wing", 2.0, 3000.0, 0.3 * 3000.0, "easa_alos_dlos"),  # DLOS 900 m is smaller
        ("multirotor", None, 5000.0, 500.0, "uk_caa_500m"),
    ],
)
def test_vlos_guidance(aircraft: str, cd: float | None, visibility: float, expected: float, basis: str) -> None:
    g = vlos_guidance(aircraft, cd, visibility, 120.0)
    assert g.guidance_m == pytest.approx(expected, abs=0.05)
    assert g.basis == basis
    assert g.ring_radius_m == pytest.approx(math.sqrt(max(expected**2 - 120.0**2, 0.0)), abs=0.05)
    assert "Non-binding" in g.reference or "not a limit" in g.reference


def test_flat_ground_is_fully_visible_within_range() -> None:
    z = np.full((101, 101), 500.0)
    vis = viewshed(z, 30.0, (50.5, 50.5), 501.7, 120.0, K_VISIBLE)
    assert vis.all()
    limited = viewshed(z, 30.0, (50.5, 50.5), 501.7, 120.0, K_VISIBLE, max_range_m=600.0)
    r, c = np.mgrid[0:101, 0:101]
    dist = np.hypot(c - 50, r - 50) * 30.0
    assert np.array_equal(limited, dist <= 600.0)


def _wall_reference(eye: float, wall: float, wall_m: float, target: float, s: float, k: float) -> bool:
    """Is a target at ``s`` metres behind a wall at ``wall_m`` visible (curvature included)?"""
    drop = (lambda d: d * d / (2.0 * k * EARTH_RADIUS_M)) if math.isfinite(k) else (lambda d: 0.0)
    wall_slope = (wall - drop(wall_m) - eye) / wall_m
    target_slope = (target - drop(s) - eye) / s
    return target_slope >= wall_slope


@pytest.mark.parametrize("axis", ["east", "north"])
def test_wall_casts_the_exact_shadow_on_the_ground(axis: str) -> None:
    # Eye 10 m above level ground, a 4 m wall 300 m away: ground is hidden from 300 m to
    # 300·10/(10 − 4) = 500 m behind it, and visible again beyond.
    cell = 30.0
    z = np.zeros((21, 40))
    z[:, 10] = 4.0
    observer = (0.5, 10.5)
    if axis == "north":
        z = z.T.copy()
        observer = (10.5, 0.5)
    vis = viewshed(z, cell, observer, 10.0, 0.0, K_VISIBLE)
    line = vis[10] if axis == "east" else vis[:, 10]
    shadow_end = 300.0 * 10.0 / (10.0 - 4.0)
    assert shadow_end == pytest.approx(500.0)
    for i in range(1, 40):
        s = i * cell
        expected = True if i <= 10 else _wall_reference(10.0, 4.0, 300.0, 0.0, s, K_VISIBLE)
        assert bool(line[i]) is expected, (axis, i, s)
        if 10 < i:
            assert expected is (s > shadow_end)  # curvature moves the edge by centimetres only
    hidden = [i * cell for i in range(40) if not line[i]]
    assert min(hidden) == 330.0 and max(hidden) == 480.0  # cells inside (300 m, 500 m)


def test_wall_hides_a_high_aircraft_beyond_the_analytic_distance() -> None:
    # Eye 1.7 m, 30 m wall at 300 m, aircraft at 120 m: hidden beyond s* = 300·(120 − 1.7)/(30 − 1.7)
    # = 1,254.1 m (curvature shifts it by under a metre).
    cell = 30.0
    z = np.zeros((5, 80))
    z[:, 10] = 30.0
    vis = viewshed(z, cell, (0.5, 2.5), 1.7, 120.0, K_VISIBLE)
    s_star = 300.0 * (120.0 - 1.7) / (30.0 - 1.7)
    assert s_star == pytest.approx(1254.06, abs=0.01)
    for i in range(11, 80):
        s = i * cell
        assert bool(vis[2, i]) is _wall_reference(1.7, 30.0, 300.0, 120.0, s, K_VISIBLE)
        assert bool(vis[2, i]) is (s < s_star)


def test_curvature_hides_a_ground_target_at_10_km() -> None:
    # Level 0 m ground, eye at 0 m, target 10 km away: the Earth drops it by 10²/(2·(4/3)·6371) km =
    # 5.89 m, so it is hidden at 0 m and 5.8 m above the ground and seen at 6.0 m.
    cell = 25.0
    z = np.zeros((3, 401))
    observer = (0.5, 1.5)
    target_cell = (1, 400)  # 400 cells × 25 m = 10 km
    for height, seen in ((0.0, False), (5.8, False), (6.0, True)):
        vis = viewshed(z, cell, observer, 0.0, height, K_RADIO)
        assert bool(vis[target_cell]) is seen, height
    assert bool(viewshed(z, cell, observer, 0.0, 0.0, math.inf)[target_cell]) is True  # no curvature


def test_viewshed_of_a_400_by_400_grid_takes_under_two_seconds() -> None:
    r, c = np.mgrid[0:400, 0:400].astype(np.float64)
    z = 900.0 + 40.0 * np.sin(c / 11.0) * np.cos(r / 9.0)
    start = time.perf_counter()
    vis = viewshed(z, 30.0, (0.5, 0.5), 901.7, 120.0, K_VISIBLE)  # corner: the longest rays
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"{elapsed:.2f} s"
    assert 0.0 < vis.mean() < 1.0


def _exact_viewshed(terrain: np.ndarray, tops: np.ndarray, cell: float, observer: tuple[float, float],
                    eye: float, target: float, k: float) -> np.ndarray:
    """Reference: the straight line from the eye to every cell centre, sampled every 0.05 cell,
    against the bilinear terrain and the obstacle top of each crossed cell (the observer's and the
    target's own cells excluded)."""
    rows, cols = terrain.shape
    ox, oy = observer
    own = (math.floor(oy), math.floor(ox))
    curvature = 1.0 / (2.0 * k * EARTH_RADIUS_M)
    out = np.zeros(terrain.shape, dtype=bool)
    for r in range(rows):
        for c in range(cols):
            d = math.hypot(c + 0.5 - ox, r + 0.5 - oy)
            t = np.linspace(0.0, 1.0, int(d / 0.05) + 2)[1:-1]
            px, py = ox + (c + 0.5 - ox) * t, oy + (r + 0.5 - oy) * t
            ri, ci = np.floor(py).astype(int), np.floor(px).astype(int)
            top = tops[ri, ci].copy()
            top[((ri == r) & (ci == c)) | ((ri == own[0]) & (ci == own[1]))] = np.nan
            surface = np.fmax(grid_bilinear(terrain, px, py), top)
            s = d * t * cell
            horizon = np.max((surface - s * s * curvature - eye) / s) if s.size else -np.inf
            dc = d * cell
            out[r, c] = (terrain[r, c] + target - dc * dc * curvature - eye) / dc >= horizon - 1e-9
    return out


def test_viewshed_agrees_with_exact_lines_over_rough_terrain_and_towers() -> None:
    # Hills of ±15 m and twenty 40 m towers on 30 m cells; the sweep (rays every half cell, nearest
    # ray per cell) must agree with exact lines to every cell centre on at least 98 % of the cells.
    rng = np.random.default_rng(3)
    r, c = np.mgrid[0:40, 0:40].astype(np.float64)
    terrain = 900.0 + 15.0 * np.sin(c / 5.0) * np.cos(r / 4.0)
    tops = np.full(terrain.shape, np.nan)
    for i, j in zip(rng.integers(0, 40, 20), rng.integers(0, 40, 20), strict=True):
        tops[i, j] = terrain[i, j] + 40.0
    observer = (20.3, 17.6)
    eye = float(grid_bilinear(terrain, np.array([observer[0]]), np.array([observer[1]]))[0]) + 1.7
    for target in (0.0, 10.0, 60.0):  # about 34 %, 59 % and 94 % of the cells visible
        reference = _exact_viewshed(terrain, tops, 30.0, observer, eye, target, K_VISIBLE)
        swept = viewshed(terrain, 30.0, observer, eye, target, K_VISIBLE, obstacles_m=tops)
        assert 0.2 < reference.mean() < 0.99
        assert np.mean(swept != reference) <= 0.02, target


def test_a_low_eye_on_a_plane_slope_sees_the_ground() -> None:
    # A plane rising 3 m per 30 m cell in x and y: every line from an eye above the plane stays
    # above it, so the ground (target 0 m) is visible, up and down the slope. Sampling the cell
    # steps instead of the interpolated plane would bury a 1.7 m eye (about 3 % visible).
    r, c = np.mgrid[0:60, 0:60].astype(np.float64)
    plane = 100.0 + 3.0 * (c + r)
    vis = viewshed(plane, 30.0, (30.5, 30.5), float(plane[30, 30]) + 1.7, 0.0, math.inf)
    assert vis.mean() >= 0.98


def test_an_obstacle_in_the_observers_own_cell_does_not_block() -> None:
    z = np.full((41, 41), 500.0)
    tops = np.full(z.shape, np.nan)
    tops[20, 20] = 560.0  # a 60 m mast in the observer's cell
    assert viewshed(z, 30.0, (20.3, 20.6), 501.7, 2.0, K_VISIBLE, obstacles_m=tops).all()
    # The same mast in the next cell east casts a shadow behind it (and only there).
    tops[20, 20], tops[20, 22] = np.nan, 560.0
    vis = viewshed(z, 30.0, (20.5, 20.5), 501.7, 2.0, K_VISIBLE, obstacles_m=tops)
    assert not vis[20, 23:].any() and vis[20, :22].all() and vis[:, :20].all()


def test_no_data_cells_are_never_visible() -> None:
    z = np.full((20, 20), 100.0)
    z[5, 5] = np.nan
    vis = viewshed(z, 30.0, (10.5, 10.5), 101.7, 50.0, K_VISIBLE)
    assert not vis[5, 5]
    assert vis.sum() == 399


def test_flat_area_is_fully_visible(flat_pack: DataPack) -> None:
    result = _run(flat_pack, geometry=_square(400.0), observers=[_observer("Pilot", -400.0, -400.0)])
    assert result.combined_visible_pct == 100.0
    assert result.hidden_area_m2 == 0.0
    assert result.largest_hidden_patch is None
    pilot = result.observers[0]
    assert pilot.los_kind == "visual" and pilot.k_factor == pytest.approx(K_VISIBLE, abs=1e-4)
    assert pilot.ground_m == pytest.approx(900.0) and pilot.eye_amsl_m == pytest.approx(901.7)
    # The farthest area cell centre is the opposite corner, ≈ 800·√2 m away.
    assert pilot.max_visible_m == pytest.approx(800.0 * math.sqrt(2.0), abs=45.0)
    assert result.grid.grid_m == 30.0 and not result.grid.coarsened
    assert {"input", "observers", "visible", "hidden", "vlos_guidance", "radio_paths"} <= set(result.layers)
    assert result.layers["hidden"]["features"] == []
    LineOfSightResult.model_validate(json.loads(result.model_dump_json()))


def test_max_range_limits_visibility(flat_pack: DataPack) -> None:
    result = _run(flat_pack, geometry=_square(600.0), observers=[_observer("Pilot", 0.0, 0.0)], max_range_m=300.0,
                  radio_check=False)
    # Area cells within 300 m of the centre: about π·300² of the 1,200 m square.
    expected = 100.0 * math.pi * 300.0**2 / 1200.0**2
    assert result.combined_visible_pct == pytest.approx(expected, abs=1.5)
    assert result.radio is None and result.radio_los_pct is None


def test_missing_observer_is_assumed_at_the_area_centre(flat_pack: DataPack) -> None:
    result = _run(flat_pack, geometry=_square(300.0))
    assert [i.code for i in result.issues if i.code == "observer_assumed"] == ["observer_assumed"]
    pilot = result.observers[0]
    assert pilot.assumed and pilot.name == "Pilot" and pilot.height_agl_m == 1.7
    assert pilot.lon == pytest.approx(LON0, abs=1e-6) and pilot.lat == pytest.approx(LAT0, abs=1e-6)


def test_ground_station_only_assumes_the_pilot_there(flat_pack: DataPack) -> None:
    result = _run(flat_pack, geometry=_square(300.0), observers=[_observer("GCS", -300.0, 0.0, role="gcs")])
    codes = [i.code for i in result.issues]
    assert "pilot_assumed_at_gcs" in codes
    pilot, gcs = result.observers
    assert pilot.assumed and pilot.role == "pilot" and pilot.lon == gcs.lon
    assert gcs.los_kind == "radio" and gcs.k_factor == pytest.approx(K_RADIO, abs=1e-4)
    assert gcs.height_agl_m == 2.0
    assert result.radio is not None and result.radio.station_name == "GCS"
    assert result.radio_los_pct == gcs.visible_pct == 100.0


def test_adding_an_observer_never_lowers_combined_coverage(hilly_pack: DataPack) -> None:
    area = _square(1500.0)
    one = _run(hilly_pack, geometry=area, observers=[_observer("Pilot", -1500.0, -1500.0)], target_height_agl_m=10.0,
               radio_check=False)
    two = _run(hilly_pack, geometry=area, target_height_agl_m=10.0, radio_check=False,
               observers=[_observer("Pilot", -1500.0, -1500.0), _observer("Observer", 1200.0, 900.0, role="observer")])
    assert one.combined_visible_pct is not None and two.combined_visible_pct is not None
    assert 5.0 < one.combined_visible_pct < 95.0  # the hills hide part of the area
    assert two.combined_visible_pct >= one.combined_visible_pct
    assert two.combined_visible_pct >= max(o.visible_pct or 0.0 for o in two.observers)
    assert two.observers[0].visible_pct == one.observers[0].visible_pct
    patch = one.largest_hidden_patch
    assert patch is not None and patch.area_m2 == patch.cells * 30.0 * 30.0
    assert one.hidden_area_m2 is not None and patch.area_m2 <= one.hidden_area_m2
    # The visible layer is split by the number of observers that see each cell.
    assert {f["properties"]["observers"] for f in two.layers["visible"]["features"]} <= {1, 2}


def test_route_visibility_and_radio_paths_to_each_vertex(flat_pack: DataPack) -> None:
    a, b, c = _offset(-500.0, 0.0), _offset(0.0, 0.0), _offset(500.0, 200.0)
    route = {"type": "LineString", "coordinates": [list(a), list(b), list(c)]}
    result = _run(flat_pack, geometry=route, observers=[_observer("GCS", -500.0, -50.0, role="gcs"),
                                                        _observer("Pilot", -500.0, -40.0)])
    assert result.route is not None
    length = 500.0 + math.hypot(500.0, 200.0)
    assert result.route.length_m == pytest.approx(length, rel=2e-3)
    assert result.route.visible_pct == 100.0
    assert result.route.max_continuous_visible_m == pytest.approx(length, rel=2e-3)
    assert result.route.hidden_segments == []
    assert result.radio is not None
    assert [p.to_name for p in result.radio.paths] == ["Route point 1", "Route point 2", "Route point 3"]
    for path in result.radio.paths:
        assert path.fspl_db == pytest.approx(fspl_db(path.slant_m, 2400.0), abs=0.01)
        clearances = [s.clearance_pct for s in path.profile if s.clearance_pct is not None]
        assert path.worst_clearance_pct == pytest.approx(min(clearances), abs=0.05)
        assert not path.los_blocked
    assert {f["properties"]["band"] for f in result.layers["radio_paths"]["features"]} <= {"clear", "below"}
    assert all(f["properties"]["visible"] for f in result.layers["route"]["features"])


def test_radio_target_with_an_obstacle_on_the_path(tmp_path: Path) -> None:
    mid_lon, mid_lat = _offset(0.0, 0.0)
    obstacles = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [mid_lon, mid_lat]},
        "properties": {"kind": "building", "height_m": 60.0, "name": "Tower block"},
    }]
    pack = _make_pack(tmp_path / "obstacle", np.full((200, 200), 900.0), obstacles=obstacles)
    target_lon, target_lat = _offset(500.0, 0.0)
    result = _run(
        pack,
        geometry=_square(100.0, centre=(400.0, 300.0)),
        observers=[_observer("GCS", -500.0, 0.0, role="gcs", height=10.0)],
        radio_target={"name": "Relay mast", "lon": target_lon, "lat": target_lat, "height_agl_m": 30.0},
        frequency_mhz=5800.0,
    )
    assert result.radio is not None and len(result.radio.paths) == 1
    path = result.radio.paths[0]
    assert path.to_name == "Relay mast" and path.obstacles_on_path == 1
    assert path.distance_m == pytest.approx(1000.0, abs=1.0)
    assert path.los_blocked and path.below_threshold
    # At the obstacle (500 m) the LOS is 900 + 10 + 20·0.5 − bulge; the top is 960 m.
    los_mid = 920.0 - 500.0 * 500.0 / (2.0 * K_RADIO * EARTH_RADIUS_M)
    r1_mid = fresnel_radius_m(500.0, 500.0, 5800.0)
    assert path.worst_at_m == pytest.approx(500.0, abs=10.0)
    assert path.worst_clearance_pct == pytest.approx((los_mid - 960.0) / r1_mid * 100.0, abs=0.5)
    assert path.segments_below and path.segments_below[0].min_clearance_pct == path.worst_clearance_pct
    assert any(s.obstacle_m == pytest.approx(960.0) for s in path.profile)
    assert "radio_los_blocked" in [i.code for i in result.issues]
    # The same obstacle raises its viewshed cell: it is reported as used.
    assert result.grid.obstacles_used == 1


def test_beyond_guidance_share_uses_the_slant_distance(flat_pack: DataPack) -> None:
    result = _run(flat_pack, geometry=_square(1000.0), observers=[_observer("Pilot", 0.0, 0.0)],
                  target_height_agl_m=0.0, radio_check=False)
    assert result.vlos_guidance.basis == "uk_caa_500m" and result.vlos_guidance_m == 500.0
    # Ground target, eye 1.7 m: beyond ≈ everything outside a 500 m circle in a 2 km square.
    expected = 100.0 * (1.0 - math.pi * 500.0**2 / 2000.0**2)
    assert result.beyond_guidance_pct == pytest.approx(expected, abs=1.0)
    ring = result.layers["vlos_guidance"]["features"][0]
    assert ring["properties"]["radius_m"] == pytest.approx(500.0, abs=0.1)


def test_large_extent_coarsens_the_grid(flat_pack: DataPack) -> None:
    a, b = _offset(-7000.0, 0.0), _offset(7000.0, 0.0)
    result = _run(flat_pack, geometry={"type": "LineString", "coordinates": [list(a), list(b)]},
                  observers=[_observer("Pilot", 0.0, 0.0)], radio_check=False)
    assert result.grid.coarsened and result.grid.grid_m > 30.0
    assert max(result.grid.cols, result.grid.rows) <= MAX_GRID_SIDE
    assert "grid_coarsened" in [i.code for i in result.issues]


def test_a_coarse_grid_keeps_the_radio_profile_on_the_dem(tmp_path: Path) -> None:
    # A 52 km route coarsens the grid to about 132 m cells; the radio profile still follows the
    # ~55 m DEM, so a 200 m mast 60 m beside the path (outside its first Fresnel zone, r1 ≈ 33 m)
    # is not put on it (half a coarse cell, 66 m, would have).
    mast_lon, mast_lat = _offset(-8000.0, 60.0)
    obstacles = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [mast_lon, mast_lat]},
        "properties": {"kind": "mast", "height_m": 200.0},
    }]
    pack = _make_pack(tmp_path / "wide", np.full((1000, 1000), 900.0), obstacles=obstacles)
    a, b = _offset(-26_000.0, 0.0), _offset(26_000.0, 0.0)
    target_lon, target_lat = _offset(10_000.0, 0.0)
    result = _run(
        pack,
        geometry={"type": "LineString", "coordinates": [list(a), list(b)]},
        observers=[_observer("GCS", -26_000.0, 0.0, role="gcs", height=60.0)],
        radio_target={"name": "Relay", "lon": target_lon, "lat": target_lat, "height_agl_m": 60.0},
    )
    assert result.grid.coarsened and result.grid.grid_m >= 130.0
    path = result.radio.paths[0] if result.radio else None
    assert path is not None and path.distance_m == pytest.approx(36_000.0, rel=1e-3)
    assert path.obstacles_on_path == 0 and not path.los_blocked
    assert all(s.obstacle_m is None for s in path.profile)
    assert "every ≤ 55" in result.method  # min(cell, DEM pixel ≈ 55.3 m)


def test_far_observer_is_refused(flat_pack: DataPack) -> None:
    with pytest.raises(AnalysisError) as info:
        _run(flat_pack, geometry=_square(200.0), observers=[_observer("Pilot", 70_000.0, 0.0)])
    assert info.value.code == "area_too_large"


def test_observer_outside_the_dem_is_out_of_coverage(flat_pack: DataPack) -> None:
    with pytest.raises(AnalysisError) as info:
        _run(flat_pack, geometry=_square(200.0), observers=[_observer("Pilot", 0.0, 15_000.0)])
    assert info.value.code == "out_of_coverage"


def test_requires_a_dem() -> None:
    with pytest.raises(AnalysisError) as info:
        run_analysis_sync("line_of_sight", {"geometry": mapping(box(77.0, 12.0, 77.01, 12.01))})
    assert info.value.code == "datapack_missing"


def test_cancellation_stops_the_viewshed(flat_pack: DataPack) -> None:
    event = threading.Event()

    def progress(stage: str, fraction: float) -> None:
        if stage.startswith("Viewshed"):
            event.set()

    ctx = AnalysisContext(datapack=flat_pack, cancel_event=event, progress=progress)
    with pytest.raises(AnalysisCancelled):
        run(LineOfSightParams.model_validate({"geometry": _square(200.0)}), ctx)


def test_invalid_params_are_rejected() -> None:
    area = mapping(box(77.0, 12.0, 77.01, 12.01))
    for bad in (
        {"geometry": area, "observers": [{"name": "X", "role": "drone", "lon": 77.0, "lat": 12.0}]},
        {"geometry": area, "observers": [{"name": " ", "lon": 77.0, "lat": 12.0}]},
        {"geometry": area, "fresnel_clearance_pct": 120},
        {"geometry": area, "grid_m": 1},
        {"geometry": area, "observers": [{"name": f"O{i}", "lon": 77.0, "lat": 12.0} for i in range(11)]},
    ):
        with pytest.raises(AnalysisError) as info:
            run_analysis_sync("line_of_sight", bad)
        assert info.value.code == "invalid_params"


def test_bengaluru_survey_on_the_synthetic_pack(synthetic_pack: DataPack) -> None:
    area = {"type": "Polygon", "coordinates": [[[77.485, 12.900], [77.497, 12.900], [77.497, 12.904],
                                                 [77.490, 12.910], [77.485, 12.910], [77.485, 12.900]]]}
    envelope = run_analysis_sync(
        "line_of_sight",
        {"geometry": area, "observers": [{"name": "Pilot", "lon": 77.488, "lat": 12.902},
                                         {"name": "GCS", "role": "gcs", "lon": 77.4875, "lat": 12.9015}]},
        datapack=synthetic_pack,
    )
    result = LineOfSightResult.model_validate(envelope.result)
    assert envelope.kind == "line_of_sight" and envelope.datapack_synthetic
    # Smooth synthetic terrain: nearly all visible at 120 m; the three obstacles near the area
    # (65 m mast, 42 m block, 48 m tower) are raised onto the grid and cast thin shadows.
    assert result.grid.obstacles_used >= 3
    assert result.combined_visible_pct is not None and 90.0 <= result.combined_visible_pct < 100.0
    assert result.radio is not None and result.radio.paths[0].to_name == "Farthest corner"
    assert result.radio.paths[0].fspl_db == pytest.approx(fspl_db(result.radio.paths[0].slant_m, 2400.0), abs=0.01)
    codes = {i.code for i in result.issues}
    assert {"synthetic_data", "surface_model", "obstacles_raised"} <= codes
    assert "Geometric line-of-sight evidence" in result.disclaimer
    assert "d²/(2·k·R)" in result.method and "17.32" in result.method and "32.44" in result.method
    assert len(json.dumps(envelope.result)) < 300_000


def test_route_with_a_hidden_stretch(tmp_path: Path) -> None:
    # A 20 m ridge across the route 300 m from the pilot hides the ground behind it.
    rows, cols = 200, 200
    z = np.full((rows, cols), 900.0)
    z[:, 106:108] = 920.0  # about 330–440 m east of the centre at 0.0005° (≈ 54 m) pixels
    pack = _make_pack(tmp_path / "ridge", z)
    a, b = _offset(0.0, 0.0), _offset(1500.0, 0.0)
    result = _run(pack, geometry={"type": "LineString", "coordinates": [list(a), list(b)]},
                  observers=[_observer("Pilot", -100.0, 0.0)], target_height_agl_m=2.0, radio_check=False)
    assert result.route is not None
    assert 0.0 < result.route.visible_pct < 100.0
    assert result.route.hidden_segments
    first = result.route.hidden_segments[0]
    assert first.start_m > 300.0  # the ridge itself is seen; the ground behind it is not
    assert result.route.max_continuous_visible_m < result.route.length_m
    hidden_layer = [f for f in result.layers["route"]["features"] if not f["properties"]["visible"]]
    assert len(hidden_layer) == len(result.route.hidden_segments)
    assert LineString(hidden_layer[0]["geometry"]["coordinates"]).length > 0
