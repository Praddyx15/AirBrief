"""Tests for airbrief_core.data.raster (RasterSampler) on tiny known-answer rasters."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Geod
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point, box

from airbrief_core.data.raster import RasterSampler, geographic_cell_area_km2
from airbrief_core.errors import AnalysisError, DataPackError

ORIGIN_LON = 77.0
ORIGIN_LAT = 13.0
RES = 0.01


def _write(path: Path, array: np.ndarray, *, res: float = RES, nodata: float | None = -9999.0,
           west: float = ORIGIN_LON, north: float = ORIGIN_LAT) -> Path:
    h, w = array.shape
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(west, north, res, res), nodata=nodata,
    ) as dst:
        dst.write(array.astype(np.float32), 1)
    return path


def _plane(rows: int = 20, cols: int = 20) -> np.ndarray:
    r, c = np.mgrid[0:rows, 0:cols]
    return 100.0 + 10.0 * c + 5.0 * r


def _centre(row: float, col: float) -> tuple[float, float]:
    return ORIGIN_LON + (col + 0.5) * RES, ORIGIN_LAT - (row + 0.5) * RES


@pytest.fixture()
def plane(tmp_path: Path) -> RasterSampler:
    return RasterSampler(_write(tmp_path / "plane.tif", _plane()), kind="dem")


def test_pixel_centres_exact(plane: RasterSampler) -> None:
    lon, lat = _centre(3, 7)
    assert float(plane.sample(lon, lat)) == pytest.approx(100 + 70 + 15)
    assert float(plane.sample(lon, lat, method="nearest")) == pytest.approx(185)


def test_bilinear_on_plane_is_exact(plane: RasterSampler) -> None:
    lons = np.array([77.0234, 77.1111, 77.0567])
    lats = np.array([12.9123, 12.8888, 12.9555])
    col_f = (lons - ORIGIN_LON) / RES - 0.5
    row_f = (ORIGIN_LAT - lats) / RES - 0.5
    expected = 100 + 10 * col_f + 5 * row_f
    np.testing.assert_allclose(plane.sample(lons, lats), expected, atol=1e-4)


def test_nearest_picks_containing_pixel(plane: RasterSampler) -> None:
    lon, lat = _centre(4, 2)
    value = plane.sample(lon + 0.004, lat - 0.004, method="nearest")
    assert float(value) == pytest.approx(100 + 20 + 20)


def test_outside_is_nan_and_shape_preserved(plane: RasterSampler) -> None:
    lons = np.array([[76.5, 77.05], [77.05, 77.3]])
    lats = np.array([[12.95, 12.95], [13.5, 12.95]])
    out = plane.sample(lons, lats)
    assert out.shape == (2, 2)
    assert math.isnan(out[0, 0]) and math.isnan(out[1, 0]) and math.isnan(out[1, 1])
    assert math.isfinite(out[0, 1])


def test_edges_are_inside(plane: RasterSampler) -> None:
    # The outer half-pixel is still inside the raster and is clamped, not NaN.
    assert float(plane.sample(ORIGIN_LON + 1e-9, ORIGIN_LAT - 1e-9)) == pytest.approx(100.0, abs=1e-3)
    assert plane.bounds == pytest.approx((77.0, 12.8, 77.2, 13.0))


def test_nodata_is_nan_and_excluded_from_neighbours(tmp_path: Path) -> None:
    arr = _plane()
    arr[5, 5] = -9999.0
    sampler = RasterSampler(_write(tmp_path / "nd.tif", arr))
    assert math.isnan(float(sampler.sample(*_centre(5, 5))))
    # Halfway between a valid pixel centre and the nodata pixel: nearest pixel is valid,
    # so the nodata neighbour is dropped from the weights.
    lon, lat = _centre(5, 6)
    assert float(sampler.sample(lon - 0.004, lat)) == pytest.approx(arr[5, 6], abs=1e-3)
    assert np.isnan(sampler.array[5, 5])


def test_invalid_method(plane: RasterSampler) -> None:
    with pytest.raises(AnalysisError):
        plane.sample(77.05, 12.95, method="cubic")  # type: ignore[arg-type]


def test_missing_file_raises(tmp_path: Path) -> None:
    sampler = RasterSampler(tmp_path / "nope.tif")
    with pytest.raises(DataPackError) as info:
        sampler.sample(77.0, 13.0)
    assert info.value.code == "layer_missing"


def test_cell_area_known_values() -> None:
    # 1 deg x 1 deg at the equator is about 12,308 km².
    assert float(geographic_cell_area_km2(0.0, 1.0, 1.0)) == pytest.approx(12_308.8, rel=1e-3)
    # Whole-Earth area of WGS84 ≈ 510,065,621.7 km².
    assert float(geographic_cell_area_km2(-90.0, 90.0, 360.0)) == pytest.approx(510_065_621.7, rel=1e-8)
    # Matches pyproj's geodesic polygon area for a small cell.
    geod = Geod(ellps="WGS84")
    area, _ = geod.polygon_area_perimeter([77.0, 77.01, 77.01, 77.0], [12.9, 12.9, 12.91, 12.91])
    assert float(geographic_cell_area_km2(12.9, 12.91, 0.01)) == pytest.approx(abs(area) / 1e6, rel=1e-6)


def test_population_density_and_integral(tmp_path: Path) -> None:
    people = np.full((20, 20), 100.0)
    pop = RasterSampler(_write(tmp_path / "pop.tif", people, nodata=-99999.0), kind="population")
    lon, lat = _centre(10, 10)
    pixel_area = float(geographic_cell_area_km2(lat - RES / 2, lat + RES / 2, RES))
    assert float(pop.density_per_km2(lon, lat)) == pytest.approx(100.0 / pixel_area, rel=1e-4)
    # 10 x 10 pixels aligned with the grid hold 10,000 people.
    square = box(77.05, 12.85, 77.15, 12.95)
    assert pop.integrate_density(square, step_m=100) == pytest.approx(10_000.0, rel=0.02)
    # Lines and points contain nobody.
    assert pop.integrate_density(LineString([(77.05, 12.9), (77.1, 12.9)])) == 0.0


def test_integral_ignores_area_outside_raster(tmp_path: Path) -> None:
    people = np.full((20, 20), 50.0)
    pop = RasterSampler(_write(tmp_path / "pop2.tif", people, nodata=-99999.0))
    half_outside = box(77.15, 12.85, 77.25, 12.95)  # 5 of 10 columns inside
    assert pop.integrate_density(half_outside, step_m=100) == pytest.approx(5 * 10 * 50.0, rel=0.03)


def test_slope_on_tilted_plane(tmp_path: Path) -> None:
    geod = Geod(ellps="WGS84")
    res = 0.0005
    rows, cols = 200, 60
    south = ORIGIN_LAT - rows * res
    lat_centres = ORIGIN_LAT - (np.arange(rows) + 0.5) * res
    _, _, north_m = geod.inv(np.full(rows, 77.0), np.full(rows, south), np.full(rows, 77.0), lat_centres)
    grade = 0.1
    z = np.repeat((900.0 + grade * np.asarray(north_m))[:, None], cols, axis=1)
    dem = RasterSampler(_write(tmp_path / "slope.tif", z, res=res))
    lons = np.full(5, 77.015)
    lats = np.linspace(12.93, 12.97, 5)
    slope = dem.slope_deg(lons, lats, delta_m=30)
    np.testing.assert_allclose(slope, math.degrees(math.atan(grade)), atol=0.05)

    flat = RasterSampler(_write(tmp_path / "flat.tif", np.full((50, 50), 900.0), res=res))
    assert float(flat.slope_deg(77.01, 12.99)) == pytest.approx(0.0, abs=1e-9)
    assert math.isnan(float(flat.slope_deg(70.0, 10.0)))


def test_stats(plane: RasterSampler) -> None:
    poly = box(77.02, 12.85, 77.08, 12.95)
    s = plane.stats(poly, step_m=500)
    assert s["count"] == s["valid"] > 0
    assert s["min"] <= s["mean"] <= s["max"]
    assert 100 <= s["min"] and s["max"] <= 100 + 190 + 95
    line = plane.stats(LineString([(77.02, 12.95), (77.08, 12.95)]), step_m=200)
    assert line["valid"] >= 2
    point = plane.stats(Point(*_centre(1, 1)))
    assert point["count"] == 1 and point["max"] == pytest.approx(115.0)
    empty = plane.stats(Point(60.0, 10.0))
    assert empty["valid"] == 0 and empty["mean"] is None


def test_lazy_load_is_thread_safe(tmp_path: Path) -> None:
    sampler = RasterSampler(_write(tmp_path / "threads.tif", _plane()))
    lons = np.linspace(77.01, 77.19, 50)
    lats = np.linspace(12.81, 12.99, 50)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: sampler.sample(lons, lats), range(16)))
    for r in results[1:]:
        np.testing.assert_array_equal(r, results[0])
    fresh = RasterSampler(sampler.path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        dens = list(pool.map(lambda _: fresh.density_per_km2(lons, lats), range(8)))
    for d in dens[1:]:
        np.testing.assert_array_equal(d, dens[0])


def test_info(plane: RasterSampler) -> None:
    info = plane.info()
    assert info["rows"] == 20 and info["cols"] == 20
    assert info["resolution"] == pytest.approx([0.01, 0.01])
    assert 1000 < info["resolution_m"] < 1200


def test_synthetic_pack_rasters(synthetic_pack) -> None:  # type: ignore[no-untyped-def]
    dem = synthetic_pack.dem()
    pop = synthetic_pack.population()
    assert dem is not None and pop is not None
    lons = np.linspace(77.32, 77.93, 40)
    lats = np.linspace(12.72, 13.28, 40)
    z = dem.sample(*np.meshgrid(lons, lats))
    assert np.all(np.isfinite(z))
    assert 850.0 < float(z.min()) and float(z.max()) < 1000.0
    centre = float(pop.density_per_km2(77.5946, 12.9716))
    assert 18_000 < centre < 22_500
    rural = float(pop.density_per_km2(77.90, 13.28))
    assert rural < 1_000
    km2 = box(77.5900, 12.9671, 77.5992, 12.9761)  # ≈ 1 km x 1 km around the centre
    assert pop.integrate_density(km2, step_m=50) == pytest.approx(centre * 0.99, rel=0.1)
    slope = dem.slope_deg(np.array([77.49, 77.6]), np.array([12.905, 12.97]))
    assert np.all((slope >= 0) & (slope < 5))
