"""UAS geographical zones: EUROCAE ED-269 JSON and ED-318 GeoJSON in and out, and the zone
properties of any dataset feature.

The ED-318 text is sold by EUROCAE, so this follows what national authorities publish about
both formats. Checked against: the Swiss FOCA INTERLIS model ``UASGeographicalZone_V1`` (the
ED-269 data model: ``uomDimensions``, ``lowerLimit``/``lowerVerticalReference``,
``upperLimit``/``upperVerticalReference``, ``horizontalProjection``, ``applicability`` with
``permanent``, ``startDateTime``, ``endDateTime`` and a ``schedule`` of ``day``/``startTime``/
``endTime``, ``zoneAuthority``) and ENAIRE's ED-318 exchange specification S431-24-INS-001-1.1
(the ``type`` codes USPACE, PROHIBITED, REQ_AUTHORIZATION, CONDITIONAL, NO_RESTRICTION,
``variant``, ``reason`` codes, ``lower``/``upper`` with ``lowerReference``/``upperReference`` in
AGL, AMSL or WGS84 and ``uom`` M or FT, RFC 3339 times). Assumed, not verified against the
standards' text: an ED-269 circle is ``{"type": "Circle", "center": [lon, lat], "radius": r}``,
an ED-318 one a Point with ``"extent": {"subType": "Circle", "radius": r}``, the vertical limits of
an ED-318 feature sit in ``geometry.layer``, and radii use the volume's unit.

Restriction → zone: PROHIBITED red; REQ_AUTHORISATION/REQ_AUTHORIZATION yellow (permission);
CONDITIONAL yellow with a note; USPACE yellow, noted as U-space; NO_RESTRICTION green.
Circles become 64-gons built in a local azimuthal-equidistant frame; feet become metres;
99999 (or "UNL") means unlimited.

Files are hostile input: nesting, zone, volume, schedule and vertex counts are capped and any
malformed zone is refused with ``invalid_params`` naming its index.
"""

from __future__ import annotations

import math
import html
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from typing import Any, Literal

import shapely
from pydantic import ValidationError
from shapely.geometry import Point, mapping, shape
from shapely.geometry.base import BaseGeometry

from ..errors import AnalysisError
from ..geo.frame import LocalFrame
from ..geo.parse import check_json_limits
from ..models.common import UserZone, VerticalReference, ZoneKind, ZoneSchedule
from ..models.zones import ZoneLayerMapping

__all__ = [
    "CIRCLE_SEGMENTS",
    "MAX_DOCUMENT_ZONES",
    "Ed318Limits",
    "GeozoneFormat",
    "detect_geozone_format",
    "geozone_features",
    "parse_ed269",
    "parse_ed318",
    "parse_geozones",
    "schedule_intervals",
    "to_ed318",
    "zone_from_properties",
    "zone_properties",
]

GeozoneFormat = Literal["ed269", "ed318"]
Ed318Limits = Literal["geometry", "properties"]
"""Where :func:`to_ed318` writes the vertical limits: ``geometry.layer`` (EUROCAE ED-318, ENAIRE)
or the feature properties, as some national feeds do."""

CIRCLE_SEGMENTS = 64
MAX_DOCUMENT_ZONES = 5_000
MAX_VOLUMES = 100  # the Swiss national file (FOCA) has a zone of 63: Geneva's police sites
MAX_PERIODS = 10
MAX_SCHEDULES = 20
MAX_ZONE_VERTICES = 1_000_000
MAX_CIRCLE_RADIUS_M = 500_000.0
UNLIMITED = 99_999.0
FT_TO_M = 0.3048

RESTRICTIONS: dict[str, tuple[ZoneKind, str | None]] = {
    "PROHIBITED": ("red", None),
    "REQ_AUTHORISATION": ("yellow", None),
    "REQ_AUTHORIZATION": ("yellow", None),
    "CONDITIONAL": ("yellow", "Conditional: flight allowed only under the zone's conditions"),
    "USPACE": ("yellow", "U-space airspace"),
    "U-SPACE": ("yellow", "U-space airspace"),
    "NO_RESTRICTION": ("green", None),
}
REASON_CODES = frozenset(
    {"AIR_TRAFFIC", "SENSITIVE", "PRIVACY", "POPULATION", "NATURE", "NOISE", "FOREIGN_TERRITORY",
     "DAR", "EMERGENCY", "OTHER"}
)
_DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN", "ANY")
_COLOURS: dict[str, ZoneKind] = {"red": "red", "yellow": "yellow", "amber": "yellow", "green": "green"}
_ZONE_FIELDS = ("zone", "colour", "color", "restriction")
_CEILING_FIELDS = ("ceiling_agl_m", "ceiling_m", "ceiling")
_TIME = re.compile(r"^(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")


def _bad(message: str, **details: Any) -> AnalysisError:
    return AnalysisError("invalid_params", message, details=details)


def _text(value: Any, limit: int) -> str | None:
    """Plain text from a string, a number or a list of ``{"text": …}`` items (ED-318 names)."""
    if isinstance(value, list):
        parts = [_text(v, limit) for v in value[:20]]
        value = "; ".join(p for p in parts if p)
    elif isinstance(value, dict):
        value = value.get("text")
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    out = " ".join(str(value).split())
    return out[:limit] or None


def _items(value: Any, limit: int, what: str, index: int) -> list[dict[str, Any]]:
    """A list of objects (a single object counts as a list of one), at most ``limit``."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    if len(items) > limit:
        raise _bad(f"Zone {index} has more than {limit} {what}.", zone=index, limit=limit)
    if not all(isinstance(v, dict) for v in items):
        raise _bad(f"Zone {index}: every entry of {what} must be an object.", zone=index)
    return items


def _instant(value: Any, index: int, field: str) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise _bad(f"Zone {index}: {field} must be an RFC 3339 date-time.", zone=index, field=field)
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise _bad(f"Zone {index}: {field} is not an RFC 3339 date-time.", zone=index, field=field) from exc
    # A time without an offset is read as UTC (ED-269/ED-318 times carry one).
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _clock(value: Any, index: int, field: str) -> int | None:
    """Minutes after midnight UTC of an ED schedule time (24:00 is 1440), None when absent. With a
    UTC offset the result can be below 0 or above 1440: the time falls on the day before or after."""
    if value is None or value == "":
        return None
    text = str(value).strip()
    m = _TIME.match(text)
    if not m:
        raise _bad(f"Zone {index}: {field} must be a time as hh:mm[:ss][Z].", zone=index, field=field)
    hours, minutes = int(m.group(1)), int(m.group(2))
    if hours > 24 or minutes > 59 or (hours == 24 and minutes):
        raise _bad(f"Zone {index}: {field} is not a valid time of day.", zone=index, field=field)
    total = hours * 60 + minutes
    offset = m.group(4)
    if offset and offset != "Z":
        sign = -1 if offset[0] == "-" else 1
        digits = offset[1:].replace(":", "")
        total -= sign * (int(digits[:2]) * 60 + int(digits[2:]))
    return total


def _plain(text: str | None) -> str | None:
    """``text`` without HTML markup (ENAIRE's messages carry ``<p>``, ``<font>`` and entities)."""
    if text is None or "<" not in text and "&" not in text:
        return text
    return " ".join(html.unescape(re.sub(r"</?[A-Za-z][^>]*>", " ", text)).split()) or None


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _schedules(entries: list[dict[str, Any]], index: int) -> tuple[list[ZoneSchedule], bool]:
    """Daily periods in UTC; the flag says a sunrise/sunset event was read as all day."""
    out: list[ZoneSchedule] = []
    by_event = False
    for entry in entries:
        raw_days = entry.get("day")
        days = raw_days if isinstance(raw_days, list) else [raw_days]
        names = [str(d).strip().upper() for d in days if d is not None and str(d).strip()][:8]
        if not names and not any(entry.get(k) for k in ("startTime", "endTime", "startEvent", "endEvent")):
            continue  # an empty placeholder entry (ENAIRE): no schedule, not a malformed one
        if not names or any(d not in _DAYS for d in names):
            raise _bad(f"Zone {index}: schedule days must be MON–SUN or ANY.", zone=index)
        start = _clock(entry.get("startTime"), index, "startTime")
        end = _clock(entry.get("endTime"), index, "endTime")
        if start is None or end is None:
            # startEvent/endEvent (twilight, sunrise, sunset) are not evaluated: active all day.
            by_event = by_event or bool(entry.get("startEvent") or entry.get("endEvent"))
            start, end = 0, 1440
        shift = start // 1440
        if shift and "ANY" not in names:
            names = [_DAYS[(_DAYS.index(d) + shift) % 7] for d in names]
        start -= shift * 1440
        end -= shift * 1440
        if not 0 <= end <= 1440:
            end %= 1440  # ends on the next day: ZoneSchedule runs an end before its start past midnight
        out.append(ZoneSchedule(days=list(dict.fromkeys(names)), start=_hhmm(start), end=_hhmm(end)))  # type: ignore[arg-type]
    if out and all(s.days == ["ANY"] and s.start == "00:00" and s.end == "24:00" for s in out):
        return [], by_event  # every day, all day: the same as no schedule
    return out, by_event


def _limit(value: Any, unit: str, index: int, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value.strip().upper() in ("UNL", "UNLIMITED"):
            return None
        try:
            value = float(value)
        except ValueError as exc:
            raise _bad(f"Zone {index}: {field} must be a number.", zone=index, field=field) from exc
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _bad(f"Zone {index}: {field} must be a number.", zone=index, field=field)
    if value >= UNLIMITED:
        return None
    metres = float(value) * (FT_TO_M if unit == "FT" else 1.0)
    return round(metres, 2) if unit == "FT" else metres


def _unit(value: Any, index: int) -> str:
    unit = str(value or "M").strip().upper()
    if unit not in ("M", "FT"):
        raise _bad(f"Zone {index}: the unit must be M or FT.", zone=index)
    return unit


def _reference(value: Any, index: int) -> VerticalReference:
    ref = str(value or "AGL").strip().upper()
    if ref not in ("AGL", "AMSL", "WGS84"):
        raise _bad(f"Zone {index}: vertical references are AGL, AMSL or WGS84.", zone=index)
    return ref  # type: ignore[return-value]


def _band(
    lower: Any, lower_ref: Any, upper: Any, upper_ref: Any, unit: str, index: int
) -> tuple[float | None, float | None, VerticalReference]:
    """One vertical reference for the zone: the upper limit's. A lower limit in another reference
    cannot be converted without terrain, so it becomes "from the ground" (wider, never narrower)."""
    lo = _limit(lower, unit, index, "lower limit")
    up = _limit(upper, unit, index, "upper limit")
    lo_ref = _reference(lower_ref, index)
    ref = _reference(upper_ref, index) if upper_ref is not None else lo_ref
    if lo is not None and (lo_ref != ref or (lo_ref == "AGL" and lo <= 0.0)):
        lo = None
    if lo is not None and up is not None and up <= lo:
        raise _bad(f"Zone {index}: the upper limit must be above the lower limit.", zone=index)
    return lo, up, ref


def _circle(centre: Any, radius: Any, unit: str, index: int) -> BaseGeometry:
    if not (isinstance(centre, list) and len(centre) >= 2 and all(isinstance(c, int | float) for c in centre[:2])):
        raise _bad(f"Zone {index}: a circle needs a [lon, lat] centre.", zone=index)
    lon, lat = float(centre[0]), float(centre[1])
    try:
        r = float(radius) * (FT_TO_M if unit == "FT" else 1.0)
    except (TypeError, ValueError):
        r = math.nan
    if isinstance(radius, bool) or not (-180 <= lon <= 180 and -90 <= lat <= 90) or not 0 < r <= MAX_CIRCLE_RADIUS_M:
        raise _bad(f"Zone {index}: the circle's centre or radius is out of range.", zone=index)
    disk = Point(0.0, 0.0).buffer(r, quad_segs=CIRCLE_SEGMENTS // 4)
    return LocalFrame(lon, lat).to_wgs84(disk)


def _polygon(geom: Any, index: int, budget: list[int]) -> BaseGeometry:
    if not isinstance(geom, dict) or geom.get("type") not in ("Polygon", "MultiPolygon"):
        raise _bad(f"Zone {index}: the area must be a Polygon, a MultiPolygon or a circle.", zone=index)
    try:
        built = shape({"type": geom["type"], "coordinates": geom.get("coordinates")})
    except (ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        raise _bad(f"Zone {index}: the polygon coordinates are malformed.", zone=index) from exc
    if built.is_empty:
        raise _bad(f"Zone {index}: the polygon is empty.", zone=index)
    minx, miny, maxx, maxy = built.bounds
    if not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)) or not (-180 <= minx and maxx <= 180 and -90 <= miny and maxy <= 90):
        raise _bad(f"Zone {index}: coordinates must be WGS84 longitude/latitude.", zone=index)
    _charge(built, index, budget)
    return built


def _charge(geom: BaseGeometry, index: int, budget: list[int], copies: int = 1) -> None:
    budget[0] += int(shapely.get_num_coordinates(geom)) * copies
    if budget[0] > MAX_ZONE_VERTICES:
        raise _bad(f"The zones have more than {MAX_ZONE_VERTICES:,} vertices in all.", zone=index, limit=MAX_ZONE_VERTICES)


def _restriction(code: Any) -> str | None:
    text = str(code or "").strip().upper()
    return text if text in RESTRICTIONS else None


def _reason(props: Mapping[str, Any]) -> str | None:
    raw = props.get("reason", props.get("reasons"))  # ENAIRE: "reasons"
    codes = [str(c).strip().upper() for c in (raw if isinstance(raw, list) else [raw]) if c is not None][:10]
    other = _text(props.get("otherReasonInfo"), 200)
    if codes == ["OTHER"] and other:
        return other[:300]
    text = ", ".join(codes)
    if other:
        text = f"{text}; {other}" if text else other
    return text[:300] or None


def _authority(props: Mapping[str, Any], index: int) -> str | None:
    names = []
    for item in _items(props.get("zoneAuthority"), 10, "authorities", index):
        name = _text(item.get("name"), 120)
        contact = _text(item.get("siteURL") or item.get("email") or item.get("phone"), 120)
        if name or contact:
            names.append(" · ".join(p for p in (name, contact) if p))
    return "; ".join(names)[:300] or None


def _periods(props: Mapping[str, Any], key: str, index: int) -> list[tuple[datetime | None, datetime | None, list[ZoneSchedule], bool]]:
    """Validity periods of a zone; one unlimited period when it has none or is permanent."""
    out = []
    for entry in _items(props.get(key), MAX_PERIODS, "applicability periods", index):
        if str(entry.get("permanent") or "").strip().upper() == "YES":
            return [(None, None, [], False)]
        start = _instant(entry.get("startDateTime"), index, "startDateTime")
        end = _instant(entry.get("endDateTime"), index, "endDateTime")
        if start is not None and end is not None and end <= start:
            raise _bad(f"Zone {index}: endDateTime must be after startDateTime.", zone=index)
        schedule, by_event = _schedules(_items(entry.get("schedule"), MAX_SCHEDULES, "schedule entries", index), index)
        out.append((start, end, schedule, by_event))
    return out or [(None, None, [], False)]


def _zone(
    *,
    index: int,
    name: str,
    area: BaseGeometry,
    restriction: str | None,
    band: tuple[float | None, float | None, VerticalReference],
    period: tuple[datetime | None, datetime | None, list[ZoneSchedule], bool],
    props: Mapping[str, Any],
    source: Literal["ed269", "ed318"],
    own: Mapping[str, Any] | None,
) -> UserZone:
    authority, reason = _authority(props, index), _reason(props)
    zone_kind: ZoneKind
    if own is not None:
        zone_kind, ceiling = own["zone"], own.get("ceiling_agl_m")
        restriction, source_format, note = own.get("restriction"), own.get("source_format", source), own.get("note")
        name, authority, reason = own.get("name") or name, own.get("authority"), own.get("reason")
    else:
        if restriction is None:
            raise _bad(f"Zone {index}: unknown restriction; expected one of {', '.join(sorted(RESTRICTIONS))}.", zone=index)
        zone_kind, extra = RESTRICTIONS[restriction]
        ceiling = None
        source_format = source
        message = _plain(_text(props.get("message"), 1800))
        note = "; ".join(p for p in (extra, message) if p) or None
    if period[3]:
        note = f"{note}; " if note else ""
        note += "sunrise/sunset schedule read as all day"
    try:
        return UserZone(
            name=name,
            zone=zone_kind,
            geometry=mapping(area),
            ceiling_agl_m=ceiling,
            note=note,
            valid_from=period[0],
            valid_to=period[1],
            schedule=period[2],
            lower_limit_m=band[0],
            upper_limit_m=band[1],
            vertical_reference=band[2],
            restriction=restriction,
            source_format=source_format,
            authority=authority,
            reason=reason,
        )
    except ValidationError as exc:
        raise _bad(f"Zone {index} is not a valid zone.", zone=index, errors=exc.errors(include_url=False, include_context=False)[:5]) from exc


def _zone_name(props: Mapping[str, Any], index: int, part: int, parts: int) -> str:
    base = _text(props.get("name"), 100) or _text(props.get("identifier"), 100) or f"Zone {index + 1}"
    ident = _text(props.get("identifier"), 40)
    if ident and ident not in base:
        base = f"{ident} {base}"
    return (f"{base} ({part + 1}/{parts})" if parts > 1 else base)[:120]


def _expand(
    *,
    props: Mapping[str, Any],
    volumes: list[tuple[BaseGeometry, tuple[float | None, float | None, VerticalReference]]],
    periods: list[tuple[datetime | None, datetime | None, list[ZoneSchedule], bool]],
    restriction: str | None,
    index: int,
    source: Literal["ed269", "ed318"],
    own: Mapping[str, Any] | None,
    total: list[int],
    budget: list[int],
) -> list[UserZone]:
    pairs = [(v, p) for v in volumes for p in periods]
    # counted before anything is built: volumes x periods can multiply a small file many times over
    total[0] += len(pairs)
    if total[0] > MAX_DOCUMENT_ZONES:
        raise _bad(
            f"The zones split into more than {MAX_DOCUMENT_ZONES:,} parts (volumes x applicability periods); "
            f"at most {MAX_DOCUMENT_ZONES:,} are read.",
            zone=index,
            limit=MAX_DOCUMENT_ZONES,
        )
    if len(periods) > 1:
        # each period gets its own copy of every volume's coordinates
        for area, _ in volumes:
            _charge(area, index, budget, copies=len(periods) - 1)
    return [
        _zone(index=index, name=_zone_name(props, index, i, len(pairs)), area=area, restriction=restriction,
              band=band, period=period, props=props, source=source, own=own)
        for i, ((area, band), period) in enumerate(pairs)
    ]


def _unwrap(doc: Any) -> Any:
    """The zone document inside the envelopes publishers use: an ED-318 service reply
    (``{"uasZones": FeatureCollection, …}``, DFS) or a bare list of ED-269 zones (DFS, one per category)."""
    if isinstance(doc, dict) and "features" not in doc and isinstance(doc.get("uasZones"), dict | list):
        inner = doc["uasZones"]
        return inner if isinstance(inner, dict) else {"type": "FeatureCollection", "features": inner}
    if isinstance(doc, list):
        return {"features": doc}
    return doc


def _flat(props: Mapping[str, Any]) -> Mapping[str, Any]:
    """ENAIRE nests a zone's fields in ``properties.UASZone``: lift them to the properties."""
    inner = props.get("UASZone")
    if not isinstance(inner, dict):
        return props
    return {**{k: v for k, v in props.items() if k != "UASZone"}, **inner}


def _features(doc: Any, fmt: str) -> list[Any]:
    doc = _unwrap(doc)
    if not isinstance(doc, dict):
        raise _bad(f"An {fmt.upper()} document must be a JSON object.")
    check_json_limits(doc, max_vertices=MAX_ZONE_VERTICES)
    feats = doc.get("features")
    if not isinstance(feats, list):
        raise _bad(f"An {fmt.upper()} document needs a 'features' array.")
    if len(feats) > MAX_DOCUMENT_ZONES:
        raise _bad(f"The file has {len(feats):,} zones; at most {MAX_DOCUMENT_ZONES:,} are read.", limit=MAX_DOCUMENT_ZONES)
    return feats


def _ed269_pairs(doc: Any) -> list[tuple[UserZone, Mapping[str, Any]]]:
    out: list[tuple[UserZone, Mapping[str, Any]]] = []
    budget, total = [0], [0]
    for index, item in enumerate(_features(doc, "ed269")):
        if not isinstance(item, dict):
            raise _bad(f"Zone {index} is not an object.", zone=index)
        volumes = []
        for vol in _items(item.get("geometry"), MAX_VOLUMES, "volumes", index):
            unit = _unit(vol.get("uomDimensions"), index)
            proj = vol.get("horizontalProjection")
            if isinstance(proj, dict) and str(proj.get("type")) == "Circle":
                area = _circle(proj.get("center"), proj.get("radius"), unit, index)
                _charge(area, index, budget)
            else:
                area = _polygon(proj, index, budget)
            band = _band(vol.get("lowerLimit"), vol.get("lowerVerticalReference"), vol.get("upperLimit"),
                         vol.get("upperVerticalReference"), unit, index)
            volumes.append((area, band))
        if not volumes:
            raise _bad(f"Zone {index} has no geometry.", zone=index)
        zones = _expand(props=item, volumes=volumes, periods=_periods(item, "applicability", index),
                        restriction=_restriction(item.get("restriction")), index=index, source="ed269", own=None,
                        total=total, budget=budget)
        out += [(z, item) for z in zones]
    return out


def parse_ed269(doc: Any) -> list[UserZone]:
    """Zones of an ED-269 JSON document (``{"features": [zone, …]}``).

    A zone with several volumes or applicability periods becomes one :class:`UserZone` each.
    Raises ``invalid_params`` (with the zone's index) or ``too_complex``.
    """
    return [z for z, _ in _ed269_pairs(doc)]


def _ed318_volumes(geom: Any, props: Mapping[str, Any], index: int, budget: list[int]) -> list[tuple[BaseGeometry, tuple[float | None, float | None, VerticalReference]]]:
    if not isinstance(geom, dict):
        raise _bad(f"Zone {index} has no geometry.", zone=index)
    parts = geom.get("geometries") if geom.get("type") == "GeometryCollection" else [geom]
    if not isinstance(parts, list) or not parts or len(parts) > MAX_VOLUMES:
        raise _bad(f"Zone {index} needs 1 to {MAX_VOLUMES} geometries.", zone=index)
    out = []
    for part in parts:
        if not isinstance(part, dict):
            raise _bad(f"Zone {index}: every geometry must be an object.", zone=index)
        layer = part.get("layer") if isinstance(part.get("layer"), dict) else geom.get("layer")
        # Some publishers put the limits in the properties instead: flat, or in a verticalLayer (ENAIRE).
        if not isinstance(layer, dict):
            layer = props.get("verticalLayer")
        source = layer if isinstance(layer, dict) else props
        unit = _unit(source.get("uom") or source.get("UOM") or source.get("uomDimensions"), index)
        extent = part.get("extent")
        if part.get("type") == "Point" and isinstance(extent, dict) and str(extent.get("subType")) == "Circle":
            area = _circle(part.get("coordinates"), extent.get("radius"), unit, index)
            _charge(area, index, budget)
        else:
            area = _polygon(part, index, budget)
        band = _band(source.get("lower"), source.get("lowerReference"), source.get("upper"),
                     source.get("upperReference"), unit, index)
        out.append((area, band))
    return out


def _own(props: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """What :func:`to_ed318` kept in ``extendedProperties.airbrief``, when it is well formed."""
    ext = props.get("extendedProperties")
    own = ext.get("airbrief") if isinstance(ext, dict) else None
    if not isinstance(own, dict) or own.get("zone") not in ("red", "yellow", "green"):
        return None
    if not all(own.get(k) is None or isinstance(own.get(k), str) for k in ("name", "note", "authority", "reason")):
        return None
    ceiling = own.get("ceiling_agl_m")
    if ceiling is not None and (isinstance(ceiling, bool) or not isinstance(ceiling, int | float)):
        return None
    if own.get("restriction") is not None and _restriction(own.get("restriction")) is None:
        return None
    if own.get("source_format", "user") not in ("user", "ed269", "ed318"):
        return None
    return own


def _ed318_pairs(doc: Any) -> list[tuple[UserZone, Mapping[str, Any]]]:
    out: list[tuple[UserZone, Mapping[str, Any]]] = []
    budget, total = [0], [0]
    for index, feat in enumerate(_features(doc, "ed318")):
        if not isinstance(feat, dict) or not isinstance(feat.get("properties"), dict):
            raise _bad(f"Zone {index} is not a GeoJSON Feature with properties.", zone=index)
        props = _flat(feat["properties"])
        restriction = _restriction(props.get("type")) or _restriction(props.get("restriction"))
        key = "limitedApplicability" if props.get("limitedApplicability") is not None else "applicability"
        zones = _expand(props=props, volumes=_ed318_volumes(feat.get("geometry"), props, index, budget),
                        periods=_periods(props, key, index), restriction=restriction, index=index,
                        source="ed318", own=_own(props), total=total, budget=budget)
        out += [(z, props) for z in zones]
    return out


def parse_ed318(doc: Any) -> list[UserZone]:
    """Zones of an ED-318 GeoJSON FeatureCollection (restriction in ``type``, or ``restriction``
    as in ED-269; validity in ``limitedApplicability`` or ``applicability``).

    Raises ``invalid_params`` (with the zone's index) or ``too_complex``.
    """
    return [z for z, _ in _ed318_pairs(doc)]


def detect_geozone_format(doc: Any) -> GeozoneFormat | None:
    """``ed269``, ``ed318`` or None (not a zone file), from the first few features."""
    doc = _unwrap(doc)
    if not isinstance(doc, dict) or not isinstance(doc.get("features"), list):
        return None
    sample = [f for f in doc["features"][:20] if isinstance(f, dict)]
    if not sample:
        return None
    if doc.get("type") == "FeatureCollection":
        for feat in sample:
            props = feat.get("properties")
            geom = feat.get("geometry")
            if not isinstance(props, dict):
                continue
            props = _flat(props)
            if "zone" in props and "source_format" in props:
                continue  # AirBrief's own export of zones: flat properties, read by zone_from_properties
            if isinstance(geom, dict) and ("layer" in geom or "extent" in geom):
                return "ed318"
            if "identifier" in props and (_restriction(props.get("type")) or _restriction(props.get("restriction"))):
                return "ed318"
        return None
    if doc.get("type") is None and any(isinstance(f.get("geometry"), list) and "restriction" in f for f in sample):
        return "ed269"
    return None


def parse_geozones(doc: Any) -> tuple[GeozoneFormat, list[UserZone]]:
    """Detect and parse an ED-269 or ED-318 document (``invalid_params`` when it is neither)."""
    fmt = detect_geozone_format(doc)
    if fmt is None:
        raise _bad("This is not an ED-269 JSON or ED-318 GeoJSON zone file.")
    return fmt, parse_ed269(doc) if fmt == "ed269" else parse_ed318(doc)


def zone_properties(zone: UserZone) -> dict[str, Any]:
    """Flat feature properties of a zone (what an imported zone file stores per feature)."""
    data = zone.model_dump(mode="json", exclude={"geometry"})
    if not data["schedule"]:
        data.pop("schedule")
    return {k: v for k, v in data.items() if v is not None}


def geozone_features(doc: Any, fmt: GeozoneFormat) -> list[dict[str, Any]]:
    """GeoJSON Features of a zone file for ingest: circles as polygons, limits and validity as
    flat properties, beside the file's own plain (non-list, non-object) properties."""
    pairs = _ed269_pairs(doc) if fmt == "ed269" else _ed318_pairs(doc)
    features = []
    for i, (zone, original) in enumerate(pairs):
        props = {k: v for k, v in original.items() if k != "geometry" and not isinstance(v, dict | list)}
        props.update(zone_properties(zone))
        features.append({"type": "Feature", "id": i, "geometry": zone.geometry, "properties": props})
    return features


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


_ED318_SPELLING = {"REQ_AUTHORISATION": "REQ_AUTHORIZATION", "U-SPACE": "USPACE"}


def _code_for(zone: UserZone) -> str:
    if zone.restriction:
        return _ED318_SPELLING.get(zone.restriction, zone.restriction)
    if zone.zone == "red":
        return "PROHIBITED"
    if zone.zone == "green":
        return "NO_RESTRICTION"
    return "REQ_AUTHORIZATION" if zone.ceiling_agl_m is None else "CONDITIONAL"


def to_ed318(zones: Sequence[UserZone], *, name: str = "AirBrief zones", limits: Ed318Limits = "geometry") -> dict[str, Any]:
    """An ED-318 GeoJSON FeatureCollection of ``zones`` (``parse_ed318`` reads it back unchanged).

    Limits go to ``geometry.layer`` in metres, or with ``limits="properties"`` to the feature
    properties (``lower``, ``lowerReference``, ``upper``, ``upperReference``, ``uom``); a zone
    from the ground has ``lower`` 0 AGL and an unlimited one ``upper`` 99999. What ED-318 cannot
    say exactly (AirBrief's zone kind and ceiling, the restriction as given, the full name, note,
    authority and reason) is also kept in ``extendedProperties.airbrief``, which this module
    reads first.
    """
    features = []
    for i, zone in enumerate(zones):
        layer: dict[str, Any] = {
            "upper": zone.upper_limit_m if zone.upper_limit_m is not None else UNLIMITED,
            "upperReference": zone.vertical_reference,
            "lower": zone.lower_limit_m if zone.lower_limit_m is not None else 0,
            "lowerReference": zone.vertical_reference if zone.lower_limit_m is not None else "AGL",
            "uom": "M",
        }
        props: dict[str, Any] = {
            "identifier": f"AA{i + 1:05d}",
            "name": zone.name,
            "type": _code_for(zone),
            "variant": "COMMON",
            "message": zone.note,
            "extendedProperties": {"airbrief": {
                "zone": zone.zone,
                "ceiling_agl_m": zone.ceiling_agl_m,
                "restriction": zone.restriction,
                "source_format": zone.source_format,
                "name": zone.name,
                "note": zone.note,
                "authority": zone.authority,
                "reason": zone.reason,
            }},
        }
        if zone.reason:
            codes = [c.strip() for c in zone.reason.split(",")]
            if all(c in REASON_CODES for c in codes):
                props["reason"] = codes
            else:
                props["reason"] = ["OTHER"]
                props["otherReasonInfo"] = zone.reason
        if zone.authority:
            props["zoneAuthority"] = [{"name": zone.authority, "purpose": "AUTHORIZATION"}]
        if zone.valid_from or zone.valid_to or zone.schedule:
            period: dict[str, Any] = {}
            if zone.valid_from:
                period["startDateTime"] = _iso(zone.valid_from)
            if zone.valid_to:
                period["endDateTime"] = _iso(zone.valid_to)
            if zone.schedule:
                period["schedule"] = [
                    {"day": list(s.days), "startTime": f"{s.start}:00Z", "endTime": f"{s.end}:00Z"} for s in zone.schedule
                ]
            props["limitedApplicability"] = [period]
        geometry = dict(zone.geometry)
        if limits == "properties":
            props.update(layer)
        else:
            geometry["layer"] = layer
        features.append({"type": "Feature", "id": props["identifier"], "geometry": geometry, "properties": props})
    return {"type": "FeatureCollection", "name": name, "features": features}


def _find(props: Mapping[str, Any], field: str | None, candidates: Sequence[str]) -> Any:
    if field:
        return props.get(field)
    lowered = {str(k).lower(): v for k, v in props.items()}
    for key in candidates:
        if lowered.get(key) not in (None, ""):
            return lowered[key]
    return None


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def zone_from_properties(
    props: Mapping[str, Any],
    geometry: dict[str, Any],
    *,
    name: str,
    layer_mapping: ZoneLayerMapping | None = None,
) -> UserZone:
    """A zone from a dataset feature's (or a pack zone's) properties, read as ``layer_mapping`` says.

    The flat properties an imported ED-269/ED-318 file stores (limits, reference, schedule,
    restriction, authority, reason) are read too. Raises ``invalid_params`` when a value is out of
    range (an inverted validity period, a ceiling above 10 km).
    """
    m = layer_mapping or ZoneLayerMapping()
    raw = _find(props, m.zone_field, _ZONE_FIELDS)
    text = str(raw).strip() if raw is not None else ""
    zone: ZoneKind = _COLOURS.get(text.lower()) or (RESTRICTIONS[text.upper()][0] if text.upper() in RESTRICTIONS else m.default_zone)
    restriction = _restriction(props.get("restriction")) or _restriction(text)
    ceiling = _number(_find(props, m.ceiling_field, _CEILING_FIELDS))
    schedule: list[ZoneSchedule] = []
    raw_schedule = props.get("schedule")
    if isinstance(raw_schedule, list):
        try:
            schedule = [ZoneSchedule.model_validate(s) for s in raw_schedule[:MAX_SCHEDULES]]
        except ValidationError:
            schedule = []  # unreadable: the zone counts as active all day, never less
    reference = str(props.get("vertical_reference") or "AGL").upper()
    source_format = props.get("source_format")
    try:
        return UserZone(
            name=name[:120] or "Zone",
            zone=zone,
            geometry=geometry,
            ceiling_agl_m=ceiling,
            note=_text(props.get("note") or props.get("message"), 2000),
            valid_from=_when(_find(props, m.valid_from_field, ("valid_from",))),
            valid_to=_when(_find(props, m.valid_to_field, ("valid_to",))),
            schedule=schedule,
            lower_limit_m=_number(props.get("lower_limit_m")),
            upper_limit_m=_number(props.get("upper_limit_m")),
            vertical_reference=reference if reference in ("AGL", "AMSL", "WGS84") else "AGL",  # type: ignore[arg-type]
            restriction=restriction,
            source_format=source_format if source_format in ("ed269", "ed318") else "user",
            authority=_text(props.get("authority"), 300),
            reason=_text(props.get("reason"), 300),
        )
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False)
        field = ".".join(str(p) for p in errors[0].get("loc", ())) if errors else ""
        raise _bad(f"Zone '{name[:60]}' has an invalid {field or 'value'}: {errors[0]['msg'] if errors else ''}".rstrip(": "),
                   field=field) from exc


def schedule_intervals(zone: UserZone, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """The UTC intervals in which ``zone`` is active between ``start`` and ``end``."""
    lo = max(start, zone.valid_from) if zone.valid_from else start
    hi = min(end, zone.valid_to) if zone.valid_to else end
    if hi <= lo:
        return []
    if not zone.schedule:
        return [(lo, hi)]
    out = []
    day = datetime.combine(lo.date() - timedelta(days=1), time(0, tzinfo=UTC))
    while day < hi:
        weekday = _DAYS[day.weekday()]
        for s in zone.schedule:
            if "ANY" not in s.days and weekday not in s.days:
                continue
            a = day + timedelta(minutes=_minutes(s.start))
            b = day + timedelta(minutes=_minutes(s.end))
            if b <= a:
                b += timedelta(days=1)
            if a < hi and lo < b:
                out.append((max(a, lo), min(b, hi)))
        day += timedelta(days=1)
    return out


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)
