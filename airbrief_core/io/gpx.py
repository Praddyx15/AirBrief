"""GPX reader (1.0, 1.1 and namespace-less files), on the hardened lxml parser of :mod:`.kml`.

Documents that declare entities are rejected, and anything above :data:`MAX_GPX_BYTES` is
refused before parsing. A ``trk`` becomes one LineString, its segments joined in file order like
KML ``gx:MultiTrack``, and keeps its times only when every point has one and they never go
backwards. ``ele`` is metres above mean sea level, hence ``altitude_mode="absolute"``. The full
element mapping is in docs/features/tracks.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
from lxml import etree
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from ..errors import IngestError
from ..geo.crs import WGS84
from ..models.common import Issue, Severity
from .kml import _ENTITY_MARKERS, _child, _children, _local, _parser, _text
from .reader import RawFeature, RawLayer, charge_vertices

__all__ = ["GPX10_NS", "GPX11_NS", "MAX_GPX_BYTES", "parse_gpx"]

GPX10_NS = "http://www.topografix.com/GPX/1/0"
GPX11_NS = "http://www.topografix.com/GPX/1/1"
MAX_GPX_BYTES = 100 * 1024 * 1024
"""Largest GPX document parsed (a backstop: uploads are already capped by ``max_upload_bytes``)."""

_WAYPOINT_TEXT_FIELDS = ("desc", "cmt", "sym", "type", "src")
_LINE_TEXT_FIELDS = ("desc", "cmt", "src", "type", "number")


@dataclass
class _Fix:
    """One GPX point (``wpt``, ``rtept`` or ``trkpt``)."""

    lon: float
    lat: float
    ele: float | None
    time: datetime | None
    time_text: str | None


class _BadPoint(ValueError):
    """A point whose ``lat``/``lon`` is missing, not numeric or out of range."""


def _float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        value = float(text.strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _parse_time(text: str | None) -> datetime | None:
    """GPX ``<time>`` (``xsd:dateTime``, UTC by the GPX schema) as an aware UTC datetime."""
    if not text:
        return None
    value = text.strip()
    if value[-1:] in ("Z", "z"):
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


def _fix(el: Any) -> _Fix:
    lat = _float(el.get("lat"))
    lon = _float(el.get("lon"))
    if lat is None or lon is None:
        raise _BadPoint("lat/lon missing or not numeric")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise _BadPoint(f"lat {lat}, lon {lon} out of range")
    time_text = _text(_child(el, "time"))
    return _Fix(lon=lon, lat=lat, ele=_float(_text(_child(el, "ele"))), time=_parse_time(time_text), time_text=time_text)


def _warn(issues: list[Issue], message: str) -> None:
    issues.append(Issue(code="gpx_geometry", message=message, severity=Severity.WARNING))


def _info(issues: list[Issue], message: str) -> None:
    issues.append(Issue(code="gpx_note", message=message, severity=Severity.INFO))


def _fixes(elements: list[Any]) -> tuple[list[_Fix], int]:
    """Valid points of a route or track segment, and how many invalid ones were skipped."""
    out: list[_Fix] = []
    bad = 0
    for el in elements:
        try:
            out.append(_fix(el))
        except _BadPoint:
            bad += 1
    return out, bad


def _warn_skipped(issues: list[Issue], label: str, bad: int) -> None:
    # One note per route or track (never per segment: a file of one-point segments would
    # otherwise store a warning per segment).
    if bad:
        _warn(issues, f"{label}: {bad} point(s) without a valid lat/lon were skipped.")


def _heights(fixes: list[_Fix], label: str, issues: list[Issue]) -> list[float] | None:
    """Per-point heights: all ``ele``, missing ones interpolated along the line, or ``None`` (2D)."""
    known = [i for i, f in enumerate(fixes) if f.ele is not None]
    if not known:
        return None
    values = [f.ele for f in fixes]
    if len(known) == len(fixes):
        return [float(v) for v in values if v is not None]
    filled = np.interp(
        np.arange(len(fixes), dtype=np.float64),
        np.asarray(known, dtype=np.float64),
        np.asarray([values[i] for i in known], dtype=np.float64),
    )
    _info(
        issues,
        f"{label}: {len(fixes) - len(known)} of {len(fixes)} points had no <ele>; "
        "their heights were interpolated from the neighbouring points.",
    )
    return [float(v) for v in filled]


def _line(fixes: list[_Fix], heights: list[float] | None) -> BaseGeometry:
    if heights is None:
        coords: list[tuple[float, ...]] = [(f.lon, f.lat) for f in fixes]
    else:
        coords = [(f.lon, f.lat, z) for f, z in zip(fixes, heights, strict=True)]
    return Point(coords[0]) if len(coords) == 1 else LineString(coords)


def _text_properties(el: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for key in fields:
        value = _text(_child(el, key))
        if value is not None:
            props[key] = value
    return props


def _waypoint(el: Any, index: int, issues: list[Issue]) -> RawFeature:
    name = _text(_child(el, "name"))
    feature = RawFeature(fid=f"wpt{index + 1}", name=name, geometry=None, properties={"gpx_element": "wpt"})
    try:
        fix = _fix(el)
    except _BadPoint as exc:
        label = name or f"waypoint {index + 1}"
        feature.unsupported_reason = f"Invalid GPX waypoint: {exc}"
        _warn(issues, f"Waypoint '{label}': {exc}; it has no position.")
        return feature
    if fix.ele is not None:
        feature.properties["ele"] = fix.ele
    if fix.time_text is not None:
        feature.properties["time"] = fix.time_text
    feature.properties.update(_text_properties(el, _WAYPOINT_TEXT_FIELDS))
    feature.geometry = Point(fix.lon, fix.lat, fix.ele) if fix.ele is not None else Point(fix.lon, fix.lat)
    feature.altitude_mode = "absolute" if fix.ele is not None else "clampToGround"
    return feature


def _route(el: Any, index: int, issues: list[Issue]) -> RawFeature:
    name = _text(_child(el, "name"))
    label = f"Route '{name}'" if name else f"Route {index + 1}"
    props: dict[str, Any] = {"gpx_element": "rte", **_text_properties(el, _LINE_TEXT_FIELDS)}
    feature = RawFeature(fid=f"rte{index + 1}", name=name, geometry=None, properties=props)
    fixes, bad = _fixes(_children(el, "rtept"))
    _warn_skipped(issues, label, bad)
    if not fixes:
        _warn(issues, f"{label} has no valid points.")
        return feature
    heights = _heights(fixes, label, issues)
    feature.geometry = _line(fixes, heights)
    feature.altitude_mode = "absolute" if heights is not None else "clampToGround"
    props["points"] = len(fixes)
    return feature


def _track(el: Any, index: int, issues: list[Issue]) -> RawFeature:
    name = _text(_child(el, "name"))
    label = f"Track '{name}'" if name else f"Track {index + 1}"
    props: dict[str, Any] = {"gpx_element": "trk", **_text_properties(el, _LINE_TEXT_FIELDS)}
    feature = RawFeature(fid=f"trk{index + 1}", name=name, geometry=None, properties=props)
    fixes: list[_Fix] = []
    segments = 0
    bad = 0
    for seg in _children(el, "trkseg"):
        seg_fixes, seg_bad = _fixes(_children(seg, "trkpt"))
        bad += seg_bad
        if seg_fixes:
            segments += 1
            fixes.extend(seg_fixes)
    _warn_skipped(issues, label, bad)
    if not fixes:
        _warn(issues, f"{label} has no valid points.")
        return feature
    if segments > 1:
        _info(issues, f"{label}: {segments} segments were joined into one line (gaps are bridged by straight lines).")
    heights = _heights(fixes, label, issues)
    feature.geometry = _line(fixes, heights)
    feature.altitude_mode = "absolute" if heights is not None else "clampToGround"
    props["points"] = len(fixes)
    props["segments"] = segments

    stamps = [f.time for f in fixes]
    if all(s is not None for s in stamps):
        times = [s for s in stamps if s is not None]
        t0 = times[0]
        seconds = [round((t - t0).total_seconds(), 6) for t in times]
        if all(b >= a for a, b in zip(seconds, seconds[1:], strict=False)):
            feature.times = seconds
            feature.time_origin_utc = _iso(t0)
            props["start_time"] = _iso(t0)
            props["duration_s"] = seconds[-1]
        else:
            _warn(issues, f"{label}: point times go backwards, so the track is kept without times.")
    elif any(s is not None for s in stamps):
        missing = sum(1 for s in stamps if s is None)
        _warn(issues, f"{label}: {missing} of {len(stamps)} points have no valid <time>, so the track is kept without times.")
    return feature


def _reject_entities(data: bytes) -> None:
    if any(marker in data for marker in _ENTITY_MARKERS):
        raise IngestError(
            "corrupt_file",
            "The GPX declares XML entities, which are not allowed.",
            details={"reason": "entity_declaration"},
        )


def _parse_xml(data: bytes) -> Any:
    _reject_entities(data)
    stripped = data.lstrip(b" \t\r\n")
    payload = stripped if stripped.startswith(b"<") else data
    try:
        root = etree.fromstring(payload, _parser())
    except etree.XMLSyntaxError as exc:
        raise IngestError("corrupt_file", "The GPX is not well-formed XML.", details={"reason": str(exc)}) from exc
    except ValueError as exc:
        raise IngestError("corrupt_file", "The GPX could not be decoded.", details={"reason": str(exc)}) from exc
    if root is None:
        raise IngestError("corrupt_file", "The GPX document is empty.")
    return root


def _namespace(root: Any) -> str:
    tag = root.tag if isinstance(root.tag, str) else ""
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _document_name(root: Any) -> str | None:
    metadata = _child(root, "metadata")
    if metadata is not None:
        name = _text(_child(metadata, "name"))
        if name:
            return name
    return _text(_child(root, "name"))


def parse_gpx(data: bytes, layer_prefix: str | None = None, *, max_bytes: int = MAX_GPX_BYTES) -> list[RawLayer]:
    """Up to three layers (waypoints, routes, tracks) from GPX bytes.

    ``layer_prefix`` starts every layer name (the file stem when an archive holds several
    files); without it the document's name is used, else ``"GPX"``.

    Raises:
        IngestError: ``file_too_large`` above ``max_bytes``; ``corrupt_file`` for malformed XML,
            entity declarations or a root element that is not ``<gpx>``.
    """
    if len(data) > max_bytes:
        raise IngestError(
            "file_too_large",
            f"The GPX file is larger than {max_bytes // (1024 * 1024)} MB.",
            details={"size_bytes": len(data), "max_bytes": max_bytes},
        )
    root = _parse_xml(data)
    if _local(root) != "gpx":
        raise IngestError(
            "corrupt_file",
            f"The XML root element <{_local(root)}> is not GPX.",
            details={"root": _local(root)},
        )
    prefix = layer_prefix or _document_name(root) or "GPX"
    namespace = _namespace(root)
    loose: list[Issue] = []
    if namespace not in ("", GPX10_NS, GPX11_NS):
        loose.append(
            Issue(
                code="gpx_namespace",
                message=f"Unknown GPX namespace '{namespace[:120]}': the file was read like GPX 1.1.",
                severity=Severity.INFO,
            )
        )

    layers: list[RawLayer] = []
    for title, tag, build in (
        ("Waypoints", "wpt", _waypoint),
        ("Routes", "rte", _route),
        ("Tracks", "trk", _track),
    ):
        issues: list[Issue] = []
        features: list[RawFeature] = []
        for i, el in enumerate(_children(root, tag)):
            feature = build(el, i, issues)
            charge_vertices(feature.geometry)
            features.append(feature)
        if features:
            layers.append(
                RawLayer(name=f"{prefix} / {title}", crs=WGS84, crs_source="gpx", features=features, issues=issues)
            )
    if layers and loose:
        layers[0].issues.extend(loose)
    return layers
