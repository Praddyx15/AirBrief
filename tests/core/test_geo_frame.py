"""Tests for LocalFrame, geodesic distance/bearing and sampling helpers."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box

from airbrief_core.errors import AnalysisError
from airbrief_core.geo.crs import WGS84, local_area_crs
from airbrief_core.geo.frame import LocalFrame
from airbrief_core.geo.geodesic import bearing_deg, distance_m, geodesic_area_perimeter
from airbrief_core.geo.sampling import grid_points, sample_line
from airbrief_core.geo.transform import transform_geom


def test_origin_maps_to_zero() -> None:
    frame = LocalFrame(77.6, 12.97)
    xs, ys = frame.xy([77.6], [12.97])
    assert xs[0] == pytest.approx(0.0, abs=1e-6)
    assert ys[0] == pytest.approx(0.0, abs=1e-6)
    assert "+proj=aeqd" in frame.proj4


def test_distances_from_origin_are_exact() -> None:
    frame = LocalFrame(77.6, 12.97)
    lons, lats = frame.lonlat([1000.0, 0.0, 30_000.0], [0.0, 2000.0, 40_000.0])
    d = distance_m(np.full(3, 77.6), np.full(3, 12.97), lons, lats)
    assert d == pytest.approx([1000.0, 2000.0, 50_000.0], rel=1e-7)
    assert bearing_deg(77.6, 12.97, float(lons[0]), float(lats[0])) == pytest.approx(90.0, abs=1e-5)
    north = bearing_deg(77.6, 12.97, float(lons[1]), float(lats[1]))
    assert min(north, 360.0 - north) < 1e-5


@settings(max_examples=50, deadline=None)
@given(
    st.floats(min_value=-180.0, max_value=180.0),
    st.floats(min_value=-85.0, max_value=85.0),
    st.floats(min_value=-50_000.0, max_value=50_000.0),
    st.floats(min_value=-50_000.0, max_value=50_000.0),
)
def test_xy_lonlat_round_trip(lon0: float, lat0: float, x: float, y: float) -> None:
    frame = LocalFrame(lon0, lat0)
    lons, lats = frame.lonlat([x], [y])
    xs, ys = frame.xy(lons, lats)
    # PROJ's aeqd inverse returns the centre for anything within ~0.64 mm of it (EPS10 on x/a)
    assert xs[0] == pytest.approx(x, abs=1e-3)
    assert ys[0] == pytest.approx(y, abs=1e-3)


def test_geometry_round_trip_keeps_z() -> None:
    frame = LocalFrame(-3.7, 40.4)
    local = LineString([(0.0, 0.0, 10.0), (100.0, 50.0, 20.0)])
    wgs = frame.to_wgs84(local)
    back = frame.to_local(wgs)
    for a, b in zip(back.coords, local.coords, strict=True):
        assert a == pytest.approx(b, abs=1e-6)


def test_for_geometry_uses_centroid() -> None:
    frame = LocalFrame.for_geometry(box(10.0, 20.0, 10.2, 20.2))
    assert frame.lon0 == pytest.approx(10.1)
    assert frame.lat0 == pytest.approx(20.1)
    point_frame = LocalFrame.for_geometry(Point(5.0, 6.0))
    assert (point_frame.lon0, point_frame.lat0) == (5.0, 6.0)


def test_for_geometry_antimeridian() -> None:
    crossing = Polygon([(179.9, 0.0), (-179.9, 0.0), (-179.9, 0.1), (179.9, 0.1)])
    frame = LocalFrame.for_geometry(crossing)
    assert abs(frame.lon0) == pytest.approx(180.0, abs=1e-6)
    local = frame.to_local(crossing)
    minx, _, maxx, _ = local.bounds
    assert maxx - minx < 30_000.0


def test_invalid_frames() -> None:
    with pytest.raises(AnalysisError):
        LocalFrame(float("nan"), 0.0)
    with pytest.raises(AnalysisError):
        LocalFrame(0.0, 95.0)
    with pytest.raises(AnalysisError):
        LocalFrame.for_geometry(Polygon())


def test_distance_and_bearing_vectorised() -> None:
    assert distance_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(111_319.49, rel=1e-6)
    assert isinstance(distance_m(0.0, 0.0, 1.0, 0.0), float)
    d = distance_m(np.zeros(2), np.zeros(2), np.array([1.0, 0.0]), np.array([0.0, 1.0]))
    assert isinstance(d, np.ndarray)
    assert d == pytest.approx([111_319.49, 110_574.39], rel=1e-6)
    b = bearing_deg(0.0, 0.0, np.array([0.0, -1.0, 0.0]), np.array([-1.0, 0.0, 1.0]))
    assert b == pytest.approx([180.0, 270.0, 0.0], abs=1e-9)


def test_sample_line_even_with_endpoints() -> None:
    line = LineString([(0.0, 0.0), (1.0, 0.0)])
    dist, lons, lats = sample_line(line, 1000.0)
    total = 111_319.49
    assert dist[0] == 0.0
    assert dist[-1] == pytest.approx(total, rel=1e-6)
    assert len(dist) == 113  # ceil(111.32) = 112 intervals
    steps = np.diff(dist)
    assert np.allclose(steps, steps[0])
    assert steps[0] <= 1000.0
    assert (lons[0], lats[0]) == (0.0, 0.0)
    assert (lons[-1], lats[-1]) == (1.0, 0.0)
    assert np.allclose(lats, 0.0, atol=1e-9)
    measured = distance_m(np.zeros(len(lons)), np.zeros(len(lons)), lons, lats)
    assert measured == pytest.approx(dist, rel=1e-9, abs=1e-6)


def test_sample_line_multi_vertex_and_multipart() -> None:
    frame = LocalFrame(77.6, 12.97)
    bent = frame.to_wgs84(LineString([(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0)]))
    dist, lons, lats = sample_line(bent, 100.0)
    assert len(dist) == 21
    assert dist[-1] == pytest.approx(2000.0, rel=1e-6)
    xs, ys = frame.xy(lons, lats)
    assert xs[10] == pytest.approx(1000.0, abs=0.5)
    assert ys[10] == pytest.approx(0.0, abs=0.5)
    parts = MultiLineString([[(0.0, 0.0), (0.01, 0.0)], [(1.0, 1.0), (1.01, 1.0)]])
    dist2, _, _ = sample_line(parts, 500.0)
    assert np.all(np.diff(dist2) >= 0.0)
    assert dist2[-1] == pytest.approx(1113.19 + distance_m(1.0, 1.0, 1.01, 1.0), rel=1e-3)


def test_sample_line_degenerate_and_errors() -> None:
    dist, lons, lats = sample_line(LineString([(5.0, 5.0), (5.0, 5.0)]), 10.0)
    assert list(dist) == [0.0] and list(lons) == [5.0] and list(lats) == [5.0]
    with pytest.raises(AnalysisError):
        sample_line(LineString([(0, 0), (1, 1)]), 0.0)
    with pytest.raises(AnalysisError):
        sample_line(box(0, 0, 1, 1), 10.0)


def _square(lon0: float, lat0: float, side: float) -> Polygon:
    half = side / 2.0
    geom = transform_geom(box(-half, -half, half, half), local_area_crs(lon0, lat0), WGS84)
    assert isinstance(geom, Polygon)
    return geom


def test_grid_points_cover_area() -> None:
    sq = _square(77.6, 12.97, 1000.0)
    lons, lats, cell_area = grid_points(sq, 100.0, 10_000)
    assert cell_area == pytest.approx(10_000.0)
    assert len(lons) == len(lats)
    assert len(lons) == 100
    assert len(lons) * cell_area == pytest.approx(1_000_000.0, rel=0.25)
    assert all(sq.buffer(1e-6).contains(Point(x, y)) for x, y in zip(lons, lats, strict=True))


def test_grid_points_respects_max_points() -> None:
    sq = _square(77.6, 12.97, 10_000.0)
    lons, _, cell_area = grid_points(sq, 10.0, 500)
    assert 0 < len(lons) <= 500
    area, _ = geodesic_area_perimeter(sq)
    assert len(lons) * cell_area == pytest.approx(area, rel=0.2)


def test_grid_points_tiny_polygon() -> None:
    sq = _square(77.6, 12.97, 5.0)
    lons, lats, cell_area = grid_points(sq, 100.0, 1000)
    assert len(lons) == 1
    assert cell_area == pytest.approx(25.0, rel=1e-3)
    assert sq.contains(Point(lons[0], lats[0]))


def test_grid_points_errors() -> None:
    with pytest.raises(AnalysisError):
        grid_points(LineString([(0, 0), (1, 1)]), 10.0, 10)
    with pytest.raises(AnalysisError):
        grid_points(box(0, 0, 0.1, 0.1), -1.0, 10)
    with pytest.raises(AnalysisError):
        grid_points(box(0, 0, 0.1, 0.1), 10.0, 0)
