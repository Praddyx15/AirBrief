"""FlightMD report adapter: a FlightMD JSON report (or library object) → a lon/lat/alt track.

FlightMD (https://github.com/Praddyx15/FlightMD) analyses ``.ulg``/``.bin``/``.tlog`` logs and
emits a JSON report. AirBrief only needs the GPS path and a few descriptive fields, so this
adapter is deliberately tolerant:

* **Input shapes.** A plain ``dict``; a dict with the report nested under ``"report"`` (the
  FlightMD API envelope); a JSON ``str``/``bytes``; or a Pydantic object with ``model_dump``
  such as ``flightmd_core.FlightMDReport`` (installed with ``airbrief-core[flightmd]``). The
  ``flightmd_core`` package is imported lazily and is never required.
* **Schemas 1.0 to 1.7.** The GPS path is read from ``metadata.gps_path`` (1.2 and later) or a
  top-level ``gps_path`` (1.0 layout). Missing optional fields (``letter_grade``,
  ``location_name``, ``overall_score``…) simply stay ``None``.
* **1.6 / 1.7 fields.** ``metadata.armed_windows`` (``[start_s, end_s]`` pairs), the findings'
  log-relative ``t_start_s`` / ``t_end_s`` (or ``timestamp_*_ms`` / 1000), ``metadata.parse_warnings``,
  ``Finding.params`` (1.7; before that the names in ``param_changes``), ``metadata.log_sha256``,
  ``metadata.source_format``, ``metadata.airframe_name`` and ``metadata.planning_brief_sha256``,
  the hash of the AirBrief brief the flight was planned with. ``metadata.gps_path_t_s`` (one
  log-relative time per GPS point, same timebase as the armed windows) becomes the track's
  times, so the audit can place findings on the track; without it findings are listed by time
  only. A malformed optional field is dropped with a note; it never fails the audit.
* **Coordinate order.** ``gps_path`` entries are ``[lat, lon, alt_m]`` (alt is GPS altitude,
  metres AMSL); they are converted to GeoJSON order ``[lon, lat, alt]``. Entries given as
  objects (``{"lat", "lon"|"lng", "alt"|"altitude"}``) are accepted too. Points at exactly
  ``(0, 0)`` are GPS "no fix" placeholders and are dropped with a note.

Known upstream FlightMD quirks, tolerated here (fixes are offered upstream separately):

* ``schema_version`` is hard-coded to ``"1.2"`` by the report builder while the report model
  (and its payload) is ``"1.5"``. The declared value is therefore not trusted on its own: a
  report declaring 1.2 but carrying 1.5-only fields (``letter_grade``,
  ``metadata.gps_path_hdop``) is treated as 1.5 and a note says so. A genuine 1.2 report gives
  the note "FlightMD schema 1.2 detected".
* The FlightMD KML export writes GPS (AMSL) altitudes but declares
  ``<altitudeMode>relativeToGround</altitudeMode>``, so the track floats hundreds of metres
  too high in Google Earth. AirBrief never reads that KML for audits; it uses the JSON
  ``gps_path``, whose altitudes it treats as AMSL. (A FlightMD KML uploaded as an ordinary
  dataset keeps its declared mode; interpret its heights with care.)
* No home point or bounding box is stored: the home point is the first valid GPS fix.

Invalid input raises :class:`~airbrief_core.errors.AnalysisError` with code
``report_invalid`` (no ``gps_path``, fewer than two valid points, non-numeric or non-finite
values, latitude/longitude out of range).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..errors import AnalysisError
from ..models.analyses import AuditFinding, FindingSeverity, FlightInfo

__all__ = ["FlightTrack", "detect_schema_version", "parse_flightmd_report"]

_V15_MARKERS_REPORT = ("letter_grade",)
_V15_MARKERS_METADATA = ("gps_path_hdop",)
# 1.6+ dumps always carry these keys (possibly null or empty), so presence is enough
_V16_MARKERS_METADATA = ("armed_windows", "parse_warnings")
_V16_MARKERS_FINDING = ("t_start_s", "kind")
_V17_MARKERS_METADATA = ("planning_brief_sha256", "log_sha256", "source_format", "gps_path_t_s")
_V17_MARKERS_FINDING = ("params",)

MAX_FINDINGS = 200
MAX_PARSE_WARNINGS = 50
MAX_ARMED_WINDOWS = 1000
_SEVERITIES: dict[str, FindingSeverity] = {"critical": "critical", "warning": "warning", "info": "info", "good": "good"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class FlightTrack:
    """A flight to audit: a parsed FlightMD report, or a plain position track
    (:func:`airbrief_core.integrations.position_track.parse_position_track`).

    Attributes:
        points: ``[lon, lat, alt_m]`` per GPS fix, in flight order (FlightMD: alt is AMSL as
            logged; a position track: as recorded, read through ``altitude_mode``).
        schema_version: the effective FlightMD schema version (see the module docstring), or
            ``None`` when the report declares none and no marker identifies it (or for a track).
        notes: human-readable remarks about tolerated quirks (dropped points, schema detection).
        times: seconds per fix, never decreasing (position tracks with times; FlightMD 1.7 reports
            with ``gps_path_t_s``, whose times are log-relative like the findings').
        findings: FlightMD findings (time windows from schema 1.6), not yet placed.
        altitude_mode: the track's altitude mode (``absolute``, ``relativeToGround``…); ``None``
            for FlightMD, whose GPS altitudes are AMSL.
        has_heights: ``False`` when the track carries no altitudes at all (2D).
    """

    points: list[list[float]]
    info: FlightInfo
    schema_version: str | None
    notes: list[str] = field(default_factory=list)
    times: list[float] | None = None
    altitude_mode: str | None = None
    has_heights: bool = True
    findings: list[AuditFinding] = field(default_factory=list)


def _invalid(message: str, **details: Any) -> AnalysisError:
    return AnalysisError("report_invalid", message, details=details)


def _flightmd_core_types() -> tuple[type, ...]:
    """``flightmd_core`` report classes when the optional extra is installed, else ``()``."""
    try:
        import flightmd_core
    except ImportError:
        return ()
    found = getattr(flightmd_core, "FlightMDReport", None)
    if found is None:
        models = getattr(flightmd_core, "models", None)
        found = getattr(models, "FlightMDReport", None) if models is not None else None
    return (found,) if isinstance(found, type) else ()


def _dump_model(obj: Any) -> Any:
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:
            return dump()
    legacy = getattr(obj, "dict", None)
    if callable(legacy):
        return legacy()
    return obj


def _as_mapping(obj: Any) -> Mapping[str, Any]:
    """Normalise the accepted input shapes to the report mapping (un-nesting ``"report"``)."""
    if obj is None:
        raise _invalid("No FlightMD report was given")
    if isinstance(obj, bytes | bytearray):
        try:
            obj = bytes(obj).decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise _invalid("The FlightMD report is not UTF-8 text") from exc
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except json.JSONDecodeError as exc:
            raise _invalid(f"The FlightMD report is not valid JSON: {exc.msg}", line=exc.lineno) from exc
    core_types = _flightmd_core_types()
    if core_types and isinstance(obj, core_types):
        obj = _dump_model(obj)
    elif not isinstance(obj, Mapping) and (hasattr(obj, "model_dump") or hasattr(obj, "dict")):
        obj = _dump_model(obj)
    if not isinstance(obj, Mapping):
        raise _invalid("The FlightMD report must be a JSON object", type=type(obj).__name__)
    # API envelopes nest the report (possibly as a model object) under "report".
    for _ in range(3):
        if "gps_path" in obj or isinstance(obj.get("metadata"), Mapping):
            break
        inner = obj.get("report")
        if inner is None:
            break
        if not isinstance(inner, Mapping):
            inner = _dump_model(inner)
        if not isinstance(inner, Mapping):
            raise _invalid("'report' must be a JSON object", type=type(inner).__name__)
        obj = inner
    return obj


def detect_schema_version(report: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(effective_version, note)`` for a report mapping.

    The declared ``schema_version`` is corrected to ``"1.5"`` when 1.5-only fields are present
    (upstream hard-codes ``"1.2"``). ``note`` explains the decision, or is ``None``.
    """
    raw = report.get("schema_version")
    declared = None if raw is None else str(raw).strip() or None
    metadata = report.get("metadata")
    meta = metadata if isinstance(metadata, Mapping) else {}
    raw_findings = report.get("findings")
    findings = [f for f in raw_findings if isinstance(f, Mapping)] if isinstance(raw_findings, list) else []
    marked: str | None = None
    if any(k in meta for k in _V17_MARKERS_METADATA) or any(k in f for f in findings for k in _V17_MARKERS_FINDING):
        marked = "1.7"
    elif any(k in meta for k in _V16_MARKERS_METADATA) or any(k in f for f in findings for k in _V16_MARKERS_FINDING):
        marked = "1.6"
    elif any(report.get(k) is not None for k in _V15_MARKERS_REPORT) or any(
        meta.get(k) is not None for k in _V15_MARKERS_METADATA
    ):
        marked = "1.5"
    if marked is not None and (declared is None or _version_key(declared) < _version_key(marked)):
        shown = declared if declared is not None else "none"
        quirk = " (known upstream quirk: schema_version hard-coded to 1.2)" if declared in (None, "1.2") else ""
        return marked, (
            f"FlightMD report declares schema {shown} but carries {marked} fields; treated as schema {marked}{quirk}."
        )
    if declared is None:
        if "gps_path" in report and not meta:
            return "1.0", "FlightMD schema not declared; the 1.0 layout (top-level gps_path) was detected."
        return None, "FlightMD report does not declare a schema_version."
    if declared.startswith("1.2"):
        return declared, "FlightMD schema 1.2 detected (no letter grade or HDOP series)."
    if declared.startswith("1.0") or declared.startswith("1.1"):
        return declared, f"FlightMD schema {declared} detected (legacy layout)."
    if _version_key(declared)[0] > 1:
        return declared, f"FlightMD schema {declared} is newer than this reader (1.x); fields it added are not read."
    return declared, None


def _version_key(version: str) -> tuple[int, ...]:
    parts = []
    for piece in version.split(".")[:3]:
        digits = re.match(r"\d+", piece)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts)


def _number(value: Any, what: str, index: int) -> float:
    # The value itself is never echoed: a fetched report may come from an internal service.
    if isinstance(value, bool) or value is None:
        raise _invalid(f"gps_path point {index}: {what} is not a number", index=index)
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise _invalid(f"gps_path point {index}: {what} is not a number", index=index) from exc
    if not math.isfinite(out):
        raise _invalid(f"gps_path point {index}: {what} is not finite", index=index)
    return out


def _point(entry: Any, index: int) -> tuple[float, float, float]:
    """``(lat, lon, alt)`` from one ``gps_path`` entry (list or object)."""
    if isinstance(entry, Mapping):
        lat_raw = entry.get("lat", entry.get("latitude"))
        lon_raw = entry.get("lon", entry.get("lng", entry.get("longitude")))
        alt_raw = entry.get("alt", entry.get("altitude", entry.get("alt_m")))
    elif isinstance(entry, Sequence) and not isinstance(entry, str | bytes):
        if len(entry) < 3:
            raise _invalid(
                f"gps_path point {index} must be [lat, lon, alt]", index=index, length=len(entry)
            )
        lat_raw, lon_raw, alt_raw = entry[0], entry[1], entry[2]
    else:
        raise _invalid(f"gps_path point {index} must be [lat, lon, alt]", index=index, type=type(entry).__name__)
    lat = _number(lat_raw, "latitude", index)
    lon = _number(lon_raw, "longitude", index)
    alt = _number(alt_raw, "altitude", index)
    if not -90.0 <= lat <= 90.0:
        raise _invalid(f"gps_path point {index}: latitude {lat} is outside [-90, 90]", index=index, lat=lat)
    if not -180.0 <= lon <= 180.0:
        raise _invalid(f"gps_path point {index}: longitude {lon} is outside [-180, 180]", index=index, lon=lon)
    return lat, lon, alt


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_flightmd_report(obj: dict[str, Any] | object, *, flightmd_url: str | None = None) -> FlightTrack:
    """Parse a FlightMD report (any shape the module docstring lists) into a :class:`FlightTrack`.

    ``flightmd_url`` is kept on ``info.flightmd_url``. No usable GPS path raises
    ``AnalysisError("report_invalid")``.
    """
    report = _as_mapping(obj)
    metadata_raw = report.get("metadata")
    metadata: Mapping[str, Any] = metadata_raw if isinstance(metadata_raw, Mapping) else {}

    gps_path = metadata.get("gps_path")
    if gps_path is None:
        gps_path = report.get("gps_path")
    if gps_path is None:
        raise _invalid("The FlightMD report has no GPS path (metadata.gps_path)")
    if isinstance(gps_path, str | bytes) or not isinstance(gps_path, Sequence):
        raise _invalid("metadata.gps_path must be a list of [lat, lon, alt] points", type=type(gps_path).__name__)

    notes: list[str] = []
    points: list[list[float]] = []
    kept: list[int] = []
    dropped = 0
    for index, entry in enumerate(gps_path):
        lat, lon, alt = _point(entry, index)
        if lat == 0.0 and lon == 0.0:
            dropped += 1
            continue
        points.append([lon, lat, alt])
        kept.append(index)
    if dropped:
        notes.append(f"{dropped} GPS point(s) at 0°, 0° (no fix) were ignored.")
    if len(points) < 2:
        raise _invalid(
            "The FlightMD GPS path needs at least two valid points",
            points=len(points),
            dropped=dropped,
        )

    version, version_note = detect_schema_version(report)
    if version_note:
        notes.append(version_note)

    times = _gps_times(metadata.get("gps_path_t_s"), len(gps_path), kept, notes)
    armed = _armed_windows(metadata.get("armed_windows"), notes)
    warnings = _parse_warnings(metadata.get("parse_warnings"))
    findings = _findings(report.get("findings"), notes)

    info = FlightInfo(
        report_id=_opt_str(report.get("report_id") or report.get("id")),
        file_name=_opt_str(report.get("file_name") or metadata.get("file_name")),
        schema_version=version,
        location_name=_opt_str(metadata.get("location_name") or report.get("location_name")),
        log_start_utc=_opt_str(metadata.get("log_start_utc") or report.get("log_start_utc")),
        overall_score=_opt_float(report.get("overall_score")),
        letter_grade=_opt_str(report.get("letter_grade")),
        points=len(points),
        flightmd_url=_opt_str(flightmd_url),
        armed_windows=armed,
        parse_warnings=warnings,
        planning_brief_sha256=_sha256(metadata.get("planning_brief_sha256"), "planning_brief_sha256", notes),
        log_sha256=_sha256(metadata.get("log_sha256"), "log_sha256", notes),
        source_format=_short(metadata.get("source_format")),
        airframe_name=_short(metadata.get("airframe_name")),
        gps_times=times is not None,
        duration_s=round(times[-1] - times[0], 3) if times is not None else None,
    )
    return FlightTrack(points=points, info=info, schema_version=version, notes=notes, times=times, findings=findings)


def _short(value: Any, limit: int = 100) -> str | None:
    text = _opt_str(value)
    return text[:limit] if text else None


def _sha256(value: Any, name: str, notes: list[str]) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not _SHA256.match(text):
        notes.append(f"metadata.{name} is not a SHA-256 hex digest and was ignored.")
        return None
    return text


def _gps_times(value: Any, count: int, kept: list[int], notes: list[str]) -> list[float] | None:
    """Times of the kept GPS points, or None (with a note) when absent or unusable."""
    if value is None:
        return None
    if isinstance(value, str | bytes) or not isinstance(value, Sequence) or len(value) != count:
        notes.append("metadata.gps_path_t_s does not match gps_path point for point; findings are not placed on the track.")
        return None
    times: list[float] = []
    for index in kept:
        t = _opt_float(value[index])
        if t is None or (times and t < times[-1]):
            notes.append("metadata.gps_path_t_s has a missing or decreasing time; findings are not placed on the track.")
            return None
        times.append(t)
    return times


def _armed_windows(value: Any, notes: list[str]) -> list[list[float]] | None:
    if value is None:
        return None
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        notes.append("metadata.armed_windows is not a list and was ignored.")
        return None
    windows: list[list[float]] = []
    bad = 0
    for entry in list(value)[:MAX_ARMED_WINDOWS]:
        pair = entry if isinstance(entry, Sequence) and not isinstance(entry, str | bytes) and len(entry) == 2 else None
        start = _opt_float(pair[0]) if pair is not None else None
        end = _opt_float(pair[1]) if pair is not None else None
        if start is None or end is None or end < start:
            bad += 1
            continue
        windows.append([start, end])
    if bad:
        notes.append(f"{bad} armed window(s) in the FlightMD report were malformed and ignored.")
    return windows


def _parse_warnings(value: Any) -> list[str]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        return []
    out = []
    for item in value:
        text = _opt_str(item) if isinstance(item, str) else None
        if text:
            out.append(text[:500])
        if len(out) == MAX_PARSE_WARNINGS:
            break
    return out


def _findings(value: Any, notes: list[str]) -> list[AuditFinding]:
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        return []
    entries = [f for f in value if isinstance(f, Mapping)]
    if len(entries) > MAX_FINDINGS:
        notes.append(f"Only the first {MAX_FINDINGS} of {len(entries)} FlightMD findings are listed.")
        entries = entries[:MAX_FINDINGS]
    out: list[AuditFinding] = []
    for index, f in enumerate(entries):
        start = _opt_float(f.get("t_start_s"))
        start_ms = _opt_float(f.get("timestamp_start_ms"))
        if start is None and start_ms is not None:
            start = start_ms / 1000.0
        end = _opt_float(f.get("t_end_s"))
        end_ms = _opt_float(f.get("timestamp_end_ms"))
        if end is None and end_ms is not None:
            end = end_ms / 1000.0
        if start is not None and end is not None and end < start:
            end = None
        raw_params = f.get("params")
        if raw_params is None:
            # before 1.7 the names were only inside param_changes
            changes = f.get("param_changes")
            raw_params = [c.get("param_name") for c in changes if isinstance(c, Mapping)] if isinstance(changes, list) else []
        params = [p.strip()[:64] for p in raw_params if isinstance(p, str) and p.strip()][:32] if isinstance(raw_params, list) else []
        severity = str(f.get("severity") or "").strip().lower()
        out.append(
            AuditFinding(
                id=(_opt_str(f.get("id")) or f"finding-{index + 1}")[:64],
                title=(_opt_str(f.get("title")) or f"Finding {index + 1}")[:300],
                severity=_SEVERITIES.get(severity, "info"),
                category=_short(f.get("category"), 64),
                kind=_short(f.get("kind"), 100),
                params=params,
                recommendation=_short(f.get("recommendation"), 1000),
                t_start_s=start,
                t_end_s=end,
            )
        )
    return out
