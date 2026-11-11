"""End-to-end tests for process_file: stage → read → repair → reproject → measure."""

from __future__ import annotations

import json
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import shapefile as pyshp
from pyproj import CRS

from airbrief_core import processing
from airbrief_core.errors import IngestError
from airbrief_core.geo.crs import WGS84, crs_label
from airbrief_core.io.ingest import IngestLimits
from airbrief_core.models.features import MeasurementStatus
from airbrief_core.processing import json_safe, process_file

UTM43N = CRS.from_epsg(32643)
# A 100 m x 100 m square near Bengaluru in UTM 43N (clockwise, ESRI exterior).
E0, N0 = 780_000.0, 1_430_000.0
SQUARE_CW = [[E0, N0], [E0, N0 + 100], [E0 + 100, N0 + 100], [E0 + 100, N0], [E0, N0]]

KML_DOC = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document><name>Site</name>
  <Folder><name>Areas</name>
    <Placemark><name>field</name><Polygon><outerBoundaryIs><LinearRing>
      <coordinates>77.50,12.90 77.51,12.90 77.51,12.91 77.50,12.91 77.50,12.90</coordinates>
    </LinearRing></outerBoundaryIs></Polygon></Placemark>
    <Placemark><name>bowtie</name><Polygon><outerBoundaryIs><LinearRing>
      <coordinates>77.50,12.90 77.51,12.91 77.51,12.90 77.50,12.91 77.50,12.90</coordinates>
    </LinearRing></outerBoundaryIs></Polygon></Placemark>
  </Folder>
  <Placemark><name>mast</name><Point><coordinates>77.505,12.905,30</coordinates></Point></Placemark>
  <Placemark><name>route</name><LineString><coordinates>77.50,12.90 77.52,12.90</coordinates></LineString></Placemark>
  <Placemark><name>model</name><Model><Link><href>m.dae</href></Link></Model></Placemark>
</Document></kml>
"""


def _write_shapefile(base: Path, *, prj: CRS | None = UTM43N, n: int = 1) -> None:
    w = pyshp.Writer(str(base), shapeType=pyshp.POLYGON)
    w.field("name", "C", size=20)
    w.field("surveyed", "D")
    for i in range(n):
        ring = [[x + i * 200, y] for x, y in SQUARE_CW]
        w.poly([ring])
        w.record(f"plot {i}", date(2023, 12, 31))
    w.close()
    if prj is not None:
        base.with_suffix(".prj").write_text(prj.to_wkt(version="WKT1_ESRI"), encoding="ascii")


def _zip_dir(src_dir: Path, out: Path, extra: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src_dir.iterdir()):
            zf.write(p, p.name)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return out


def _shapefile_zip(tmp_path: Path, *, prj: CRS | None = UTM43N, n: int = 1, extra: dict[str, bytes] | None = None) -> Path:
    src = tmp_path / "shp_src"
    src.mkdir()
    _write_shapefile(src / "plots", prj=prj, n=n)
    return _zip_dir(src, tmp_path / "plots.zip", extra)


def test_kml_end_to_end(tmp_path: Path) -> None:
    path = tmp_path / "site.kml"
    path.write_bytes(KML_DOC)
    stages: list[tuple[str, float]] = []
    result = process_file(path, "site.kml", progress=lambda s, f: stages.append((s, f)))

    assert result.file_type == "kml"
    assert result.crs == crs_label(WGS84)
    assert result.crs_source == "kml"
    assert result.feature_count == 5
    assert [layer.name for layer in result.layers] == ["Site / Areas", "Site"]
    assert [f.index for f in result.features] == [0, 1, 2, 3, 4]

    by_name = {f.name: f for f in result.features}
    field = by_name["field"]
    assert field.measurement.status == MeasurementStatus.MEASURED
    assert field.measurement.area_m2 is not None and field.measurement.area_m2 > 1_000_000
    assert field.geometry_wgs84 is not None and field.bbox_wgs84 is not None
    assert field.bbox_wgs84.min_lon == pytest.approx(77.50)

    bowtie = by_name["bowtie"]
    assert bowtie.measurement.repaired is True
    assert any(i.code == "geometry_repaired" and i.feature_index == bowtie.index for i in result.issues)

    assert by_name["mast"].measurement.status == MeasurementStatus.NOT_APPLICABLE
    assert by_name["route"].measurement.status == MeasurementStatus.MEASURED
    assert by_name["route"].measurement.length_m == pytest.approx(2170, rel=0.01)
    model = by_name["model"]
    assert model.measurement.status == MeasurementStatus.UNSUPPORTED
    assert model.geometry is None

    totals = result.totals
    assert totals.feature_count == 5
    assert totals.unsupported == 1
    assert totals.not_applicable == 1
    assert totals.measured == 3
    assert result.bbox_wgs84 is not None
    assert stages[0][0] == "staging" and stages[-1] == ("done", 1.0)
    assert all(0.0 <= f <= 1.0 for _, f in stages)


def test_shapefile_zip_measured_in_projected_crs(tmp_path: Path) -> None:
    result = process_file(_shapefile_zip(tmp_path), "plots.zip")
    assert result.file_type == "shapefile"
    assert result.crs == crs_label(UTM43N)
    assert result.crs_source == "prj"
    f = result.features[0]
    assert f.layer == "plots"
    assert f.measurement.status == MeasurementStatus.MEASURED
    assert f.measurement.area_m2 == pytest.approx(10_000, rel=2e-3)
    assert f.measurement.grid_area_m2 == pytest.approx(10_000, rel=1e-9)
    assert f.geometry is not None
    assert min(c[0] for c in f.geometry["coordinates"][0]) == pytest.approx(E0)
    assert f.geometry_wgs84 is not None
    lon, lat = f.geometry_wgs84["coordinates"][0][0][0], f.geometry_wgs84["coordinates"][0][0][1]
    assert 70 < lon < 85 and 10 < lat < 15
    # Dates become ISO strings in the JSON-safe properties.
    assert f.properties["surveyed"] == "2023-12-31"
    assert json.dumps(f.properties)


def test_shapefile_without_prj_is_not_measured(tmp_path: Path) -> None:
    result = process_file(_shapefile_zip(tmp_path, prj=None), "plots.zip")
    assert result.crs is None
    f = result.features[0]
    assert f.crs is None
    assert f.measurement.status == MeasurementStatus.SKIPPED
    assert f.geometry is not None
    assert f.geometry_wgs84 is None
    assert any(i.code == "missing_prj" for i in result.issues)


def test_crs_override_measures_prjless_shapefile(tmp_path: Path) -> None:
    result = process_file(_shapefile_zip(tmp_path, prj=None), "plots.zip", crs_override="EPSG:32643")
    assert result.crs == crs_label(UTM43N)
    assert result.crs_source == "override"
    assert result.features[0].measurement.status == MeasurementStatus.MEASURED


def test_invalid_crs_override(tmp_path: Path) -> None:
    with pytest.raises(IngestError) as exc:
        process_file(_shapefile_zip(tmp_path, prj=None), "plots.zip", crs_override="EPSG:999999999")
    assert exc.value.code == "invalid_crs"


def test_mixed_crs_dataset(tmp_path: Path) -> None:
    path = _shapefile_zip(tmp_path, extra={"site.kml": KML_DOC})
    result = process_file(path, "plots.zip")
    assert result.crs == "mixed"
    layer_names = [layer.name for layer in result.layers]
    assert "plots" in layer_names and "site / Site / Areas" in layer_names


def test_missing_shx_in_zip(tmp_path: Path) -> None:
    src = tmp_path / "shp_src"
    src.mkdir()
    _write_shapefile(src / "plots")
    (src / "plots.shx").unlink()
    path = _zip_dir(src, tmp_path / "plots.zip")
    with pytest.raises(IngestError) as exc:
        process_file(path, "plots.zip")
    assert exc.value.code == "missing_component"


def test_one_missing_layer_is_skipped_not_fatal(tmp_path: Path) -> None:
    src = tmp_path / "shp_src"
    src.mkdir()
    _write_shapefile(src / "good")
    _write_shapefile(src / "broken")
    (src / "broken.dbf").unlink()
    result = process_file(_zip_dir(src, tmp_path / "two.zip"), "two.zip")
    assert [layer.name for layer in result.layers] == ["good"]
    assert any(i.code == "missing_component" for i in result.issues)


def test_too_many_features(tmp_path: Path) -> None:
    with pytest.raises(IngestError) as exc:
        process_file(_shapefile_zip(tmp_path, n=5), "plots.zip", limits=IngestLimits(max_features=4))
    assert exc.value.code == "too_many_features"


def test_bad_feature_does_not_abort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = processing.measure_geometry
    calls = {"n": 0}

    def flaky(geom: Any, crs: Any, *, repaired: bool = False) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real(geom, crs, repaired=repaired)

    monkeypatch.setattr(processing, "measure_geometry", flaky)
    result = process_file(_shapefile_zip(tmp_path, n=3), "plots.zip")
    statuses = [f.measurement.status for f in result.features]
    assert statuses == [MeasurementStatus.MEASURED, MeasurementStatus.ERROR, MeasurementStatus.MEASURED]
    assert result.totals.error == 1
    assert result.features[1].geometry is not None
    assert any(i.code == "feature_error" and i.feature_index == 1 for i in result.issues)


def test_geojson_legacy_crs(tmp_path: Path) -> None:
    doc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32643"}},
        "features": [
            {
                "type": "Feature",
                "id": 7,
                "properties": {"name": "plot", "when": None},
                "geometry": {"type": "Polygon", "coordinates": [[[E0, N0], [E0 + 100, N0], [E0 + 100, N0 + 100], [E0, N0 + 100], [E0, N0]]]},
            },
            {"type": "Feature", "properties": {"name": "nothing"}, "geometry": None},
        ],
    }
    path = tmp_path / "plots.geojson"
    path.write_text(json.dumps(doc), encoding="utf-8")
    result = process_file(path, "plots.geojson")
    assert result.file_type == "geojson"
    assert result.crs == crs_label(UTM43N)
    first, second = result.features
    assert first.fid == "7"
    assert first.measurement.area_m2 == pytest.approx(10_000, rel=2e-3)
    assert second.measurement.status == MeasurementStatus.SKIPPED


def test_geojson_default_is_wgs84(tmp_path: Path) -> None:
    path = tmp_path / "pt.json"
    path.write_text(json.dumps({"type": "Point", "coordinates": [77.5, 12.9, 5]}), encoding="utf-8")
    result = process_file(path, "pt.json")
    assert result.crs == crs_label(WGS84)
    assert result.crs_source == "geojson"
    assert result.features[0].geometry_wgs84 is not None
    assert result.features[0].geometry_wgs84["coordinates"][2] == pytest.approx(5)


def test_corrupt_geojson(tmp_path: Path) -> None:
    path = tmp_path / "bad.geojson"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(IngestError) as exc:
        process_file(path, "bad.geojson")
    assert exc.value.code == "corrupt_file"


def test_kmz_prefers_doc_kml(tmp_path: Path) -> None:
    other = KML_DOC.replace(b"<name>Site</name>", b"<name>Other</name>")
    path = tmp_path / "site.kmz"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("files/other.kml", other)
        zf.writestr("doc.kml", KML_DOC)
    result = process_file(path, "site.kmz")
    assert result.file_type == "kmz"
    assert result.layers[0].name.startswith("Site")


def test_csv_tracks_with_times(tmp_path: Path) -> None:
    path = tmp_path / "show.csv"
    path.write_bytes(b"id,t,lon,lat,alt\nd1,0,77.5,12.9,0\nd1,1,77.5001,12.9,10\n")
    result = process_file(path, "show.csv")
    f = result.features[0]
    assert result.file_type == "csv"
    assert f.times == [0.0, 1.0]
    assert f.measurement.status == MeasurementStatus.MEASURED
    assert f.measurement.length_3d_m is not None and f.measurement.length_3d_m > f.measurement.length_m  # type: ignore[operator]


def test_unknown_extension(tmp_path: Path) -> None:
    path = tmp_path / "x.dxf"
    path.write_text("0\nSECTION\n", encoding="utf-8")
    with pytest.raises(IngestError) as exc:
        process_file(path, "x.dxf")
    assert exc.value.code == "unsupported_format"


def test_zip_slip_through_process_file(tmp_path: Path) -> None:
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("../../evil.kml", KML_DOC)
    with pytest.raises(IngestError) as exc:
        process_file(path, "evil.zip")
    assert exc.value.code == "unsafe_archive"


def test_temporary_workdir_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[str] = []
    real = tempfile.TemporaryDirectory

    class Recording(real):  # type: ignore[valid-type,misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["dir"] = str(tmp_path)
            super().__init__(*args, **kwargs)
            created.append(self.name)

    monkeypatch.setattr(processing.tempfile, "TemporaryDirectory", Recording)
    process_file(_shapefile_zip(tmp_path), "plots.zip")
    assert len(created) == 1
    assert not Path(created[0]).exists()

    with pytest.raises(IngestError):
        process_file(tmp_path / "plots.zip", "plots.zip", limits=IngestLimits(max_features=0))
    assert len(created) == 2
    assert not Path(created[1]).exists()


def test_explicit_workdir_kept(tmp_path: Path) -> None:
    work = tmp_path / "work"
    process_file(_shapefile_zip(tmp_path), "plots.zip", workdir=work)
    assert (work / "extracted" / "plots.shp").exists()


def test_json_safe() -> None:
    value = {
        "d": date(2024, 1, 2),
        "b": b"abc",
        "nb": b"\xff\xfe",
        "nan": float("nan"),
        "nested": [(1, 2), {3: "x"}],
    }
    assert json_safe(value) == {"d": "2024-01-02", "b": "abc", "nb": "//4=", "nan": None, "nested": [[1, 2], {"3": "x"}]}
