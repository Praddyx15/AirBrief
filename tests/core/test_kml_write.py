"""KML writer (airbrief_core.io.kml_write): round trip through the hardened reader."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from airbrief_core.io.kml import parse_kml
from airbrief_core.io.kml_write import KmlDocument, append_geometry, has_z, kml_value, xml_text


def test_round_trip_folders_names_extended_data_and_geometry_types() -> None:
    doc = KmlDocument("Evidence", "Evidence, not authorisation.", styles=True)
    zones = doc.folder("Zones")
    doc.placemark(zones, "Yellow ring", [("zone", "yellow"), ("ceiling_agl_m", 60.0), ("permission", False), ("skip", None)],
                  {"type": "Polygon", "coordinates": [[[77.5, 12.9], [77.6, 12.9], [77.6, 13.0], [77.5, 12.9]]]}, style="zone-yellow")
    paths = doc.folder("Paths")
    doc.placemark(paths, "Route", [("tags", ["a", "b"])],
                  {"type": "MultiLineString", "coordinates": [[[77.5, 12.9, 30], [77.6, 13.0, 40]], [[77.7, 13.1], [77.8, 13.2]]]})
    doc.placemark(paths, "Pads", [], {"type": "MultiPoint", "coordinates": [[77.5, 12.9], [77.51, 12.91]]})
    doc.placemark(paths, "Mixed", [], {"type": "GeometryCollection", "geometries": [
        {"type": "Point", "coordinates": [77.5, 12.9]}, {"type": "LineString", "coordinates": [[77.5, 12.9], [77.6, 13.0]]}]})
    layers = {layer.name: layer for layer in parse_kml(doc.tobytes())}
    assert set(layers) == {"Evidence / Zones", "Evidence / Paths"}
    ring = layers["Evidence / Zones"].features[0]
    assert ring.name == "Yellow ring" and ring.geometry is not None and ring.geometry.geom_type == "Polygon"
    assert ring.properties["zone"] == "yellow" and ring.properties["ceiling_agl_m"] == "60.0"
    assert ring.properties["permission"] == "false" and "skip" not in ring.properties
    assert ring.properties["styleUrl"] == "#zone-yellow"
    route, pads, mixed = layers["Evidence / Paths"].features
    assert route.geometry is not None and route.geometry.geom_type == "MultiLineString"
    assert not route.geometry.has_z  # without an altitude mode the evidence is written 2D
    assert route.properties["tags"] == '["a", "b"]'
    assert pads.geometry is not None and pads.geometry.geom_type == "MultiPoint"
    assert mixed.geometry is not None and mixed.geometry.geom_type == "GeometryCollection"


def test_altitude_mode_keeps_z() -> None:
    root = ET.Element("Placemark")
    append_geometry(root, {"type": "LineString", "coordinates": [[77.5, 12.9, 30.25], [77.6, 13.0, 40]]}, "absolute")
    text = ET.tostring(root, encoding="unicode")
    assert "<altitudeMode>absolute</altitudeMode>" in text and "77.5,12.9,30.25 77.6,13,40" in text
    root = ET.Element("Placemark")
    append_geometry(root, {"type": "Point", "coordinates": [77.5, 12.9, 30.0]}, "bogusMode")
    assert "altitudeMode" not in ET.tostring(root, encoding="unicode")  # unknown modes write 2D
    assert has_z({"type": "Point", "coordinates": [1, 2, 3]}) and not has_z({"type": "Point", "coordinates": [1, 2]})


def test_markup_in_names_and_values_is_escaped_text() -> None:
    nasty = '<Placemark><name>x</name></Placemark> & "q" \'a\' ]]> <![CDATA[y]]>'
    doc = KmlDocument(nasty, nasty)
    doc.placemark(doc.folder(nasty), nasty, [(nasty, nasty)], {"type": "Point", "coordinates": [77.5, 12.9]})
    raw = doc.tobytes()
    assert b"<Placemark><name>x</name>" not in raw and b"<![CDATA[" not in raw
    (layer,) = parse_kml(raw)
    (feature,) = layer.features  # the text never became a second placemark
    assert layer.name == f"{nasty} / {nasty}" and feature.name == nasty
    assert feature.properties[nasty] == nasty


def test_xml_illegal_characters_are_dropped() -> None:
    assert xml_text("Plot\x01A\ttab\ufffe\uffff") == "PlotA\ttab"
    assert kml_value({"note": "a\x1fb"}) == '{"note": "a\\u001fb"}'  # JSON escapes controls itself
    doc = KmlDocument("Doc\x02 name")
    doc.placemark(doc.folder("F\x0b"), "P\x00", [("k\x01", "v\x1f")], {"type": "Point", "coordinates": [77.5, 12.9]})
    (layer,) = parse_kml(doc.tobytes())  # raises if the document is not well-formed
    assert layer.name == "Doc name / F" and layer.features[0].properties == {"k": "v"}
