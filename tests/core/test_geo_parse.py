"""Typed and pasted locations: ``airbrief_core.geo.parse``.

Reference values are hand-computed: D° M′ S″ = D + M/60 + S/3600 (e.g. 12°58′17.8″ =
12 + 0.966667 + 0.004944 = 12.971611°), southern and western hemispheres negative.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
import shapely
from shapely.geometry import shape

from airbrief_core.errors import AnalysisError
from airbrief_core.geo.parse import (
    MAX_NESTING,
    MAX_TEXT_BYTES,
    MAX_VERTICES,
    check_json_limits,
    detect_text_format,
    format_lat_lon,
    parse_coordinates,
    parse_geometry_text,
)


def _one(text: str) -> tuple[float, float, set[str]]:
    points, issues = parse_coordinates(text)
    assert len(points) == 1
    lon, lat = points[0]
    return lon, lat, {i.code for i in issues}


@pytest.mark.parametrize("text", ["12.9716, 77.5946", "12.9716 77.5946", "12.9716,77.5946", "(12.9716, 77.5946)"])
def test_decimal_degrees_lat_lon(text: str) -> None:
    lon, lat, codes = _one(text)
    assert (lon, lat) == pytest.approx((77.5946, 12.9716))
    # Both values are within ±90°, so the order is assumed and said so.
    assert "ambiguous_order" in codes


def test_negative_decimal_degrees_and_unicode_minus() -> None:
    lon, lat, codes = _one("-33.8688, 151.2093")
    assert (lon, lat) == pytest.approx((151.2093, -33.8688))
    assert "ambiguous_order" not in codes  # 151 can only be a longitude
    lon, lat, _ = _one("−33.8688 151.2093")
    assert lat == pytest.approx(-33.8688)


def test_swapped_order_detected_by_range() -> None:
    lon, lat, codes = _one("151.2093, -33.8688")
    assert (lon, lat) == pytest.approx((151.2093, -33.8688))
    assert "lonlat_order" in codes


def test_square_brackets_are_geojson_order() -> None:
    lon, lat, codes = _one("[77.5946, 12.9716]")
    assert (lon, lat) == pytest.approx((77.5946, 12.9716))
    assert "lonlat_order" in codes and "ambiguous_order" not in codes


@pytest.mark.parametrize("text", ["12,9716 77,5946", "12,9716, 77,5946", "12,9716 / 77,5946"])
def test_decimal_comma(text: str) -> None:
    lon, lat, codes = _one(text)
    assert (lon, lat) == pytest.approx((77.5946, 12.9716))
    assert "decimal_comma" in codes


def test_comma_between_integers_is_a_separator() -> None:
    lon, lat, codes = _one("12,77")
    assert (lon, lat) == (77.0, 12.0)
    assert "decimal_comma" not in codes


def test_decimal_commas_without_spaces() -> None:
    # Four numbers joined by commas can only be two decimal-comma values.
    lon, lat, codes = _one("12,9716,77,5946")
    assert (lon, lat) == pytest.approx((77.5946, 12.9716))
    assert "decimal_comma" in codes
    # "12,5, 77": the comma followed by a space separates; the other is a decimal comma.
    assert _one("12,5, 77")[:2] == (77.0, 12.5)
    assert _one("12, 5,77")[:2] == pytest.approx((5.77, 12.0))


def test_three_numbers_joined_by_commas_are_refused() -> None:
    # 12.5, 77 or 12, 5.77: either reading is possible, so neither is guessed.
    with pytest.raises(AnalysisError) as err:
        parse_coordinates("12,5,77")
    assert err.value.code == "invalid_coordinates"
    assert "read two ways" in err.value.message


def test_only_ascii_digits_are_numbers() -> None:
    # The browser's parser (frontend/lib/coords.ts) reads ASCII digits only; so does the API.
    with pytest.raises(AnalysisError) as err:
        parse_coordinates("١٢.٩٧, ٧٧.٥٩")  # Arabic-Indic digits
    assert err.value.code == "invalid_coordinates"


def test_line_breaks_number_the_lines() -> None:
    points, _ = parse_coordinates("13.1, 77.1\r\n13.2, 77.2\u202813.3, 77.3")
    assert len(points) == 3
    with pytest.raises(AnalysisError) as err:
        parse_coordinates("13.1, 77.1\r\n13.2, 77.2\r\n95, 200")
    assert err.value.details["line"] == 3


@pytest.mark.parametrize(
    "text",
    [
        "12.9716 N, 77.5946 E",
        "12.9716N 77.5946E",
        "N12.9716 E77.5946",
        "N 12.9716, E 77.5946",
        "77.5946 E 12.9716 N",  # letters decide, whatever the order
        "12.9716° N, 77.5946° E",
        "12.9716 N 77.5946 E",  # the narrow no-break space AirBrief itself writes
        "12.9716 n 77.5946 e",
    ],
)
def test_hemisphere_letters(text: str) -> None:
    lon, lat, codes = _one(text)
    assert (lon, lat) == pytest.approx((77.5946, 12.9716))
    assert not codes & {"ambiguous_order", "lonlat_order"}


@pytest.mark.parametrize(
    "text",
    [
        "12°58'17.8\"N 77°35'40.6\"E",
        "12°58′17.8″N 77°35′40.6″E",
        "12° 58' 17.8\" N, 77° 35' 40.6\" E",
        "12 58 17.8 N 77 35 40.6 E",
        "N 12 58 17.8 E 77 35 40.6",
        "12°58'17.8''N 77°35'40.6''E",
    ],
)
def test_degrees_minutes_seconds(text: str) -> None:
    lon, lat, _ = _one(text)
    assert lat == pytest.approx(12 + 58 / 60 + 17.8 / 3600, abs=1e-9)  # 12.971611°
    assert lon == pytest.approx(77 + 35 / 60 + 40.6 / 3600, abs=1e-9)  # 77.594611°


def test_degrees_decimal_minutes() -> None:
    lon, lat, _ = _one("N 12° 58.297' E 77° 35.677'")
    assert lat == pytest.approx(12 + 58.297 / 60, abs=1e-9)
    assert lon == pytest.approx(77 + 35.677 / 60, abs=1e-9)


def test_southern_and_western_hemispheres() -> None:
    lon, lat, _ = _one("33°52'07.7\"S 151°12'33.5\"E")  # Sydney
    assert (lat, lon) == pytest.approx((-(33 + 52 / 60 + 7.7 / 3600), 151 + 12 / 60 + 33.5 / 3600))
    lon, lat, _ = _one("40°42'46\"N 74°00'22\"W")  # New York
    assert (lat, lon) == pytest.approx((40.712778, -74.006111), abs=1e-6)


def test_one_hemisphere_letter_decides_the_other_axis() -> None:
    lon, lat, codes = _one("77.5946 E, 12.9716")
    assert (lon, lat) == pytest.approx((77.5946, 12.9716))
    assert "ambiguous_order" not in codes


def test_points_on_lines_and_semicolons() -> None:
    points, issues = parse_coordinates("13.1979, 77.7063; 13.20, 77.71\n\n13.21 77.70\n")
    assert points == [(77.7063, 13.1979), (77.71, 13.2), (77.7, 13.21)]
    assert [i.code for i in issues].count("ambiguous_order") == 1


def test_one_proven_order_applies_to_every_point() -> None:
    points, issues = parse_coordinates("151.2093, -33.8688\n151.0, -33.0")
    assert points == [(151.2093, -33.8688), (151.0, -33.0)]
    assert {i.code for i in issues} == {"lonlat_order"}
    # The second line alone would be ambiguous; the first proves longitude-first for both.
    points, _ = parse_coordinates("100.5, 12.9\n77.59, 12.97")
    assert points == [(100.5, 12.9), (77.59, 12.97)]


def test_mixed_orders_are_refused() -> None:
    with pytest.raises(AnalysisError) as err:
        parse_coordinates("12.9, 100.5\n100.5, 12.9")
    assert err.value.code == "invalid_coordinates"
    assert "mix the two orders" in err.value.message


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("95, 200", "can be a latitude"),
        ("91 N 10 E", "latitude 91"),
        ("10 N 181 E", "longitude 181"),
        ("12.97", "exactly two values"),
        ("12.97 77.59 100.0", "exactly two values"),
        ("12.97 N 77.59 N", "two latitudes"),
        ("-12 S 77 E", "either a minus sign"),
        ("12 61 0 N 77 0 0 E", "below 60"),
        ("hello 12 77", "unexpected text"),
        ("", "at least one point"),
    ],
)
def test_invalid_coordinates(text: str, fragment: str) -> None:
    with pytest.raises(AnalysisError) as err:
        parse_coordinates(text)
    assert err.value.code == "invalid_coordinates"
    assert fragment in err.value.message


def test_too_many_points() -> None:
    with pytest.raises(AnalysisError) as err:
        parse_coordinates("1, 1\n" * (MAX_VERTICES + 1))
    assert err.value.code == "too_complex"


def test_format_lat_lon() -> None:
    assert format_lat_lon(77.5946, 12.9716) == "12.97160° N, 77.59460° E"
    assert format_lat_lon(-74.006, -33.86, decimals=2) == "33.86° S, 74.01° W"


def _geom(text: str, shape_: str = "auto") -> tuple[dict, set[str]]:
    geometry, issues = parse_geometry_text(text, shape_)  # type: ignore[arg-type]
    return geometry, {i.code for i in issues}


def test_detect_text_format() -> None:
    assert detect_text_format('{"type": "Point", "coordinates": [1, 2]}') == "geojson"
    assert detect_text_format("[77.5, 12.9]") == "geojson"
    assert detect_text_format("[77.5, 12.9]\n[77.6, 12.8]") == "coordinates"
    assert detect_text_format("  polygon ((0 0, 1 0, 1 1, 0 0))") == "wkt"
    assert detect_text_format("SRID=4326;POINT(77 12)") == "wkt"
    assert detect_text_format("12.97, 77.59") == "coordinates"


def test_wkt_point_and_polygon() -> None:
    geometry, codes = _geom("POINT (77.5946 12.9716)")
    assert geometry == {"type": "Point", "coordinates": [77.5946, 12.9716]}
    assert codes == set()
    geometry, _ = _geom("POLYGON ((77.70 13.19, 77.72 13.19, 77.72 13.21, 77.70 13.21, 77.70 13.19))")
    assert geometry["type"] == "Polygon"
    assert shape(geometry).bounds == pytest.approx((77.70, 13.19, 77.72, 13.21))


def test_ewkt_srid() -> None:
    geometry, _ = _geom("SRID=4326;LINESTRING (77 12, 77.1 12.1)")
    assert geometry["type"] == "LineString"
    with pytest.raises(AnalysisError) as err:
        _geom("SRID=3857;POINT (8649000 1456000)")
    assert err.value.code == "invalid_geometry"


def test_geojson_geometry_feature_and_collection() -> None:
    point = {"type": "Point", "coordinates": [77.5946, 12.9716]}
    assert _geom(json.dumps(point))[0] == point
    feature = {"type": "Feature", "properties": {"name": "x"}, "geometry": point}
    assert _geom(json.dumps(feature))[0] == point
    collection = {
        "type": "FeatureCollection",
        "features": [
            feature,
            {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[77, 12], [77.1, 12.1]]}},
        ],
    }
    geometry, _ = _geom(json.dumps(collection))
    assert geometry["type"] == "GeometryCollection"
    assert [g["type"] for g in geometry["geometries"]] == ["Point", "LineString"]


def test_geojson_position_arrays() -> None:
    geometry, codes = _geom("[77.5946, 12.9716]")
    assert geometry == {"type": "Point", "coordinates": [77.5946, 12.9716]}
    assert "lonlat_order" in codes
    ring = [[77.70, 13.19], [77.72, 13.19], [77.72, 13.21], [77.70, 13.19]]
    assert _geom(json.dumps(ring))[0]["type"] == "Polygon"


def test_geojson_with_a_projected_crs_member_is_refused() -> None:
    doc = {
        "type": "Point",
        "coordinates": [500000, 1400000],
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::32643"}},
    }
    with pytest.raises(AnalysisError) as err:
        _geom(json.dumps(doc))
    assert err.value.code == "invalid_geometry"
    assert "32643" in err.value.message


def test_projected_coordinates_are_refused() -> None:
    with pytest.raises(AnalysisError) as err:
        _geom("POINT (500000 1400000)")
    assert err.value.code == "invalid_coordinates"


def test_typed_coordinates_become_a_shape() -> None:
    assert _geom("13.1979, 77.7063")[0] == {"type": "Point", "coordinates": [77.7063, 13.1979]}
    line, codes = _geom("13.19 77.70\n13.20 77.71\n13.21 77.70")
    assert line["type"] == "LineString" and "read_as_line" in codes
    closed, _ = _geom("13.19 77.70\n13.20 77.71\n13.21 77.70\n13.19 77.70")
    assert closed["type"] == "Polygon"
    area, _ = _geom("13.19 77.70\n13.20 77.71\n13.21 77.70", "polygon")
    assert area["type"] == "Polygon"
    assert area["coordinates"][0][0] == area["coordinates"][0][-1]  # closed ring
    assert len(area["coordinates"][0]) == 4
    assert _geom("13.19 77.70\n13.20 77.71", "point")[0]["type"] == "MultiPoint"
    with pytest.raises(AnalysisError):
        _geom("13.19 77.70", "line")
    with pytest.raises(AnalysisError):
        _geom("13.19 77.70\n13.20 77.71", "polygon")


def test_heights_are_dropped() -> None:
    geometry, codes = _geom('{"type": "Point", "coordinates": [77.5, 12.9, 920]}')
    assert geometry["coordinates"] == [77.5, 12.9]
    assert "z_dropped" in codes


def test_self_intersecting_polygon_is_repaired() -> None:
    geometry, codes = _geom("POLYGON ((0 0, 1 1, 1 0, 0 1, 0 0))")  # bow tie
    assert "geometry_repaired" in codes
    assert shape(geometry).is_valid
    assert shape(geometry).area == pytest.approx(0.5)


@pytest.mark.parametrize(
    "text",
    ['{"type": "Point", "coordinates": [77.5', "POLYGON ((0 0, 1 1, 2 2, 0 0))", "POINT EMPTY", "LINESTRING (0 0", "   "],
)
def test_invalid_geometry_text(text: str) -> None:
    with pytest.raises(AnalysisError) as err:
        _geom(text)
    assert err.value.code == "invalid_geometry"


def test_json_nested_too_deeply_is_refused_cleanly() -> None:
    # refused on depth before json.loads, so the answer is the same on every Python version
    for text in ("[" * 50_000, '{"type": "GeometryCollection", "geometries": [' * 1500):
        with pytest.raises(AnalysisError) as err:
            _geom(text)
        assert err.value.code == "too_complex"


@pytest.mark.parametrize(
    ("doc", "fragment"),
    [
        ({"type": "Point", "coordinates": ["a", 1]}, "a Point needs one [longitude, latitude]"),
        ({"type": "LineString", "coordinates": [[0, 0]]}, "at least two"),
        ({"type": "Polygon", "coordinates": [[[0, 0], [1, 0]]]}, "at least four"),
    ],
)
def test_malformed_geojson_says_what_the_type_needs(doc: dict, fragment: str) -> None:
    with pytest.raises(AnalysisError) as err:
        _geom(json.dumps(doc))
    assert err.value.code == "invalid_geometry"
    assert fragment in err.value.message
    # No Shapely / NumPy internals in the message.
    assert "np." not in err.value.message and "gufunc" not in err.value.message


def test_wkt_error_keeps_the_reason_without_the_exception_name() -> None:
    with pytest.raises(AnalysisError) as err:
        _geom("POLYGON ((0 0, 1 1")
    assert err.value.message.startswith("The WKT could not be read: ")
    assert "Exception" not in err.value.message


def test_text_size_cap() -> None:
    text = "POINT (77.5 12.9)" + " " * MAX_TEXT_BYTES
    with pytest.raises(AnalysisError) as err:
        _geom(text)
    assert err.value.code == "too_complex"


def test_vertex_cap() -> None:
    line = "LINESTRING (" + ",".join(f"{i % 90} 1" for i in range(MAX_VERTICES + 1)) + ")"
    assert len(line.encode()) < MAX_TEXT_BYTES  # the vertex cap, not the size cap, applies
    with pytest.raises(AnalysisError) as err:
        _geom(line)
    assert err.value.code == "too_complex"
    ok = "LINESTRING (" + ",".join(f"{i % 90} 1" for i in range(MAX_VERTICES)) + ")"
    assert shapely.get_num_coordinates(shape(_geom(ok)[0])) == MAX_VERTICES


@pytest.mark.parametrize(
    "text",
    [
        "1" + " " * 1500 + "!",  # decimal pair: overlapping \s* around the separator
        "1,1" + " " * 1500 + "!",  # decimal-comma pair: three overlapping whitespace runs
        "N" + " " * 1500 + "!",  # hemisphere prefix: \s* [-+]? \s* before the degrees
        "12 58" + " " * 1500 + "NN",
        "\n".join(["1" + " " * 200 + "!"] * 50),  # many such lines
    ],
)
def test_whitespace_runs_cannot_make_the_parser_backtrack(text: str) -> None:
    # Before the fix each of these took from seconds to hours (polynomial backtracking): one
    # request to POST /api/geometry/parse/ or GET /api/places/ could hold an API thread.
    started = time.perf_counter()
    with pytest.raises(AnalysisError) as err:
        _geom(text)
    assert time.perf_counter() - started < 1.0
    assert err.value.code == "invalid_coordinates"


def test_whitespace_runs_still_read_like_one_space() -> None:
    assert _one("12.9716 ,   77.5946")[:2] == pytest.approx((77.5946, 12.9716))
    assert _one("12,9716  \t  77,5946")[:2] == pytest.approx((77.5946, 12.9716))
    assert _one("N   12°  58′   17.8″    E   77°  35′  40.6″")[:2] == pytest.approx((77.594611, 12.971611), abs=1e-6)


@pytest.mark.parametrize(
    "text",
    [
        "GEOMETRYCOLLECTION(" * 1000 + "POINT(0 0)" + ")" * 1000,
        "SRID=4326;" + "GEOMETRYCOLLECTION (" * 40 + "POINT (0 0)" + ")" * 40,
        '{"type":"GeometryCollection","geometries":[' * 900 + '{"type":"Point","coordinates":[1,2]}' + "]}" * 900,
    ],
    ids=["wkt-1000", "ewkt-40", "geojson-900"],
)
def test_deeply_nested_geometry_is_refused(text: str) -> None:
    # Before the fix: a RecursionError (500) when the result was written back, and deeper WKT
    # crashed the whole API process (next test).
    with pytest.raises(AnalysisError) as err:
        _geom(text)
    assert err.value.code == "too_complex"
    assert "nested" in err.value.message


def test_nesting_that_real_geometries_use_is_accepted() -> None:
    wkt = "GEOMETRYCOLLECTION (POINT (77.5 12.9), GEOMETRYCOLLECTION (MULTIPOLYGON (((77 12, 78 12, 78 13, 77 12)))))"
    assert _geom(wkt)[0]["type"] == "GeometryCollection"
    feature = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"a": {"b": [[1, 2], [3]]}},
                      "geometry": {"type": "MultiPolygon", "coordinates": [[[[77, 12], [78, 12], [78, 13], [77, 12]]]]}}],
    }
    assert _geom(json.dumps(feature))[0]["type"] in ("GeometryCollection", "MultiPolygon")


def test_wkt_nested_deep_enough_to_overflow_geos_never_reaches_it() -> None:
    # ~3,500 nested collections (70 kB, under the 100 kB cap) overflowed the C stack inside
    # shapely.from_wkt and killed the process. Run in a child process so a regression fails
    # this test instead of the test run.
    code = (
        "from airbrief_core.geo.parse import parse_geometry_text\n"
        "from airbrief_core.errors import AnalysisError\n"
        "text = 'GEOMETRYCOLLECTION(' * 4500 + 'POINT(0 0)' + ')' * 4500\n"
        "try:\n"
        "    parse_geometry_text(text)\n"
        "except AnalysisError as exc:\n"
        "    print(exc.code)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                          cwd=Path(__file__).resolve().parents[2])
    assert done.returncode == 0, done.stderr[-500:]
    assert done.stdout.strip() == "too_complex"


# VERIFY-03: geometry inside request JSON gets the same caps as pasted text.


def _nested_collection(depth: int) -> dict:
    geometry: dict = {"type": "Point", "coordinates": [77.5, 12.9]}
    for _ in range(depth):
        geometry = {"type": "GeometryCollection", "geometries": [geometry]}
    return geometry


def _line(vertices: int) -> dict:
    return {"type": "LineString", "coordinates": [[77.0 + i * 1e-5, 12.9] for i in range(vertices)]}


def test_json_limits_accept_real_parameters() -> None:
    params = {
        "geometry": {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"a": {"b": [1]}}, "geometry": _line(MAX_VERTICES)}]},
        "user_zones": [{"name": "z", "zone": "red", "geometry": _nested_collection(5)}],
        "report": {"metadata": {"gps_path": [[12.9, 77.5, 900.0]] * (MAX_VERTICES + 5)}},
        "tracks": [{"id": "a", "times": [0, 1], "coordinates": [[77.5, 12.9, 0.0]] * (MAX_VERTICES + 5)}],
    }
    check_json_limits(params)


def test_json_limits_refuse_deep_nesting() -> None:
    with pytest.raises(AnalysisError) as err:
        check_json_limits({"geometry": _nested_collection(40)})
    assert err.value.code == "too_complex"
    assert err.value.details["limit"] == MAX_NESTING
    # Not geometry at all: any part of the parameters counts.
    deep: list = []
    for _ in range(5000):
        deep = [deep]
    with pytest.raises(AnalysisError) as err:
        check_json_limits({"report": {"x": deep}})
    assert err.value.code == "too_complex"


def test_json_limits_count_vertices_per_geojson_object() -> None:
    with pytest.raises(AnalysisError) as err:
        check_json_limits({"geometry": _line(MAX_VERTICES + 1)})
    assert err.value.code == "too_complex"
    assert err.value.details["limit"] == MAX_VERTICES
    # Collections and features count everything under them.
    parts = {"type": "GeometryCollection", "geometries": [_line(MAX_VERTICES // 2 + 1), _line(MAX_VERTICES // 2)]}
    with pytest.raises(AnalysisError):
        check_json_limits({"geometry": {"type": "Feature", "geometry": parts}})
    # Two separate geometries each under the cap are fine.
    check_json_limits({"geometry": _line(MAX_VERTICES), "training_box": _line(MAX_VERTICES)})


def test_json_limits_ignore_odd_type_values() -> None:
    check_json_limits({"geometry": {"type": ["Point"], "coordinates": [1, 2]}, "x": {"type": None}})
