"""Tests for the H3 grid helpers."""

from __future__ import annotations

import math

import h3
import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box

from airbrief_core.errors import AnalysisError
from airbrief_core.geo.crs import WGS84, local_area_crs
from airbrief_core.geo.geodesic import distance_m, geodesic_area_perimeter
from airbrief_core.geo.h3grid import (
    cell_area_m2,
    cell_centroids,
    cell_polygon,
    cells_for_polygon,
    cells_to_feature_collection,
    neighbours,
    ring_radius_k,
)
from airbrief_core.geo.transform import transform_geom


def square_wgs84(lon0: float, lat0: float, side_m: float) -> Polygon:
    half = side_m / 2.0
    geom = transform_geom(box(-half, -half, half, half), local_area_crs(lon0, lat0), WGS84)
    assert isinstance(geom, Polygon)
    return geom


def test_cells_for_square_count_matches_area() -> None:
    sq = square_wgs84(77.6, 12.97, 5000.0)
    cells, truncated = cells_for_polygon(sq, 9, 10_000)
    assert truncated is False
    expected = 25e6 / h3.average_hexagon_area(9, unit="m^2")
    assert 0.6 * expected <= len(cells) <= 1.4 * expected
    assert cells == sorted(cells)
    assert all(h3.get_resolution(c) == 9 for c in cells)
    lons, lats = cell_centroids(cells)
    assert lons.shape == lats.shape == (len(cells),)
    assert all(sq.buffer(0.01).contains(Point(x, y)) for x, y in zip(lons, lats, strict=True))


def test_cells_for_polygon_coarsens_when_too_many() -> None:
    sq = square_wgs84(77.6, 12.97, 20_000.0)
    cells, truncated = cells_for_polygon(sq, 10, 200)
    assert truncated is True
    assert 0 < len(cells) <= 200
    assert h3.get_resolution(cells[0]) < 10


def test_cells_for_polygon_truncates_at_res_zero() -> None:
    sq = square_wgs84(10.0, 45.0, 3_000_000.0)
    cells, truncated = cells_for_polygon(sq, 1, 2)
    assert truncated is True
    assert 1 <= len(cells) <= 2


def test_small_polygon_gets_one_cell() -> None:
    sq = square_wgs84(77.6, 12.97, 10.0)
    cells, truncated = cells_for_polygon(sq, 7, 100)
    assert truncated is False
    assert len(cells) == 1
    assert cells[0] == h3.latlng_to_cell(12.97, 77.6, 7)


def test_multipolygon_and_holes() -> None:
    a = square_wgs84(77.6, 12.97, 3000.0)
    b = square_wgs84(77.8, 13.1, 3000.0)
    cells_a, _ = cells_for_polygon(a, 9, 10_000)
    cells_b, _ = cells_for_polygon(b, 9, 10_000)
    cells_ab, truncated = cells_for_polygon(MultiPolygon([a, b]), 9, 10_000)
    assert truncated is False
    assert set(cells_ab) == set(cells_a) | set(cells_b)
    holed = Polygon(a.exterior.coords, [square_wgs84(77.6, 12.97, 1500.0).exterior.coords])
    cells_holed, _ = cells_for_polygon(holed, 9, 10_000)
    assert len(cells_holed) < len(cells_a)


def test_antimeridian_polygon_cells() -> None:
    sq = square_wgs84(180.0, 0.0, 5000.0)
    cells, truncated = cells_for_polygon(sq, 8, 10_000)
    assert truncated is False
    area, _ = geodesic_area_perimeter(sq)
    expected = area / h3.average_hexagon_area(8, unit="m^2")
    assert 0.5 * expected <= len(cells) <= 1.5 * expected
    lons, _ = cell_centroids(cells)
    assert all(abs(abs(lon) - 180.0) < 0.1 for lon in lons)


def test_cells_for_polygon_errors() -> None:
    with pytest.raises(AnalysisError) as info:
        cells_for_polygon(LineString([(0, 0), (1, 1)]), 8, 100)
    assert info.value.code == "geometry_type_not_supported"
    with pytest.raises(AnalysisError):
        cells_for_polygon(box(0, 0, 0.01, 0.01), 16, 100)
    with pytest.raises(AnalysisError):
        cells_for_polygon(box(0, 0, 0.01, 0.01), 8, 0)
    assert cells_for_polygon(Polygon(), 8, 100) == ([], False)


def test_cell_polygon_and_area() -> None:
    cell = h3.latlng_to_cell(12.97, 77.6, 8)
    poly = cell_polygon(cell)
    assert poly.is_valid
    assert len(poly.exterior.coords) == 7  # 6 vertices + closing point
    assert poly.contains(Point(77.6, 12.97))
    geo_area, _ = geodesic_area_perimeter(poly)
    assert cell_area_m2(cell) == pytest.approx(geo_area, rel=0.02)
    assert cell_area_m2(cell) == pytest.approx(h3.cell_area(cell, unit="km^2") * 1e6)


def test_cell_polygon_antimeridian_is_continuous() -> None:
    cell = h3.latlng_to_cell(0.0, 179.999, 5)
    poly = cell_polygon(cell)
    minx, _, maxx, _ = poly.bounds
    assert maxx - minx < 10.0
    assert poly.is_valid


def test_feature_collection() -> None:
    cells, _ = cells_for_polygon(square_wgs84(77.6, 12.97, 2000.0), 9, 1000)
    props = {cells[0]: {"score": 0.5}}
    fc = cells_to_feature_collection(cells, props)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == len(cells)
    first = fc["features"][0]
    assert first["id"] == cells[0]
    assert first["properties"] == {"h3": cells[0], "score": 0.5}
    assert first["geometry"]["type"] == "Polygon"
    assert isinstance(first["geometry"]["coordinates"][0][0], list)
    assert fc["features"][1]["properties"] == {"h3": cells[1]}


def test_neighbours() -> None:
    cell = h3.latlng_to_cell(12.97, 77.6, 8)
    ring = neighbours(cell)
    assert len(ring) == 6
    assert cell not in ring
    assert ring == sorted(ring)
    lon0, lat0 = cell_centroids([cell])
    lons, lats = cell_centroids(ring)
    spacing = math.sqrt(3.0) * h3.average_hexagon_edge_length(8, unit="m")
    d = distance_m(lon0[0], lat0[0], lons, lats)
    assert all(0.7 * spacing < v < 1.3 * spacing for v in d)


@pytest.mark.parametrize(("res", "radius"), [(7, 5_000.0), (8, 1_000.0), (9, 1_000.0), (10, 250.0)])
def test_ring_radius_k(res: int, radius: float) -> None:
    k = ring_radius_k(res, radius)
    spacing = math.sqrt(3.0) * h3.average_hexagon_edge_length(res, unit="m")
    assert k * spacing >= radius
    assert (k - 1) * spacing < radius


def test_ring_radius_k_zero() -> None:
    assert ring_radius_k(8, 0.0) == 0
    assert ring_radius_k(8, 1.0) == 1
