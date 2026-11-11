"""Tests for the CSV reader: tracks (lon/lat/alt and local x/y/z with an origin), point lists,
kept attribute columns and archives of CSV files."""

from __future__ import annotations

import math
import time
import zipfile
from pathlib import Path

import pytest

from airbrief_core.errors import IngestError
from airbrief_core.io.tracks import parse_cell_value, read_track_csv
from airbrief_core.processing import process_file

LONLAT = b"""# demo show
id,t,lon,lat,alt
d1,0,77.5000,12.9000,0
d1,1,77.5001,12.9000,5
d2,0,77.5010,12.9010,0
d1,2,77.5002,12.9000,10
d2,1,77.5011,12.9010,6
"""


def test_lonlat_tracks_grouped_and_sorted() -> None:
    layer = read_track_csv(LONLAT, "show", None)
    assert layer.crs is not None and layer.crs.to_epsg() == 4326
    assert layer.crs_source == "csv"
    by_id = {f.fid: f for f in layer.features}
    assert set(by_id) == {"d1", "d2"}
    d1 = by_id["d1"]
    assert d1.geometry is not None and d1.geometry.geom_type == "LineString" and d1.geometry.has_z
    assert [c[2] for c in d1.geometry.coords] == [0.0, 5.0, 10.0]
    assert d1.times == [0.0, 1.0, 2.0]
    assert d1.time_origin_utc is None
    assert d1.altitude_mode == "relativeToGround"
    assert d1.properties["samples"] == 3


def test_semicolon_delimiter_and_aliases() -> None:
    data = b"Longitude;Latitude;Altitude\n77.5;12.9;100\n77.6;12.95;120\n"
    layer = read_track_csv(data, "path", None)
    f = layer.features[0]
    assert f.name == "path"
    assert f.times is None
    assert f.geometry is not None and list(f.geometry.coords)[1] == (77.6, 12.95, 120.0)


def test_iso_times_relative_to_earliest() -> None:
    data = b"""drone,time,lat,lon,alt
a,2024-05-01T10:00:05Z,12.9,77.5,10
a,2024-05-01T10:00:06Z,12.9,77.5001,10
b,2024-05-01T10:00:00Z,12.9,77.6,10
b,2024-05-01T10:00:02Z,12.9,77.6001,10
"""
    layer = read_track_csv(data, "t", None)
    by_id = {f.fid: f for f in layer.features}
    assert by_id["a"].times == [5.0, 6.0]
    assert by_id["b"].times == [0.0, 2.0]
    assert by_id["a"].time_origin_utc == "2024-05-01T10:00:00Z"


def test_local_xyz_with_origin_comment() -> None:
    data = b"""# origin=77.5,12.9,900
# altitude_mode=absolute
id,t,x,y,z
d1,0,0,0,0
d1,1,100,0,50
"""
    layer = read_track_csv(data, "local", None)
    f = layer.features[0]
    assert f.geometry is not None
    (lon0, lat0, z0), (lon1, lat1, z1) = list(f.geometry.coords)
    assert lon0 == pytest.approx(77.5, abs=1e-9)
    assert lat0 == pytest.approx(12.9, abs=1e-9)
    assert z0 == pytest.approx(900.0)
    assert z1 == pytest.approx(950.0)
    # 100 m east at 12.9°N is about 100 / (111320 * cos(lat)) degrees of longitude.
    assert lon1 - lon0 == pytest.approx(100 / (111_320 * math.cos(math.radians(12.9))), rel=1e-2)
    assert lat1 == pytest.approx(12.9, abs=1e-6)
    assert f.altitude_mode == "absolute"
    assert f.properties["source_format"] == "local_xyz"


def test_local_xyz_origin_argument_wins() -> None:
    data = b"# origin=0,0,0\nx,y,z\n0,0,1\n10,10,1\n"
    layer = read_track_csv(data, "local", (77.5, 12.9, 0.0))
    coords = list(layer.features[0].geometry.coords)  # type: ignore[union-attr]
    assert coords[0][0] == pytest.approx(77.5, abs=1e-9)
    assert any(i.code == "origin_override" for i in layer.issues)


def test_local_xyz_without_origin_fails() -> None:
    with pytest.raises(IngestError) as exc:
        read_track_csv(b"x,y,z\n0,0,0\n1,1,1\n", "local", None)
    assert exc.value.code == "invalid_crs"


def test_bad_rows_skipped_with_issue() -> None:
    data = b"lon,lat,alt\n77.5,12.9,1\nbad,row,x\n200,12.9,1\n77.6,12.9,2\n"
    layer = read_track_csv(data, "p", None)
    assert len(list(layer.features[0].geometry.coords)) == 2  # type: ignore[union-attr]
    assert any(i.code == "rows_skipped" for i in layer.issues)


def test_single_sample_is_point() -> None:
    layer = read_track_csv(b"lon,lat\n77.5,12.9\n", "p", None)
    geom = layer.features[0].geometry
    assert geom is not None and geom.geom_type == "Point"


def test_no_position_columns() -> None:
    with pytest.raises(IngestError) as exc:
        read_track_csv(b"a,b,c\n1,2,3\n", "p", None)
    assert exc.value.code == "corrupt_file"


def test_no_valid_rows() -> None:
    with pytest.raises(IngestError) as exc:
        read_track_csv(b"lon,lat\nfoo,bar\n", "p", None)
    assert exc.value.code == "no_supported_content"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("  42 ", 42),
        ("-3.50", -3.5),
        ("+7", 7),
        ("1e3", 1000.0),
        ("0", 0),
        ("0.25", 0.25),
        ("007", "007"),
        ("1,200", "1,200"),
        ("NaN", "NaN"),
        ("1e400", "1e400"),
        ("1234567890123456", "1234567890123456"),
        ("123456789012345", 123456789012345),
        ("Tower A", "Tower A"),
    ],
)
def test_cell_values_are_numbers_only_when_clean(text: str, expected: object) -> None:
    value = parse_cell_value(text)
    assert value == expected
    assert type(value) is type(expected)


TOWERS = b"""asset_id,name,lon,lat,height,structure,voltage_kv
T-001,Hebbal mast,77.5900,13.0350,45,lattice,220
T-002,Yelahanka mast,77.5960,13.1000,38.5,monopole,
T-003,007,77.6000,13.1100,30,lattice,33
"""


def test_tower_list_becomes_points_with_attributes() -> None:
    layer = read_track_csv(TOWERS, "towers", None)
    assert [f.fid for f in layer.features] == ["T-001", "T-002", "T-003"]
    first, second, third = layer.features
    assert first.name == "Hebbal mast"
    assert first.geometry is not None and first.geometry.geom_type == "Point"
    assert tuple(first.geometry.coords)[0] == (77.59, 13.035, 45.0)
    assert first.times is None
    assert first.properties == {
        "asset_id": "T-001",
        "name": "Hebbal mast",
        "height": 45,
        "structure": "lattice",
        "voltage_kv": 220,
        "source_format": "lon_lat",
    }
    assert second.properties["height"] == 38.5 and second.properties["voltage_kv"] is None
    # A name made of digits with a leading zero stays text (an identifier, not a number).
    assert third.name == "007" and third.properties["name"] == "007"


def test_points_without_height_are_2d() -> None:
    layer = read_track_csv(b"site_id,lon,lat\nS1,77.5,12.9\nS2,77.6,12.95\n", "sites", None)
    assert [f.geometry.geom_type for f in layer.features if f.geometry is not None] == ["Point", "Point"]
    assert not any(f.geometry is not None and f.geometry.has_z for f in layer.features)


def test_repeated_ids_without_time_stay_tracks() -> None:
    data = b"id,lon,lat\na,77.5,12.9\na,77.6,12.9\nb,77.7,12.9\n"
    layer = read_track_csv(data, "lines", None)
    by_id = {f.fid: f for f in layer.features}
    assert by_id["a"].geometry is not None and by_id["a"].geometry.geom_type == "LineString"
    assert by_id["b"].geometry is not None and by_id["b"].geometry.geom_type == "Point"


def test_geometry_directive_forces_points_or_tracks() -> None:
    points = read_track_csv(b"# geometry=points\nlon,lat,t,note\n77.5,12.9,0,start\n77.6,12.9,5,end\n", "drops", None)
    assert [f.geometry.geom_type for f in points.features if f.geometry is not None] == ["Point", "Point"]
    assert points.features[0].properties["t"] == 0 and points.features[1].properties["note"] == "end"
    assert points.features[0].fid == "1" and points.features[0].name is None

    hint = read_track_csv(b"# geometry_hint=points\nlon,lat\n77.5,12.9\n77.6,12.9\n", "drops", None)
    assert len(hint.features) == 2

    tracks = read_track_csv(b"# geometry=tracks\nid,lon,lat\na,77.5,12.9\nb,77.6,12.9\n", "t", None)
    assert all(f.properties.get("samples") == 1 for f in tracks.features)


def test_track_keeps_constant_columns_only() -> None:
    data = b"""drone_id,t,lon,lat,alt,model,battery,colour
d1,0,77.5000,12.9000,0,X500,98,red
d1,1,77.5001,12.9000,5,X500,97,green
d2,0,77.5010,12.9010,0,X600,99,blue
d2,1,77.5011,12.9010,6,X600,99,blue
"""
    layer = read_track_csv(data, "show", None)
    by_id = {f.fid: f for f in layer.features}
    assert by_id["d1"].properties == {"model": "X500", "track_id": "d1", "samples": 2, "source_format": "lon_lat"}
    # On d2 the battery and colour happen to stay constant, so that track keeps them.
    assert by_id["d2"].properties["battery"] == 99 and by_id["d2"].properties["colour"] == "blue"
    notes = [i for i in layer.issues if i.code == "csv_columns_not_kept"]
    assert len(notes) == 1 and "battery, colour" in notes[0].message


def test_column_named_like_a_system_property_is_prefixed() -> None:
    layer = read_track_csv(b"samples,lon,lat\n12,77.5,12.9\n12,77.6,12.9\n", "x", None)
    props = layer.features[0].properties
    assert props["csv_samples"] == 12 and props["samples"] == 2


def test_skybrush_style_headers_with_units_and_milliseconds() -> None:
    data = b"""Time [msec],x [m],y [m],z [m],Red,Green,Blue
0,0,0,0,255,0,0
500,1,0,2,255,0,0
1000,2,0,4,0,255,0
"""
    layer = read_track_csv(data, "Drone 7", (77.5, 12.9, 0.0))
    (feature,) = layer.features
    assert feature.name == "Drone 7"
    assert feature.times == [0.0, 0.5, 1.0]
    assert feature.geometry is not None and [c[2] for c in feature.geometry.coords] == [0.0, 2.0, 4.0]
    assert any(i.code == "csv_columns_not_kept" and "Green, Red" in i.message for i in layer.issues)


def test_time_msec_column_name() -> None:
    layer = read_track_csv(b"Time_msec,x_m,y_m,z_m\n0,0,0,0\n250,1,0,1\n", "d", (77.5, 12.9, 0.0))
    assert layer.features[0].times == [0.0, 0.25]


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_zip_of_per_drone_csvs(tmp_path: Path) -> None:
    drone = b"Time_msec,x_m,y_m,z_m,Red,Green,Blue\n0,{x},0,0,1,2,3\n1000,{x},0,5,4,5,6\n2000,{x},0,10,7,8,9\n"
    entries = {f"show/D{i:02d}.csv": drone.replace(b"{x}", str(3 * i).encode()) for i in range(4)}
    path = _zip(tmp_path / "skybrush.zip", entries)
    result = process_file(path, "skybrush.zip", workdir=tmp_path / "work", csv_origin=(77.5920, 13.0005, 0.0))
    assert result.file_type == "csv"
    assert [f.name for f in result.features] == ["D00", "D01", "D02", "D03"]
    assert all(f.times == [0.0, 1.0, 2.0] for f in result.features)
    notes = [i for i in result.issues if i.code == "csv_columns_not_kept"]
    assert len(notes) == 1 and "The same applies to 3 other CSV files." in notes[0].message


def test_zip_of_local_csvs_without_origin_fails(tmp_path: Path) -> None:
    path = _zip(tmp_path / "s.zip", {"a.csv": b"x,y,z\n0,0,0\n1,1,1\n", "b.csv": b"x,y,z\n0,0,0\n1,1,1\n"})
    with pytest.raises(IngestError) as exc:
        process_file(path, "s.zip", workdir=tmp_path / "work")
    assert exc.value.code == "invalid_crs"


def test_unreadable_csv_next_to_kml_is_skipped(tmp_path: Path) -> None:
    kml = (
        b'<kml xmlns="http://www.opengis.net/kml/2.2"><Placemark><name>Show box</name><Polygon><outerBoundaryIs>'
        b"<LinearRing><coordinates>0,0 0.001,0 0.001,0.001 0,0</coordinates></LinearRing></outerBoundaryIs>"
        b"</Polygon></Placemark></kml>"
    )
    path = _zip(tmp_path / "site.zip", {"site.kml": kml, "readme.csv": b"note\nhello\n"})
    result = process_file(path, "site.zip", workdir=tmp_path / "work")
    assert [f.name for f in result.features] == ["Show box"]
    assert any(i.code == "corrupt_file" and "'readme.csv' skipped" in i.message for i in result.issues)


def test_track_without_a_height_column_is_2d() -> None:
    # No height column: nothing says how high it flew, so the track is 2D (never read as 0 m).
    layer = read_track_csv(b"t,lon,lat\n0,77.5,12.9\n10,77.501,12.9\n20,77.502,12.9\n", "walk", None)
    (track,) = layer.features
    assert track.geometry is not None and track.geometry.geom_type == "LineString"
    assert not track.geometry.has_z
    assert track.altitude_mode == "clampToGround"
    assert track.times == [0.0, 10.0, 20.0]


def test_blank_heights_inside_a_track_are_interpolated() -> None:
    data = b"id,t,lon,lat,alt\na,0,77.5,12.9,100\na,1,77.501,12.9,\na,2,77.502,12.9,\na,3,77.503,12.9,130\n"
    layer = read_track_csv(data, "gaps", None)
    (track,) = layer.features
    assert track.geometry is not None
    assert [c[2] for c in track.geometry.coords] == pytest.approx([100.0, 110.0, 120.0, 130.0])
    assert track.altitude_mode == "relativeToGround"
    assert any(i.code == "heights_interpolated" and "2 row(s)" in i.message for i in layer.issues)


def test_altitude_mode_directive_still_wins_without_heights() -> None:
    layer = read_track_csv(b"# altitude_mode=absolute\nt,lon,lat\n0,77.5,12.9\n1,77.501,12.9\n", "x", None)
    assert layer.features[0].altitude_mode == "absolute"
    assert layer.features[0].geometry is not None and not layer.features[0].geometry.has_z


def test_zip_of_csvs_keeps_the_zip_slip_guard(tmp_path: Path) -> None:
    good = b"t,lon,lat,alt\n0,77.5,12.9,0\n1,77.501,12.9,5\n"
    path = _zip(tmp_path / "slip.zip", {"D01.csv": good, "../../evil.csv": good})
    with pytest.raises(IngestError) as exc:
        process_file(path, "slip.zip", workdir=tmp_path / "work")
    assert exc.value.code == "unsafe_archive"
    assert not (tmp_path / "evil.csv").exists()


def test_zip_of_csvs_keeps_the_expansion_limit(tmp_path: Path) -> None:
    from airbrief_core.io.ingest import IngestLimits, stage_upload

    row = b"0,77.5,12.9,0\n" * 2000  # ~28 kB per file: ten of them expand past the 100 kB limit
    entries = {f"D{i:02d}.csv": b"t,lon,lat,alt\n" + row for i in range(10)}
    path = _zip(tmp_path / "bomb.zip", entries)
    with pytest.raises(IngestError) as exc:
        stage_upload(path, "bomb.zip", tmp_path / "work", IngestLimits(max_unzipped_bytes=100_000))
    assert exc.value.code == "archive_too_large"


def test_oversized_cell_is_a_corrupt_file_and_skipped_inside_an_archive(tmp_path: Path) -> None:
    huge = b'lon,lat,note\n77.5,12.9,"' + b"a" * 200_000 + b'"\n'
    with pytest.raises(IngestError) as exc:
        read_track_csv(huge, "x", None)
    assert exc.value.code == "corrupt_file"
    good = b"t,lon,lat,alt\n0,77.5,12.9,0\n1,77.501,12.9,5\n"
    path = _zip(tmp_path / "mixed.zip", {"D01.csv": good, "D02.csv": huge})
    result = process_file(path, "mixed.zip", workdir=tmp_path / "work")
    assert [f.name for f in result.features] == ["D01"]
    assert any(i.code == "corrupt_file" and "'D02.csv' skipped" in i.message for i in result.issues)


@pytest.mark.parametrize(
    "data",
    [
        # A header cell like "a   (   x": the unit pattern backtracked for hours (8 s at 200 spaces).
        ("lon,lat,a" + " " * 3000 + "(" + " " * 3000 + "x\n77.5,12.9,1\n").encode(),
        ("lon,lat,[" * 1 + "[" * 3000 + " " * 3000 + "\n77.5,12.9,1\n").encode(),
        # A directive comment with a long run of spaces inside (quadratic: 22 s at 40,000).
        ("# geometry=points" + " " * 200_000 + "x\nlon,lat\n77.5,12.9\n").encode(),
    ],
    ids=["unit-spaces", "unit-brackets", "directive-spaces"],
)
def test_hostile_header_cells_and_comments_are_read_in_linear_time(data: bytes) -> None:
    started = time.perf_counter()
    layer = read_track_csv(data, "hostile", None)
    assert time.perf_counter() - started < 1.0
    assert len(layer.features) == 1


@pytest.mark.parametrize(
    ("header", "key"),
    [
        ("x [m]", "x"),
        ("Altitude (m)", "altitude"),
        ("Time [msec]", "time"),
        ("Tower height (m)", "tower_height"),
        ("a (b) (m)", "a_(b)"),
        ("(m)", "(m)"),
        ("plain name", "plain_name"),
        ("x [m) ", "x"),
    ],
)
def test_unit_suffixes_are_still_read(header: str, key: str) -> None:
    from airbrief_core.io.tracks import _column_key

    assert _column_key(header)[0] == key
