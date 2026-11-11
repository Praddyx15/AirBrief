"""Locations typed or pasted by a user: coordinates, GeoJSON and WKT.

:func:`parse_coordinates` reads one point per line (or per ``;``-separated part):

* decimal degrees: ``12.9716, 77.5946`` · ``12.9716 77.5946`` · ``-33.8688 151.2093``;
* hemisphere letters before or after each value: ``12.9716 N, 77.5946 E`` · ``N12.9716 E77.5946``;
* degrees, minutes and seconds with ``° ′ ″`` (or ``' "``), or separated by spaces:
  ``12°58′17.8″N 77°35′40.6″E`` · ``12 58 17.8 N 77 35 40.6 E`` · ``N 12° 58.297′ E 77° 35.677′``;
* a decimal comma where it cannot be the separator: ``12,9716 77,5946`` · ``12,9716, 77,5946``
  (``12,5,77`` could be 12.5, 77 or 12, 5.77 and is refused);
* ``[lon, lat]`` in square brackets, read in GeoJSON order.

Which value is the latitude: hemisphere letters decide; without them a value beyond ±90° must be
the longitude; when both values are within ±90° the text is read as "latitude, longitude" (the
order people type and map apps copy) and an ``ambiguous_order`` issue says so. One order applies
to every point of the text, and points that contradict it are refused.

:func:`parse_geometry_text` reads GeoJSON (a geometry, a Feature or a FeatureCollection, or a
bare ``[lon, lat]`` position array), WKT / EWKT (``SRID=4326;`` only) or coordinates as above,
validates it (WGS84 ranges, finite values, polygon validity) and caps it at
:data:`MAX_TEXT_BYTES` of text, :data:`MAX_VERTICES` vertices and :data:`MAX_NESTING` levels of
nesting. Runs of whitespace in typed coordinates count as one space. :func:`check_json_limits`
applies the vertex and nesting caps to JSON that arrives already decoded (analysis parameters).

Errors are :class:`~airbrief_core.errors.AnalysisError` with code ``invalid_coordinates``
(unreadable or out-of-range coordinates), ``invalid_geometry`` (unreadable GeoJSON / WKT, empty or
degenerate shapes) or ``too_complex`` (over the caps).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import shapely
from shapely.errors import GEOSException, ShapelyError
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.geometry.base import BaseGeometry

from ..errors import AnalysisError
from ..models.common import Issue, Severity
from .transform import geojson_to_geometry, geometry_to_geojson
from .validity import drop_z, polygonal_part, repair

__all__ = [
    "MAX_NESTING",
    "MAX_TEXT_BYTES",
    "MAX_VERTICES",
    "GeometryShape",
    "TextFormat",
    "check_json_limits",
    "detect_text_format",
    "format_lat_lon",
    "parse_coordinates",
    "parse_geometry_text",
]

MAX_TEXT_BYTES = 100_000
"""Largest text (UTF-8 bytes) :func:`parse_geometry_text` reads. Bigger inputs belong in a file upload."""
MAX_VERTICES = 10_000
"""Most vertices (or typed points) one parsed geometry may have."""
MAX_NESTING = 32
"""Deepest nesting of WKT parentheses or JSON arrays/objects read. Real geometries need under 10;
thousands of nested GEOMETRYCOLLECTIONs overflow the C stack in GEOS's WKT reader (and Python's
recursion limit when the geometry is written back), so deeper text is refused before parsing."""

GeometryShape = Literal["auto", "point", "line", "polygon"]
"""What typed coordinates become: ``auto`` = 1 point → Point, 2 → LineString, ≥ 3 closed → Polygon,
≥ 3 open → LineString (with a ``read_as_line`` note)."""
TextFormat = Literal["geojson", "wkt", "coordinates"]

Axis = Literal["lat", "lon"]

_FORMAT_HINT = (
    "Use decimal degrees such as '12.9716, 77.5946', hemispheres such as '12.9716 N 77.5946 E', "
    "or degrees, minutes and seconds such as 12°58′17.8″N 77°35′40.6″E."
)

# Typographic variants people paste from documents and map apps, mapped to plain ASCII.
_NORMALISE = str.maketrans(
    {
        "−": "-",  # minus sign
        "′": "'",  # prime
        "’": "'",
        "‘": "'",
        "´": "'",
        "`": "'",
        "″": '"',  # double prime
        "“": '"',
        "”": '"',
        "º": "°",  # masculine ordinal used as a degree sign
        "˚": "°",  # ring above
        " ": " ",  # no-break space
        " ": " ",  # narrow no-break space (AirBrief joins numbers and units with it)
        " ": " ",  # thin space
        "\t": " ",
    }
)

# Digits are ASCII [0-9] (not \d, which also matches other scripts' digits), as in the browser's
# parser (frontend/lib/coords.ts), so both read exactly the same texts.
_NUM = r"[-+]?[0-9]+(?:\.[0-9]+)?"
_DOT_PAIR = re.compile(rf"\s*({_NUM})\s*(?:,|\s)\s*({_NUM})\s*")
"""Two decimal numbers (decimal point) separated by a comma or whitespace."""
_NUM_COMMA = r"[-+]?[0-9]+(?:,[0-9]+)?"
_COMMA_PAIR = re.compile(rf"\s*({_NUM_COMMA})\s*(?:,\s+|\s+|/)\s*({_NUM_COMMA})\s*")
"""Two numbers with decimal commas, separated by whitespace, ", " or "/"."""
_AMBIGUOUS_COMMAS = re.compile(r"\s*[-+]?[0-9]+,[0-9]+,[-+]?[0-9]+\s*")
"""Three whole numbers joined by bare commas ("12,5,77"): 12.5, 77 or 12, 5.77 — refused."""

_SIGN = r"(?P<sign>[-+])?\s*"
# Degrees, minutes[, seconds] (degrees are whole) — or decimal degrees. Each part ends with its
# symbol or whitespace, so "12.97" is never read as 1° 2.97′.
_BODY = (
    r"(?:"
    r"(?P<d>[0-9]{1,3})(?:\s*°\s*|\s+)"
    r"(?P<m>[0-9]{1,2}(?:[.,][0-9]+)?)(?:\s*'\s*|\s+|(?=\s*[NSEWnsew,/]|\s*$))"
    r"(?:(?P<s>[0-9]{1,2}(?:[.,][0-9]+)?)\s*(?:\"|'')?)?"
    r"|"
    r"(?P<dd>[0-9]{1,3}(?:[.,][0-9]+)?)\s*°?"
    r")"
)
_COMPONENT_SUFFIX = re.compile(_SIGN + _BODY + r"\s*(?P<h>[NSEWnsew])?(?![A-Za-z])")
_COMPONENT_PREFIX = re.compile(r"(?P<h>[NSEWnsew])?\s*" + _SIGN + _BODY)
_SEPARATOR = re.compile(r"[\s,/]*")
_WHITESPACE_RUN = re.compile(r"\s+")
"""Collapsed to one space before matching: the patterns above treat any run like one space, and
their overlapping ``\\s*`` would otherwise backtrack polynomially on long runs (ReDoS)."""
_LINE_BREAK = re.compile("\r\n|[\n\r\v\f\x85\u2028\u2029]")
"""Line breaks between points (the browser's parser splits on the same ones)."""
_WKT_START = re.compile(
    r"^\s*(?:SRID\s*=\s*(?P<srid>\d+)\s*;\s*)?"
    r"(?P<kind>POINT|LINESTRING|POLYGON|MULTIPOINT|MULTILINESTRING|MULTIPOLYGON|GEOMETRYCOLLECTION)\b",
    re.IGNORECASE,
)
_WGS84_CRS_NAMES = frozenset(
    {
        "urn:ogc:def:crs:ogc:1.3:crs84",
        "urn:ogc:def:crs:ogc::crs84",
        "urn:ogc:def:crs:epsg::4326",
        "epsg:4326",
        "crs84",
    }
)


@dataclass(frozen=True)
class _Component:
    """One coordinate value as typed: degrees (signed), and the axis its hemisphere letter names."""

    value: float
    axis: Axis | None


@dataclass(frozen=True)
class _RawPoint:
    line: int
    text: str
    first: _Component
    second: _Component
    geojson_order: bool


def _error(code: str, message: str, **details: Any) -> AnalysisError:
    return AnalysisError(code, message, details=details)


def _float(text: str) -> float:
    return float(text.replace(",", "."))


def _decimal_pair(text: str) -> tuple[float, float, bool] | None:
    """``(a, b, decimal_comma)`` for two plain numbers, or None."""
    m = _DOT_PAIR.fullmatch(text)
    if m:
        return float(m.group(1)), float(m.group(2)), False
    m = _COMMA_PAIR.fullmatch(text)
    if m and ("," in m.group(1) or "," in m.group(2)):
        return _float(m.group(1)), _float(m.group(2)), True
    return None


def _component(match: re.Match[str], line: int, text: str) -> _Component:
    sign = -1.0 if match.group("sign") == "-" else 1.0
    if match.group("dd") is not None:
        value = _float(match.group("dd"))
    else:
        degrees = float(match.group("d"))
        minutes = _float(match.group("m"))
        seconds = _float(match.group("s")) if match.group("s") is not None else 0.0
        if minutes >= 60 or seconds >= 60:
            raise _error(
                "invalid_coordinates",
                f"Line {line}: minutes and seconds must be below 60 in '{text}'. {_FORMAT_HINT}",
                line=line,
            )
        if match.group("s") is not None and not float(minutes).is_integer():
            raise _error(
                "invalid_coordinates",
                f"Line {line}: minutes with decimals cannot be followed by seconds in '{text}'.",
                line=line,
            )
        value = degrees + minutes / 60.0 + seconds / 3600.0
    hemisphere = (match.group("h") or "").upper()
    if hemisphere and match.group("sign"):
        raise _error(
            "invalid_coordinates",
            f"Line {line}: use either a minus sign or a hemisphere letter, not both, in '{text}'.",
            line=line,
        )
    axis: Axis | None = None
    if hemisphere:
        axis = "lat" if hemisphere in "NS" else "lon"
        if hemisphere in "SW":
            sign = -1.0
    return _Component(value=sign * value, axis=axis)


def _dms_components(text: str, line: int) -> list[_Component]:
    """Components of a point written with symbols, hemisphere letters or space-separated DMS."""
    prefix = text.lstrip()[:1].upper() in ("N", "S", "E", "W")
    pattern = _COMPONENT_PREFIX if prefix else _COMPONENT_SUFFIX
    components: list[_Component] = []
    position = 0
    for match in pattern.finditer(text):
        if match.end() == match.start():
            continue
        if not _SEPARATOR.fullmatch(text, position, match.start()):
            break
        components.append(_component(match, line, text))
        position = match.end()
    if not components or not _SEPARATOR.fullmatch(text, position, len(text)):
        raise _error("invalid_coordinates", f"Line {line}: could not read '{text}'. {_FORMAT_HINT}", line=line)
    return components


def _split_points(text: str) -> list[tuple[int, str]]:
    """Non-empty point texts with their 1-based line numbers (``;`` also separates points)."""
    out: list[tuple[int, str]] = []
    for number, line in enumerate(_LINE_BREAK.split(text), start=1):
        for part in line.split(";"):
            part = part.strip()
            if part:
                out.append((number, part))
    return out


def _raw_point(line: int, text: str) -> tuple[_RawPoint, bool]:
    """One point's two components (and whether a decimal comma was read)."""
    body = _WHITESPACE_RUN.sub(" ", text.translate(_NORMALISE).replace("''", '"')).strip()
    geojson_order = False
    if body.startswith("[") and body.endswith("]"):
        body, geojson_order = body[1:-1].strip(), True
    elif body.startswith("(") and body.endswith(")"):
        body = body[1:-1].strip()
    pair = None if re.search(r"[°'\"A-Za-z]", body) else _decimal_pair(body)
    if pair is not None:
        a, b, comma = pair
        return _RawPoint(line, text, _Component(a, None), _Component(b, None), geojson_order), comma
    letters = {ch.upper() for ch in re.findall(r"[A-Za-z]", body)}
    if letters - set("NSEW"):
        raise _error(
            "invalid_coordinates",
            f"Line {line}: unexpected text in '{text}'. {_FORMAT_HINT}",
            line=line,
        )
    if _AMBIGUOUS_COMMAS.fullmatch(body):
        raise _error(
            "invalid_coordinates",
            f"Line {line}: '{text}' can be read two ways: which comma is the decimal comma? "
            "Put a space after the comma between the two values (e.g. '12,5, 77') or use decimal points.",
            line=line,
        )
    components = _dms_components(body, line)
    if len(components) != 2:
        raise _error(
            "invalid_coordinates",
            f"Line {line}: a point needs exactly two values (latitude and longitude), found "
            f"{len(components)} in '{text}'. Separate points with new lines or ';'.",
            line=line,
            values=len(components),
        )
    comma = bool(re.search(r"[0-9],[0-9]", body))
    return _RawPoint(line, text, components[0], components[1], geojson_order), comma


def _order_vote(point: _RawPoint) -> Literal["latlon", "lonlat"] | None:
    """The order this point's values prove on their own (None when either order fits)."""
    a, b = abs(point.first.value), abs(point.second.value)
    if a > 90 and b > 90:
        raise _error(
            "invalid_coordinates",
            f"Line {point.line}: neither value of '{point.text}' can be a latitude (both are beyond ±90°).",
            line=point.line,
        )
    if a > 90:
        return "lonlat"
    if b > 90:
        return "latlon"
    return None


def _check_range(lon: float, lat: float, point: _RawPoint) -> None:
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise _error("invalid_coordinates", f"Line {point.line}: '{point.text}' is not a number.", line=point.line)
    if abs(lat) > 90:
        raise _error(
            "invalid_coordinates",
            f"Line {point.line}: latitude {lat:g}° is outside −90° to 90°.",
            line=point.line,
            latitude=lat,
        )
    if abs(lon) > 180:
        raise _error(
            "invalid_coordinates",
            f"Line {point.line}: longitude {lon:g}° is outside −180° to 180°.",
            line=point.line,
            longitude=lon,
        )


def parse_coordinates(text: str) -> tuple[list[tuple[float, float]], list[Issue]]:
    """Points typed as text, as ``[(lon, lat), ...]`` plus notes about how they were read.

    See the module docstring for the accepted forms and the order rule. Notes (issues):
    ``ambiguous_order`` (warning: read as latitude, longitude because both orders fit),
    ``lonlat_order`` (info: read as longitude, latitude), ``decimal_comma`` (info).

    Raises:
        AnalysisError: ``invalid_coordinates`` for unreadable, contradictory or out-of-range
            values (the message names the line); ``too_complex`` above :data:`MAX_VERTICES` points.
    """
    parts = _split_points(text)
    if not parts:
        raise _error("invalid_coordinates", f"Type at least one point. {_FORMAT_HINT}")
    if len(parts) > MAX_VERTICES:
        raise _error(
            "too_complex",
            f"{len(parts):,} points is more than the {MAX_VERTICES:,} that can be typed or pasted; upload a file instead.",
            points=len(parts),
            limit=MAX_VERTICES,
        )
    raw: list[_RawPoint] = []
    decimal_comma = False
    for line, part in parts:
        point, comma = _raw_point(line, part)
        raw.append(point)
        decimal_comma = decimal_comma or comma

    # One order for every point whose hemisphere letters do not decide it.
    votes: dict[str, _RawPoint] = {}
    undecided = [p for p in raw if p.first.axis is None and p.second.axis is None and not p.geojson_order]
    for p in undecided:
        vote = _order_vote(p)
        if vote is not None:
            votes.setdefault(vote, p)
    if len(votes) > 1:
        raise _error(
            "invalid_coordinates",
            f"Points mix the two orders: '{votes['latlon'].text}' (line {votes['latlon'].line}) is latitude, "
            f"longitude but '{votes['lonlat'].text}' (line {votes['lonlat'].line}) is longitude, latitude. "
            "Use one order for every point.",
            lines=sorted({votes["latlon"].line, votes["lonlat"].line}),
        )
    order: Literal["latlon", "lonlat"] = "lonlat" if "lonlat" in votes else "latlon"

    issues: list[Issue] = []
    points: list[tuple[float, float]] = []
    for p in raw:
        first, second = p.first, p.second
        if first.axis is not None and second.axis is not None:
            if first.axis == second.axis:
                word = "latitudes (N/S)" if first.axis == "lat" else "longitudes (E/W)"
                raise _error(
                    "invalid_coordinates",
                    f"Line {p.line}: '{p.text}' has two {word}; one value must be N/S and the other E/W.",
                    line=p.line,
                )
            lat, lon = (first.value, second.value) if first.axis == "lat" else (second.value, first.value)
        elif first.axis is not None or second.axis is not None:
            known, other = (first, second) if first.axis is not None else (second, first)
            lat, lon = (known.value, other.value) if known.axis == "lat" else (other.value, known.value)
        elif p.geojson_order or order == "lonlat":
            lon, lat = first.value, second.value
        else:
            lat, lon = first.value, second.value
        _check_range(lon, lat, p)
        points.append((lon, lat))

    if undecided and not votes:
        issues.append(
            Issue(
                code="ambiguous_order",
                message=(
                    "Both values fit either order, so they were read as latitude, longitude. "
                    "If you meant longitude, latitude, swap them or add N/S and E/W letters."
                ),
                severity=Severity.WARNING,
            )
        )
    elif votes.get("lonlat") is not None:
        issues.append(
            Issue(
                code="lonlat_order",
                message="Read as longitude, latitude: a first value beyond ±90° can only be a longitude.",
                severity=Severity.INFO,
            )
        )
    if any(p.geojson_order for p in raw):
        issues.append(
            Issue(
                code="lonlat_order",
                message="Values in [square brackets] were read in GeoJSON order: longitude, latitude.",
                severity=Severity.INFO,
            )
        )
    if decimal_comma:
        issues.append(
            Issue(code="decimal_comma", message="A comma between digits was read as a decimal point.", severity=Severity.INFO)
        )
    return points, issues


def format_lat_lon(lon: float, lat: float, decimals: int = 5) -> str:
    """``"12.97160° N, 77.59460° E"`` (latitude first, hemisphere letters, no signs)."""
    return (
        f"{abs(lat):.{decimals}f}° {'N' if lat >= 0 else 'S'}, "
        f"{abs(lon):.{decimals}f}° {'E' if lon >= 0 else 'W'}"
    )


def detect_text_format(text: str) -> TextFormat:
    """What ``text`` looks like: GeoJSON (``{`` or a JSON array), WKT, or typed coordinates."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return "geojson"
    if stripped.startswith("["):
        try:
            json.loads(stripped)
        except RecursionError:
            return "geojson"  # nested too deeply for JSON: refused as such by _from_json
        except ValueError:
            return "coordinates"  # e.g. several "[lon, lat]" lines
        return "geojson"
    if _WKT_START.match(stripped):
        return "wkt"
    return "coordinates"


def _shape_from_points(points: Sequence[tuple[float, float]], shape: GeometryShape) -> tuple[BaseGeometry, list[Issue]]:
    issues: list[Issue] = []
    distinct: list[tuple[float, float]] = []
    for p in points:
        if not distinct or distinct[-1] != p:
            distinct.append(p)
    closed = len(distinct) >= 4 and distinct[0] == distinct[-1]
    if shape == "auto":
        if len(distinct) == 1:
            shape = "point"
        elif closed:
            shape = "polygon"
        else:
            shape = "line"
            if len(distinct) >= 3:
                issues.append(
                    Issue(
                        code="read_as_line",
                        message=(
                            f"{len(distinct)} points were read as a line. Repeat the first point at the end, "
                            "or choose Area, to close them into a polygon."
                        ),
                        severity=Severity.INFO,
                    )
                )
    if shape == "point":
        if len(points) == 1:
            return Point(points[0]), issues
        return MultiPoint(list(points)), issues
    if shape == "line":
        if len(distinct) < 2:
            raise _error("invalid_geometry", "A line needs at least two different points.")
        return LineString(distinct), issues
    ring = distinct[:-1] if closed else distinct
    if len(set(ring)) < 3:
        raise _error("invalid_geometry", "An area needs at least three different points.")
    return Polygon(ring), issues


def _check_wgs84_crs(doc: dict[str, Any]) -> None:
    """Refuse GeoJSON whose legacy ``crs`` member names anything but WGS84 longitude/latitude."""
    crs = doc.get("crs")
    if not isinstance(crs, dict):
        return
    props = crs.get("properties")
    name = str(props.get("name") or "") if isinstance(props, dict) else ""
    if name and name.strip().lower() not in _WGS84_CRS_NAMES:
        raise _error(
            "invalid_geometry",
            f"This GeoJSON declares the coordinate system '{name}'. Paste WGS84 longitude/latitude, "
            "or upload it as a file so its coordinates can be converted.",
            crs=name,
        )


_GEOJSON_NEEDS: dict[str, str] = {
    "Point": "a Point needs one [longitude, latitude] position of numbers",
    "MultiPoint": "every position must be [longitude, latitude] numbers",
    "LineString": "a LineString needs at least two [longitude, latitude] positions of numbers",
    "MultiLineString": "each line needs at least two [longitude, latitude] positions of numbers",
    "Polygon": "each ring needs at least four [longitude, latitude] positions, the last equal to the first",
    "MultiPolygon": "each ring needs at least four [longitude, latitude] positions, the last equal to the first",
}
"""What each GeoJSON type needs, for a malformed pasted geometry."""


def _too_deep(depth: int) -> AnalysisError:
    return _error(
        "too_complex",
        f"The geometry is nested more than {MAX_NESTING} levels deep (found {depth}); real geometries need far fewer.",
        nesting=depth,
        limit=MAX_NESTING,
    )


def _check_wkt_nesting(text: str) -> None:
    """Refuse WKT whose parentheses nest deeper than :data:`MAX_NESTING` (before GEOS reads it)."""
    depth = deepest = 0
    for match in re.finditer(r"[()]", text):
        depth += 1 if match.group() == "(" else -1
        if depth > deepest:
            deepest = depth
            if deepest > MAX_NESTING:
                raise _too_deep(deepest)


def _check_json_nesting(doc: Any) -> None:
    """Refuse JSON whose arrays/objects nest deeper than :data:`MAX_NESTING` (iterative walk)."""
    stack: list[tuple[Any, int]] = [(doc, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict):
            children: Any = node.values()
        elif isinstance(node, list):
            children = node
        else:
            continue
        if depth > MAX_NESTING:
            raise _too_deep(depth)
        stack.extend((child, depth + 1) for child in children if isinstance(child, dict | list))


def _check_json_text_nesting(text: str) -> None:
    """The same cap on the text, before ``json.loads``: on Python 3.11 that already hits the
    recursion limit at a few hundred levels, and the error would read as invalid JSON."""
    depth = 0
    for match in re.finditer(r'"(?:[^"\\]|\\.)*"|[\[\]{}]', text):
        bracket = match.group()
        if bracket in "[{":
            depth += 1
            if depth > MAX_NESTING:
                raise _too_deep(depth)
        elif bracket in "]}":
            depth -= 1


_GEOJSON_OBJECTS = frozenset(
    {
        "Point",
        "MultiPoint",
        "LineString",
        "MultiLineString",
        "Polygon",
        "MultiPolygon",
        "GeometryCollection",
        "Feature",
        "FeatureCollection",
    }
)


def check_json_limits(doc: Any, max_vertices: int = MAX_VERTICES) -> None:
    """Hold decoded JSON from a request (analysis parameters, a fetched FlightMD report) to the
    caps of pasted geometry before shapely or a serializer recurses into it: at most
    :data:`MAX_NESTING` levels of nesting anywhere, and at most ``max_vertices`` positions in
    each GeoJSON object (a dict whose ``type`` is a GeoJSON type: the ``coordinates`` of all its
    geometries, features and collections together). The walk is iterative, so hostile depth
    cannot overflow the stack.

    Raises:
        AnalysisError: ``too_complex``.
    """
    counts: list[int] = []
    # node, depth, index of the outermost GeoJSON object above it, inside a "coordinates" value
    stack: list[tuple[Any, int, int | None, bool]] = [(doc, 1, None, False)]
    while stack:
        node, depth, root, coords = stack.pop()
        if depth > MAX_NESTING:
            raise _error(
                "too_complex",
                f"The JSON is nested more than {MAX_NESTING} levels deep; real inputs need far fewer.",
                nesting=depth,
                limit=MAX_NESTING,
            )
        if isinstance(node, dict):
            kind = node.get("type")
            if root is None and isinstance(kind, str) and kind in _GEOJSON_OBJECTS:
                root = len(counts)
                counts.append(0)
            stack.extend(
                (value, depth + 1, root, root is not None and key == "coordinates")
                for key, value in node.items()
                if isinstance(value, dict | list)
            )
            continue
        if coords and root is not None and node and isinstance(node[0], int | float):
            counts[root] += 1
            if counts[root] > max_vertices:
                raise _error(
                    "too_complex",
                    f"A geometry has more than {max_vertices:,} vertices, the most a request can carry. "
                    "Upload it as a file instead.",
                    limit=max_vertices,
                )
            continue
        stack.extend((child, depth + 1, root, coords) for child in node if isinstance(child, dict | list))


def _from_json(text: str, shape: GeometryShape) -> tuple[BaseGeometry, list[Issue]]:
    _check_json_text_nesting(text)
    try:
        doc = json.loads(text)
    except RecursionError as exc:
        raise _error("invalid_geometry", "The JSON is nested too deeply to be a geometry.") from exc
    except ValueError as exc:
        line = getattr(exc, "lineno", None)
        where = f" (line {line})" if line else ""
        reason = getattr(exc, "msg", None) or "unreadable"
        raise _error("invalid_geometry", f"The text is not valid JSON{where}: {reason}.") from exc
    _check_json_nesting(doc)
    if isinstance(doc, list):
        # A bare position array in GeoJSON order: [lon, lat] or [[lon, lat], ...].
        positions = [doc] if doc and all(isinstance(v, int | float) for v in doc) else doc
        points: list[tuple[float, float]] = []
        for item in positions:
            if (
                not isinstance(item, list)
                or len(item) < 2
                or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in item[:2])
            ):
                raise _error("invalid_geometry", "A JSON array must hold [longitude, latitude] positions.")
            points.append((float(item[0]), float(item[1])))
        if not points:
            raise _error("invalid_geometry", "The JSON array is empty.")
        if len(points) > MAX_VERTICES:
            raise _error("too_complex", f"More than {MAX_VERTICES:,} positions.", limit=MAX_VERTICES)
        geom, issues = _shape_from_points(points, shape)
        issues.insert(0, Issue(code="lonlat_order", message="Read in GeoJSON order: longitude, latitude.", severity=Severity.INFO))
        return geom, issues
    if not isinstance(doc, dict):
        raise _error("invalid_geometry", "Paste a GeoJSON geometry, Feature or FeatureCollection.")
    _check_wgs84_crs(doc)
    try:
        return geojson_to_geometry(doc), []
    except RecursionError as exc:
        raise _error("invalid_geometry", "The GeoJSON is nested too deeply to be a geometry.") from exc
    except AnalysisError as exc:
        if exc.__cause__ is None:
            raise
        # Shapely / NumPy could not build it: say what the type needs, not their internal text.
        m = re.match(r"Invalid (\w+) geometry", exc.message)
        kind = m.group(1) if m else "geometry"
        need = _GEOJSON_NEEDS.get(kind, "positions must be [longitude, latitude] numbers")
        raise _error("invalid_geometry", f"The GeoJSON {kind} is malformed: {need}.", geometry_type=kind) from exc


def _from_wkt(text: str) -> BaseGeometry:
    m = _WKT_START.match(text)
    body = text
    if m and m.group("srid") is not None:
        if m.group("srid") != "4326":
            raise _error(
                "invalid_geometry",
                f"Only SRID=4326 (WGS84 longitude/latitude) WKT can be pasted, not SRID={m.group('srid')}.",
                srid=m.group("srid"),
            )
        body = text[text.index(";") + 1 :]
    _check_wkt_nesting(body)
    try:
        return shapely.from_wkt(body.strip())
    except (GEOSException, ShapelyError, ValueError) as exc:
        # GEOS says e.g. "ParseException: Expected word but encountered end of stream": keep the reason.
        reason = re.sub(r"^\w*(?:Exception|Error):\s*", "", str(exc).strip()) or "unreadable"
        raise _error("invalid_geometry", f"The WKT could not be read: {reason}.") from exc


def parse_geometry_text(text: str, shape: GeometryShape = "auto") -> tuple[dict[str, Any], list[Issue]]:
    """A GeoJSON geometry (WGS84, 8 decimals) from pasted GeoJSON, WKT or coordinates, plus notes.

    ``shape`` says what typed coordinates (or a bare JSON position array) become; GeoJSON and
    WKT already say what they are. A FeatureCollection becomes a GeometryCollection of its
    features' geometries. Heights (Z) are dropped (``z_dropped``); invalid polygons are repaired
    with ``make_valid`` (``geometry_repaired``).

    Raises:
        AnalysisError: ``invalid_geometry``, ``invalid_coordinates`` or ``too_complex``.
    """
    size = len(text.encode("utf-8"))
    if size > MAX_TEXT_BYTES:
        raise _error(
            "too_complex",
            f"The pasted text is {size / 1000:,.0f} kB; at most {MAX_TEXT_BYTES // 1000} kB can be pasted. Upload it as a file instead.",
            bytes=size,
            limit_bytes=MAX_TEXT_BYTES,
        )
    if not text.strip():
        raise _error("invalid_geometry", "Paste GeoJSON, WKT or coordinates.")

    fmt = detect_text_format(text)
    issues: list[Issue] = []
    if fmt == "geojson":
        geom, issues = _from_json(text, shape)
    elif fmt == "wkt":
        geom = _from_wkt(text)
    else:
        points, issues = parse_coordinates(text)
        geom, more = _shape_from_points(points, shape)
        issues.extend(more)

    if geom is None or geom.is_empty:
        raise _error("invalid_geometry", "The geometry is empty.")
    vertices = int(shapely.get_num_coordinates(geom))
    if vertices > MAX_VERTICES:
        raise _error(
            "too_complex",
            f"The geometry has {vertices:,} vertices; at most {MAX_VERTICES:,} can be pasted. Upload it as a file instead.",
            vertices=vertices,
            limit=MAX_VERTICES,
        )
    coords = shapely.get_coordinates(geom)
    if not np.all(np.isfinite(coords)):
        raise _error("invalid_geometry", "The geometry contains coordinates that are not numbers.")
    if coords.size and (np.abs(coords[:, 0]).max() > 180 or np.abs(coords[:, 1]).max() > 90):
        raise _error(
            "invalid_coordinates",
            "Coordinates are outside longitude ±180° / latitude ±90°. They look projected (metres): "
            "paste WGS84 longitude/latitude, or upload the file with its coordinate system.",
        )
    if geom.has_z:
        geom = drop_z(geom)
        issues.append(
            Issue(code="z_dropped", message="Heights in the pasted geometry are ignored; set heights in the module.", severity=Severity.INFO)
        )
    fixed, repaired = repair(geom)
    if repaired:
        issues.append(
            Issue(
                code="geometry_repaired",
                message="The polygon crossed itself or had overlapping rings, so it was repaired (make_valid).",
                severity=Severity.WARNING,
            )
        )
    if fixed is None or fixed.is_empty or (geom.geom_type in ("Polygon", "MultiPolygon") and polygonal_part(fixed) is None):
        raise _error("invalid_geometry", "The area has no size: its points lie on one line.")
    return geometry_to_geojson(fixed, decimals=8), issues
