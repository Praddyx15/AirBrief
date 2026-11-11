"""KML reader (lxml, hardened, namespace-agnostic).

* The parser never resolves entities, never loads a DTD and never touches the network;
  documents that *declare* entities are rejected outright (billion-laughs / XXE).
* Elements are matched by local name, so KML 2.0/2.1/2.2, ``gx:`` extensions and
  namespace-less files all work.
* Layers follow the container hierarchy: a placemark inside Folder ``Sub`` of Folder
  ``Folder`` of Document ``Doc`` lands in layer ``"Doc / Folder / Sub"``.
* ``ExtendedData`` ``Data``/``value`` and ``SchemaData``/``SimpleData`` become properties.
* ``altitudeMode`` / ``gx:altitudeMode`` are kept (KML default: ``clampToGround``).
* ``MultiGeometry`` → the matching Multi* type when homogeneous, otherwise a
  GeometryCollection. ``gx:Track`` → LineString Z with ``times`` in seconds from the first
  ``<when>``; ``gx:MultiTrack`` concatenates its tracks. ``Model`` is reported unsupported.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from lxml import etree
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from ..errors import IngestError
from ..geo.crs import WGS84
from ..models.common import Issue, Severity
from .reader import RawFeature, RawLayer, charge_vertices

_CONTAINERS = {"Document", "Folder"}
_GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry", "Model", "Track", "MultiTrack"}
_OVERLAYS = {"GroundOverlay", "ScreenOverlay", "PhotoOverlay"}
_ENTITY_MARKERS = (
    b"<!ENTITY",
    "<!ENTITY".encode("utf-16-le"),
    "<!ENTITY".encode("utf-16-be"),
)
DEFAULT_ALTITUDE_MODE = "clampToGround"


class _GeometryError(ValueError):
    """Raised for malformed coordinates inside one placemark."""


class _Unsupported(ValueError):
    """Raised for geometry kinds that cannot be represented (e.g. Model)."""


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=True,
        remove_pis=True,
        recover=False,
    )


def _local(el: Any) -> str:
    tag = el.tag
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag.split(":")[-1]


def _children(el: Any, name: str | None = None) -> list[Any]:
    return [c for c in el if isinstance(c.tag, str) and (name is None or _local(c) == name)]


def _child(el: Any, name: str) -> Any | None:
    for c in el:
        if isinstance(c.tag, str) and _local(c) == name:
            return c
    return None


def _text(el: Any | None) -> str | None:
    if el is None:
        return None
    text = "".join(el.itertext()).strip()
    return text or None


def parse_kml_coordinates(text: str | None) -> list[tuple[float, ...]]:
    """Parse a KML ``<coordinates>`` string (``lon,lat[,alt]`` tuples, whitespace separated).

    Tolerates spaces around commas (``"77.5, 12.9, 0"``) and trailing commas. All tuples are
    returned with the same dimension: missing altitudes become 0 when any tuple has one.
    """
    if text is None or not text.strip():
        return []
    # Whitespace around commas removed with str methods: the regex ``\s*,\s*`` rescanned every
    # whitespace run from each of its positions (quadratic on a long run, ReDoS).
    normalised = ",".join(part.strip() for part in text.strip().split(","))
    coords: list[list[float]] = []
    for token in normalised.split():
        values = [v for v in token.split(",") if v != ""]
        if len(values) < 2:
            raise _GeometryError(f"coordinate '{token}' needs at least longitude and latitude")
        try:
            numbers = [float(v) for v in values[:3]]
        except ValueError as exc:
            raise _GeometryError(f"coordinate '{token}' is not numeric") from exc
        if any(n != n or n in (float("inf"), float("-inf")) for n in numbers):
            raise _GeometryError(f"coordinate '{token}' is not finite")
        coords.append(numbers)
    has_z = any(len(c) == 3 for c in coords)
    if has_z:
        return [(c[0], c[1], c[2] if len(c) == 3 else 0.0) for c in coords]
    return [(c[0], c[1]) for c in coords]


def _ring(el: Any | None, what: str) -> list[tuple[float, ...]]:
    if el is None:
        raise _GeometryError(f"{what} has no LinearRing")
    ring_el = el if _local(el) == "LinearRing" else _child(el, "LinearRing")
    if ring_el is None:
        raise _GeometryError(f"{what} has no LinearRing")
    coords = parse_kml_coordinates(_text(_child(ring_el, "coordinates")))
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
    if len(coords) < 4:
        raise _GeometryError(f"{what} ring needs at least 3 distinct positions")
    return coords


def _same_dims(rings: list[list[tuple[float, ...]]]) -> list[list[tuple[float, ...]]]:
    if any(len(r[0]) == 3 for r in rings) and not all(len(r[0]) == 3 for r in rings):
        return [[(c[0], c[1], c[2] if len(c) == 3 else 0.0) for c in r] for r in rings]
    return rings


def _polygon(el: Any) -> Polygon:
    outer = _child(el, "outerBoundaryIs")
    shell = _ring(outer, "Polygon outer boundary")
    holes: list[list[tuple[float, ...]]] = []
    for inner in _children(el, "innerBoundaryIs"):
        rings = _children(inner, "LinearRing")
        for ring_el in rings:
            holes.append(_ring(ring_el, "Polygon inner boundary"))
    all_rings = _same_dims([shell, *holes])
    return Polygon(all_rings[0], all_rings[1:])


def _parse_when(text: str | None) -> datetime | None:
    if not text:
        return None
    value = text.strip()
    if value.endswith("Z") or value.endswith("z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _track_points(el: Any) -> tuple[list[tuple[float, float, float]], list[datetime | None]]:
    coords: list[tuple[float, float, float]] = []
    for c in _children(el, "coord"):
        values = (c.text or "").split()
        if len(values) < 2:
            raise _GeometryError("gx:coord needs at least longitude and latitude")
        try:
            lon, lat = float(values[0]), float(values[1])
            alt = float(values[2]) if len(values) > 2 else 0.0
        except ValueError as exc:
            raise _GeometryError(f"gx:coord '{c.text}' is not numeric") from exc
        coords.append((lon, lat, alt))
    whens = [_parse_when(_text(w)) for w in _children(el, "when")]
    return coords, whens


class _Track:
    """Result of reading gx:Track / gx:MultiTrack."""

    def __init__(self, geometry: BaseGeometry, times: list[float] | None, origin: str | None, note: str | None) -> None:
        self.geometry = geometry
        self.times = times
        self.origin = origin
        self.note = note


def _track(el: Any) -> _Track:
    if _local(el) == "MultiTrack":
        tracks = _children(el, "Track")
    else:
        tracks = [el]
    coords: list[tuple[float, float, float]] = []
    whens: list[datetime | None] = []
    times_valid = True
    for t in tracks:
        c, w = _track_points(t)
        if len(w) != len(c):
            times_valid = False
        coords.extend(c)
        whens.extend(w[: len(c)] + [None] * max(0, len(c) - len(w)))
    if not coords:
        raise _GeometryError("gx:Track has no gx:coord positions")
    geom: BaseGeometry = Point(coords[0]) if len(coords) == 1 else LineString(coords)
    note: str | None = None
    times: list[float] | None = None
    origin: str | None = None
    if times_valid and whens and all(w is not None for w in whens):
        stamps = [w for w in whens if w is not None]
        t0 = stamps[0]
        times = [round((w - t0).total_seconds(), 6) for w in stamps]
        origin = _iso(t0)
    else:
        note = "gx:Track <when> values are missing, unparseable or do not match the gx:coord count; times dropped"
    return _Track(geom, times, origin, note)


def _collect_geometries(el: Any, out: list[BaseGeometry], notes: list[str]) -> None:
    """Flatten a MultiGeometry's members into ``out``."""
    for child in _children(el):
        name = _local(child)
        if name == "MultiGeometry":
            _collect_geometries(child, out, notes)
        elif name == "Model":
            notes.append("a Model member of the MultiGeometry was skipped (3D models are not supported)")
        elif name in ("Track", "MultiTrack"):
            out.append(_track(child).geometry)
        elif name in _GEOMETRY_TAGS:
            out.append(_simple_geometry(child))


def _combine(geoms: list[BaseGeometry]) -> BaseGeometry | None:
    if not geoms:
        return None
    if len(geoms) == 1:
        return geoms[0]
    kinds = {g.geom_type for g in geoms}
    if kinds == {"Point"}:
        return MultiPoint(geoms)
    if kinds == {"LineString"}:
        return MultiLineString(geoms)
    if kinds == {"Polygon"}:
        return MultiPolygon(geoms)
    return GeometryCollection(geoms)


def _simple_geometry(el: Any) -> BaseGeometry:
    name = _local(el)
    if name == "Point":
        coords = parse_kml_coordinates(_text(_child(el, "coordinates")))
        if not coords:
            raise _GeometryError("Point has no coordinates")
        return Point(coords[0])
    if name in ("LineString", "LinearRing"):
        coords = parse_kml_coordinates(_text(_child(el, "coordinates")))
        if len(coords) < 2:
            raise _GeometryError(f"{name} needs at least 2 positions")
        return LineString(coords)
    if name == "Polygon":
        return _polygon(el)
    if name == "Model":
        raise _Unsupported("KML Model (3D COLLADA model) geometry is not supported")
    raise _GeometryError(f"unexpected geometry element {name}")


def _altitude_mode(geom_el: Any) -> str | None:
    for candidate in geom_el.iter():
        if isinstance(candidate.tag, str) and _local(candidate) == "altitudeMode":
            value = _text(candidate)
            if value:
                return value
    return None


def _properties(placemark: Any) -> dict[str, Any]:
    props: dict[str, Any] = {}
    description = _text(_child(placemark, "description"))
    if description is not None:
        props["description"] = description
    for simple in ("address", "phoneNumber", "styleUrl"):
        value = _text(_child(placemark, simple))
        if value is not None:
            props[simple] = value
    timestamp = _child(placemark, "TimeStamp")
    if timestamp is not None:
        when = _text(_child(timestamp, "when"))
        if when is not None:
            props["timestamp"] = when
    span = _child(placemark, "TimeSpan")
    if span is not None:
        for key in ("begin", "end"):
            value = _text(_child(span, key))
            if value is not None:
                props[key] = value
    extended = _child(placemark, "ExtendedData")
    if extended is not None:
        for data in _children(extended, "Data"):
            key = data.get("name")
            if not key:
                continue
            value_el = _child(data, "value")
            props[key] = _text(value_el) if value_el is not None else _text(data)
        for schema_data in _children(extended, "SchemaData"):
            for simple in _children(schema_data, "SimpleData"):
                key = simple.get("name")
                if key:
                    props[key] = _text(simple)
    return props


def _placemark(pm: Any) -> tuple[RawFeature, list[str]]:
    notes: list[str] = []
    name = _text(_child(pm, "name"))
    fid = pm.get("id")
    props = _properties(pm)
    geom_el = next((c for c in _children(pm) if _local(c) in _GEOMETRY_TAGS), None)
    feature = RawFeature(fid=fid, name=name, geometry=None, properties=props)
    if geom_el is None:
        return feature, notes
    feature.altitude_mode = _altitude_mode(geom_el) or DEFAULT_ALTITUDE_MODE
    kind = _local(geom_el)
    label = name or fid or "unnamed placemark"
    try:
        if kind in ("Track", "MultiTrack"):
            track = _track(geom_el)
            feature.geometry = track.geometry
            feature.times = track.times
            feature.time_origin_utc = track.origin
            if track.note:
                notes.append(f"Placemark '{label}': {track.note}.")
        elif kind == "MultiGeometry":
            parts: list[BaseGeometry] = []
            member_notes: list[str] = []
            _collect_geometries(geom_el, parts, member_notes)
            notes.extend(f"Placemark '{label}': {n}." for n in member_notes)
            feature.geometry = _combine(parts)
            if feature.geometry is None and member_notes:
                feature.unsupported_reason = "MultiGeometry contains only unsupported members (3D models)"
        else:
            feature.geometry = _simple_geometry(geom_el)
    except _Unsupported as exc:
        feature.geometry = None
        feature.unsupported_reason = str(exc)
    except (_GeometryError, ValueError) as exc:
        feature.geometry = None
        feature.unsupported_reason = f"Invalid {kind} geometry: {exc}"
        notes.append(f"Placemark '{label}': invalid {kind} geometry ({exc}).")
    return feature, notes


def _reject_entities(data: bytes) -> None:
    if any(marker in data for marker in _ENTITY_MARKERS):
        raise IngestError(
            "corrupt_file",
            "The KML declares XML entities, which are not allowed.",
            details={"reason": "entity_declaration"},
        )


def _parse_xml(data: bytes) -> Any:
    _reject_entities(data)
    payload = data
    stripped = data.lstrip(b" \t\r\n")
    if stripped.startswith(b"<"):
        payload = stripped
    try:
        root = etree.fromstring(payload, _parser())
    except etree.XMLSyntaxError as exc:
        raise IngestError("corrupt_file", "The KML is not well-formed XML.", details={"reason": str(exc)}) from exc
    except ValueError as exc:
        raise IngestError("corrupt_file", "The KML could not be decoded.", details={"reason": str(exc)}) from exc
    if root is None:
        raise IngestError("corrupt_file", "The KML document is empty.")
    return root


def parse_kml(data: bytes, layer_prefix: str | None = None) -> list[RawLayer]:
    """Parse KML bytes into layers (one per Document/Folder that directly holds placemarks).

    Args:
        data: the raw KML document.
        layer_prefix: optional first path segment of every layer name (e.g. the file stem when
            an archive holds several KML files).

    Raises:
        IngestError: ``corrupt_file`` for malformed XML, entity declarations or a root
            element that is not KML.
    """
    root = _parse_xml(data)
    root_name = _local(root)
    if root_name not in ("kml", "Document", "Folder", "Placemark"):
        raise IngestError(
            "corrupt_file",
            f"The XML root element <{root_name}> is not KML.",
            details={"root": root_name},
        )

    layers: dict[str, RawLayer] = {}
    loose_issues: list[Issue] = []
    base_path = [layer_prefix] if layer_prefix else []

    def layer_for(path: list[str]) -> RawLayer:
        name = " / ".join(path) if path else "KML"
        layer = layers.get(name)
        if layer is None:
            layer = RawLayer(name=name, crs=WGS84, crs_source="kml")
            layers[name] = layer
        return layer

    def visit(el: Any, path: list[str]) -> None:
        name = _local(el)
        if name in _CONTAINERS:
            label = _text(_child(el, "name")) or name
            path = [*path, label]
            for child in _children(el):
                visit(child, path)
        elif name == "kml":
            for child in _children(el):
                visit(child, path)
        elif name == "Placemark":
            feature, notes = _placemark(el)
            charge_vertices(feature.geometry)
            layer = layer_for(path)
            layer.features.append(feature)
            for note in notes:
                layer.issues.append(Issue(code="kml_geometry", message=note, severity=Severity.WARNING))
        elif name == "NetworkLink":
            label = _text(_child(el, "name")) or "unnamed"
            loose_issues.append(
                Issue(
                    code="kml_network_link",
                    message=f"NetworkLink '{label}' was not followed (remote content is never fetched).",
                    severity=Severity.INFO,
                )
            )
        elif name in _OVERLAYS:
            label = _text(_child(el, "name")) or "unnamed"
            loose_issues.append(
                Issue(
                    code="kml_overlay",
                    message=f"{name} '{label}' was not imported (image overlays are not supported).",
                    severity=Severity.INFO,
                )
            )

    visit(root, base_path)
    result = list(layers.values())
    if result and loose_issues:
        result[0].issues.extend(loose_issues)
    return result
