"""CSV reader for tracks (drone shows, flight logs) and point lists (towers, sites, drop points).

Two position layouts are accepted (header names are case-insensitive; a unit in brackets or
parentheses after the name is ignored, e.g. ``x [m]``, ``Altitude (m)``):

* **Geographic:** ``lon``/``lng``/``longitude``, ``lat``/``latitude`` and optionally
  ``alt``/``altitude``/``height``/``elevation``.
* **Local metres:** ``x``/``east``, ``y``/``north`` and optionally ``z``/``up``, relative to an
  origin. The origin comes from the ``origin`` argument (the upload form) or, failing that,
  from a comment line ``# origin=lon,lat,alt``. Positions are converted to WGS84 with an
  azimuthal-equidistant :class:`~airbrief_core.geo.frame.LocalFrame`; altitude becomes
  ``origin_alt + z``.

Optional columns: an id (``id``/``drone``/``drone_id``/``track``/``track_id``/``asset_id``/
``tower_id``/``site_id``/``point_id``/``name``/``asset``/``tower``/``site``/``label``, first match
wins) and a time (``t``/``time``/``time_s``/``seconds``/``timestamp`` as seconds or ISO 8601;
``time_msec``/``time_ms``/``t_ms`` or a ``[ms]``/``[msec]`` unit as milliseconds, the Skybrush
per-drone export). Numeric times are kept as given (a shared show clock); ISO times are converted
to seconds from the earliest timestamp in the file, reported as ``time_origin_utc``.

**Tracks or points.** By default the rows form tracks: one LineString feature per id (one for
the whole file without an id), rows sorted by time within each track. The rows become **one
Point feature per row** instead when the file has no time column and an id column whose values
are all different (a tower or site list), or when a ``# geometry=points`` (or
``# geometry_hint=points``) comment says so; ``# geometry=tracks`` forces tracks.

**Attributes.** Columns that are not positions, the id or the time are kept as feature
properties under their header text (``Tower height (m)``). Values are parsed as numbers when they
are clean decimals (``42``, ``-3.5``, ``1e3``); anything else stays text, including identifiers
with leading zeros (``007``), thousands separators and integers longer than 15 digits (which
JavaScript could not hold exactly). Points keep every column of their row (the id and the
height included). Tracks keep a column only when its value is the same on every row of the track
(``drone_model``); columns that change along a track (LED colours, battery) belong to single
samples and are not kept (one info issue names them).

**Heights.** Without a height column the features are 2D (``clampToGround``, like a GPX without
``<ele>``), so nothing reads them as 0 m; blank height cells inside a track are interpolated from
the neighbouring rows (info issue). ``altitude_mode`` is otherwise ``relativeToGround`` (or
``absolute`` for an ``elevation``/``ele`` column, which is above sea level like GPX ``<ele>``,
and for local tracks whose origin has a non-zero altitude); a ``# altitude_mode=...`` comment
overrides it.
All layers are EPSG:4326 with ``crs_source="csv"``.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
from shapely.geometry import LineString, Point

from ..errors import IngestError
from ..geo.crs import WGS84
from ..geo.frame import LocalFrame
from ..models.common import Issue, Severity
from .reader import RawFeature, RawLayer, charge_vertices

_LON = ("lon", "lng", "long", "longitude")
_LAT = ("lat", "latitude")
_ALT = ("alt", "altitude", "alt_m", "altitude_m", "height", "height_m", "elevation", "ele")
_ALT_ABOVE_SEA_LEVEL = frozenset({"elevation", "ele"})
"""Height columns that are above sea level by name (read as ``absolute``)."""
_X = ("x", "x_m", "east")
_Y = ("y", "y_m", "north")
_Z = ("z", "z_m", "up")
_ID = (
    "id", "drone", "drone_id", "track", "track_id", "asset_id", "tower_id", "site_id", "point_id",
    "name", "asset", "tower", "site", "label",
)
_NAME = ("name", "title", "label")
_TIME = ("t", "time", "time_s", "seconds", "timestamp", "t_s")
_TIME_MS = ("time_msec", "time_ms", "t_ms", "timestamp_ms", "t_msec")
_MS_UNITS = ("ms", "msec", "millisecond", "milliseconds")
_SYSTEM_KEYS = frozenset({"track_id", "samples", "source_format"})
# Matched against stripped lines, so the value runs to the end: no lazy ``(.+?)\s*$``, which
# backtracked quadratically over a long run of spaces inside a comment (ReDoS).
_DIRECTIVE_RE = re.compile(r"^#\s*([A-Za-z_]+)\s*[=:]\s*(.+)$")
_NUMBER_RE = re.compile(r"^[+-]?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_INTEGER_RE = re.compile(r"^[+-]?\d+$")
_MAX_INT_DIGITS = 15
_POINTS_HINTS = {"points", "point"}
_TRACK_HINTS = {"tracks", "track", "lines", "line", "linestring"}
_ALTITUDE_MODES = {
    "absolute": "absolute",
    "relativetoground": "relativeToGround",
    "clamptoground": "clampToGround",
    "relativetoseafloor": "relativeToSeaFloor",
    "clamptoseafloor": "clampToSeaFloor",
}


@dataclass
class _Row:
    """One CSV row with a valid position."""

    row_no: int
    a: float
    b: float
    c: float | None
    cells: list[str]


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _parse_origin(text: str) -> tuple[float, float, float] | None:
    parts = [p for p in re.split(r"[,\s;]+", text.strip()) if p]
    if len(parts) < 2:
        return None
    try:
        values = [float(p) for p in parts[:3]]
    except ValueError:
        return None
    lon, lat = values[0], values[1]
    alt = values[2] if len(values) > 2 else 0.0
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0) or not all(math.isfinite(v) for v in values):
        return None
    return lon, lat, alt


def _column_key(text: str) -> tuple[str, str | None]:
    """Matching key and unit of a header cell: ``"Time [msec]"`` → ``("time", "msec")``."""
    # The unit is the text after the first "[" or "(" that follows the last earlier closing bracket,
    # up to the closing bracket that ends the cell. Found with str methods: the equivalent regex
    # backtracked for hours on a header cell of a few hundred spaces and brackets (ReDoS).
    raw = text.strip().lower()
    if raw[-1:] in ("]", ")"):
        end = len(raw) - 1
        start = max(raw.rfind("]", 0, end), raw.rfind(")", 0, end)) + 1
        opens = [i for i in (raw.find("[", start, end), raw.find("(", start, end)) if i >= 0]
        if opens:
            name = raw[: min(opens)].strip()
            if name and "\n" not in name:
                return re.sub(r"\s+", "_", name), raw[min(opens) + 1 : end].strip() or None
    return re.sub(r"\s+", "_", raw), None


def _find(keys: list[str], names: tuple[str, ...]) -> int | None:
    for name in names:
        if name in keys:
            return keys.index(name)
    return None


def _parse_time(text: str, scale: float) -> float | datetime | None:
    value = text.strip()
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        number = None
    if number is not None:
        return number * scale if math.isfinite(number) else None
    iso = value[:-1] + "+00:00" if value[-1:] in ("Z", "z") else value
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sniff_dialect(sample: str) -> type[csv.Dialect] | csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel


def parse_cell_value(text: str) -> Any:
    """A CSV cell as a property value: a number when it is a clean decimal, else the text.

    ``""`` → ``None``; ``"42"`` → ``42``; ``"-3.50"`` → ``-3.5``; ``"1e3"`` → ``1000.0``;
    ``"007"``, ``"1,200"``, ``"NaN"`` and integers longer than 15 digits stay text.
    """
    value = text.strip()
    if not value:
        return None
    if not _NUMBER_RE.match(value):
        return value
    if _INTEGER_RE.match(value):
        digits = value.lstrip("+-")
        return int(value) if len(digits) <= _MAX_INT_DIGITS else value
    number = float(value)
    return number if math.isfinite(number) else value


def _cell(cells: list[str], index: int) -> str:
    return cells[index] if index < len(cells) else ""


def _property_key(header: str) -> str:
    key = header.strip() or "column"
    return f"csv_{key}" if key in _SYSTEM_KEYS else key


def _seconds(stamp: float | datetime | None, origin: datetime | None) -> float:
    if isinstance(stamp, datetime):
        return (stamp - origin).total_seconds() if origin is not None else 0.0
    return float(stamp) if stamp is not None else 0.0


def read_track_csv(data: bytes, layer_name: str, origin: tuple[float, float, float] | None) -> RawLayer:
    """Parse a CSV of track samples or points into one layer (see the module docstring).

    Raises:
        IngestError: ``corrupt_file`` when no position columns are found or the time column
            mixes seconds and ISO timestamps, ``invalid_crs`` for local x/y tracks without an
            origin, ``no_supported_content`` when no row holds a valid position.
    """
    text = _decode(data)
    issues: list[Issue] = []
    directives: dict[str, str] = {}
    body_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            match = _DIRECTIVE_RE.match(stripped)
            if match:
                directives[match.group(1).lower()] = match.group(2)
            continue
        body_lines.append(line)
    if not body_lines:
        raise IngestError("no_supported_content", "The CSV file has no rows.")

    sample = "\n".join(body_lines[:20])
    reader = csv.reader(io.StringIO("\n".join(body_lines)), _sniff_dialect(sample))
    try:
        rows = list(reader)
    except csv.Error as exc:  # e.g. a cell above the csv module's field size limit (128 kB)
        raise IngestError(
            "corrupt_file", f"The CSV could not be read: {exc}.", details={"reason": str(exc)[:200]}
        ) from exc
    headers = [h.strip() for h in rows[0]]
    parsed_keys = [_column_key(h) for h in headers]
    keys = [k for k, _ in parsed_keys]
    records = rows[1:]

    i_lon, i_lat = _find(keys, _LON), _find(keys, _LAT)
    i_x, i_y = _find(keys, _X), _find(keys, _Y)
    geographic = i_lon is not None and i_lat is not None
    local = not geographic and i_x is not None and i_y is not None
    if not geographic and not local:
        raise IngestError(
            "corrupt_file",
            "The CSV needs lon/lat columns (with optional alt) or x/y columns (with optional z) and a header row.",
            details={"columns": keys[:30]},
        )
    i_alt = _find(keys, _ALT) if geographic else _find(keys, _Z)
    i_id = _find(keys, _ID)
    i_name = _find(keys, _NAME)
    i_time = _find(keys, _TIME)
    time_scale = 1.0
    i_time_ms = _find(keys, _TIME_MS)
    if i_time is None and i_time_ms is not None:
        i_time, time_scale = i_time_ms, 0.001
    elif i_time is not None and (parsed_keys[i_time][1] or "") in _MS_UNITS:
        time_scale = 0.001

    frame: LocalFrame | None = None
    origin_alt = 0.0
    if local:
        file_origin = _parse_origin(directives["origin"]) if "origin" in directives else None
        if "origin" in directives and file_origin is None:
            issues.append(
                Issue(
                    code="invalid_origin",
                    message=f"Layer '{layer_name}': the '# origin=' comment could not be parsed.",
                    severity=Severity.WARNING,
                )
            )
        chosen = origin if origin is not None else file_origin
        if origin is not None and file_origin is not None and tuple(origin) != tuple(file_origin):
            issues.append(
                Issue(
                    code="origin_override",
                    message=f"Layer '{layer_name}': the supplied origin was used instead of the file's '# origin=' comment.",
                    severity=Severity.INFO,
                )
            )
        if chosen is None:
            raise IngestError(
                "invalid_crs",
                "Local x/y/z tracks need an origin: add a '# origin=lon,lat,alt' line or supply the origin.",
            )
        lon0, lat0, origin_alt = float(chosen[0]), float(chosen[1]), float(chosen[2]) if len(chosen) > 2 else 0.0
        frame = LocalFrame(lon0, lat0)

    mode_directive = directives.get("altitude_mode") or directives.get("altitudemode")
    if mode_directive and mode_directive.strip().lower() in _ALTITUDE_MODES:
        altitude_mode = _ALTITUDE_MODES[mode_directive.strip().lower()]
    elif i_alt is None:
        # No height column: 2D positions, like a GPX without <ele> (nothing says how high it was).
        altitude_mode = "clampToGround"
    elif geographic and keys[i_alt] in _ALT_ABOVE_SEA_LEVEL:
        # An "elevation" is above sea level (as GPX <ele>), never a height above the ground.
        altitude_mode = "absolute"
    else:
        altitude_mode = "absolute" if (local and origin_alt != 0.0) else "relativeToGround"

    i_a = i_lon if geographic else i_x
    i_b = i_lat if geographic else i_y
    if i_a is None or i_b is None:
        raise IngestError("corrupt_file", "The CSV position columns could not be located.")

    valid: list[_Row] = []
    skipped = 0
    for row_no, row in enumerate(records):
        if len(row) <= max(i_a, i_b):
            skipped += 1
            continue
        try:
            a = float(row[i_a])
            b = float(row[i_b])
            alt_text = row[i_alt].strip() if i_alt is not None and i_alt < len(row) else ""
            c = float(alt_text) if alt_text else None
        except ValueError:
            skipped += 1
            continue
        if not (math.isfinite(a) and math.isfinite(b) and (c is None or math.isfinite(c))):
            skipped += 1
            continue
        if geographic and not (-180.0 <= a <= 180.0 and -90.0 <= b <= 90.0):
            skipped += 1
            continue
        valid.append(_Row(row_no=row_no, a=a, b=b, c=c, cells=row))

    hint = (directives.get("geometry") or directives.get("geometry_hint") or "").strip().lower()
    ids = [_cell(r.cells, i_id).strip() for r in valid] if i_id is not None else []
    if hint in _POINTS_HINTS:
        as_points = True
    elif hint in _TRACK_HINTS:
        as_points = False
    else:
        as_points = i_time is None and i_id is not None and all(ids) and len(set(ids)) == len(ids)

    def to_wgs84(a_vals: list[float], b_vals: list[float]) -> tuple[list[float], list[float]]:
        if frame is None:
            return a_vals, b_vals
        lons, lats = frame.lonlat(np.asarray(a_vals, dtype=float), np.asarray(b_vals, dtype=float))
        return (
            [float(v) for v in np.asarray(lons, dtype=float).reshape(-1)],
            [float(v) for v in np.asarray(lats, dtype=float).reshape(-1)],
        )

    source_format = "local_xyz" if local else "lon_lat"
    features: list[RawFeature] = []

    if as_points:
        position_columns = {i_a, i_b}
        lons, lats = to_wgs84([r.a for r in valid], [r.b for r in valid])
        for k, r in enumerate(valid):
            z = None if r.c is None else r.c + (origin_alt if frame is not None else 0.0)
            geometry = Point(lons[k], lats[k]) if z is None else Point(lons[k], lats[k], z)
            charge_vertices(geometry)
            props: dict[str, Any] = {
                _property_key(headers[i]): parse_cell_value(_cell(r.cells, i))
                for i in range(len(headers))
                if i not in position_columns
            }
            props["source_format"] = source_format
            fid = ids[k] if ids and ids[k] else str(r.row_no + 1)
            name_text = _cell(r.cells, i_name).strip() if i_name is not None else ""
            features.append(
                RawFeature(
                    fid=fid,
                    name=name_text or (ids[k] if ids and ids[k] else None),
                    geometry=geometry,
                    properties=props,
                    altitude_mode=altitude_mode,
                )
            )
    else:
        tracks: dict[str, list[tuple[float | datetime | None, _Row]]] = {}
        for k, r in enumerate(valid):
            stamp = _parse_time(_cell(r.cells, i_time), time_scale) if i_time is not None else None
            if i_time is not None and stamp is None:
                skipped += 1
                continue
            track_id = ids[k] if ids and ids[k] else layer_name
            tracks.setdefault(track_id, []).append((stamp, r))

        all_stamps = [s for samples in tracks.values() for s, _ in samples if s is not None]
        has_dt = any(isinstance(s, datetime) for s in all_stamps)
        has_num = any(not isinstance(s, datetime) for s in all_stamps)
        if has_dt and has_num:
            raise IngestError(
                "corrupt_file",
                "The CSV time column mixes numeric seconds and ISO timestamps.",
            )
        time_origin: datetime | None = min((s for s in all_stamps if isinstance(s, datetime)), default=None)
        used_columns = {i for i in (i_a, i_b, i_alt, i_id, i_time) if i is not None}
        extra_columns = [i for i in range(len(headers)) if i not in used_columns]
        not_kept: set[str] = set()
        interpolated = 0

        for track_id, samples in tracks.items():
            if i_time is not None:
                samples.sort(key=lambda s: (_seconds(s[0], time_origin), s[1].row_no))
            lons, lats = to_wgs84([r.a for _, r in samples], [r.b for _, r in samples])
            raw_heights = [r.c for _, r in samples] if i_alt is not None else []
            known = [k for k, h in enumerate(raw_heights) if h is not None]
            coords: list[tuple[float, ...]]
            if not known:
                # No height column (or no height on any row): a 2D track, like a GPX without <ele>
                # (the flight audit then says heights were not assessed).
                coords = list(zip(lons, lats, strict=True))
            else:
                heights = [float(h) for h in raw_heights if h is not None]
                if len(known) < len(raw_heights):
                    # Blank height cells inside a track: interpolated along it (not read as 0 m).
                    interpolated += len(raw_heights) - len(known)
                    filled = np.interp(
                        np.arange(len(raw_heights), dtype=np.float64),
                        np.asarray(known, dtype=np.float64),
                        np.asarray(heights, dtype=np.float64),
                    )
                    heights = [float(v) for v in filled.tolist()]
                base_alt = origin_alt if frame is not None else 0.0
                coords = [(lo, la, h + base_alt) for lo, la, h in zip(lons, lats, heights, strict=True)]
            geometry = Point(coords[0]) if len(coords) == 1 else LineString(coords)
            charge_vertices(geometry)
            times = [round(_seconds(s, time_origin), 6) for s, _ in samples] if i_time is not None else None
            props = {}
            for i in extra_columns:
                values = {_cell(r.cells, i).strip() for _, r in samples}
                if len(values) == 1:
                    value = parse_cell_value(values.pop())
                    if value is not None:
                        props[_property_key(headers[i])] = value
                else:
                    not_kept.add(headers[i].strip() or f"column {i + 1}")
            props.update({"track_id": track_id, "samples": len(samples), "source_format": source_format})
            features.append(
                RawFeature(
                    fid=track_id,
                    name=track_id,
                    geometry=geometry,
                    properties=props,
                    altitude_mode=altitude_mode,
                    times=times,
                    time_origin_utc=_iso(time_origin) if time_origin is not None else None,
                )
            )
        if not_kept:
            names = sorted(not_kept)
            shown = ", ".join(names[:10]) + (f" and {len(names) - 10} more" if len(names) > 10 else "")
            issues.append(
                Issue(
                    code="csv_columns_not_kept",
                    message=(
                        f"Layer '{layer_name}': column(s) {shown} change from row to row along the tracks, "
                        "so they were not kept as properties."
                    ),
                    severity=Severity.INFO,
                )
            )
        if interpolated:
            issues.append(
                Issue(
                    code="heights_interpolated",
                    message=(
                        f"Layer '{layer_name}': {interpolated} row(s) had no height; their heights were "
                        "interpolated from the neighbouring rows of the same track."
                    ),
                    severity=Severity.INFO,
                )
            )

    if skipped:
        issues.append(
            Issue(
                code="rows_skipped",
                message=f"Layer '{layer_name}': {skipped} row(s) without a valid position or time were skipped.",
                severity=Severity.WARNING,
            )
        )
    if not features:
        raise IngestError("no_supported_content", "The CSV contains no valid track positions.")
    return RawLayer(name=layer_name, crs=WGS84, crs_source="csv", features=features, issues=issues)
