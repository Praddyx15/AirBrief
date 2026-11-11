"""Known-answer and property tests for per-feature measurement (docs/SPEC.md "Measurement rules")."""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    box,
)

from airbrief_core.geo.crs import WGS84, local_area_crs, local_length_crs
from airbrief_core.geo.frame import LocalFrame
from airbrief_core.geo.geodesic import geodesic_area_perimeter, geodesic_length
from airbrief_core.geo.measure import REASON_CRS_UNKNOWN, measure_geometry
from airbrief_core.geo.transform import transform_geom
from airbrief_core.geo.validity import (
    drop_z,
    geometry_type_name,
    lineal_part,
    polygonal_part,
    repair,
)
from airbrief_core.models.features import MeasurementStatus

REL = 1e-4  # 0.01 %


def square_wgs84(lon0: float, lat0: float, side_m: float = 1000.0) -> Polygon:
    """A side x side metre square built in a local LAEA and inverse-projected to WGS84."""
    half = side_m / 2.0
    geom = transform_geom(box(-half, -half, half, half), local_area_crs(lon0, lat0), WGS84)
    assert isinstance(geom, Polygon)
    return geom


@pytest.mark.parametrize(("lon0", "lat0"), [(0.0, 0.0), (10.0, 60.0), (77.59, 12.97), (-70.0, -45.0)])
def test_one_km_square(lon0: float, lat0: float) -> None:
    m = measure_geometry(square_wgs84(lon0, lat0), WGS84)
    assert m.status == MeasurementStatus.MEASURED
    assert m.area_m2 == pytest.approx(1_000_000.0, rel=REL)
    assert m.perimeter_m == pytest.approx(4_000.0, rel=REL)
    assert m.geodesic_area_m2 == pytest.approx(1_000_000.0, rel=REL)
    assert m.geodesic_perimeter_m == pytest.approx(4_000.0, rel=REL)
    assert m.length_m is None
    assert m.deviation_pct is not None and m.deviation_pct < 0.01
    assert m.method == "local-projection"
    assert m.area_crs is not None and "+proj=laea" in m.area_crs
    assert m.length_crs is not None and "+proj=tmerc" in m.length_crs and "+k=1" in m.length_crs
    assert m.grid_area_m2 is None and m.grid_length_m is None
    assert m.parts == {"Polygon": 1}
    assert m.has_z is False


def test_utm_context_for_square() -> None:
    m = measure_geometry(square_wgs84(77.59, 12.97), WGS84)
    assert m.utm_epsg == 32643
    assert m.utm_zone == "43N"


def test_equatorial_degree_line() -> None:
    m = measure_geometry(LineString([(0.0, 0.0), (1.0, 0.0)]), WGS84)
    assert m.status == MeasurementStatus.MEASURED
    assert m.length_m == pytest.approx(111_319.49, rel=REL)
    assert m.geodesic_length_m == pytest.approx(111_319.49, rel=1e-6)
    assert m.area_m2 is None and m.perimeter_m is None and m.area_crs is None
    assert m.length_3d_m is None
    assert m.deviation_pct is not None and m.deviation_pct < 0.01


def test_meridian_degree_line() -> None:
    m = measure_geometry(LineString([(0.0, 0.0), (0.0, 1.0)]), WGS84)
    assert m.length_m == pytest.approx(110_574.39, rel=REL)
    assert m.geodesic_length_m == pytest.approx(110_574.39, rel=1e-6)


def test_three_d_length_climbing_line() -> None:
    tm = local_length_crs(77.6, 12.97)
    line_local = LineString([(0.0, 0.0, 0.0), (1000.0, 0.0, 100.0)])
    line = transform_geom(line_local, tm, WGS84)
    assert line.has_z
    m = measure_geometry(line, WGS84)
    assert m.has_z is True
    assert m.length_m == pytest.approx(1000.0, rel=REL)
    assert m.length_3d_m == pytest.approx(1004.99, rel=REL)
    assert m.length_3d_m == pytest.approx(math.sqrt(1000.0**2 + 100.0**2), rel=REL)


def test_three_d_length_in_projected_source() -> None:
    line = LineString([(777_000.0, 1_435_000.0, 900.0), (777_000.0, 1_436_000.0, 1000.0)])
    m = measure_geometry(line, CRS.from_epsg(32643))
    assert m.grid_length_m == pytest.approx(1000.0, abs=1e-9)
    assert m.length_3d_m is not None and m.length_m is not None
    assert m.length_3d_m > m.length_m
    expected = math.sqrt(m.length_m**2 + 100.0**2)
    assert m.length_3d_m == pytest.approx(expected, rel=1e-6)


def test_projected_utm_rectangle() -> None:
    rect = box(777_000.0, 1_435_000.0, 777_200.0, 1_435_100.0)
    m = measure_geometry(rect, CRS.from_epsg(32643))
    assert m.status == MeasurementStatus.MEASURED
    assert m.grid_area_m2 == 20_000.0
    assert m.grid_length_m == pytest.approx(600.0)
    assert m.area_m2 is not None and m.geodesic_area_m2 is not None
    assert abs(m.area_m2 - m.geodesic_area_m2) / m.geodesic_area_m2 < 0.002
    assert m.deviation_pct is not None and m.deviation_pct < 0.01
    # Ground area differs from grid area by the UTM scale factor (well under 0.2 % here).
    assert abs(m.area_m2 - 20_000.0) / 20_000.0 < 0.002


def test_projected_feet_unit_factor() -> None:
    # NAD83 / California zone 3 (ftUS)
    crs = CRS.from_epsg(2227)
    rect = box(6_000_000.0, 2_100_000.0, 6_001_000.0, 2_100_500.0)
    m = measure_geometry(rect, crs)
    ft = 1200.0 / 3937.0
    assert m.grid_area_m2 == pytest.approx(1000.0 * 500.0 * ft * ft, rel=1e-9)
    assert m.area_m2 == pytest.approx(m.grid_area_m2, rel=0.002)


@pytest.mark.parametrize("geom", [Point(77.6, 12.9), MultiPoint([(77.6, 12.9), (77.7, 13.0)])])
def test_points_not_applicable(geom: Point | MultiPoint) -> None:
    m = measure_geometry(geom, WGS84)
    assert m.status == MeasurementStatus.NOT_APPLICABLE
    assert m.area_m2 is None and m.length_m is None


@pytest.mark.parametrize("geom", [None, Polygon(), LineString(), GeometryCollection()])
def test_none_or_empty_skipped(geom: Polygon | LineString | GeometryCollection | None) -> None:
    m = measure_geometry(geom, WGS84)
    assert m.status == MeasurementStatus.SKIPPED
    assert m.reason


def test_unknown_crs_skipped() -> None:
    m = measure_geometry(box(0, 0, 10, 10), None)
    assert m.status == MeasurementStatus.SKIPPED
    assert m.reason == REASON_CRS_UNKNOWN
    assert m.area_m2 is None


def test_repaired_flag_passes_through() -> None:
    m = measure_geometry(square_wgs84(5.0, 5.0), WGS84, repaired=True)
    assert m.repaired is True


def test_geometry_collection_parts() -> None:
    square = square_wgs84(30.0, 20.0)
    line = LineString([(30.0, 20.0), (31.0, 20.0)])
    gc = GeometryCollection([square, line, Point(30.0, 20.0)])
    m = measure_geometry(gc, WGS84)
    assert m.status == MeasurementStatus.MEASURED
    assert m.parts == {"Polygon": 1, "LineString": 1, "Point": 1}
    assert m.area_m2 == pytest.approx(1_000_000.0, rel=REL)
    assert m.perimeter_m == pytest.approx(4_000.0, rel=REL)
    assert m.length_m == pytest.approx(geodesic_length(line), rel=REL)
    assert m.geodesic_length_m == pytest.approx(geodesic_length(line), rel=1e-9)


def test_multipolygon_and_multilinestring() -> None:
    mp = MultiPolygon([square_wgs84(0.0, 0.0), square_wgs84(0.1, 0.1)])
    m = measure_geometry(mp, WGS84)
    assert m.area_m2 == pytest.approx(2_000_000.0, rel=REL)
    assert m.perimeter_m == pytest.approx(8_000.0, rel=REL)
    assert m.parts == {"Polygon": 2}
    ml = MultiLineString([[(0.0, 0.0), (1.0, 0.0)], [(0.0, 0.0), (0.0, 1.0)]])
    m2 = measure_geometry(ml, WGS84)
    assert m2.length_m == pytest.approx(111_319.49 + 110_574.39, rel=REL)


def test_polygon_with_hole() -> None:
    laea = local_area_crs(20.0, 40.0)
    holed_local = Polygon(
        [(-500, -500), (500, -500), (500, 500), (-500, 500)],
        [[(-100, -100), (-100, 100), (100, 100), (100, -100)]],
    )
    holed = transform_geom(holed_local, laea, WGS84)
    m = measure_geometry(holed, WGS84)
    assert m.area_m2 == pytest.approx(1_000_000.0 - 40_000.0, rel=REL)
    assert m.perimeter_m == pytest.approx(4_000.0 + 800.0, rel=REL)
    assert m.geodesic_area_m2 == pytest.approx(960_000.0, rel=REL)


def test_antimeridian_polygon() -> None:
    square = square_wgs84(180.0, 0.0)
    lons = [c[0] for c in square.exterior.coords]
    assert min(lons) < 0.0 < max(lons)  # really crosses the antimeridian
    m = measure_geometry(square, WGS84)
    assert m.status == MeasurementStatus.MEASURED
    assert m.area_m2 == pytest.approx(1_000_000.0, rel=REL)
    assert m.perimeter_m == pytest.approx(4_000.0, rel=REL)
    assert m.geodesic_area_m2 == pytest.approx(1_000_000.0, rel=REL)
    assert m.deviation_pct is not None and m.deviation_pct < 0.01


def test_antimeridian_line() -> None:
    m = measure_geometry(LineString([(179.5, 0.0), (-179.5, 0.0)]), WGS84)
    assert m.length_m == pytest.approx(111_319.49, rel=REL)


def test_degenerate_collapsed_polygon_does_not_raise() -> None:
    sliver = Polygon([(10.0, 10.0), (10.001, 10.0), (10.002, 10.0), (10.0, 10.0)])
    m = measure_geometry(sliver, WGS84)
    assert m.status in (MeasurementStatus.MEASURED, MeasurementStatus.ERROR)


# Under 100 km across, projected and geodesic must agree within 0.01 %.
@st.composite
def small_polygons(draw: st.DrawFn) -> Polygon:
    lon0 = draw(st.floats(min_value=-179.9, max_value=179.9, allow_nan=False))
    lat0 = draw(st.floats(min_value=-80.0, max_value=80.0, allow_nan=False))
    radius = draw(st.floats(min_value=200.0, max_value=49_000.0, allow_nan=False))
    n = draw(st.integers(min_value=4, max_value=12))
    jitters = draw(st.lists(st.floats(min_value=0.0, max_value=0.6), min_size=n, max_size=n))
    radii = draw(st.lists(st.floats(min_value=0.5, max_value=1.0), min_size=n, max_size=n))
    pts = []
    for i in range(n):
        angle = 2.0 * math.pi * (i + jitters[i]) / n
        pts.append((radius * radii[i] * math.cos(angle), radius * radii[i] * math.sin(angle)))
    frame = LocalFrame(lon0, lat0)
    poly = frame.to_wgs84(Polygon(pts))
    assert isinstance(poly, Polygon)
    return poly


@settings(max_examples=80, deadline=None)
@given(small_polygons())
def test_property_small_polygons_match_geodesic(poly: Polygon) -> None:
    m = measure_geometry(poly, WGS84)
    assert m.status == MeasurementStatus.MEASURED
    assert m.deviation_pct is not None
    assert m.deviation_pct < 0.01


def test_geodesic_area_ignores_orientation() -> None:
    sq = square_wgs84(0.0, 45.0)
    reversed_sq = Polygon(list(sq.exterior.coords)[::-1])
    a1, p1 = geodesic_area_perimeter(sq)
    a2, p2 = geodesic_area_perimeter(reversed_sq)
    assert a1 == pytest.approx(a2)
    assert p1 == pytest.approx(p2)
    assert geodesic_area_perimeter(LineString([(0, 0), (1, 1)])) == (0.0, 0.0)


def test_repair_bowtie() -> None:
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    assert not bowtie.is_valid
    fixed, repaired = repair(bowtie)
    assert repaired is True
    assert fixed is not None and fixed.is_valid
    assert fixed.geom_type in ("Polygon", "MultiPolygon")
    assert fixed.area == pytest.approx(0.5)


def test_repair_valid_and_none() -> None:
    sq = box(0, 0, 1, 1)
    assert repair(sq) == (sq, False)
    assert repair(None) == (None, False)


def test_parts_helpers() -> None:
    gc = GeometryCollection([box(0, 0, 1, 1), LineString([(0, 0), (1, 1)]), Point(0, 0), box(2, 2, 3, 3)])
    poly = polygonal_part(gc)
    assert poly is not None and poly.geom_type == "MultiPolygon"
    line = lineal_part(gc)
    assert line is not None and line.geom_type == "LineString"
    assert polygonal_part(Point(0, 0)) is None
    assert lineal_part(box(0, 0, 1, 1)) is None
    assert geometry_type_name(None) == "None"
    assert geometry_type_name(box(0, 0, 1, 1)) == "Polygon"
    assert drop_z(LineString([(0, 0, 5), (1, 1, 6)])).has_z is False
