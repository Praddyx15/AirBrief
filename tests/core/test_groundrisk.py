"""Ground-risk analysis: iGRC table lookup, the SORA 2.5 operational volume (contingency volume,
ground risk buffer, density grid, adjacent area, assemblies) and runs on synthetic data packs."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from shapely.geometry import box, shape

from airbrief_core.analyses import groundrisk
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.analyses.groundrisk import (
    GRAVITY_MS2,
    adjacent_distance_m,
    assembly_band,
    auto_grid_m,
    ballistic_distance_m,
    buffer_distance_m,
    build_operational_volume,
    contingency_volume,
    density_band,
    density_grid_collection,
    igrc_lookup,
    igrc_table,
    run,
)
from airbrief_core.data import DataPack
from airbrief_core.data.assemblies import load_assemblies, parse_capacity
from airbrief_core.data.datapack import DataPackManifest, LayerEntry, write_manifest
from airbrief_core.data.raster import geographic_cell_area_km2
from airbrief_core.errors import AnalysisCancelled, AnalysisError
from airbrief_core.geo.geodesic import GEOD, geodesic_area_perimeter
from airbrief_core.geo.transform import geometry_to_geojson
from airbrief_core.models.analyses import GroundRiskParams, GroundRiskResult
from airbrief_core.models.ground_risk_volume import AssemblyInput, OperationalVolumeParams
from airbrief_core.models.presets import (
    DISCLAIMER_GROUND_RISK,
    SORA_EVIDENCE_LABEL,
    SORA_IGRC_ROWS,
    UAS_PRESETS,
)
from seed.datapack import assemblies_from_overpass, assemblies_query

CITY_CENTRE = (77.5946, 12.9716)
SURVEY = {
    "type": "Polygon",
    "coordinates": [[[77.485, 12.900], [77.497, 12.900], [77.497, 12.904], [77.490, 12.910],
                     [77.485, 12.910], [77.485, 12.900]]],
}  # the bengaluru_survey sample area


def _square(lon: float, lat: float, half_deg: float) -> dict:
    return geometry_to_geojson(box(lon - half_deg, lat - half_deg, lon + half_deg, lat + half_deg))


@pytest.mark.parametrize(
    ("density", "expected", "band"),
    [
        (0.0, 2, "< 5 people/km²"),
        (4.999, 2, "< 5 people/km²"),
        (5.0, 3, "< 50 people/km²"),
        (49.9, 3, "< 50 people/km²"),
        (50.0, 4, "< 500 people/km²"),
        (500.0, 5, "< 5,000 people/km²"),
        (5_000.0, 6, "< 50,000 people/km²"),
        (49_999.0, 6, "< 50,000 people/km²"),
        (50_000.0, 7, "≥ 50,000 people/km²"),
        (1e7, 7, "≥ 50,000 people/km²"),
    ],
)
def test_density_band_boundaries(density: float, expected: int, band: str) -> None:
    igrc, column, band_label, small = igrc_lookup(1.0, 25.0, density)
    assert igrc == expected
    assert band_label == band
    assert column == "≤ 1 m, ≤ 25 m/s"
    assert small is False
    assert density_band(density) == band


@pytest.mark.parametrize(
    ("dim", "speed", "column_index"),
    [
        (1.0, 25.0, 0),
        (0.5, 10.0, 0),
        (1.01, 10.0, 1),
        (0.5, 25.1, 1),
        (3.0, 35.0, 1),
        (3.5, 20.0, 2),
        (8.0, 75.0, 2),
        (20.0, 120.0, 3),
        (15.0, 130.0, 4),  # over 120 m/s moves to the 40 m / 200 m/s column
        (21.0, 10.0, 4),
        (40.0, 200.0, 4),
    ],
)
def test_column_boundaries(dim: float, speed: float, column_index: int) -> None:
    igrc, _, _, _ = igrc_lookup(dim, speed, 100.0)  # "< 500" row
    assert igrc == SORA_IGRC_ROWS[3][2][column_index]


def test_beyond_last_column_is_out_of_scope() -> None:
    igrc, column, band, small = igrc_lookup(41.0, 50.0, 10.0)
    assert igrc is None
    assert "Beyond table" in column
    assert band == "< 50 people/km²"
    assert small is False


def test_none_cell_is_out_of_scope() -> None:
    igrc, column, band, _ = igrc_lookup(8.0, 75.0, 60_000.0)
    assert igrc is None
    assert column == "≤ 8 m, ≤ 75 m/s"
    assert band.startswith("≥ 50,000")
    # The first two columns are still in scope at that density.
    assert igrc_lookup(3.0, 35.0, 60_000.0)[0] == 8


def test_controlled_ground_area_uses_first_row() -> None:
    igrc, _, band, _ = igrc_lookup(1.0, 10.0, 30_000.0, controlled=True)
    assert igrc == 1
    assert band == "Controlled ground area"
    assert igrc_lookup(20.0, 100.0, 1e6, controlled=True)[0] == 3
    assert igrc_lookup(41.0, 100.0, 0.0, controlled=True)[0] is None


def test_small_uas_rule() -> None:
    igrc, _, _, small = igrc_lookup(0.25, 16.0, 100_000.0, takeoff_mass_kg=0.249)
    assert (igrc, small) == (1, True)
    # Boundaries are inclusive.
    assert igrc_lookup(0.3, 25.0, 10_000.0, takeoff_mass_kg=0.25)[0:4:3] == (1, True)
    # Too heavy, too fast, or no mass given: the table applies.
    assert igrc_lookup(0.3, 16.0, 10_000.0, takeoff_mass_kg=0.26) == (6, "≤ 1 m, ≤ 25 m/s", "< 50,000 people/km²", False)
    assert igrc_lookup(0.3, 26.0, 0.0, takeoff_mass_kg=0.2)[0] == 3
    assert igrc_lookup(0.3, 16.0, 10_000.0)[3] is False


def test_lookup_rejects_bad_input() -> None:
    with pytest.raises(AnalysisError) as exc:
        igrc_lookup(1.0, 10.0, -1.0)
    assert exc.value.code == "invalid_params"
    with pytest.raises(AnalysisError):
        igrc_lookup(0.0, 10.0, 1.0)
    with pytest.raises(AnalysisError):
        igrc_lookup(1.0, 10.0, math.nan)


def test_buffer_rules() -> None:
    geom = _square(*CITY_CENTRE, 0.001)
    one = GroundRiskParams(geometry=geom, altitude_agl_m=90.0)
    assert buffer_distance_m(one) == pytest.approx(90.0)
    ballistic = GroundRiskParams(geometry=geom, altitude_agl_m=120.0, max_speed_ms=15.0, buffer_rule="ballistic")
    assert buffer_distance_m(ballistic) == pytest.approx(15.0 * math.sqrt(240.0 / GRAVITY_MS2))


def test_city_centre_run(synthetic_pack: DataPack) -> None:
    params = GroundRiskParams(geometry=_square(*CITY_CENTRE, 0.003), h3_resolution=9)
    result = run(params, AnalysisContext(datapack=synthetic_pack))

    assert 15_000.0 < result.max_density_per_km2 < 25_000.0
    assert result.density_band == "< 50,000 people/km²"
    assert "< 50,000" in result.density_band
    assert result.igrc == 6
    assert result.in_scope is True
    assert result.small_uas_rule is False
    assert result.column == "≤ 1 m, ≤ 25 m/s"
    assert result.buffer_m == pytest.approx(120.0)
    assert result.p95_density_per_km2 <= result.max_density_per_km2
    assert result.mean_density_per_km2 <= result.p95_density_per_km2 + 1e-6
    assert result.disclaimer == DISCLAIMER_GROUND_RISK
    assert set(result.layers) == {"input", "footprint"}
    for layer in result.layers.values():
        assert layer["type"] == "FeatureCollection"
        assert layer["features"]

    assert result.cells
    assert 0 < len(result.hotspots) <= 10
    dens = [c.density_per_km2 for c in result.hotspots]
    assert dens == sorted(dens, reverse=True)
    assert dens[0] == pytest.approx(result.max_density_per_km2, rel=1e-6)

    # Population ≈ density × footprint area (smooth field near the peak).
    assert result.footprint_area_km2 > 0.4
    per_km2 = result.population_in_footprint / result.footprint_area_km2
    assert 10_000.0 < per_km2 < 25_000.0
    assert any(i.code == "synthetic_data" for i in result.issues)


def test_p95_and_controlled_area(synthetic_pack: DataPack) -> None:
    params = GroundRiskParams(
        geometry=_square(*CITY_CENTRE, 0.01),
        density_statistic="p95",
        controlled_ground_area=True,
        max_dimension_m=2.0,
        max_speed_ms=30.0,
    )
    result = run(params, AnalysisContext(datapack=synthetic_pack))
    assert result.density_statistic == "p95"
    assert result.igrc == 1
    assert result.density_band == "Controlled ground area"
    assert result.column == "≤ 3 m, ≤ 35 m/s"


def test_point_geometry_and_small_uas(synthetic_pack: DataPack) -> None:
    params = GroundRiskParams(
        geometry={"type": "Point", "coordinates": list(CITY_CENTRE)},
        max_dimension_m=0.25,
        max_speed_ms=16.0,
        takeoff_mass_kg=0.249,
        altitude_agl_m=60.0,
    )
    result = run(params, AnalysisContext(datapack=synthetic_pack))
    assert result.igrc == 1
    assert result.small_uas_rule is True
    # A 60 m disc: about π·0.06² km².
    assert result.footprint_area_km2 == pytest.approx(math.pi * 0.06**2, rel=0.02)


def test_no_datapack_raises() -> None:
    params = GroundRiskParams(geometry=_square(*CITY_CENTRE, 0.003))
    with pytest.raises(AnalysisError) as exc:
        run(params, AnalysisContext())
    assert exc.value.code == "datapack_missing"


def test_outside_coverage(synthetic_pack: DataPack) -> None:
    params = GroundRiskParams(geometry=_square(10.0, 50.0, 0.003))
    with pytest.raises(AnalysisError) as exc:
        run(params, AnalysisContext(datapack=synthetic_pack))
    assert exc.value.code == "out_of_coverage"


def test_cancellation(synthetic_pack: DataPack) -> None:
    event = threading.Event()
    event.set()
    params = GroundRiskParams(geometry=_square(*CITY_CENTRE, 0.003))
    with pytest.raises(AnalysisCancelled):
        run(params, AnalysisContext(datapack=synthetic_pack, cancel_event=event))


# An independent copy of JARUS SORA 2.5 Main Body Table 2 (None = "not part of SORA").
TABLE2_COLUMNS = [(1.0, 25.0), (3.0, 35.0), (8.0, 75.0), (20.0, 120.0), (40.0, 200.0)]
TABLE2 = [
    [1, 1, 2, 3, 3],  # controlled ground area
    [2, 3, 4, 5, 6],  # < 5
    [3, 4, 5, 6, 7],  # < 50
    [4, 5, 6, 7, 8],  # < 500
    [5, 6, 7, 8, 9],  # < 5,000
    [6, 7, 8, 9, 10],  # < 50,000
    [7, 8, None, None, None],  # > 50,000
]
# Two densities inside each band: its lower bound and just below its upper bound.
ROW_DENSITIES = [(30_000.0, 30_000.0), (0.0, 4.999), (5.0, 49.99), (50.0, 499.9), (500.0, 4_999.0),
                 (5_000.0, 49_999.0), (50_000.0, 1e7)]
CELLS = [(row, col) for row in range(7) for col in range(5)]


@pytest.mark.parametrize(("row", "col"), CELLS, ids=[f"row{r}-col{c}" for r, c in CELLS])
def test_table2_every_cell(row: int, col: int) -> None:
    dim, speed = TABLE2_COLUMNS[col]
    prev_dim, prev_speed = TABLE2_COLUMNS[col - 1] if col else (0.0, 0.0)
    expected = TABLE2[row][col]
    for density in ROW_DENSITIES[row]:
        # The column's own limits, and an aircraft just past the previous column on both counts.
        for d, v in ((dim, speed), (prev_dim + 0.01, prev_speed + 0.1)):
            igrc, column, _band, small = igrc_lookup(d, v, density, controlled=row == 0)
            assert igrc == expected, (d, v, density)
            assert column == f"≤ {dim:g} m, ≤ {speed:g} m/s"
            assert small is False
    table = igrc_table(dim, speed, ROW_DENSITIES[row][1], controlled=row == 0)
    assert (table.row_index, table.column_index) == (row, col)
    assert table.rows[row].values[col] == expected


def test_20m_column_pairs_with_120_ms() -> None:
    assert igrc_lookup(20.0, 120.0, 100.0)[0:2] == (7, "≤ 20 m, ≤ 120 m/s")
    # 121 m/s at 20 m leaves the 20 m column for the 40 m / 200 m/s one.
    assert igrc_lookup(20.0, 121.0, 100.0)[0:2] == (8, "≤ 40 m, ≤ 200 m/s")
    assert igrc_table(20.0, 121.0, 100.0).column_index == 4


def test_above_50k_columns_3_to_5_are_out_of_scope() -> None:
    for dim, speed in TABLE2_COLUMNS[2:]:
        igrc, _, band, _ = igrc_lookup(dim, speed, 50_000.0)
        assert igrc is None and band == "≥ 50,000 people/km²"
    assert [igrc_lookup(d, v, 50_000.0)[0] for d, v in TABLE2_COLUMNS[:2]] == [7, 8]


def test_small_uas_rule_boundaries() -> None:
    # ≤ 250 g and ≤ 25 m/s: iGRC 1 regardless of density (both limits inclusive).
    assert igrc_lookup(0.3, 25.0, 1e6, takeoff_mass_kg=0.25)[0::3] == (1, True)
    assert igrc_lookup(0.3, 25.1, 1e6, takeoff_mass_kg=0.25)[0::3] == (8, False)
    assert igrc_lookup(0.3, 25.0, 1e6, takeoff_mass_kg=0.251)[0::3] == (7, False)


def _volume(**kw: object) -> OperationalVolumeParams:
    return OperationalVolumeParams.model_validate(kw)


@pytest.mark.parametrize(("theta", "s_cm"), [(45.0, 225.0 / (2 * GRAVITY_MS2)), (30.0, 19.8697)])
def test_multirotor_contingency_volume_by_hand(theta: float, s_cm: float) -> None:
    # v = 15 m/s: S_CM = v²/(2·g·tan θ); θ = 30° gives 225 / (2 × 9.80665 × 0.57735) ≈ 19.9 m.
    s_cv, h_cv, grb, terms = contingency_volume(
        _volume(pitch_angle_deg=theta), speed_ms=15.0, fg_height_m=120.0, dimension_m=1.0, buffer_rule="one_to_one",
    )
    by_name = {t.name: t for t in terms}
    assert by_name["S_CM"].value_m == pytest.approx(s_cm, abs=5e-4)
    assert by_name["S_R"].value_m == pytest.approx(15.0)
    assert s_cv == pytest.approx(3.0 + 1.0 + 1.0 + 15.0 + s_cm, abs=5e-4)
    # H_CV = H_FG + H_alt + v·t_R·sin 45° + v²/(2g) = 120 + 4 + 10.6066 + 11.4718 = 146.0784 m.
    assert h_cv == pytest.approx(120.0 + 4.0 + 15.0 * math.sqrt(0.5) + 225.0 / (2 * GRAVITY_MS2), abs=1e-6)
    assert h_cv == pytest.approx(146.0784, abs=1e-4)
    assert grb == pytest.approx(h_cv + 0.5)  # 1:1 rule plus half the characteristic dimension
    assert "tan " in by_name["S_CM"].formula and "= " in by_name["S_CM"].formula


def test_fixed_wing_turn_and_ballistic_buffer() -> None:
    s_cv, h_cv, grb, terms = contingency_volume(
        _volume(aircraft="fixed_wing", bank_angle_deg=30.0, reaction_time_s=2.0, gnss_error_m=0.0,
                position_error_m=0.0, map_error_m=0.0, altimetry_error_m=1.0, climb_angle_deg=30.0),
        speed_ms=20.0, fg_height_m=100.0, dimension_m=2.0, buffer_rule="ballistic",
    )
    # Turn radius v²/(g·tan φ) = 400 / (9.80665 × 0.577350) = 400 / 5.661873 = 70.648 m.
    s_cm = 400.0 / (GRAVITY_MS2 * math.tan(math.radians(30.0)))
    assert s_cm == pytest.approx(70.6480, abs=1e-4)
    assert {t.name: t for t in terms}["S_CM"].value_m == pytest.approx(s_cm)
    assert s_cv == pytest.approx(40.0 + s_cm)
    assert h_cv == pytest.approx(100.0 + 1.0 + 40.0 * 0.5 + 400.0 / (2 * GRAVITY_MS2))
    assert grb == pytest.approx(20.0 * math.sqrt(2.0 * h_cv / GRAVITY_MS2) + 1.0)
    assert grb == pytest.approx(ballistic_distance_m(20.0, h_cv) + 1.0)
    assert [t.name for t in terms] == ["S_GNSS", "S_pos", "S_map", "S_R", "S_CM", "H_FG", "H_alt", "H_R", "H_CM", "S_GRB"]


@pytest.mark.parametrize(("speed", "raw", "clamped"), [(10.0, 1_800.0, 5_000.0), (100.0, 18_000.0, 18_000.0),
                                                         (250.0, 45_000.0, 35_000.0)])
def test_adjacent_distance_clamp(speed: float, raw: float, clamped: float) -> None:
    assert adjacent_distance_m(speed) == pytest.approx((raw, clamped))


@pytest.mark.parametrize(("height", "grid"), [(30.0, 200.0), (120.0, 200.0), (152.4, 200.0), (152.5, 400.0),
                                              (304.8, 400.0), (305.0, 1_000.0), (762.0, 1_000.0), (1_500.0, 2_000.0)])
def test_auto_grid_follows_table_4(height: float, grid: float) -> None:
    assert auto_grid_m(height) == grid


@pytest.mark.parametrize(("people", "band"), [(None, "unknown"), (0, "lt_40k"), (39_999, "lt_40k"),
                                              (40_000, "40k_400k"), (400_000, "40k_400k"), (400_001, "gt_400k")])
def test_assembly_bands(people: int | None, band: str) -> None:
    assert assembly_band(people) == band


def test_presets_carry_volume_defaults() -> None:
    assert UAS_PRESETS["fixed_wing"].volume.aircraft == "fixed_wing"
    assert UAS_PRESETS["mapping_quad"].volume.aircraft == "multirotor"
    assert all(p.volume.gnss_error_m >= 0 for p in UAS_PRESETS.values())


def test_volume_inputs_are_validated() -> None:
    point = {"type": "Point", "coordinates": [10.0, 45.0]}
    with pytest.raises(ValidationError):
        GroundRiskParams(geometry=point, assemblies=[AssemblyInput(name="Fair", geometry=point)])
    with pytest.raises(ValidationError):
        GroundRiskParams(geometry=point, grid_m=200.0)
    with pytest.raises(ValidationError):
        AssemblyInput(name="Line", geometry={"type": "LineString", "coordinates": [[0, 0], [1, 1]]})
    with pytest.raises(ValidationError):
        AssemblyInput.model_validate({"name": "Fair", "geometry": point, "start_utc": "2026-10-11T10:00:00Z",
                                      "end_utc": "2026-10-11T09:00:00Z"})
    with pytest.raises(ValidationError):
        OperationalVolumeParams.model_validate({"pitch_angle_deg": 90.0})


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Point", "coordinates": [400.0, 12.9]},  # would wrap to 40° E and drop out silently
        {"type": "Point", "coordinates": [77.49, 95.0]},
        {"type": "Point", "coordinates": [77.49]},
        {"type": "Point", "coordinates": ["77.49", "12.9"]},
        {"type": "Polygon", "coordinates": []},
        {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 91], [0, 0]]]]},
    ],
)
def test_assembly_coordinates_are_checked(geometry: dict) -> None:
    with pytest.raises(ValidationError):
        AssemblyInput(name="Fair", geometry=geometry)


def test_assembly_times_are_utc() -> None:
    # A time without an offset is UTC; one with an offset is converted, so the two always compare.
    fair = AssemblyInput.model_validate({"name": "Fair", "geometry": POINT, "start_utc": "2026-10-11T10:00:00",
                                         "end_utc": "2026-10-11T17:30:00+05:30"})
    assert fair.start_utc is not None and fair.start_utc.isoformat() == "2026-10-11T10:00:00+00:00"
    assert fair.end_utc is not None and fair.end_utc.isoformat() == "2026-10-11T12:00:00+00:00"
    with pytest.raises(ValidationError):  # 15:00+05:30 is 09:30 UTC, before the start
        AssemblyInput.model_validate({"name": "Fair", "geometry": POINT, "start_utc": "2026-10-11T10:00:00",
                                      "end_utc": "2026-10-11T15:00:00+05:30"})


def test_fixed_wing_at_15_ms_and_one_to_one_buffer() -> None:
    # S_CM = v²/(g·tan φ) = 225 / (9.80665 × tan 30°) = 225 / 5.661873 = 39.7395 m (twice the
    # multirotor stop at θ = 30°); 1:1 GRB = H_CV + c/2 with c = 2 m.
    s_cv, h_cv, grb, terms = contingency_volume(
        _volume(aircraft="fixed_wing", bank_angle_deg=30.0), speed_ms=15.0, fg_height_m=120.0, dimension_m=2.0,
        buffer_rule="one_to_one",
    )
    s_cm = {t.name: t for t in terms}["S_CM"].value_m
    assert s_cm == pytest.approx(39.7395, abs=1e-4)
    assert s_cv == pytest.approx(3.0 + 1.0 + 1.0 + 15.0 + 39.7395, abs=1e-4)
    assert h_cv == pytest.approx(146.0784, abs=1e-4)  # H_CV does not depend on the manoeuvre family
    assert grb == pytest.approx(h_cv + 1.0)


def test_volumes_nest() -> None:
    fg = shape(_square(10.0, 45.0, 0.002))
    vol = build_operational_volume(fg, 40.0, 150.0, 5_000.0)
    assert vol.adjacent_outer is not None
    assert vol.contingency.contains(vol.flight_geography)
    assert vol.footprint.contains(vol.contingency)
    assert vol.adjacent_outer.contains(vol.footprint)
    # The outer edges sit at the buffer distances from the flight geography.
    assert vol.contingency.exterior.distance(vol.flight_geography) == pytest.approx(40.0, rel=0.01)
    assert vol.footprint.exterior.distance(vol.flight_geography) == pytest.approx(190.0, rel=0.01)
    assert vol.adjacent_outer.exterior.distance(vol.flight_geography) == pytest.approx(5_040.0, rel=0.01)


LON0, LAT0 = 10.0, 45.0
POINT = {"type": "Point", "coordinates": [LON0, LAT0]}
MULTIROTOR_45 = {"aircraft": "multirotor", "pitch_angle_deg": 45.0}
S_CV_15 = 3.0 + 1.0 + 1.0 + 15.0 + 225.0 / (2 * GRAVITY_MS2)  # v = 15 m/s, θ = 45°


def _raster_pack(
    root: Path,
    bbox: tuple[float, float, float, float],
    res: float,
    density: Callable[[np.ndarray, np.ndarray], np.ndarray],
) -> DataPack:
    """A pack with only a population raster: ``density(lon, lat)`` people/km² at pixel centres."""
    import rasterio
    from rasterio.transform import from_origin

    min_lon, min_lat, max_lon, max_lat = bbox
    width = int(round((max_lon - min_lon) / res))
    height = int(round((max_lat - min_lat) / res))
    rows = np.arange(height, dtype=float)
    lat_top = max_lat - rows * res
    lat_bot = max_lat - (rows + 1.0) * res
    area_km2 = geographic_cell_area_km2(lat_top, lat_bot, res)[:, None]
    lons = min_lon + (np.arange(width) + 0.5) * res
    grid_lon, grid_lat = np.meshgrid(lons, (lat_top + lat_bot) / 2.0)
    people = (density(grid_lon, grid_lat) * area_km2).astype(np.float32)
    root.mkdir(parents=True, exist_ok=True)
    with rasterio.open(root / "population.tif", "w", driver="GTiff", width=width, height=height, count=1,
                       dtype="float32", crs="EPSG:4326", transform=from_origin(min_lon, max_lat, res, res),
                       nodata=-99999.0) as dst:
        dst.write(people, 1)
    write_manifest(root, DataPackManifest(name=root.name, bbox=list(bbox),
                                          layers={"population": LayerEntry(file="population.tif", source="test")}))
    return DataPack(root)


UNIFORM = 1_234.5


@pytest.fixture(scope="module")
def uniform_pack(tmp_path_factory: pytest.TempPathFactory) -> DataPack:
    root = tmp_path_factory.mktemp("uniform") / "uniform"
    return _raster_pack(root, (LON0 - 0.15, LAT0 - 0.15, LON0 + 0.15, LAT0 + 0.15), 0.005,
                        lambda lo, la: np.full(lo.shape, UNIFORM))


def _run(pack: DataPack, **kw: object) -> GroundRiskResult:
    base: dict[str, object] = {"geometry": POINT, "max_dimension_m": 1.0, "max_speed_ms": 15.0,
                               "altitude_agl_m": 120.0, "volume": MULTIROTOR_45}
    base.update(kw)
    return run(GroundRiskParams.model_validate(base), AnalysisContext(datapack=pack))


def test_uniform_raster_adjacent_mean_equals_density(uniform_pack: DataPack) -> None:
    result = _run(uniform_pack)
    assert result.evidence_label == SORA_EVIDENCE_LABEL
    assert result.max_density_per_km2 == pytest.approx(UNIFORM, rel=1e-5)
    assert result.adjacent_mean_density_per_km2 == pytest.approx(UNIFORM, rel=1e-5)
    assert result.adjacent_coverage == pytest.approx(1.0)
    vol = result.volume
    assert vol is not None
    assert vol.s_cv_m == pytest.approx(S_CV_15, abs=1e-3)
    assert vol.adjacent_outer_km == pytest.approx(5.0)
    assert vol.adjacent_distance_km == pytest.approx(2.7)
    # Ring between the GRB edge (S_CV + S_GRB) and S_CV + 5 km around a point.
    inner = vol.s_cv_m + vol.grb_m
    ring_km2 = math.pi * ((vol.s_cv_m + 5_000.0) ** 2 - inner**2) / 1e6
    assert result.adjacent_area_km2 == pytest.approx(ring_km2, rel=0.01)
    assert result.adjacent_people == pytest.approx(UNIFORM * ring_km2, rel=0.03)
    assert result.igrc == 5 and result.density_band == "< 5,000 people/km²"
    assert result.igrc_table is not None and (result.igrc_table.row_index, result.igrc_table.column_index) == (4, 0)
    assert not [i for i in result.issues if i.code == "partial_coverage"]
    assert {i.code for i in result.issues} >= {"assemblies_layer_missing"}
    assert set(result.layers) == {"input", "footprint", "flight_geography", "contingency_volume",
                                  "ground_risk_buffer", "adjacent_area", "assemblies"}
    assert result.disclaimer.startswith(SORA_EVIDENCE_LABEL)
    assert "S_CV = S_GNSS + S_pos + S_map + S_R + S_CM" in result.method
    assert "clamp(v × 180 s, 5 km, 35 km)" in result.method


def test_result_layers_nest(uniform_pack: DataPack) -> None:
    result = _run(uniform_pack, geometry=_square(LON0, LAT0, 0.002))
    fg = shape(result.layers["flight_geography"]["features"][0]["geometry"])
    cv = shape(result.layers["contingency_volume"]["features"][0]["geometry"])
    footprint = shape(result.layers["footprint"]["features"][0]["geometry"])
    grb_ring = shape(result.layers["ground_risk_buffer"]["features"][0]["geometry"])
    adjacent = shape(result.layers["adjacent_area"]["features"][0]["geometry"])
    assert cv.contains(fg) and footprint.contains(cv)
    assert grb_ring.intersection(cv).area < 1e-12 and footprint.buffer(1e-9).contains(grb_ring)
    assert adjacent.intersection(footprint).area < 1e-12


def test_partial_coverage_of_the_adjacent_area(tmp_path: Path) -> None:
    # The pack ends 0.03° (≈2.4 km) west of the flight geography: part of the 5 km ring is outside.
    pack = _raster_pack(tmp_path / "edge", (LON0 - 0.03, LAT0 - 0.15, LON0 + 0.15, LAT0 + 0.15), 0.005,
                        lambda lo, la: np.full(lo.shape, UNIFORM))
    result = _run(pack)
    partial = [i for i in result.issues if i.code == "partial_coverage"]
    assert partial and "adjacent area" in partial[0].message
    assert result.adjacent_coverage is not None and 0.5 < result.adjacent_coverage < 0.99
    # The average covers the part inside the pack only, so a uniform raster still gives its value.
    assert result.adjacent_mean_density_per_km2 == pytest.approx(UNIFORM, rel=1e-5)


@pytest.fixture(scope="module")
def step_pack(tmp_path_factory: pytest.TempPathFactory) -> DataPack:
    """10 people/km² west of 10.0008°E, 20,000 people/km² east of it (≈63 m east of LON0)."""
    root = tmp_path_factory.mktemp("step") / "step"
    return _raster_pack(root, (9.98, 44.98, 10.02, 45.02), 0.0001,
                        lambda lo, la: np.where(lo >= 10.0008, 20_000.0, 10.0))


def test_density_comes_from_the_whole_footprint(step_pack: DataPack) -> None:
    # The dense block starts ≈63 m east of the flight geography (a point): outside the FG and the
    # CV (≈31 m), inside the GRB (≈178 m). Its fully covered 50 m cells set the iGRC density.
    result = _run(step_pack, grid_m=50.0, adjacent_area=False)
    assert result.max_density_per_km2 == pytest.approx(20_000.0, rel=1e-5)
    assert result.igrc == 6 and result.density_band == "< 50,000 people/km²"
    grid = result.density_grid
    assert grid is not None and grid.grid_m == 50.0 and grid.samples_per_cell == 64
    assert grid.hotspots[0].lon > 10.0008
    assert result.volume is not None and result.volume.grid_source == "user"
    # 1 km west, the footprint never reaches the dense block.
    west = {"type": "Point", "coordinates": [LON0 - 0.0127, LAT0]}
    far = _run(step_pack, geometry=west, grid_m=50.0, adjacent_area=False)
    assert far.max_density_per_km2 == pytest.approx(10.0, rel=1e-5)
    assert far.igrc == 3


def _east(distance_m: float) -> list[float]:
    lon, lat, _ = GEOD.fwd(LON0, LAT0, 90.0, distance_m)
    return [float(lon), float(lat)]


def test_assemblies_within_one_km(uniform_pack: DataPack) -> None:
    near = {"name": "Fair at 999 m", "geometry": {"type": "Point", "coordinates": _east(S_CV_15 + 999.0)},
            "expected_people": 50_000, "start_utc": "2026-10-11T10:00:00Z", "end_utc": "2026-10-11T18:00:00Z"}
    far = {"name": "Fair at 1001 m", "geometry": {"type": "Point", "coordinates": _east(S_CV_15 + 1_001.0)},
           "expected_people": 500_000}
    ground = geometry_to_geojson(box(_east(450.0)[0], LAT0 - 0.0005, _east(600.0)[0], LAT0 + 0.0005))
    venue = {"name": "Cricket ground", "geometry": ground}
    result = _run(uniform_pack, adjacent_area=False, assemblies=[near, far, venue])
    assert [a.name for a in result.assemblies] == ["Cricket ground", "Fair at 999 m"]  # nearest first
    ground_hit, fair = result.assemblies
    assert ground_hit.band == "unknown" and ground_hit.band_label == "Capacity unknown"
    assert ground_hit.distance_m == pytest.approx(450.0 - S_CV_15, abs=1.0)
    assert fair.distance_m == pytest.approx(999.0, abs=0.1)
    assert fair.band == "40k_400k" and fair.capacity == 50_000 and fair.source == "user"
    assert fair.start_utc is not None and fair.start_utc.startswith("2026-10-11T10:00:00")
    assert len(result.layers["assemblies"]["features"]) == 2


def test_assembly_inside_the_footprint_is_flagged(uniform_pack: DataPack) -> None:
    inside = {"name": "Car boot sale", "geometry": {"type": "Point", "coordinates": _east(100.0)}}
    result = _run(uniform_pack, adjacent_area=False, assemblies=[inside])
    assert result.assemblies[0].distance_m == pytest.approx(100.0 - S_CV_15, abs=0.5)
    assert any(i.code == "assembly_in_footprint" and "Car boot sale" in i.message for i in result.issues)


def test_wide_buffer_makes_assemblies_context_only(uniform_pack: DataPack) -> None:
    result = _run(uniform_pack, altitude_agl_m=1_200.0, adjacent_area=False)
    assert result.volume is not None and result.volume.grb_m > 1_000.0
    assert result.volume.grid_m == 2_000.0  # Table 4: 1,200 m lies between 2,500 ft and 5,000 ft
    assert any(i.code == "assemblies_not_applicable" for i in result.issues)


def test_adjacent_area_not_required_when_the_buffer_is_wider(
    uniform_pack: DataPack, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(groundrisk, "adjacent_distance_m", lambda speed: (100.0, 100.0))
    result = _run(uniform_pack)
    assert result.adjacent_mean_density_per_km2 is None and result.adjacent_area_km2 is None
    assert any(i.code == "adjacent_not_required" for i in result.issues)
    assert result.layers["adjacent_area"]["features"] == []
    assert "not required" in result.method


def test_volume_options(uniform_pack: DataPack) -> None:
    ballistic = _run(uniform_pack, buffer_rule="ballistic", adjacent_area=False)
    vol = ballistic.volume
    assert vol is not None and vol.grb_rule == "ballistic"
    assert vol.grb_m == pytest.approx(ballistic_distance_m(15.0, vol.h_cv_m) + 0.5, abs=1e-3)
    assert ballistic.buffer_m == pytest.approx(vol.s_cv_m + vol.grb_m, abs=2e-3)

    p95 = _run(uniform_pack, density_statistic="p95", adjacent_area=False)
    assert any(i.code == "density_statistic_p95" for i in p95.issues)

    tiny = _run(uniform_pack, max_dimension_m=0.25, max_speed_ms=16.0, takeoff_mass_kg=0.249)
    assert tiny.igrc == 1 and tiny.small_uas_rule
    assert any(i.code == "adjacent_small_uas" for i in tiny.issues)

    fg_height = _run(uniform_pack, volume={**MULTIROTOR_45, "fg_height_m": 60.0}, adjacent_area=False)
    assert fg_height.volume is not None and fg_height.volume.fg_height_m == 60.0


def test_survey_area_with_the_mapping_quad(synthetic_pack: DataPack) -> None:
    preset = UAS_PRESETS["mapping_quad"]
    params = GroundRiskParams(
        geometry=SURVEY, max_dimension_m=preset.max_dimension_m, max_speed_ms=preset.max_speed_ms,
        takeoff_mass_kg=preset.takeoff_mass_kg, volume=OperationalVolumeParams(**preset.volume.model_dump()),
    )
    result = run(params, AnalysisContext(datapack=synthetic_pack))
    stadium = [a for a in result.assemblies if a.name == "Synthetic Kengeri stadium"]
    assert stadium and stadium[0].source == "datapack" and stadium[0].band == "40k_400k"
    assert 600.0 < stadium[0].distance_m < 900.0
    assert all(a.name != "Synthetic city market" for a in result.assemblies)
    grid = result.density_grid
    assert grid is not None and grid.grid_m == 200.0 and grid.cells
    assert max(c.density_per_km2 for c in grid.cells) == pytest.approx(result.max_density_per_km2, abs=1e-3)
    assert result.cells == [] and result.hotspots == []  # the H3 grid belongs to the footprint method
    table = result.igrc_table
    assert table is not None and table.rows[table.row_index].values[table.column_index or 0] == result.igrc
    # Export polygons are the averaged squares.
    collection = density_grid_collection(result.model_dump(mode="json"))
    assert collection is not None and len(collection["features"]) == len(grid.cells)
    first = collection["features"][0]
    area, _ = geodesic_area_perimeter(shape(first["geometry"]))
    assert area == pytest.approx(200.0**2, rel=2e-3)
    assert first["properties"]["id"] == grid.cells[0].id


def test_grid_is_coarsened_to_the_cell_limit(synthetic_pack: DataPack) -> None:
    params = GroundRiskParams(geometry=_square(*CITY_CENTRE, 0.05), volume=OperationalVolumeParams(),
                              adjacent_area=False, max_cells=500)
    result = run(params, AnalysisContext(datapack=synthetic_pack))
    assert result.density_grid is not None and result.density_grid.grid_m >= 800.0
    assert len(result.density_grid.cells) + result.density_grid.nodata_cells <= 500
    assert result.volume is not None and result.volume.grid_source == "coarsened"
    assert result.cells_truncated is True
    assert any(i.code == "grid_coarsened" for i in result.issues)


def test_volume_run_cancellation(synthetic_pack: DataPack) -> None:
    event = threading.Event()
    event.set()
    params = GroundRiskParams(geometry=SURVEY, volume=OperationalVolumeParams())
    with pytest.raises(AnalysisCancelled):
        run(params, AnalysisContext(datapack=synthetic_pack, cancel_event=event))


def test_density_grid_collection_ignores_other_results() -> None:
    assert density_grid_collection({"cells": []}) is None
    empty = density_grid_collection({"density_grid": {"grid_m": 200.0, "origin_lon": 10.0, "origin_lat": 45.0,
                                                      "cells": []}})
    assert empty == {"type": "FeatureCollection", "features": []}


@pytest.mark.parametrize(
    ("raw", "people"),
    [("40000", 40_000), ("40,000", 40_000), ("40 000", 40_000), ("40.000", 40_000), ("approx. 5000", 5_000),
     ("1200;35000", 35_000), (52_000, 52_000), (12.4, 12), ("", None), ("unknown", None), ("-5", None),
     (0, None), (True, None), (None, None), ("20000000", None)],
)
def test_parse_capacity(raw: object, people: int | None) -> None:
    assert parse_capacity(raw) == people


def test_assemblies_from_overpass() -> None:
    ring = [{"lon": 77.50, "lat": 12.90}, {"lon": 77.51, "lat": 12.90}, {"lon": 77.51, "lat": 12.91},
            {"lon": 77.50, "lat": 12.91}, {"lon": 77.50, "lat": 12.90}]
    payload = {"elements": [
        {"type": "way", "id": 1, "tags": {"leisure": "stadium", "name": "Stadium", "capacity": "35,000"}, "geometry": ring},
        {"type": "node", "id": 2, "lon": 77.6, "lat": 12.97, "tags": {"amenity": "marketplace", "name": "Market"}},
        {"type": "node", "id": 3, "lon": 77.6, "lat": 12.97, "tags": {"amenity": "cafe"}},
        {"type": "way", "id": 4, "tags": {"tourism": "theme_park", "name:en": "Park"}, "geometry": ring[:3]},
        {"type": "way", "id": 1, "tags": {"leisure": "stadium"}, "geometry": ring},  # duplicate id
    ]}
    feats = assemblies_from_overpass(payload)
    assert [f["properties"]["name"] for f in feats] == ["Stadium", "Market", "Park"]
    stadium, market, park = feats
    assert stadium["geometry"]["type"] == "Polygon" and stadium["properties"]["capacity"] == 35_000
    assert stadium["properties"]["kind"] == "stadium"
    assert market["geometry"]["type"] == "Point" and market["properties"]["capacity"] is None
    assert park["geometry"]["type"] == "Point"  # an open way becomes its centroid
    query = assemblies_query([77.4, 12.8, 77.8, 13.1])
    for selector in ('nwr["leisure"="stadium"]', 'nwr["leisure"="sports_centre"]["capacity"]',
                     'nwr["tourism"="theme_park"]', 'nwr["amenity"="marketplace"]'):
        assert selector in query
    assert query.endswith("out geom;")


def test_synthetic_pack_assemblies(synthetic_pack: DataPack) -> None:
    assert "assemblies" in synthetic_pack.layers
    venues = {a.name: a for a in synthetic_pack.assemblies()}
    assert set(venues) == {"Synthetic Kengeri stadium", "Synthetic city market"}
    assert venues["Synthetic Kengeri stadium"].capacity == 45_000 and venues["Synthetic Kengeri stadium"].kind == "stadium"
    assert venues["Synthetic city market"].capacity is None
    # Only points and polygons are kept.
    lines = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "Parade"}, "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
        {"type": "Feature", "properties": {"capacity": "900"}, "geometry": {"type": "Point", "coordinates": [0, 0]}},
    ]}
    loaded = load_assemblies(lines)
    assert [(a.name, a.capacity) for a in loaded] == [("Assembly 2", 900)]
