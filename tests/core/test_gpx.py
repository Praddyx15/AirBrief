"""Tests for the GPX reader (airbrief_core.io.gpx) and its dispatch through ingest."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from airbrief_core.errors import IngestError
from airbrief_core.io.gpx import parse_gpx
from airbrief_core.io.ingest import IngestLimits, detect_kind, stage_upload
from airbrief_core.io.reader import read_staged
from airbrief_core.processing import process_file
from airbrief_core.samples import (
    TRAINING_DURATION_S,
    TRAINING_HOME,
    TRAINING_HOME_ELE_M,
    build_sample,
)

GPX11 = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
  <metadata><name>Morning flight</name></metadata>
  <wpt lat="12.9000" lon="77.5000"><ele>910.5</ele><time>2026-01-01T06:00:00Z</time>
    <name>Pad A</name><desc>Take-off pad</desc><sym>Flag</sym><type>pad</type></wpt>
  <wpt lat="12.9010" lon="77.5010"><name>Mast</name></wpt>
  <rte><name>Planned loop</name><number>3</number>
    <rtept lat="12.9000" lon="77.5000"><ele>900</ele></rtept>
    <rtept lat="12.9010" lon="77.5000"><ele>910</ele></rtept>
    <rtept lat="12.9010" lon="77.5010"><ele>920</ele></rtept>
  </rte>
  <trk><name>Flight 1</name><type>survey</type>
    <trkseg>
      <trkpt lat="12.9000" lon="77.5000"><ele>900</ele><time>2026-01-01T06:00:00Z</time>
        <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>90</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions></trkpt>
      <trkpt lat="12.9001" lon="77.5000"><ele>905</ele><time>2026-01-01T06:00:01.5Z</time></trkpt>
      <trkpt lat="12.9002" lon="77.5000"><ele>910</ele><time>2026-01-01T06:00:03Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>
"""

GPX10 = b"""<?xml version="1.0"?>
<gpx version="1.0" creator="old logger" xmlns="http://www.topografix.com/GPX/1/0">
  <name>Legacy log</name>
  <time>2025-06-01T10:00:00Z</time>
  <trk><name>Old track</name><trkseg>
    <trkpt lat="13.0" lon="77.6"><ele>800</ele><time>2025-06-01T10:00:00Z</time><speed>4.2</speed></trkpt>
    <trkpt lat="13.0005" lon="77.6"><ele>820</ele><time>2025-06-01T10:00:10Z</time><speed>5.0</speed></trkpt>
  </trkseg></trk>
</gpx>
"""


def _layers_by_kind(layers: list) -> dict:  # type: ignore[type-arg]
    return {layer.name.rsplit(" / ", 1)[-1]: layer for layer in layers}


def test_gpx11_waypoints_routes_and_timed_tracks() -> None:
    layers = parse_gpx(GPX11)
    assert [layer.name for layer in layers] == [
        "Morning flight / Waypoints",
        "Morning flight / Routes",
        "Morning flight / Tracks",
    ]
    assert all(layer.crs is not None and layer.crs.to_epsg() == 4326 for layer in layers)
    assert all(layer.crs_source == "gpx" for layer in layers)
    by = _layers_by_kind(layers)

    pad, mast = by["Waypoints"].features
    assert pad.name == "Pad A" and pad.fid == "wpt1"
    assert pad.geometry is not None and pad.geometry.has_z
    assert tuple(pad.geometry.coords)[0] == (77.5, 12.9, 910.5)
    assert pad.altitude_mode == "absolute"
    assert pad.properties == {
        "gpx_element": "wpt",
        "ele": 910.5,
        "time": "2026-01-01T06:00:00Z",
        "desc": "Take-off pad",
        "sym": "Flag",
        "type": "pad",
    }
    assert pad.times is None  # a waypoint is not a track
    assert mast.geometry is not None and not mast.geometry.has_z
    assert mast.altitude_mode == "clampToGround"

    (route,) = by["Routes"].features
    assert route.name == "Planned loop"
    assert route.geometry is not None and route.geometry.geom_type == "LineString"
    assert [c[2] for c in route.geometry.coords] == [900.0, 910.0, 920.0]
    assert route.properties["number"] == "3" and route.properties["points"] == 3
    assert route.times is None

    (track,) = by["Tracks"].features
    assert track.name == "Flight 1" and track.fid == "trk1"
    assert track.geometry is not None and track.geometry.geom_type == "LineString"
    assert [c[2] for c in track.geometry.coords] == [900.0, 905.0, 910.0]
    assert track.times == [0.0, 1.5, 3.0]
    assert track.time_origin_utc == "2026-01-01T06:00:00Z"
    assert track.altitude_mode == "absolute"
    assert track.properties["duration_s"] == 3.0
    assert track.properties["segments"] == 1
    assert track.properties["type"] == "survey"


def test_gpx10_namespace_and_document_name() -> None:
    (layer,) = parse_gpx(GPX10)
    assert layer.name == "Legacy log / Tracks"
    (track,) = layer.features
    assert track.times == [0.0, 10.0]
    assert track.time_origin_utc == "2025-06-01T10:00:00Z"
    assert not layer.issues


def test_namespace_less_and_layer_prefix() -> None:
    data = b'<gpx><wpt lat="1" lon="2"><name>P</name></wpt></gpx>'
    (layer,) = parse_gpx(data, layer_prefix="pads")
    assert layer.name == "pads / Waypoints"
    assert layer.features[0].geometry is not None


def test_unknown_namespace_is_read_with_a_note() -> None:
    data = b'<gpx xmlns="urn:example:gpx2"><wpt lat="1" lon="2"/></gpx>'
    (layer,) = parse_gpx(data)
    assert layer.name == "GPX / Waypoints"
    assert any(i.code == "gpx_namespace" for i in layer.issues)


def test_segments_are_joined_with_continuous_times() -> None:
    data = b"""<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><name>T</name>
      <trkseg><trkpt lat="1" lon="1"><ele>10</ele><time>2026-01-01T00:00:00Z</time></trkpt>
              <trkpt lat="1.001" lon="1"><ele>11</ele><time>2026-01-01T00:00:05Z</time></trkpt></trkseg>
      <trkseg/>
      <trkseg><trkpt lat="1.002" lon="1"><ele>12</ele><time>2026-01-01T00:01:00Z</time></trkpt></trkseg>
    </trk></gpx>"""
    (layer,) = parse_gpx(data)
    (track,) = layer.features
    assert track.geometry is not None and len(track.geometry.coords) == 3
    assert track.times == [0.0, 5.0, 60.0]
    assert track.properties["segments"] == 2
    assert any("2 segments were joined" in i.message and i.severity == "info" for i in layer.issues)


def test_missing_elevations_are_interpolated() -> None:
    data = b"""<gpx><trk><trkseg>
      <trkpt lat="0" lon="0"><ele>100</ele></trkpt>
      <trkpt lat="0" lon="0.001"></trkpt>
      <trkpt lat="0" lon="0.002"></trkpt>
      <trkpt lat="0" lon="0.003"><ele>130</ele></trkpt>
    </trkseg></trk></gpx>"""
    (layer,) = parse_gpx(data)
    track = layer.features[0]
    assert track.geometry is not None
    assert [c[2] for c in track.geometry.coords] == pytest.approx([100.0, 110.0, 120.0, 130.0])
    assert track.times is None
    assert any("2 of 4 points had no <ele>" in i.message for i in layer.issues)


def test_track_without_any_elevation_is_2d() -> None:
    data = b'<gpx><trk><trkseg><trkpt lat="0" lon="0"/><trkpt lat="0" lon="0.001"/></trkseg></trk></gpx>'
    (layer,) = parse_gpx(data)
    track = layer.features[0]
    assert track.geometry is not None and not track.geometry.has_z
    assert track.altitude_mode == "clampToGround"


@pytest.mark.parametrize(
    ("times", "fragment"),
    [
        (("2026-01-01T00:00:10Z", "2026-01-01T00:00:05Z"), "times go backwards"),
        (("2026-01-01T00:00:00Z", None), "1 of 2 points have no valid <time>"),
        (("2026-01-01T00:00:00Z", "yesterday"), "1 of 2 points have no valid <time>"),
    ],
)
def test_untrustworthy_times_are_dropped(times: tuple[str | None, str | None], fragment: str) -> None:
    pts = "".join(
        f'<trkpt lat="0" lon="{i * 0.001}">' + (f"<time>{t}</time>" if t else "") + "</trkpt>" for i, t in enumerate(times)
    )
    (layer,) = parse_gpx(f"<gpx><trk><trkseg>{pts}</trkseg></trk></gpx>".encode())
    assert layer.features[0].times is None
    assert any(fragment in i.message and i.severity == "warning" for i in layer.issues)


def test_invalid_points_are_skipped_and_waypoints_kept_without_geometry() -> None:
    data = b"""<gpx>
      <wpt lat="95" lon="0"><name>Bad</name></wpt>
      <trk><trkseg><trkpt lat="0" lon="0"/><trkpt lat="x" lon="0"/><trkpt lat="0" lon="200"/>
        <trkpt lat="0" lon="0.001"/></trkseg></trk></gpx>"""
    waypoints, tracks = parse_gpx(data)
    bad = waypoints.features[0]
    assert bad.geometry is None and bad.unsupported_reason is not None
    track = tracks.features[0]
    assert track.geometry is not None and len(track.geometry.coords) == 2
    assert any("2 point(s) without a valid lat/lon were skipped" in i.message for i in tracks.issues)


def test_invalid_points_give_one_warning_per_track_not_per_segment() -> None:
    # Security review: a warning per segment let a 50 MB file of one-point segments store over a
    # million issues (about 160 MB of JSON in the dataset, sent with every GET of it).
    segments = 5000
    data = (
        b"<gpx><trk><name>T</name><trkseg><trkpt lat='0' lon='0'/><trkpt lat='0' lon='0.001'/></trkseg>"
        + b"<trkseg><trkpt lat='x' lon='0'/></trkseg>" * segments
        + b"</trk><rte><rtept lat='0' lon='0'/><rtept lat='y' lon='0'/><rtept lat='z' lon='0'/></rte></gpx>"
    )
    routes, tracks = parse_gpx(data)
    track_skips = [i for i in tracks.issues if "without a valid lat/lon" in i.message]
    assert len(track_skips) == 1
    assert f"{segments} point(s) without a valid lat/lon were skipped" in track_skips[0].message
    assert [i.message for i in routes.issues if "without a valid lat/lon" in i.message] == [
        "Route 1: 2 point(s) without a valid lat/lon were skipped."
    ]


def test_empty_track_has_no_geometry() -> None:
    (layer,) = parse_gpx(b"<gpx><trk><name>Nothing</name><trkseg/></trk></gpx>")
    assert layer.features[0].geometry is None
    assert any("has no valid points" in i.message for i in layer.issues)


@pytest.mark.parametrize(
    "data",
    [
        b'<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
        b"<gpx><wpt lat='0' lon='0'><name>&b;</name></wpt></gpx>",
        b'<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<gpx><wpt lat='0' lon='0'><name>&xxe;</name></wpt></gpx>",
        '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE gpx [<!ENTITY a "x">]><gpx/>'.encode("utf-16-le"),
    ],
    ids=["billion-laughs", "xxe", "utf16-entity"],
)
def test_entity_declarations_are_rejected(data: bytes) -> None:
    with pytest.raises(IngestError) as exc:
        parse_gpx(data)
    assert exc.value.code == "corrupt_file"
    assert exc.value.details.get("reason") == "entity_declaration"


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        (b"<gpx><wpt lat='0' lon='0'>", "not well-formed"),
        (b"<kml><Placemark/></kml>", "is not GPX"),
    ],
)
def test_malformed_or_foreign_xml_is_corrupt(data: bytes, fragment: str) -> None:
    with pytest.raises(IngestError) as exc:
        parse_gpx(data)
    assert exc.value.code == "corrupt_file"
    assert fragment in exc.value.message


def test_utf16_be_entity_declaration_is_rejected() -> None:
    data = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE gpx [<!ENTITY a "x">]><gpx/>'.encode("utf-16-be")
    with pytest.raises(IngestError) as exc:
        parse_gpx(b"\xfe\xff" + data)
    assert exc.value.details.get("reason") == "entity_declaration"


def test_external_dtd_is_never_loaded(tmp_path: Path) -> None:
    # A DTD on disk that would give every waypoint a position by attribute default. The parser
    # (no DTD loading, no network) must ignore it: the waypoint stays without a position.
    dtd = tmp_path / "evil.dtd"
    dtd.write_text('<!ATTLIST wpt lat CDATA "10" lon CDATA "20">', encoding="utf-8")
    data = (
        f'<?xml version="1.0"?><!DOCTYPE gpx SYSTEM "{dtd.as_uri()}">'
        '<gpx><wpt><name>No position</name></wpt></gpx>'
    ).encode()
    (layer,) = parse_gpx(data)
    (wpt,) = layer.features
    assert wpt.geometry is None and wpt.unsupported_reason is not None


def test_remote_dtd_is_not_fetched() -> None:
    # no_network: a remote DTD is neither fetched nor an error (the document is read as is).
    data = (
        b'<?xml version="1.0"?><!DOCTYPE gpx PUBLIC "-//X//DTD GPX//EN" "http://192.0.2.1/gpx.dtd">'
        b'<gpx><wpt lat="1" lon="2"><name>P</name></wpt></gpx>'
    )
    (layer,) = parse_gpx(data)
    assert layer.features[0].geometry is not None


@pytest.mark.parametrize(
    "data",
    [
        # huge_tree is off: libxml2 refuses nesting deeper than 256 levels…
        b"<gpx>" + b"<extensions>" * 300 + b"</extensions>" * 300 + b"</gpx>",
        # …and attribute values above 10 MB (text is bounded by the byte caps).
        b"<gpx><wpt lat='0' lon='0' x='" + b"a" * (11 * 1024 * 1024) + b"'/></gpx>",
    ],
    ids=["depth-bomb", "huge-attribute"],
)
def test_huge_tree_limits_apply(data: bytes) -> None:
    with pytest.raises(IngestError) as exc:
        parse_gpx(data)
    assert exc.value.code == "corrupt_file"


def test_gpx_uses_the_kml_parser_settings() -> None:
    from airbrief_core.io import gpx, kml

    assert gpx._parser is kml._parser


def test_size_cap() -> None:
    with pytest.raises(IngestError) as exc:
        parse_gpx(GPX11, max_bytes=100)
    assert exc.value.code == "file_too_large"


def test_vertex_budget_applies(tmp_path: Path) -> None:
    pts = "".join(f'<trkpt lat="0" lon="{i * 0.0001:.4f}"/>' for i in range(50))
    src = tmp_path / "long.gpx"
    src.write_text(f"<gpx><trk><trkseg>{pts}</trkseg></trk></gpx>", encoding="utf-8")
    staged = stage_upload(src, "long.gpx", tmp_path / "work", IngestLimits())
    with pytest.raises(IngestError) as exc:
        read_staged(staged, None, None, max_coordinates=40)
    assert exc.value.code == "too_complex"


def test_detect_kind_gpx() -> None:
    assert detect_kind("flight.GPX", b"<?xml") == "gpx"
    with pytest.raises(IngestError) as exc:
        detect_kind("flight.gpx", b"PK\x03\x04rest")
    assert exc.value.code == "corrupt_file"


def test_process_training_flight_sample(tmp_path: Path) -> None:
    path = tmp_path / "training_flight.gpx"
    path.write_bytes(build_sample("training_flight_gpx"))
    result = process_file(path, "training_flight.gpx", workdir=tmp_path / "work")
    assert result.file_type == "gpx"
    assert result.crs == "EPSG:4326" and result.crs_source == "gpx"
    names = {f.name: f for f in result.features}
    assert set(names) == {"Take-off", "Training box", "Training flight"}
    home = names["Take-off"].geometry_wgs84
    assert home is not None
    assert home["coordinates"] == pytest.approx([TRAINING_HOME[0], TRAINING_HOME[1], TRAINING_HOME_ELE_M])
    track = names["Training flight"]
    assert track.times is not None and len(track.times) == TRAINING_DURATION_S + 1
    assert track.times[-1] == float(TRAINING_DURATION_S)
    assert track.time_origin_utc == "2026-03-21T04:30:00Z"
    assert track.measurement.length_m == pytest.approx(1400.0, abs=0.5)
    box = names["Training box"].geometry_wgs84
    assert box is not None and box["coordinates"][0] == box["coordinates"][-1]


def test_zip_of_gpx_files_prefixes_layers(tmp_path: Path) -> None:
    path = tmp_path / "logs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("day1.gpx", GPX11)
        zf.writestr("day2.gpx", GPX10)
    result = process_file(path, "logs.zip", workdir=tmp_path / "work")
    assert result.file_type == "gpx"
    layer_names = [layer.name for layer in result.layers]
    assert "day1 / Tracks" in layer_names and "day2 / Tracks" in layer_names


def test_broken_gpx_next_to_a_good_one_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "logs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("good.gpx", GPX10)
        zf.writestr("bad.gpx", b"<gpx><trk>")
    result = process_file(path, "logs.zip", workdir=tmp_path / "work")
    assert [f.name for f in result.features] == ["Old track"]
    assert any(i.code == "corrupt_file" and "'bad.gpx' skipped" in i.message for i in result.issues)


def test_gpx_without_content_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.gpx"
    path.write_bytes(b'<gpx version="1.1"><metadata><name>x</name></metadata></gpx>')
    with pytest.raises(IngestError) as exc:
        process_file(path, "empty.gpx", workdir=tmp_path / "work")
    assert exc.value.code == "no_supported_content"
