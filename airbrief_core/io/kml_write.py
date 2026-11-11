"""KML writer shared by the dataset and analysis exports (no web imports).

It writes evidence geometry only: placemarks with polygons, lines and points. Never tours,
``gx:Track`` elements, waypoints or anything a flight controller reads as a mission.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from typing import Any

__all__ = [
    "ALTITUDE_MODES",
    "KML_NS",
    "KmlDocument",
    "append_geometry",
    "has_z",
    "kml_value",
    "xml_text",
]

KML_NS = "http://www.opengis.net/kml/2.2"
ALTITUDE_MODES = frozenset({"clampToGround", "relativeToGround", "absolute"})

# C0 controls other than tab/LF/CR, lone surrogates and U+FFFE/U+FFFF. Legacy DBF text often
# has some, and ElementTree writes them as-is, giving a document no parser accepts.
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")

# KML colours are aabbggrr. Zone colours match the map tokens (--zone-green/yellow/red).
_STYLES: dict[str, tuple[str, str, float]] = {
    # id: (line colour, fill colour, line width)
    "evidence": ("ff2563eb", "332563eb", 2.0),
    "zone-green": ("ff5ec522", "405ec522", 1.5),
    "zone-yellow": ("ff08b3ea", "4008b3ea", 1.5),
    "zone-red": ("ff4444ef", "404444ef", 1.5),
    "conflict": ("ff2626dc", "402626dc", 2.5),
}


def _k(tag: str) -> str:
    return f"{{{KML_NS}}}{tag}"


def xml_text(text: str) -> str:
    """``text`` without characters XML 1.0 cannot hold."""
    return _XML_ILLEGAL.sub("", text)


def kml_value(value: Any) -> str:
    """A property value as ``ExtendedData`` text: booleans as ``true``/``false``, floats at full
    precision, lists and objects as JSON."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, dict | list):
        return xml_text(json.dumps(value, ensure_ascii=False))
    return xml_text(str(value))


def _fmt_number(value: float) -> str:
    text = f"{float(value):.8f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _coord_text(coord: list[float], keep_z: bool) -> str:
    return ",".join(_fmt_number(v) for v in coord[: 3 if keep_z else 2])


def _coords_text(coords: list[list[float]], keep_z: bool) -> str:
    return " ".join(_coord_text(c, keep_z) for c in coords)


def has_z(geometry: Mapping[str, Any]) -> bool:
    """True when any position of the GeoJSON ``geometry`` has a third value."""

    def walk(value: Any) -> bool:
        if isinstance(value, list) and value and isinstance(value[0], int | float):
            return len(value) >= 3
        if isinstance(value, list):
            return any(walk(v) for v in value)
        return False

    if geometry.get("type") == "GeometryCollection":
        return any(has_z(g) for g in geometry.get("geometries", []) if isinstance(g, dict))
    return walk(geometry.get("coordinates"))


def _altitude(parent: ET.Element, mode: str | None) -> None:
    if mode:
        ET.SubElement(parent, _k("altitudeMode")).text = mode


def append_geometry(parent: ET.Element, geometry: Mapping[str, Any], altitude_mode: str | None = None) -> None:
    """Append ``geometry`` (GeoJSON) to ``parent`` as KML.

    With ``altitude_mode`` (one of :data:`ALTITUDE_MODES`) positions keep their Z and the mode is
    written on every geometry; without it positions are written 2D (clamped to the ground).
    Unknown or malformed geometry types are skipped.
    """
    mode = altitude_mode if altitude_mode in ALTITUDE_MODES else None
    keep_z = mode is not None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype == "Point" and isinstance(coords, list):
        el = ET.SubElement(parent, _k("Point"))
        _altitude(el, mode)
        ET.SubElement(el, _k("coordinates")).text = _coord_text(coords, keep_z)
    elif gtype == "LineString" and isinstance(coords, list):
        el = ET.SubElement(parent, _k("LineString"))
        _altitude(el, mode)
        ET.SubElement(el, _k("coordinates")).text = _coords_text(coords, keep_z)
    elif gtype == "Polygon" and isinstance(coords, list):
        el = ET.SubElement(parent, _k("Polygon"))
        _altitude(el, mode)
        for i, ring in enumerate(coords):
            boundary = ET.SubElement(el, _k("outerBoundaryIs" if i == 0 else "innerBoundaryIs"))
            linear = ET.SubElement(boundary, _k("LinearRing"))
            ET.SubElement(linear, _k("coordinates")).text = _coords_text(ring, keep_z)
    elif gtype in ("MultiPoint", "MultiLineString", "MultiPolygon") and isinstance(coords, list):
        multi = ET.SubElement(parent, _k("MultiGeometry"))
        single = str(gtype)[len("Multi"):]  # MultiPolygon -> Polygon etc
        for part in coords:
            append_geometry(multi, {"type": single, "coordinates": part}, mode)
    elif gtype == "GeometryCollection":
        multi = ET.SubElement(parent, _k("MultiGeometry"))
        for part in geometry.get("geometries", []):
            if isinstance(part, dict):
                append_geometry(multi, part, mode)


class KmlDocument:
    """A KML 2.2 document under construction.

    ``styles=True`` adds the shared styles (``#evidence``, ``#zone-green``, ``#zone-yellow``,
    ``#zone-red``, ``#conflict``) that :meth:`placemark` can reference with ``style``.
    """

    def __init__(self, name: str, description: str | None = None, *, styles: bool = False) -> None:
        ET.register_namespace("", KML_NS)
        self.root = ET.Element(_k("kml"))
        self.document = ET.SubElement(self.root, _k("Document"))
        ET.SubElement(self.document, _k("name")).text = xml_text(name)
        if description:
            ET.SubElement(self.document, _k("description")).text = xml_text(description)
        if styles:
            for style_id, (line, fill, width) in _STYLES.items():
                style = ET.SubElement(self.document, _k("Style"), {"id": style_id})
                line_style = ET.SubElement(style, _k("LineStyle"))
                ET.SubElement(line_style, _k("color")).text = line
                ET.SubElement(line_style, _k("width")).text = _fmt_number(width)
                poly_style = ET.SubElement(style, _k("PolyStyle"))
                ET.SubElement(poly_style, _k("color")).text = fill

    def folder(self, name: str, description: str | None = None, parent: ET.Element | None = None) -> ET.Element:
        """A new ``<Folder>`` under ``parent`` (default: the document)."""
        folder = ET.SubElement(self.document if parent is None else parent, _k("Folder"))
        ET.SubElement(folder, _k("name")).text = xml_text(name or "Layer")
        if description:
            ET.SubElement(folder, _k("description")).text = xml_text(description)
        return folder

    def placemark(
        self,
        parent: ET.Element,
        name: str,
        data: Iterable[tuple[str, Any]],
        geometry: Mapping[str, Any] | None,
        *,
        altitude_mode: str | None = None,
        description: str | None = None,
        style: str | None = None,
    ) -> ET.Element:
        """Append a placemark: its name, ``ExtendedData`` (``None`` values are left out) and geometry."""
        placemark = ET.SubElement(parent, _k("Placemark"))
        ET.SubElement(placemark, _k("name")).text = xml_text(name)
        if description:
            ET.SubElement(placemark, _k("description")).text = xml_text(description)
        if style:
            ET.SubElement(placemark, _k("styleUrl")).text = f"#{style}"
        extended = ET.SubElement(placemark, _k("ExtendedData"))
        for key, value in data:
            if value is None:
                continue
            item = ET.SubElement(extended, _k("Data"), {"name": xml_text(str(key))})
            ET.SubElement(item, _k("value")).text = kml_value(value)
        if geometry:
            append_geometry(placemark, geometry, altitude_mode)
        return placemark

    def tobytes(self) -> bytes:
        """The document as UTF-8 bytes with an XML declaration."""
        return ET.tostring(self.root, encoding="utf-8", xml_declaration=True)
