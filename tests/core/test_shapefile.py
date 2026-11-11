"""Tests for the pyshp-based shapefile reader (Z support, ring grouping, .prj and .cpg)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import shapefile as pyshp
from pyproj import CRS

from airbrief_core.errors import IngestError
from airbrief_core.io.shapefile import build_polygon, read_shapefile, shape_to_geometry

UTM43N = CRS.from_epsg(32643)

# Clockwise exterior (ESRI rule) and counter-clockwise hole, with Z.
EXTERIOR_CW = [[0.0, 0.0, 10.0], [0.0, 100.0, 11.0], [100.0, 100.0, 12.0], [100.0, 0.0, 13.0], [0.0, 0.0, 10.0]]
HOLE_CCW = [[25.0, 25.0, 5.0], [75.0, 25.0, 5.0], [75.0, 75.0, 5.0], [25.0, 75.0, 5.0], [25.0, 25.0, 5.0]]
EXTERIOR2_CW = [[200.0, 0.0, 1.0], [200.0, 50.0, 1.0], [250.0, 50.0, 1.0], [250.0, 0.0, 1.0], [200.0, 0.0, 1.0]]


def _write_prj(base: Path, crs: CRS = UTM43N) -> None:
    base.with_suffix(".prj").write_text(crs.to_wkt(version="WKT1_ESRI"), encoding="ascii")


def _polygonz(base: Path, *, prj: bool = True) -> Path:
    w = pyshp.Writer(str(base), shapeType=pyshp.POLYGONZ)
    w.field("name", "C", size=40)
    w.field("when", "D")
    w.polyz([EXTERIOR_CW, HOLE_CCW])
    w.record("field A", date(2024, 1, 2))
    w.polyz([EXTERIOR_CW, HOLE_CCW, EXTERIOR2_CW])
    w.record("two parts", date(2024, 1, 3))
    w.close()
    if prj:
        _write_prj(base)
    return base.with_suffix(".shp")


def test_polygonz_with_hole(tmp_path: Path) -> None:
    shp = _polygonz(tmp_path / "fields")
    layer = read_shapefile(shp, None)
    assert layer.name == "fields"
    assert layer.crs is not None and layer.crs.to_epsg() == 32643
    assert layer.crs_source == "prj"
    first = layer.features[0]
    geom = first.geometry
    assert geom is not None
    assert geom.geom_type == "Polygon"
    assert geom.has_z
    assert len(geom.interiors) == 1
    assert geom.area == pytest.approx(100 * 100 - 50 * 50)
    zs = sorted({c[2] for c in geom.exterior.coords})
    assert zs == [10.0, 11.0, 12.0, 13.0]
    assert first.name == "field A"
    assert first.fid == "0"
    assert first.properties["when"] == date(2024, 1, 2)


def test_two_exteriors_hole_goes_to_container(tmp_path: Path) -> None:
    shp = _polygonz(tmp_path / "fields")
    geom = read_shapefile(shp, None).features[1].geometry
    assert geom is not None and geom.geom_type == "MultiPolygon"
    polys = sorted(geom.geoms, key=lambda p: p.bounds[0])
    assert len(polys[0].interiors) == 1
    assert len(polys[1].interiors) == 0
    assert geom.area == pytest.approx(100 * 100 - 50 * 50 + 50 * 50)


def test_wrongly_oriented_rings_still_read() -> None:
    ccw_only = [list(reversed(EXTERIOR_CW))]
    geom = build_polygon([[tuple(c) for c in r] for r in ccw_only])
    assert geom is not None and geom.geom_type == "Polygon"
    assert geom.area == pytest.approx(10_000)


def test_hole_outside_every_exterior_becomes_exterior() -> None:
    far_ccw = [(500.0, 500.0), (510.0, 500.0), (510.0, 510.0), (500.0, 510.0), (500.0, 500.0)]
    ext = [(c[0], c[1]) for c in EXTERIOR_CW]
    geom = build_polygon([ext, far_ccw])
    assert geom is not None and geom.geom_type == "MultiPolygon"
    assert len(geom.geoms) == 2


def test_polyline_z_multipart(tmp_path: Path) -> None:
    base = tmp_path / "lines"
    w = pyshp.Writer(str(base), shapeType=pyshp.POLYLINEZ)
    w.field("id", "N", size=5)
    w.linez([[[0, 0, 1], [10, 0, 2]], [[20, 0, 3], [30, 0, 4], [40, 0, 5]]])
    w.record(1)
    w.linez([[[0, 0, 0], [3, 4, 12]]])
    w.record(2)
    w.close()
    _write_prj(base)
    layer = read_shapefile(base.with_suffix(".shp"), None)
    multi, single = layer.features[0].geometry, layer.features[1].geometry
    assert multi is not None and multi.geom_type == "MultiLineString" and multi.has_z
    assert [c[2] for c in multi.geoms[1].coords] == [3.0, 4.0, 5.0]
    assert single is not None and single.geom_type == "LineString"
    assert single.length == pytest.approx(5.0)


def test_pointz_and_null_shapes(tmp_path: Path) -> None:
    base = tmp_path / "pts"
    w = pyshp.Writer(str(base), shapeType=pyshp.POINTZ)
    w.field("name", "C")
    w.pointz(1.0, 2.0, 3.0)
    w.record("tower")
    w.null()
    w.record("nothing")
    w.close()
    layer = read_shapefile(base.with_suffix(".shp"), None)
    point = layer.features[0].geometry
    assert point is not None and point.geom_type == "Point"
    assert tuple(point.coords[0]) == (1.0, 2.0, 3.0)
    assert layer.features[1].geometry is None
    assert layer.features[1].unsupported_reason is None
    assert layer.features[1].name == "nothing"


def test_multipatch_is_unsupported() -> None:
    fake = SimpleNamespace(shapeType=31, points=[[0, 0], [1, 0], [0, 1]], parts=[0], z=[0, 0, 0])
    geom, reason = shape_to_geometry(fake)
    assert geom is None
    assert reason is not None and "MultiPatch" in reason


def test_no_prj_means_unknown_crs(tmp_path: Path) -> None:
    shp = _polygonz(tmp_path / "noprj", prj=False)
    layer = read_shapefile(shp, None)
    assert layer.crs is None
    assert layer.crs_source is None
    assert any(i.code == "missing_prj" for i in layer.issues)


def test_crs_override_applies(tmp_path: Path) -> None:
    shp = _polygonz(tmp_path / "noprj", prj=False)
    layer = read_shapefile(shp, UTM43N)
    assert layer.crs == UTM43N
    assert layer.crs_source == "override"
    assert any(i.code == "crs_override" for i in layer.issues)


def test_invalid_prj_adds_issue(tmp_path: Path) -> None:
    shp = _polygonz(tmp_path / "badprj", prj=False)
    shp.with_suffix(".prj").write_text("this is not a CRS at all", encoding="ascii")
    layer = read_shapefile(shp, None)
    assert layer.crs is None
    assert any(i.code == "invalid_prj" for i in layer.issues)


def test_epsg_text_prj_fallback(tmp_path: Path) -> None:
    shp = _polygonz(tmp_path / "epsgprj", prj=False)
    shp.with_suffix(".prj").write_text("EPSG:32643", encoding="ascii")
    layer = read_shapefile(shp, None)
    assert layer.crs is not None and layer.crs.to_epsg() == 32643


def test_cpg_encoding(tmp_path: Path) -> None:
    base = tmp_path / "latin"
    w = pyshp.Writer(str(base), shapeType=pyshp.POINT, encoding="cp1252")
    w.field("name", "C", size=30)
    w.point(77.5, 12.9)
    w.record("Café Müller")
    w.close()
    base.with_suffix(".cpg").write_text("1252", encoding="ascii")
    layer = read_shapefile(base.with_suffix(".shp"), None)
    assert layer.features[0].properties["name"] == "Café Müller"
    assert layer.features[0].name == "Café Müller"


def test_missing_shx(tmp_path: Path) -> None:
    shp = _polygonz(tmp_path / "noshx")
    shp.with_suffix(".shx").unlink()
    with pytest.raises(IngestError) as exc:
        read_shapefile(shp, None)
    assert exc.value.code == "missing_component"


def test_missing_dbf(tmp_path: Path) -> None:
    shp = _polygonz(tmp_path / "nodbf")
    shp.with_suffix(".dbf").unlink()
    with pytest.raises(IngestError) as exc:
        read_shapefile(shp, None)
    assert exc.value.code == "missing_component"


def test_corrupt_shp(tmp_path: Path) -> None:
    shp = _polygonz(tmp_path / "broken")
    shp.write_bytes(b"\x00\x01garbage")
    with pytest.raises(IngestError) as exc:
        read_shapefile(shp, None)
    assert exc.value.code == "corrupt_file"
