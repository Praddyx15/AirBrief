"""Tests for CRS resolution, labels, UTM helpers, transforms and GeoJSON conversion."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
    box,
)

from airbrief_core.errors import AnalysisError, IngestError
from airbrief_core.geo.crs import (
    WGS84,
    check_crs_text,
    crs_label,
    is_geographic,
    local_area_crs,
    local_area_proj4,
    local_length_crs,
    local_length_proj4,
    resolve_crs,
    try_resolve_crs,
    unit_factor,
    utm_epsg_for,
    utm_zone_label,
)
from airbrief_core.geo.transform import (
    geojson_to_geometry,
    geometry_to_geojson,
    to_wgs84,
    transform_geom,
    unwrap_antimeridian,
)

ESRI_UTM43N = (
    'PROJCS["WGS_1984_UTM_Zone_43N",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
    'PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",0.0],'
    'PARAMETER["Central_Meridian",75.0],PARAMETER["Scale_Factor",0.9996],'
    'PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]'
)


def test_resolve_crs_variants() -> None:
    assert resolve_crs(None) is None
    assert resolve_crs("") is None
    assert resolve_crs("   ") is None
    assert resolve_crs("EPSG:4326") == WGS84
    assert resolve_crs("4326") == WGS84
    assert resolve_crs(4326) == WGS84
    assert resolve_crs(WGS84) is WGS84
    utm = resolve_crs(ESRI_UTM43N)
    assert utm is not None and utm.is_projected


@pytest.mark.parametrize("bad", ["not a crs", "EPSG:999999", "+proj=nonsense"])
def test_resolve_crs_invalid(bad: str) -> None:
    with pytest.raises(IngestError) as info:
        resolve_crs(bad)
    assert info.value.code == "invalid_crs"
    assert try_resolve_crs(bad) is None


def test_crs_label() -> None:
    assert crs_label(None) is None
    assert crs_label(WGS84) == "EPSG:4326"
    assert crs_label(CRS.from_epsg(32643)) == "EPSG:32643"
    esri = resolve_crs(ESRI_UTM43N)
    assert crs_label(esri) == "EPSG:32643"
    custom = local_area_crs(77.6, 12.9)
    label = crs_label(custom)
    assert isinstance(label, str) and label


def test_is_geographic_and_unit_factor() -> None:
    assert is_geographic(WGS84)
    assert not is_geographic(CRS.from_epsg(32643))
    assert unit_factor(CRS.from_epsg(32643)) == 1.0
    assert unit_factor(CRS.from_epsg(2227)) == pytest.approx(1200.0 / 3937.0)
    compound = CRS.from_user_input("EPSG:32643+5773")
    assert not is_geographic(compound)
    assert unit_factor(compound) == 1.0


@pytest.mark.parametrize(
    ("lon", "lat", "epsg"),
    [
        (77.59, 12.97, 32643),
        (-70.6, -33.4, 32719),
        (0.5, 51.5, 32631),
        (-180.0, 10.0, 32601),
        (179.99, 10.0, 32660),
        (180.0, 10.0, 32601),
        (5.3, 60.4, 32632),  # Bergen: Norway exception
        (15.6, 78.2, 32633),  # Longyearbyen: Svalbard exception
        (0.0, 0.0, 32631),
    ],
)
def test_utm_epsg_for(lon: float, lat: float, epsg: int) -> None:
    assert utm_epsg_for(lon, lat) == epsg


def test_utm_zone_label() -> None:
    assert utm_zone_label(32643) == "43N"
    assert utm_zone_label(32719) == "19S"
    assert utm_zone_label(32601) == "1N"
    with pytest.raises(AnalysisError):
        utm_zone_label(4326)


def test_local_crs_strings() -> None:
    area = local_area_proj4(77.5, 12.25)
    assert area == "+proj=laea +lat_0=12.25 +lon_0=77.5 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
    length = local_length_proj4(-0.0, 0.0)
    assert length == "+proj=tmerc +lat_0=0 +lon_0=0 +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"
    assert local_area_crs(77.5, 12.25).is_projected
    assert local_length_crs(77.5, 12.25).is_projected
    assert local_area_crs(77.5, 12.25) is local_area_crs(77.5, 12.25)  # cached


def test_transform_round_trip_preserves_z() -> None:
    utm = CRS.from_epsg(32643)
    line = LineString([(777_000.0, 1_435_000.0, 12.5), (777_500.0, 1_435_500.0, 40.0)])
    wgs = to_wgs84(line, utm)
    assert wgs.has_z
    coords = list(wgs.coords)
    assert coords[0][2] == 12.5 and coords[1][2] == 40.0
    assert 77.0 < coords[0][0] < 78.5 and 12.5 < coords[0][1] < 13.5
    back = transform_geom(wgs, WGS84, utm)
    for (x1, y1, z1), (x2, y2, z2) in zip(back.coords, line.coords, strict=True):
        assert x1 == pytest.approx(x2, abs=1e-6)
        assert y1 == pytest.approx(y2, abs=1e-6)
        assert z1 == z2


def test_transform_same_crs_is_identity() -> None:
    poly = box(0, 0, 1, 1)
    assert transform_geom(poly, WGS84, CRS.from_epsg(4326)) is poly
    assert transform_geom(Polygon(), WGS84, CRS.from_epsg(32643)).is_empty


def test_transform_2d_stays_2d() -> None:
    wgs = to_wgs84(box(777_000.0, 1_435_000.0, 777_100.0, 1_435_100.0), CRS.from_epsg(32643))
    assert not wgs.has_z


def test_unwrap_antimeridian() -> None:
    crossing = Polygon([(179.9, 0.0), (-179.9, 0.0), (-179.9, 0.1), (179.9, 0.1)])
    unwrapped = unwrap_antimeridian(crossing)
    minx, _, maxx, _ = unwrapped.bounds
    assert minx == pytest.approx(179.9)
    assert maxx == pytest.approx(180.1)
    normal = box(10, 10, 11, 11)
    assert unwrap_antimeridian(normal) is normal
    wide = LineString([(-170.0, 0.0), (-10.0, 0.0), (10.0, 0.0), (170.0, 0.0)])
    assert unwrap_antimeridian(wide) is wide  # shifting would not reduce the span


def test_geometry_to_geojson_plain_lists_and_rounding() -> None:
    poly = Polygon([(77.123456789123, 12.987654321987), (77.2, 12.9), (77.2, 13.0), (77.123456789123, 12.987654321987)])
    gj = geometry_to_geojson(poly)
    assert gj["type"] == "Polygon"
    assert isinstance(gj["coordinates"], list)
    assert isinstance(gj["coordinates"][0], list)
    assert isinstance(gj["coordinates"][0][0], list)
    assert gj["coordinates"][0][0] == [77.12345679, 12.98765432]
    full = geometry_to_geojson(poly, decimals=None)
    assert full["coordinates"][0][0][0] == 77.123456789123
    json.dumps(gj)


def test_geometry_to_geojson_z_and_types() -> None:
    gj = geometry_to_geojson(LineString([(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]))
    assert gj == {"type": "LineString", "coordinates": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]}
    assert geometry_to_geojson(Point(1, 2)) == {"type": "Point", "coordinates": [1.0, 2.0]}
    mp = geometry_to_geojson(MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)]))
    assert mp["type"] == "MultiPolygon" and len(mp["coordinates"]) == 2
    gc = geometry_to_geojson(GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])]))
    assert gc["type"] == "GeometryCollection"
    assert [g["type"] for g in gc["geometries"]] == ["Point", "LineString"]


def test_geojson_round_trip() -> None:
    poly = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(2, 2), (2, 4), (4, 4), (4, 2)]])
    back = geojson_to_geometry(geometry_to_geojson(poly))
    assert back.equals(poly)
    feature = {"type": "Feature", "properties": {}, "geometry": geometry_to_geojson(Point(1, 2))}
    assert geojson_to_geometry(feature).equals(Point(1, 2))
    fc = {"type": "FeatureCollection", "features": [feature, {"type": "Feature", "geometry": None}]}
    assert geojson_to_geometry(fc).geom_type == "GeometryCollection"


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]},
        {"type": "Blob", "coordinates": []},
        {"type": "Point"},
        {"type": "Point", "coordinates": ["a", "b"]},
        {"type": "Feature", "geometry": None},
        {"type": "FeatureCollection", "features": []},
        {"type": "Point", "coordinates": [float("nan"), 1.0]},
    ],
)
def test_geojson_to_geometry_invalid(bad: dict) -> None:
    with pytest.raises(AnalysisError) as info:
        geojson_to_geometry(bad)
    assert info.value.code == "invalid_geometry"


# VERIFY-04: user CRS text never makes PROJ open files or go online.


@pytest.mark.parametrize(
    "text",
    [
        "+proj=longlat +ellps=WGS84 +nadgrids=C:/Windows/win.ini",
        "+proj=longlat +datum=WGS84 +nadgrids=@conus",
        "+proj=utm +zone=43 +datum=WGS84 +geoidgrids=egm96_15.gtx",
        "+init=epsg:4326",
        "+proj=hgridshift +grids=foo.tif",
        "+proj=vgridshift +grids=foo.tif",
        "+proj=deformation +xy_grids=a.tif +z_grids=b.tif +t_epoch=2010",
        "+proj=tinshift +file=/etc/passwd",
        "+proj=pipeline +step +proj=xyzgridshift +grids=x.tif",
        'GEOGCS["x",DATUM["d",SPHEROID["s",6378137,298.257]],PRIMEM["G",0],UNIT["degree",0.0174532925199433],'
        'EXTENSION["PROJ4_GRIDS","/etc/passwd"]]',
        'BOUNDCRS[SOURCECRS[GEOGCRS["x",DATUM["d",ELLIPSOID["s",6378137,298.257]],CS[ellipsoidal,2],'
        'AXIS["lat",north],AXIS["lon",east],ANGLEUNIT["degree",0.0174532925199433]]],'
        'TARGETCRS[GEOGCRS["WGS 84",DATUM["World Geodetic System 1984",ELLIPSOID["WGS 84",6378137,298.257223563]],'
        'CS[ellipsoidal,2],AXIS["lat",north],AXIS["lon",east],ANGLEUNIT["degree",0.0174532925199433]]],'
        'ABRIDGEDTRANSFORMATION["t",METHOD["NTv2"],PARAMETERFILE["Latitude and longitude difference file","/etc/passwd"]]]',
    ],
)
def test_crs_text_with_files_is_refused(text: str) -> None:
    with pytest.raises(IngestError) as info:
        resolve_crs(text)
    assert info.value.code == "invalid_crs"
    assert info.value.details["rule"] == "file_reference"
    assert "reason" not in info.value.details


def test_parsed_crs_that_carries_a_grid_file_is_refused() -> None:
    # PROJJSON names its grid file in a parameter value: only the parsed CRS shows it.
    bound = CRS.from_user_input("+proj=longlat +ellps=WGS84 +nadgrids=foo.gsb +type=crs").to_json()
    projjson = bound.replace("nadgrids=foo.gsb", "custom")
    check_crs_text(projjson)  # the text alone passes
    with pytest.raises(IngestError) as info:
        resolve_crs(projjson)
    assert info.value.details["rule"] == "file_reference"


@pytest.mark.parametrize(
    ("text", "epsg"),
    [
        ("EPSG:32643", 32643),
        ("32643", 32643),
        ("urn:ogc:def:crs:EPSG::3857", 3857),
        ("+proj=utm +zone=43 +datum=WGS84 +units=m +no_defs", 32643),
        (ESRI_UTM43N, 32643),
        (CRS.from_epsg(32643).to_wkt(), 32643),
        # GDAL writes "+nadgrids=@null" (no datum shift) into its WKT of Web Mercator
        (CRS.from_epsg(3857).to_wkt("WKT1_GDAL"), 3857),
    ],
)
def test_ordinary_crs_text_still_resolves(text: str, epsg: int) -> None:
    crs = resolve_crs(text)
    assert crs is not None and crs.to_epsg() == epsg


def test_proj_error_text_is_not_echoed() -> None:
    with pytest.raises(IngestError) as info:
        resolve_crs("+proj=nonsense")
    assert info.value.details == {"crs": "+proj=nonsense"}
    assert "proj_create" not in info.value.message


def test_proj_networking_is_off_even_when_the_environment_asks_for_it() -> None:
    code = "import airbrief_core.geo.crs, pyproj; print(pyproj.network.is_network_enabled())"
    env = {**os.environ, "PROJ_NETWORK": "ON"}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env=env,
                          cwd=Path(__file__).resolve().parents[2])
    assert done.returncode == 0, done.stderr[-500:]
    assert done.stdout.strip() == "False"


def test_shapefile_prj_with_a_grid_file_leaves_the_crs_unknown(tmp_path: Path) -> None:
    from airbrief_core.io.shapefile import read_prj

    good = tmp_path / "good.prj"
    good.write_text(ESRI_UTM43N, encoding="utf-8")
    crs, problem = read_prj(good)
    assert problem is None and crs is not None and crs.to_epsg() == 32643

    bad = tmp_path / "bad.prj"
    bad.write_text("+proj=longlat +ellps=WGS84 +nadgrids=/etc/passwd", encoding="utf-8")
    crs, problem = read_prj(bad)
    assert crs is None
    assert problem == "the .prj refers to grid or init files, which are not read"

    broken = tmp_path / "broken.prj"
    broken.write_text("PROJCS[nonsense", encoding="utf-8")
    assert read_prj(broken) == (None, "the .prj could not be parsed")


def test_sample_shapefile_prj_still_loads(tmp_path: Path) -> None:
    from airbrief_core.processing import process_file
    from airbrief_core.samples import build_sample

    path = tmp_path / "plots.zip"
    path.write_bytes(build_sample("plots_shapefile"))
    result = process_file(path, "plots.zip")
    assert result.feature_count == 3
    assert result.layers[0].crs == "EPSG:32643"


def test_geojson_legacy_crs_with_an_init_file_is_not_used(tmp_path: Path) -> None:
    from airbrief_core.processing import process_file

    doc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "+init=/etc/passwd:x"}},
        "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [1, 2]}}],
    }
    path = tmp_path / "points.geojson"
    path.write_text(json.dumps(doc), encoding="utf-8")
    result = process_file(path, "points.geojson")
    assert result.layers[0].crs is None
    assert any("is not recognised" in issue.message for issue in result.issues)
