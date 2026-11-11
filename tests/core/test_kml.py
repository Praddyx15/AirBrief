"""Tests for the hardened, namespace-agnostic KML parser."""

from __future__ import annotations

import pytest

from airbrief_core.errors import IngestError
from airbrief_core.io.kml import parse_kml, parse_kml_coordinates
from airbrief_core.io.reader import RawFeature, RawLayer

NS = 'xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2"'


def kml(body: str) -> bytes:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<kml {NS}>{body}</kml>'.encode()


def by_name(layers: list[RawLayer], name: str) -> RawFeature:
    for layer in layers:
        for f in layer.features:
            if f.name == name:
                return f
    raise AssertionError(f"feature {name!r} not found")


def test_coordinates_tolerate_spaces_after_commas() -> None:
    coords = parse_kml_coordinates("\n  77.5, 12.9, 100   77.6,13.0,110\n 77.7 ,13.1 , 120, ")
    assert coords == [(77.5, 12.9, 100.0), (77.6, 13.0, 110.0), (77.7, 13.1, 120.0)]


def test_coordinates_mixed_dimensions_pad_z() -> None:
    assert parse_kml_coordinates("1,2 3,4,5") == [(1.0, 2.0, 0.0), (3.0, 4.0, 5.0)]


def test_folder_nesting_layer_names() -> None:
    data = kml(
        """
        <Document><name>Doc</name>
          <Placemark><name>top</name><Point><coordinates>77.5,12.9</coordinates></Point></Placemark>
          <Folder><name>Folder</name>
            <Placemark><name>mid</name><Point><coordinates>77.5,12.9</coordinates></Point></Placemark>
            <Folder><name>Sub</name>
              <Placemark><name>deep</name><Point><coordinates>77.5,12.9</coordinates></Point></Placemark>
            </Folder>
          </Folder>
        </Document>
        """
    )
    layers = parse_kml(data)
    names = {layer.name: [f.name for f in layer.features] for layer in layers}
    assert names == {"Doc": ["top"], "Doc / Folder": ["mid"], "Doc / Folder / Sub": ["deep"]}
    assert all(layer.crs is not None and layer.crs.to_epsg() == 4326 for layer in layers)
    assert all(layer.crs_source == "kml" for layer in layers)


def test_layer_prefix() -> None:
    data = kml("<Document><name>D</name><Placemark><Point><coordinates>1,2</coordinates></Point></Placemark></Document>")
    assert [layer.name for layer in parse_kml(data, "routes")] == ["routes / D"]


def test_namespace_less_and_bare_placemark() -> None:
    data = b"<kml><Placemark><name>p</name><Point><coordinates>1,2,3</coordinates></Point></Placemark></kml>"
    layers = parse_kml(data)
    assert layers[0].name == "KML"
    geom = layers[0].features[0].geometry
    assert geom is not None and tuple(geom.coords[0]) == (1.0, 2.0, 3.0)


def test_extended_data_and_schema_data() -> None:
    data = kml(
        """
        <Document><name>d</name>
        <Schema name="s" id="s"><SimpleField name="height" type="double"/></Schema>
        <Placemark id="pm1"><name>tower</name><description>A mast</description>
          <ExtendedData>
            <Data name="owner"><displayName>Owner</displayName><value>ACME</value></Data>
            <Data name="empty"><value></value></Data>
            <SchemaData schemaUrl="#s"><SimpleData name="height">45.5</SimpleData></SchemaData>
          </ExtendedData>
          <Point><coordinates>77.5,12.9</coordinates></Point>
        </Placemark></Document>
        """
    )
    f = by_name(parse_kml(data), "tower")
    assert f.fid == "pm1"
    assert f.properties["owner"] == "ACME"
    assert f.properties["height"] == "45.5"
    assert f.properties["description"] == "A mast"
    assert f.properties["empty"] is None


def test_altitude_modes() -> None:
    data = kml(
        """
        <Document><name>d</name>
        <Placemark><name>abs</name><LineString><altitudeMode>absolute</altitudeMode>
          <coordinates>77.5,12.9,900 77.6,12.9,910</coordinates></LineString></Placemark>
        <Placemark><name>gx</name><LineString><gx:altitudeMode>relativeToSeaFloor</gx:altitudeMode>
          <coordinates>77.5,12.9,10 77.6,12.9,10</coordinates></LineString></Placemark>
        <Placemark><name>default</name><Point><coordinates>77.5,12.9</coordinates></Point></Placemark>
        <Placemark><name>nogeom</name></Placemark>
        </Document>
        """
    )
    layers = parse_kml(data)
    assert by_name(layers, "abs").altitude_mode == "absolute"
    assert by_name(layers, "gx").altitude_mode == "relativeToSeaFloor"
    assert by_name(layers, "default").altitude_mode == "clampToGround"
    nogeom = by_name(layers, "nogeom")
    assert nogeom.geometry is None and nogeom.altitude_mode is None and nogeom.unsupported_reason is None


def test_polygon_with_inner_boundaries() -> None:
    data = kml(
        """
        <Placemark><name>poly</name><Polygon>
          <outerBoundaryIs><LinearRing><coordinates>0,0,5 0,1,5 1,1,5 1,0,5 0,0,5</coordinates></LinearRing></outerBoundaryIs>
          <innerBoundaryIs><LinearRing><coordinates>0.2,0.2,5 0.4,0.2,5 0.4,0.4,5 0.2,0.4,5</coordinates></LinearRing>
          <LinearRing><coordinates>0.6,0.6,5 0.8,0.6,5 0.8,0.8,5 0.6,0.8,5 0.6,0.6,5</coordinates></LinearRing></innerBoundaryIs>
        </Polygon></Placemark>
        """
    )
    geom = by_name(parse_kml(data), "poly").geometry
    assert geom is not None and geom.geom_type == "Polygon" and geom.has_z
    assert len(geom.interiors) == 2


def test_multigeometry_mixed_is_collection() -> None:
    data = kml(
        """
        <Placemark><name>mixed</name><MultiGeometry>
          <Point><coordinates>1,1</coordinates></Point>
          <LineString><coordinates>0,0 1,1</coordinates></LineString>
          <MultiGeometry><Polygon><outerBoundaryIs><LinearRing>
            <coordinates>0,0 0,1 1,1 0,0</coordinates></LinearRing></outerBoundaryIs></Polygon></MultiGeometry>
        </MultiGeometry></Placemark>
        <Placemark><name>points</name><MultiGeometry>
          <Point><coordinates>1,1</coordinates></Point><Point><coordinates>2,2</coordinates></Point>
        </MultiGeometry></Placemark>
        """
    )
    layers = parse_kml(data)
    mixed = by_name(layers, "mixed").geometry
    assert mixed is not None and mixed.geom_type == "GeometryCollection"
    assert sorted(g.geom_type for g in mixed.geoms) == ["LineString", "Point", "Polygon"]
    points = by_name(layers, "points").geometry
    assert points is not None and points.geom_type == "MultiPoint"


def test_gx_track_times() -> None:
    data = kml(
        """
        <Placemark><name>flight</name><gx:Track>
          <altitudeMode>absolute</altitudeMode>
          <when>2024-05-01T10:00:00Z</when>
          <when>2024-05-01T10:00:01.5Z</when>
          <when>2024-05-01T10:00:10Z</when>
          <gx:coord>77.5 12.9 900</gx:coord>
          <gx:coord>77.5001 12.9 905</gx:coord>
          <gx:coord>77.5002 12.9001 910</gx:coord>
        </gx:Track></Placemark>
        """
    )
    f = by_name(parse_kml(data), "flight")
    assert f.geometry is not None and f.geometry.geom_type == "LineString" and f.geometry.has_z
    assert [c[2] for c in f.geometry.coords] == [900.0, 905.0, 910.0]
    assert f.times == [0.0, 1.5, 10.0]
    assert f.time_origin_utc == "2024-05-01T10:00:00Z"
    assert f.altitude_mode == "absolute"


def test_gx_track_mismatched_when_drops_times() -> None:
    data = kml(
        """
        <Placemark><name>t</name><gx:Track>
          <when>2024-05-01T10:00:00Z</when>
          <gx:coord>77.5 12.9 900</gx:coord><gx:coord>77.6 12.9 900</gx:coord>
        </gx:Track></Placemark>
        """
    )
    layers = parse_kml(data)
    f = by_name(layers, "t")
    assert f.geometry is not None and f.times is None
    assert any(i.code == "kml_geometry" for i in layers[0].issues)


def test_model_is_unsupported() -> None:
    data = kml(
        """
        <Placemark><name>building</name><Model><Location><longitude>77.5</longitude>
        <latitude>12.9</latitude></Location><Link><href>model.dae</href></Link></Model></Placemark>
        """
    )
    f = by_name(parse_kml(data), "building")
    assert f.geometry is None
    assert f.unsupported_reason is not None and "Model" in f.unsupported_reason


def test_invalid_coordinates_mark_feature_not_file() -> None:
    data = kml(
        """
        <Document><name>d</name>
        <Placemark><name>bad</name><LineString><coordinates>abc,def 1,2</coordinates></LineString></Placemark>
        <Placemark><name>good</name><Point><coordinates>1,2</coordinates></Point></Placemark>
        </Document>
        """
    )
    layers = parse_kml(data)
    assert by_name(layers, "bad").geometry is None
    assert by_name(layers, "bad").unsupported_reason is not None
    assert by_name(layers, "good").geometry is not None


def test_network_link_not_followed() -> None:
    data = kml(
        """
        <Document><name>d</name>
        <NetworkLink><name>remote</name><Link><href>http://example.com/x.kml</href></Link></NetworkLink>
        <Placemark><name>p</name><Point><coordinates>1,2</coordinates></Point></Placemark>
        </Document>
        """
    )
    layers = parse_kml(data)
    assert any(i.code == "kml_network_link" for i in layers[0].issues)


@pytest.mark.parametrize(
    "data",
    [
        b"<kml><Placemark><name>unclosed</Placemark></kml>",
        b"not xml at all",
        b"<?xml version='1.0'?><kml><Placemark>",
    ],
)
def test_malformed_xml_is_corrupt(data: bytes) -> None:
    with pytest.raises(IngestError) as exc:
        parse_kml(data)
    assert exc.value.code == "corrupt_file"


def test_non_kml_root_is_corrupt() -> None:
    with pytest.raises(IngestError) as exc:
        parse_kml(b"<html><body/></html>")
    assert exc.value.code == "corrupt_file"


BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE kml [
  <!ENTITY lol "lol">
  <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
  <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
  <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">
  <!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">
  <!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">
  <!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">
]>
<kml><Placemark><name>&lol9;</name><Point><coordinates>1,2</coordinates></Point></Placemark></kml>
"""

XXE = b"""<?xml version="1.0"?>
<!DOCTYPE kml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<kml><Placemark><name>&xxe;</name><Point><coordinates>1,2</coordinates></Point></Placemark></kml>
"""


@pytest.mark.parametrize("data", [BILLION_LAUGHS, XXE, BILLION_LAUGHS.decode().encode("utf-16")])
def test_entity_attacks_rejected(data: bytes) -> None:
    with pytest.raises(IngestError) as exc:
        parse_kml(data)
    assert exc.value.code == "corrupt_file"


def test_external_dtd_not_loaded() -> None:
    data = b"""<?xml version="1.0"?>
<!DOCTYPE kml SYSTEM "http://127.0.0.1:9/never.dtd">
<kml><Placemark><name>safe</name><Point><coordinates>1,2</coordinates></Point></Placemark></kml>
"""
    layers = parse_kml(data)
    assert layers[0].features[0].name == "safe"


def test_long_whitespace_runs_in_coordinates_are_linear() -> None:
    # Security review: the "spaces around commas" pattern rescanned every run of whitespace
    # from each of its positions (quadratic: 8 s for 40,000 spaces; a <coordinates> text node
    # may hold 10 MB).
    import time

    text = "77.5,12.9" + " " * 400_000 + "77.6 , 12.8 ,\t5"
    started = time.perf_counter()
    assert parse_kml_coordinates(text) == [(77.5, 12.9, 0.0), (77.6, 12.8, 5.0)]
    assert time.perf_counter() - started < 1.0
    assert parse_kml_coordinates(" 1 , 2 ,, 3  4,5, ") == [(1.0, 2.0, 3.0), (4.0, 5.0, 0.0)]
