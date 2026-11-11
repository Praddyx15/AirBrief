"""Operation evidence brief: several completed analyses and the operation in one document.

:func:`build_brief` turns analysis envelopes (:class:`~airbrief_core.models.brief.BriefSource`)
and the operation (:class:`~airbrief_core.models.brief.OperationMeta`) into a
:class:`~airbrief_core.models.brief.Brief`:

* **Sections** — always every topic of :data:`~airbrief_core.models.brief.SECTION_ORDER`, in that
  order: operation, airspace, heights and terrain, obstacles, ground risk, environment, line of
  sight, survey and spray effort, flight audit, drone show, eVTOL. Each analysis contributes a
  :class:`~airbrief_core.models.brief.BriefPart` (key numbers, the main tables cut to
  :data:`BRIEF_TABLE_ROWS` rows, notes, disclaimer) to the sections it informs; a topic no analysis
  covers is ``not_assessed`` with the reason, never left out or blank. A per-feature run gives
  its kind's section the count of each result and a feature table. Data gaps inside an analysis
  (no data pack or a geometry beyond it, no DEM or DEM gaps, a 2D track, no power or obstacle
  layer for interference, population outside the pack) make its part ``partial`` with a banner.
* **Cross-checks** (``warnings``) — analyses from several datasets or of areas that do not overlap,
  an aircraft larger or faster than a ground-risk analysis assumed, an environment check for
  another day than the planned window, several runs of one kind, synthetic data, zone or airspace
  parameters that differ from the defaults (also a banner in the analysis' part), a data pack
  whose content changed after an analysis read it.
* **Provenance** — AirBrief and schema versions; every data pack with its version, creation time,
  synthetic flag, per-layer source, licence and SHA-256 and its ``content_sha256`` (from its
  manifest); per analysis the SHA-256 of the canonical parameters and result JSON
  (:func:`canonical_json`), of the uploaded file it read and of the pack content it read; the
  start and finish times; the key that signs the bundle, if any; and ``brief_sha256``, the SHA-256
  of the canonical brief without ``generated_utc``, so identical analyses and operation give
  identical hashes.

Values are SI internally; texts are metric with a narrow no-break space (U+202F) between number and
unit, like the web app's ``lib/format.ts``, which converts them for imperial readers. Nothing here
is a go/no-go: the brief is evidence for planning, not an authorisation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from . import __version__
from .analyses import REGISTRY
from .batch import is_batch_result
from .data.datapack import LAYER_KEYS, DataPackManifest
from .models.analyses import ANALYSIS_KINDS, PARAMS_MODELS, AirspaceParams
from .models.brief import (
    EVIDENCE_NOTICE,
    MAX_BRIEF_ANALYSES,
    SECTION_ORDER,
    SECTION_TITLES,
    SYNTHETIC_WATERMARK,
    UNSIGNED_NOTE,
    AircraftSummary,
    Brief,
    BriefAnalysisProvenance,
    BriefDatasetRef,
    BriefFact,
    BriefPart,
    BriefProvenance,
    BriefSection,
    BriefSource,
    BriefTable,
    FactTone,
    OperationMeta,
    OperationSummary,
    PackLayerProvenance,
    PackProvenance,
    signed_note,
)
from .models.common import SCHEMA_VERSION, Issue, Severity, Verdict
from .models.ground_risk_volume import ASSEMBLY_BAND_LABELS
from .models.presets import SORA_EVIDENCE_LABEL, UAS_PRESETS, ZONE_PROFILE_LABELS, ZONE_RULE_PROFILES

__all__ = [
    "BRIEF_TABLE_ROWS",
    "build_brief",
    "canonical_json",
    "canonical_sha256",
    "kind_title",
    "pack_reading_kinds",
    "reads_synthetic_data",
    "sha256_hex",
]

BRIEF_TABLE_ROWS = 8
"""Rows of each table shown in the brief; the bundle's CSV tables hold every row."""

NNBSP = " "
MINUS = "−"

_VERDICT_ORDER = {Verdict.PASS: 0, Verdict.CAUTION: 1, Verdict.FAIL: 2}
_VERDICT_LABEL = {Verdict.PASS: "Pass", Verdict.CAUTION: "Caution", Verdict.FAIL: "Fail"}

ZONE_LABEL = {"green": "Green zone", "yellow": "Yellow zone", "red": "Red zone"}
TRACK_FORMAT_LABEL = {"gpx": "GPX track", "kml": "KML gx:Track", "csv": "CSV track", "other": "Position track"}
GRB_RULE_LABEL = {"one_to_one": "1:1 rule", "ballistic": "Ballistic"}
AUDIENCE_RULE_LABEL = {"fixed": "Fixed", "one_to_one": "1:1 height", "ballistic": "Ballistic"}
DAYLIGHT_SEGMENT_LABEL = {"night": "Night", "civil_twilight": "Civil twilight", "day": "Day"}
DAYLIGHT_STATUS_LABEL = {"allowed": "Within the profile", "lighting": "Lighting required", "outside": "Outside the profile"}
EMI_KIND_LABEL = {
    "power_line": "Power line",
    "minor_line": "Minor power line",
    "cable": "Power cable",
    "substation": "Substation",
    "plant": "Power plant",
    "comm_tower": "Communication tower",
    "mast": "Mast",
    "pylon": "Pylon",
}
ROLE_LABEL = {"pilot": "Remote pilot", "observer": "Visual observer", "gcs": "Ground station"}
VIOLATION_LABEL = {
    "speed_h": "Horizontal speed",
    "climb": "Climb rate",
    "descent": "Descent rate",
    "acceleration": "Acceleration",
    "geofence": "Outside show box",
    "audience": "Inside audience buffer",
    "ceiling": "Above ceiling",
    "endurance": "Airborne longer than endurance",
    "low_altitude_motion": "Moving below navigation height",
}
ALTITUDE_REFERENCE_TEXT = {
    "terrain_calibrated": "Terrain-corrected and calibrated at take-off (the first fix is assumed on the ground).",
    "terrain_raw": "Terrain-corrected without calibration (GPS altitude taken as AMSL).",
    "takeoff_relative": "Height above take-off (no terrain data for this area).",
    "recorded_agl": "Heights as recorded above ground (the track's altitude mode).",
}
HEIGHT_REFERENCE_LABEL = {"agl": "above ground (AGL)", "takeoff": "above take-off", "amsl": "above mean sea level (AMSL)"}


def canonical_json(value: Any) -> bytes:
    """``value`` as canonical JSON bytes: keys sorted, no whitespace, UTF-8, no NaN/Infinity.

    Floats are written with Python's shortest round-trip repr, so a JSON file re-read and
    re-serialised this way hashes to the same SHA-256.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_hex(canonical_json(value))


def kind_title(kind: str) -> str:
    spec = REGISTRY.get(kind)
    return spec.title if spec is not None else kind.replace("_", " ").capitalize()


def pack_reading_kinds() -> frozenset[str]:
    """Kinds that read data-pack layers (a required or optional layer in the registry)."""
    return frozenset(kind for kind, spec in REGISTRY.items() if spec.requires or spec.optional)


def reads_synthetic_data(kind: str, datapack_synthetic: bool) -> bool:
    """True when an analysis of ``kind`` was computed with a synthetic pack whose data it reads."""
    return bool(datapack_synthetic) and kind in pack_reading_kinds()


def _kind_rank(kind: str) -> int:
    return ANALYSIS_KINDS.index(kind) if kind in ANALYSIS_KINDS else len(ANALYSIS_KINDS)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def _auto_decimals(abs_value: float) -> int:
    if abs_value == 0:
        return 0
    if abs_value < 1:
        return 3
    if abs_value < 10:
        return 2
    if abs_value < 100:
        return 1
    return 0


def fmt_number(value: Any, decimals: int | None = None, max_decimals: int | None = None) -> str:
    """A number with thousands separators; ``decimals`` fixed, else at most ``max_decimals``
    (default: by magnitude). Non-numbers give an em dash."""
    v = _finite(value)
    if v is None:
        return "—"
    if decimals is not None:
        text = f"{v:,.{decimals}f}"
    else:
        places = _auto_decimals(abs(v)) if max_decimals is None else max_decimals
        text = f"{v:,.{places}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
    if text.startswith("-") and not any(c in "123456789" for c in text):
        text = text[1:]  # a negative value that rounds to zero
    return text.replace("-", MINUS)


def _with_unit(number: str, unit: str) -> str:
    return number if number == "—" else f"{number}{NNBSP}{unit}"


def fmt_length(m: Any, max_decimals: int | None = None) -> str:
    v = _finite(m)
    if v is None:
        return "—"
    if abs(v) < 1000:
        return _with_unit(fmt_number(v, max_decimals=max_decimals), "m")
    return _with_unit(fmt_number(v / 1000.0, max_decimals=max_decimals), "km")


def fmt_height(m: Any, max_decimals: int = 0) -> str:
    return _with_unit(fmt_number(m, max_decimals=max_decimals), "m")


def fmt_area(m2: Any) -> str:
    v = _finite(m2)
    if v is None:
        return "—"
    if abs(v) < 10_000:
        return _with_unit(fmt_number(v), "m²")
    if abs(v) < 1_000_000:
        return _with_unit(fmt_number(v / 10_000.0), "ha")
    return _with_unit(fmt_number(v / 1_000_000.0), "km²")


def fmt_speed(ms: Any) -> str:
    return _with_unit(fmt_number(ms, max_decimals=1), "m/s")


def fmt_pct(value: Any, max_decimals: int | None = None) -> str:
    v = _finite(value)
    if v is None:
        return "—"
    a = abs(v)
    places = max_decimals if max_decimals is not None else (0 if a == 0 else 3 if a < 0.1 else 2 if a < 10 else 1 if a < 100 else 0)
    return f"{fmt_number(v, max_decimals=places)}%"


def fmt_duration(seconds: Any) -> str:
    v = _finite(seconds)
    if v is None:
        return "—"
    sign = MINUS if v < 0 else ""
    s = abs(v)
    if s < 60:
        return f"{sign}{fmt_number(s, max_decimals=1 if s < 10 else 0)}{NNBSP}s"
    total = int(round(s))
    h, rest = divmod(total, 3600)
    m, sec = divmod(rest, 60)
    if h == 0:
        return f"{sign}{m}{NNBSP}min {sec:02d}{NNBSP}s"
    return f"{sign}{h}{NNBSP}h {m:02d}{NNBSP}min"


def fmt_clock(seconds: Any) -> str:
    v = _finite(seconds)
    if v is None:
        return "—"
    s = max(0.0, v)
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    if h >= 1:
        return f"{int(h)}:{int(m):02d}:{int(sec):02d}"
    return f"{int(m)}:{sec:04.1f}"


def fmt_coord(lon: Any, lat: Any) -> str:
    """Latitude then longitude, 5 decimals, the hemisphere after a narrow no-break space (as
    ``formatCoord`` of the web app, so a brief merged in the static demo reads the same)."""
    x, y = _finite(lon), _finite(lat)
    if x is None or y is None:
        return "—"
    return f"{abs(y):.5f}°{NNBSP}{'N' if y >= 0 else 'S'}, {abs(x):.5f}°{NNBSP}{'E' if x >= 0 else 'W'}"


def fmt_bearing(deg: Any) -> str:
    v = _finite(deg)
    if v is None:
        return "—"
    return f"{int(round(v)) % 360:03d}°"


def fmt_density(value: Any) -> str:
    return _with_unit(fmt_number(value, decimals=0), "people/km²")


def fmt_offset(minutes: int) -> str:
    sign = MINUS if minutes < 0 else "+"
    a = abs(int(minutes))
    return f"UTC{sign}{a // 60:02d}:{a % 60:02d}"


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def fmt_utc(value: Any) -> str:
    stamp = _parse_utc(value)
    return "—" if stamp is None else stamp.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def fmt_local(value: Any, offset_min: int) -> str:
    stamp = _parse_utc(value)
    if stamp is None:
        return "—"
    local = stamp.astimezone(UTC) + timedelta(minutes=offset_min)
    return f"{local.strftime('%Y-%m-%d %H:%M')} ({fmt_offset(offset_min)})"


def _cap(text: str) -> str:
    """``text`` with its first letter upper-cased (the rest unchanged, unlike ``str.capitalize``)."""
    return text[:1].upper() + text[1:]


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{fmt_number(count, decimals=0)} {one if count == 1 else (many or one + 's')}"


def _fact(
    label: str,
    text: str,
    value: Any = None,
    unit: str | None = None,
    *,
    note: str | None = None,
    tone: FactTone = "neutral",
    tone_label: str | None = None,
) -> BriefFact:
    if isinstance(value, float) and not math.isfinite(value):
        value = None
    return BriefFact(label=label, text=text, value=value, unit=unit, note=note, tone=tone, tone_label=tone_label)


def _table(caption: str, columns: Sequence[tuple[str, bool]], rows: Sequence[Sequence[str]]) -> BriefTable:
    return BriefTable(
        caption=caption,
        columns=[c for c, _ in columns],
        numeric=[n for _, n in columns],
        rows=[list(r) for r in rows[:BRIEF_TABLE_ROWS]],
        total=len(rows),
    )


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _issues(result: Mapping[str, Any]) -> list[Issue]:
    out: list[Issue] = []
    for raw in _dicts(result.get("issues")):
        try:
            out.append(Issue.model_validate(raw))
        except ValueError:
            continue
    return out


def _issue_message(result: Mapping[str, Any], code: str) -> str | None:
    for issue in _dicts(result.get("issues")):
        if issue.get("code") == code and isinstance(issue.get("message"), str):
            return str(issue["message"])
    return None


def _verdict(value: Any) -> Verdict | None:
    try:
        return Verdict(value) if value is not None else None
    except ValueError:
        return None


def _with_defaults(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """``params`` validated by the kind's model (defaults filled in), or as given when they no
    longer validate (stored by an older version)."""
    spec = REGISTRY.get(kind)
    if spec is None:
        return params
    try:
        return spec.params_model.model_validate(params).model_dump(mode="json")
    except ValueError:
        return params


# Airspace settings that weaken the evidence when moved this way from their default (-1: lower).
_AIRSPACE_LOOSER: dict[str, tuple[str, int]] = {
    "obstacle_search_m": ("obstacle search radius", -1),
    "vertical_clearance_m": ("obstacle clearance", -1),
    "min_agl_m": ("minimum height above ground", -1),
    "profile_spacing_m": ("terrain profile spacing", 1),
}
# Cell caps that cut or coarsen a grid when set below their default (fewer cells: a zone or a
# density peak can be missed), and show settings that let more through when raised.
_CELL_CAPS: dict[str, tuple[str, str]] = {
    "airspace": ("max_grid_cells", "flyability grid cells"),
    "ground_risk": ("max_cells", "population cells"),
}
_SHOW_LOOSER: dict[str, tuple[str, str]] = {
    "dynamics_window_s": ("dynamics averaged over", "s"),
    "airborne_threshold_m": ("drones counted as airborne only above", "m"),
}


def _looser_settings(kind: str, p: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    cap = _CELL_CAPS.get(kind)
    if cap is not None:
        key, label = cap
        value, default = _finite(p.get(key)), PARAMS_MODELS[kind].model_fields[key].default
        if value is not None and value < default:
            out.append(f"at most {fmt_number(value, decimals=0)} {label} instead of {fmt_number(default, decimals=0)} ({key})")
    if kind == "show":
        for key, (label, unit) in _SHOW_LOOSER.items():
            value, default = _finite(p.get(key)), PARAMS_MODELS["show"].model_fields[key].default
            if value is not None and value > default + 1e-9:
                out.append(f"{label} {_with_unit(fmt_number(value), unit)} instead of {_with_unit(fmt_number(default), unit)} ({key})")
    return out


# Rules equal to a named profile (DGCA 2021, conservative) are not custom.
_PRESET_RULES_JSON = frozenset(canonical_json(r.model_dump(mode="json")) for r in ZONE_RULE_PROFILES.values())


def _non_default_params(kind: str, p: Mapping[str, Any]) -> list[str]:
    """Safety-relevant zone and airspace parameters that differ from the defaults, each named with
    its parameter, e.g. ``"data-pack zones left out (include_datapack_zones: false)"``."""
    out: list[str] = []
    if p.get("include_datapack_zones") is False:
        out.append("data-pack zones left out (include_datapack_zones: false)")
    rules = p.get("zone_rules")
    if isinstance(rules, dict) and canonical_json(rules) not in _PRESET_RULES_JSON:
        out.append("custom aerodrome zone rules instead of the defaults (zone_rules)")
    user_zones = _dicts(p.get("user_zones"))
    if user_zones:
        out.append(f"{_plural(len(user_zones), 'zone')} drawn by the user (user_zones)")
    out += _looser_settings(kind, p)
    # unverified_band "exclude" is stricter than the default -> named in the part, not flagged
    if p.get("band_ceiling") == "permission":
        out.append("flying above a band's free-flight ceiling counted as permission required, not as an exceedance "
                   "(band_ceiling: permission)")
    if kind != "airspace":
        return out
    dataset_zones = _dicts(p.get("dataset_zones"))
    if p.get("zone_dataset_ids"):
        out.append(f"{_plural(len(dataset_zones), 'zone')} from {_plural(len(p['zone_dataset_ids']), 'dataset')} "
                   "chosen by the user (zone_dataset_ids)")
    off = sorted(str(k) for k, v in (p.get("aerodrome_operational") or {}).items() if v is False)
    if off:
        out.append(f"{', '.join(off)} marked not operational, so {'its' if len(off) == 1 else 'their'} bands were "
                   "not applied (aerodrome_operational)")
    timed = [z for z in [*user_zones, *dataset_zones] if z.get("valid_from") or z.get("valid_to") or z.get("schedule")]
    if timed and p.get("planned_start_utc"):
        out.append(f"a planned window that may leave out {_plural(len(timed), 'time-limited zone')} "
                   "(planned_start_utc, planned_end_utc)")
    for key, (label, way) in _AIRSPACE_LOOSER.items():
        value, default = _finite(p.get(key)), AirspaceParams.model_fields[key].default
        if value is not None and (value - default) * way > 1e-9:
            out.append(f"{label} {fmt_length(value)} instead of {fmt_length(default)} ({key})")
    if _finite(p.get("takeoff_elevation_m")) is not None:
        out.append(f"take-off ground elevation entered by hand as {fmt_height(p['takeoff_elevation_m'])} "
                   "instead of read from the DEM (takeoff_elevation_m)")
    return out


def _non_default_banner(items: list[str]) -> str:
    return (f"Run with non-default safety parameters: {'; '.join(items)}. Zones, obstacles and conflicts "
            "may differ from a run with the defaults.")


class _Source:
    def __init__(self, source: BriefSource) -> None:
        self.raw = source
        self.id = source.analysis_id
        env = source.envelope
        self.kind = str(env.get("kind"))
        # As stored (what the provenance hashes), and with the module's defaults filled in (what
        # the analysis actually used: requests store only the values they gave).
        self.stored_params: dict[str, Any] = _dict(env.get("params"))
        self.params: dict[str, Any] = _with_defaults(self.kind, self.stored_params)
        self.result: dict[str, Any] = _dict(env.get("result"))
        self.datapack: str | None = env.get("datapack") if isinstance(env.get("datapack"), str) else None
        self.synthetic = bool(env.get("datapack_synthetic"))
        self.datapack_sha256: str | None = env.get("datapack_sha256") if isinstance(env.get("datapack_sha256"), str) else None
        self.started = str(env.get("started_utc") or "")
        self.finished = str(env.get("finished_utc") or "")
        self.title = kind_title(self.kind)
        # Set by build_brief when the brief mixes synthetic and real data: each part then says
        # which it is (otherwise the brief's watermark and cross-check say it once).
        self.flag_synthetic = False
        self.non_default = _non_default_params(self.kind, self.params)

    def part(self, title: str | None = None, **kwargs: Any) -> BriefPart:
        banners: list[str] = list(kwargs.pop("banners", []))
        if self.flag_synthetic and reads_synthetic_data(self.kind, self.synthetic):
            banners.append(
                f"Computed with the synthetic demo data pack '{self.datapack}': terrain, population and "
                "obstacles are invented for testing."
            )
        return BriefPart(analysis_id=self.id, kind=self.kind, title=title or self.title, banners=banners, **kwargs)


Contribution = dict[str, BriefPart]


def _zone_rows(zones: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for z in zones:
        name = str(z.get("name") or "Zone") + (f" ({z['ident']})" if z.get("ident") else "")
        zone = ZONE_LABEL.get(str(z.get("zone")), str(z.get("zone"))) + (f" · {z['ring']}" if z.get("ring") else "")
        ceiling = "—" if z.get("ceiling_agl_m") is None else fmt_height(z.get("ceiling_agl_m"))
        distance = "—" if z.get("distance_km") is None else fmt_length(float(z["distance_km"]) * 1000.0)
        rows.append([name, zone, ceiling, "Required" if z.get("permission_required") else "No", distance])
    return rows


_ZONE_COLUMNS = [("Name", False), ("Zone", False), ("Ceiling AGL", True), ("Permission", False), ("Distance", True)]
_BAND_COLUMNS = [("Band", False), ("Aerodrome", False), ("Zone at the height flown", False), ("Ceiling AGL", True),
                 ("Highest AGL", True), ("Margin", True)]


def _band_rows(bands: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for b in bands:
        zone = ZONE_LABEL.get(str(b.get("zone")), str(b.get("zone")))
        if b.get("basis") == "verify_official_map":
            zone = "Verify on Digital Sky"
        margin = _finite(b.get("agl_margin_m"))
        rows.append([str(b.get("band") or "—").split(":")[0], str(b.get("ident") or b.get("name") or "—"), zone,
                     "—" if b.get("ceiling_agl_m") is None else fmt_height(b.get("ceiling_agl_m")),
                     "—" if b.get("worst_agl_m") is None else fmt_height(b.get("worst_agl_m")),
                     "—" if margin is None else ("+" if margin >= 0 else "−") + fmt_height(abs(margin))])
    return rows


def _segment_rows(segments: list[dict[str, Any]], peak_key: str | None = None) -> list[list[str]]:
    rows: list[list[str]] = []
    for s in segments:
        start = s.get("start_distance_m", s.get("start_m"))
        end = s.get("end_distance_m", s.get("end_m"))
        length = s.get("length_m")
        a, b = _finite(start), _finite(end)
        if length is None and a is not None and b is not None:
            length = b - a
        row = [fmt_length(start, 1), fmt_length(end, 1), fmt_length(length)]
        if peak_key is not None:
            row += [fmt_duration(s.get("duration_s")), fmt_length(s.get(peak_key))]
        rows.append(row)
    return rows


def _obstacle_name(o: Mapping[str, Any]) -> str:
    name = str(o.get("name") or o.get("id") or "Obstacle")
    if o.get("height_assumed") or o.get("assumed_height"):
        name += " (assumed height)"
    return name


def _obstacle_part(
    src: _Source, obstacles: list[dict[str, Any]], search_m: Any, subject: str, title: str | None = None
) -> BriefPart:
    conflicts = [o for o in obstacles if o.get("conflict")]
    tallest = max(obstacles, key=lambda o: _finite(o.get("height_m")) or 0.0, default=None)
    facts = [
        _fact(
            "Obstacles found",
            fmt_number(len(obstacles), decimals=0),
            len(obstacles),
            note=f"Within {fmt_length(search_m)} of the {subject}" if _finite(search_m) is not None else None,
        ),
        _fact(
            "Clearance conflicts",
            fmt_number(len(conflicts), decimals=0),
            len(conflicts),
            tone="danger" if conflicts else "ok",
            tone_label="Conflicts" if conflicts else "None",
        ),
    ]
    if tallest is not None:
        facts.append(
            _fact("Tallest obstacle", fmt_height(tallest.get("height_m")), _finite(tallest.get("height_m")), "m",
                  note=f"{_obstacle_name(tallest)}, {fmt_length(tallest.get('distance_m'))} away")
        )
    ordered = sorted(obstacles, key=lambda o: (not o.get("conflict"), _finite(o.get("distance_m")) or 0.0))
    rows = [
        [
            _obstacle_name(o),
            _humanize(str(o.get("kind"))) if o.get("kind") else "—",
            fmt_height(o.get("height_m")),
            "—" if o.get("clearance_m") is None else fmt_height(o.get("clearance_m")),
            fmt_length(o.get("distance_m")),
            "Conflict" if o.get("conflict") else "Clear",
        ]
        for o in ordered
    ]
    tables = [
        _table(
            "Obstacles (conflicts first)",
            [("Obstacle", False), ("Kind", False), ("Height", True), ("Clearance", True), ("Distance", True), ("Check", False)],
            rows,
        )
    ] if rows else []
    return src.part(title, facts=facts, tables=tables)


def _zone_rules_fact(r: Mapping[str, Any], note: str | None = None) -> BriefFact:
    # a result stored before profiles has none: it ran with the conservative rules
    profile = r.get("zone_profile")
    text = ZONE_PROFILE_LABELS.get(str(profile), str(profile)) if profile else "Conservative illustrative (before zone profiles)"
    return _fact("Zone rules", text, note=note)


def _gap_banner(r: Mapping[str, Any], fallback: str) -> str:
    """The "Airspace not assessed" banner of a result: its ``not_assessed`` issue, which already
    opens with "Airspace not assessed" / "Airspace only partly assessed", else the fallback."""
    message = _issue_message(r, "not_assessed")
    if message is not None and message.startswith("Airspace "):
        return message
    return "Airspace not assessed: " + (message or fallback)


def _airspace(src: _Source) -> Contribution:
    r, p = src.result, src.params
    complete = r.get("assessment_complete", True) is not False
    banners = []
    if not complete:
        banners.append(_gap_banner(r, "no data pack covers this area, so aerodrome zones were not checked."))
    if src.non_default:
        banners.append(_non_default_banner(src.non_default))
    alt = _finite(r.get("altitude_agl_m"))
    reference = str(p.get("height_reference") or "agl")
    # The planned value is in its reference; the ceiling is above ground: an above-take-off or
    # AMSL plan is compared with its highest height above ground (unknown without a DEM).
    highest_agl = alt if reference == "agl" else _finite(r.get("agl_max_m"))
    if r.get("permission_required"):
        ceiling = _fact("Free-flight ceiling", "Permission required", None, tone="warn", tone_label="Needs permission")
    elif r.get("effective_ceiling_agl_m") is None:
        ceiling = _fact("Free-flight ceiling", "Restricted", None, tone="danger", tone_label="No free-flight ceiling")
    else:
        eff = _finite(r.get("effective_ceiling_agl_m"))
        below = eff is not None and highest_agl is not None and eff < highest_agl - 1e-6
        ceiling = _fact("Free-flight ceiling", fmt_height(eff), eff, "m", note="Above ground",
                        tone="warn" if below else "neutral", tone_label="Below the planned height" if below else None)
    nearest = _dicts(r.get("nearest_aerodromes"))
    zones = _dicts(r.get("zones"))
    obstacles = _dicts(r.get("obstacles"))
    conflicts = [o for o in obstacles if o.get("conflict")]
    red = [z for z in zones if z.get("zone") == "red"]
    facts = [
        _fact("Planned height", fmt_height(alt), alt, "m", note=_cap(HEIGHT_REFERENCE_LABEL.get(reference, "above ground"))),
        ceiling,
        _fact(
            "Zones touched",
            fmt_number(len(zones), decimals=0),
            len(zones),
            tone="danger" if red else "warn" if zones else "neutral",
            tone_label=f"{_plural(len(red), 'red zone')}" if red else None,
        ),
        _fact(
            "Nearest aerodrome",
            fmt_length(float(nearest[0]["distance_km"]) * 1000.0) if nearest and _finite(nearest[0].get("distance_km")) is not None else "None in data",
            _finite(nearest[0].get("distance_km")) if nearest else None,
            "km" if nearest else None,
            note=(f"{nearest[0].get('name')} ({nearest[0].get('ident')}) at {fmt_bearing(nearest[0].get('bearing_deg'))}"
                  if nearest else "No aerodromes in the data pack"),
        ),
        _fact(
            "Obstacle conflicts",
            fmt_number(len(conflicts), decimals=0),
            len(conflicts),
            note=f"{_plural(len(obstacles), 'obstacle')} checked",
            tone="danger" if conflicts else "ok",
            tone_label="Conflicts" if conflicts else "None",
        ),
    ]
    facts.append(_zone_rules_fact(r, r.get("assessment_banner") if isinstance(r.get("assessment_banner"), str) else None))
    tables = []
    if zones:
        tables.append(_table("Zones touched by the planned flight", _ZONE_COLUMNS, _zone_rows(zones)))
    bands = _dicts(r.get("band_evidence"))
    if bands:
        tables.append(_table("Aerodrome bands reached", _BAND_COLUMNS, _band_rows(bands)))
    inactive = _dicts(r.get("inactive_zones"))
    if inactive:
        tables.append(_table("Zones that do not apply to this flight", [("Name", False), ("Zone", False), ("Why", False)],
                             [[str(z.get("name") or "Zone"), ZONE_LABEL.get(str(z.get("zone")), str(z.get("zone"))),
                               str(z.get("inactive_reason") or "—")] for z in inactive]))
    if nearest:
        tables.append(_table(
            "Nearest aerodromes",
            [("Aerodrome", False), ("Type", False), ("Distance", True), ("Bearing", True)],
            [[f"{a.get('name')} ({a.get('ident')})", str(a.get("type") or "—").replace("_", " "),
              fmt_length(float(a["distance_km"]) * 1000.0) if _finite(a.get("distance_km")) is not None else "—",
              fmt_bearing(a.get("bearing_deg"))] for a in nearest],
        ))
    out: Contribution = {
        "airspace": src.part(
            status="assessed" if complete else "partial",
            verdict=_verdict(r.get("verdict")),
            summary=r.get("summary") if isinstance(r.get("summary"), str) else None,
            banners=banners,
            facts=facts,
            tables=tables,
            notes=_issues(r),
            disclaimer=r.get("disclaimer") if isinstance(r.get("disclaimer"), str) else None,
        )
    }

    terrain = _dict(r.get("terrain"))
    hfacts: list[BriefFact] = []
    if terrain:
        hfacts += [
            _fact("Highest terrain", fmt_height(terrain.get("max_m")), _finite(terrain.get("max_m")), "m", note="Above mean sea level"),
            _fact("Lowest terrain", fmt_height(terrain.get("min_m")), _finite(terrain.get("min_m")), "m", note="Above mean sea level"),
            _fact("Relief", fmt_height(terrain.get("relief_m")), _finite(terrain.get("relief_m")), "m",
                  note=f"{fmt_number(terrain.get('samples'), decimals=0)} DEM samples"),
            _fact("Planned top (AMSL)", fmt_height(r.get("max_altitude_amsl_m")), _finite(r.get("max_altitude_amsl_m")), "m",
                  note="Highest terrain + planned height" if reference == "agl" else None),
        ]
    out["heights"] = _heights_part(src, hfacts, has_terrain=bool(terrain))
    out["obstacles"] = _obstacle_part(src, obstacles, p.get("obstacle_search_m"), "checked area")
    return out


def _heights_part(src: _Source, facts: list[BriefFact], *, has_terrain: bool, title: str | None = None) -> BriefPart:
    """The heights-and-terrain part of an analysis: its own terrain facts plus the height reference
    (above ground, take-off or sea level), the take-off point, the height above ground along the
    route and the stretches below the minimum or above the ceiling (airbrief_core/analyses/heights.py)."""
    r, p = src.result, src.params
    reference = str(
        r.get("height_reference") or p.get("height_reference") or r.get("cruise_reference") or p.get("cruise_reference") or "agl"
    )
    facts = list(facts)
    banners: list[str] = []
    assessed = r.get("heights_assessed") is not False
    if not assessed or not has_terrain:
        banners.append(
            "Heights not assessed: "
            + (_issue_message(r, "heights_not_assessed") or "no terrain model (DEM) covers this area, so heights above ground were not checked.")
        )
    dem = _dict(r.get("dem"))
    if dem:
        facts.append(_fact("Terrain model", str(dem.get("title") or dem.get("source") or "Data-pack DEM"),
                           note=str(dem.get("caveat")) if dem.get("caveat") else None))
    if reference != "agl":
        facts.append(_fact("Height reference", _cap(HEIGHT_REFERENCE_LABEL.get(reference, reference))))
    takeoff = _dict(r.get("takeoff"))
    if takeoff and takeoff.get("elevation_m") is not None:
        facts.append(_fact(
            "Take-off ground", fmt_height(takeoff.get("elevation_m")), _finite(takeoff.get("elevation_m")), "m",
            note=f"AMSL at {fmt_coord(takeoff.get('lon'), takeoff.get('lat'))} ({str(takeoff.get('source') or 'given').replace('_', ' ')})",
        ))
    if r.get("flight_level_amsl_m") is not None:
        facts.append(_fact("Flight level (AMSL)", fmt_height(r.get("flight_level_amsl_m")), _finite(r.get("flight_level_amsl_m")), "m"))
    agl_min, agl_max = _finite(r.get("agl_min_m")), _finite(r.get("agl_max_m"))
    if agl_min is not None and agl_max is not None and (reference != "agl" or abs(agl_max - agl_min) > 0.5):
        # One value when the range is flat (an above-take-off plan without terrain): not "120–120 m".
        text = (fmt_height(agl_max) if abs(agl_max - agl_min) <= 0.5
                else f"{fmt_number(agl_min, max_decimals=0)}–{fmt_height(agl_max)}")
        facts.append(_fact("Height above ground", text, agl_min, "m", note="Lowest to highest along the flight"))
    tables: list[BriefTable] = []
    for key, caption, label, limit_word in (
        ("below_min_agl", "Below the minimum height above ground", "Below the minimum height", "minimum"),
        ("above_ceiling", "Above the ceiling", "Above the ceiling", "ceiling"),
    ):
        segments = _dicts(r.get(key))
        if key not in r or (reference == "agl" and not segments):
            continue
        below = key == "below_min_agl"
        total = sum(_finite(s.get("length_m")) or 0.0 for s in segments)
        limit = (_finite(segments[0].get("limit_m")) if segments
                 else _finite(r.get("min_agl_m") if below else r.get("ceiling_agl_m", r.get("effective_ceiling_agl_m"))))
        if limit is None:
            note = f"{fmt_length(total)} in total" if segments else None
        else:
            note = f"{fmt_length(total)} in total; {limit_word} {fmt_height(limit)}" if segments else f"{_cap(limit_word)} {fmt_height(limit)}"
        if not segments and reference != "agl":
            # Stretches exist along routes only: an area (or point) plan is checked on its grid
            # cells and its range of heights above ground.
            cells = r.get("cells_below_min_agl" if below else "cells_above_ceiling")
            extreme = agl_min if below else agl_max
            if isinstance(cells, int) and not isinstance(cells, bool) and cells > 0:
                facts.append(_fact(label, _plural(cells, "grid cell"), cells, note=note, tone="warn", tone_label="Found"))
                continue
            if extreme is not None and limit is not None and (extreme < limit - 1e-6 if below else extreme > limit + 1e-6):
                facts.append(_fact(label, f"{'Lowest' if below else 'Highest'} {fmt_height(extreme)}", extreme, "m", note=note,
                                   tone="warn", tone_label="Found"))
                continue
        facts.append(_fact(label, _plural(len(segments), "stretch", "stretches"), len(segments), note=note,
                           tone="warn" if segments else "ok", tone_label="Found" if segments else "None"))
        if segments:
            rows = [[*row, fmt_height(s.get("peak_agl_m"))] for row, s in zip(_segment_rows(segments), segments, strict=True)]
            tables.append(_table(caption, [("From", True), ("To", True), ("Length", True), ("Height above ground", True)], rows))
    return src.part(title, status="assessed" if assessed and has_terrain else "partial", facts=facts, tables=tables, banners=banners)


def _ground_risk(src: _Source) -> Contribution:
    r, p = src.result, src.params
    volume = _dict(r.get("volume"))
    p95 = r.get("density_statistic") == "p95"
    igrc = r.get("igrc")
    igrc_value = int(igrc) if isinstance(igrc, int) and not isinstance(igrc, bool) else None
    tone: FactTone = "danger" if igrc_value is None or igrc_value >= 7 else "warn" if igrc_value >= 5 else "ok"
    facts = [
        _fact(
            "Intrinsic ground risk class",
            "Out of scope" if igrc_value is None else str(igrc_value),
            igrc_value,
            note=f"Band: {r.get('density_band')}" if r.get("in_scope") else "Outside the scope of the lookup table",
            tone=tone,
            tone_label="Beyond the table" if igrc_value is None else ("Small-UAS rule" if r.get("small_uas_rule") else f"Column {r.get('column')}"),
        ),
        _fact(
            f"Density ({'95th pct.' if p95 else 'max'})",
            fmt_density(r.get("p95_density_per_km2") if p95 else r.get("max_density_per_km2")),
            _finite(r.get("p95_density_per_km2") if p95 else r.get("max_density_per_km2")),
            "people/km²",
            note=f"Mean {fmt_number(r.get('mean_density_per_km2'), decimals=0)} /km²",
        ),
        _fact("People in footprint", fmt_number(r.get("population_in_footprint"), decimals=0),
              _finite(r.get("population_in_footprint")), note="Modelled population"),
        _fact("Footprint", fmt_area((_finite(r.get("footprint_area_km2")) or 0.0) * 1e6), _finite(r.get("footprint_area_km2")), "km²",
              note=(f"FG + CV + GRB: {fmt_length((_finite(volume.get('s_cv_m')) or 0) + (_finite(volume.get('grb_m')) or 0))} around the flight geography"
                    if volume else f"Buffer {fmt_length(r.get('buffer_m'))}")),
        _fact("Aircraft assumed", f"{fmt_length(p.get('max_dimension_m'))} · {fmt_speed(p.get('max_speed_ms'))}",
              note="Max characteristic dimension · max speed (the iGRC column)"),
    ]
    if volume:
        facts += [
            _fact("Flight geography height", fmt_height(volume.get("fg_height_m")), _finite(volume.get("fg_height_m")), "m", note="Above ground"),
            _fact("Contingency volume", fmt_length(volume.get("s_cv_m")), _finite(volume.get("s_cv_m")), "m",
                  note=f"Ceiling {fmt_height(volume.get('h_cv_m'))} AGL"),
            _fact("Ground risk buffer", fmt_length(volume.get("grb_m")), _finite(volume.get("grb_m")), "m",
                  note=GRB_RULE_LABEL.get(str(volume.get("grb_rule")), str(volume.get("grb_rule")))),
            _fact(
                "Adjacent area density",
                "Not computed" if r.get("adjacent_mean_density_per_km2") is None else fmt_density(r.get("adjacent_mean_density_per_km2")),
                _finite(r.get("adjacent_mean_density_per_km2")),
                "people/km²",
                note=f"Average out to {fmt_length((_finite(volume.get('adjacent_outer_km')) or 0) * 1000)}",
            ),
            _fact("Assemblies within 1 km", fmt_number(len(_dicts(r.get("assemblies"))), decimals=0), len(_dicts(r.get("assemblies"))),
                  note=("Only those entered: the data pack has no assemblies layer"
                        if _issue_message(r, "assemblies_layer_missing") is not None else None),
                  tone="warn" if _dicts(r.get("assemblies")) else "neutral"),
        ]
    tables: list[BriefTable] = []
    if volume:
        tables.append(_table(
            "Operational volume terms (SORA 2.5)",
            [("Term", False), ("Value", True), ("Formula (SI)", False)],
            [[f"{t.get('name')} · {t.get('label')}", fmt_length(t.get("value_m")), str(t.get("formula") or "")]
             for t in _dicts(volume.get("terms"))],
        ))
        assemblies = _dicts(r.get("assemblies"))
        if assemblies:
            tables.append(_table(
                "Outdoor assemblies within 1 km",
                [("Assembly", False), ("Size", False), ("Distance", True)],
                [[str(a.get("name")), str(a.get("band_label") or ASSEMBLY_BAND_LABELS.get(str(a.get("band")), "—")),
                  "Inside" if (_finite(a.get("distance_m")) or 0) <= 0 else fmt_length(a.get("distance_m"))] for a in assemblies],
            ))
    grid = _dict(r.get("density_grid"))
    hotspots = _dicts(grid.get("hotspots")) if grid else _dicts(r.get("hotspots"))
    if hotspots:
        tables.append(_table(
            "Density hotspots",
            [("Cell", False), ("Location", False), ("Density", True), ("Band", False)],
            [[str(h.get("id") or h.get("h3")), fmt_coord(h.get("lon"), h.get("lat")), fmt_density(h.get("density_per_km2")),
              str(h.get("band") or "—")] for h in hotspots],
        ))
    label = r.get("evidence_label") if isinstance(r.get("evidence_label"), str) else None
    # A footprint (or adjacent area) partly outside the pack: densities there are unknown, so the
    # highest density, and the class with it, may be higher (a data gap, as in airspace).
    banners = [
        f"Population only partly assessed: {issue['message']}"
        for issue in _dicts(r.get("issues"))
        if issue.get("code") == "partial_coverage" and isinstance(issue.get("message"), str)
    ]
    return {
        "ground_risk": src.part(
            status="partial" if banners else "assessed",
            banners=banners,
            summary=f"{label or SORA_EVIDENCE_LABEL}." if volume else "SORA-style footprint evidence (earlier method): not a GRC, ARC or SAIL.",
            facts=facts,
            tables=tables,
            notes=_issues(r),
            disclaimer=r.get("disclaimer") if isinstance(r.get("disclaimer"), str) else None,
        )
    }


def _environment(src: _Source) -> Contribution:
    r = src.result
    zone = str(r.get("utc_offset_label") or fmt_offset(int(r.get("utc_offset_min") or 0)))
    sun = _dict(r.get("sun"))
    daylight = _dict(r.get("daylight"))
    magnetic = _dict(r.get("magnetic"))

    def at(event: Any) -> str:
        e = _dict(event)
        if not e:
            return "None"
        day = int(e.get("day_offset") or 0)
        return f"{e.get('local')}{f' ({day:+d} d)' if day else ''} {zone}"

    facts = [
        _fact("Date", str(r.get("date") or "—"), note=f"Local times {zone}"),
        _fact("Sunrise", at(sun.get("sunrise")), note=f"Civil dawn {at(sun.get('civil_dawn'))}"),
        _fact("Sunset", at(sun.get("sunset")), note=f"Civil dusk {at(sun.get('civil_dusk'))}"),
    ]
    window = _dict(daylight.get("window"))
    if window:
        outside = _finite(daylight.get("minutes_outside")) or 0.0
        lighting = _finite(daylight.get("minutes_lighting")) or 0.0
        facts.append(_fact(
            "Planned window",
            f"{window.get('start_local')}–{window.get('end_local')} {zone}",
            note=f"{fmt_number(outside, decimals=0)} min outside the profile ({daylight.get('profile_label')})",
            tone="warn" if outside >= 0.5 else "info" if lighting >= 0.5 else "ok",
            tone_label="Outside the profile" if outside >= 0.5 else f"Lighting required for {fmt_number(lighting, decimals=0)} min" if lighting >= 0.5 else "Within the profile",
        ))
    if magnetic:
        dec = _finite(magnetic.get("declination_deg")) or 0.0
        side = "Along" if abs(dec) < 0.05 else "East of" if dec > 0 else "West of"
        caution = magnetic.get("blackout") or magnetic.get("caution") or magnetic.get("within_validity") is False
        facts.append(_fact(
            "Magnetic declination",
            f"{fmt_number(dec, decimals=1)}°",
            dec,
            "deg",
            note=f"{side} true north; inclination {fmt_number(magnetic.get('inclination_deg'), decimals=1)}° ({magnetic.get('model', 'WMM2025')})",
            tone="danger" if magnetic.get("blackout") else "warn" if caution else "neutral",
            tone_label=("WMM blackout zone" if magnetic.get("blackout") else "WMM caution zone" if magnetic.get("caution")
                        else "Outside the model's validity" if magnetic.get("within_validity") is False else None),
        ))
    wind = _dict(r.get("wind"))
    if wind:
        margin = _finite(wind.get("margin_ms"))
        gust_margin = _finite(wind.get("gust_margin_ms"))
        mean_over = margin is not None and margin < 0
        gust_over = gust_margin is not None and gust_margin < 0
        limit_text = fmt_speed(wind.get("wind_limit_ms"))
        if margin is not None and wind.get("wind_limit_ms") is not None:
            note = f"{fmt_speed(abs(margin))} {'below' if margin >= 0 else 'above'} the {limit_text} rated limit (power-law estimate)"
            if gust_margin is not None:
                note += (f"; gusts {fmt_speed(wind.get('gust_at_height_ms'))}, {fmt_speed(abs(gust_margin))} "
                         f"{'below' if gust_margin >= 0 else 'above'} the {limit_text} limit")
        else:
            note = f"Power-law estimate from {fmt_speed(wind.get('surface_wind_ms'))} at {fmt_height(wind.get('reference_height_m'))}"
        facts.append(_fact(
            f"Wind at {fmt_height(r.get('height_agl_m'))}",
            fmt_speed(wind.get("wind_at_height_ms")),
            _finite(wind.get("wind_at_height_ms")),
            "m/s",
            note=note,
            tone="warn" if mean_over or gust_over else "neutral",
            tone_label="Estimate above the rated limit" if mean_over else "Gusts above the rated limit" if gust_over else None,
        ))
    emi = _dicts(r.get("emi"))
    power_checked = bool(r.get("emi_assessed")) and _issue_message(r, "power_not_assessed") is None
    line = next((i for i in emi if i.get("source") == "power" and i.get("kind") in ("power_line", "minor_line", "cable")), None)

    def emi_name(i: Mapping[str, Any]) -> str:
        kind = EMI_KIND_LABEL.get(str(i.get("kind")), str(i.get("kind")))
        head = f"{fmt_number(i.get('voltage_kv'))} kV {kind.lower()}" if _finite(i.get("voltage_kv")) is not None else kind
        return f"{head} · {i.get('name')}" if i.get("name") else head

    facts.append(_fact(
        "Nearest power line",
        "Not assessed" if not power_checked else ("Crosses" if line and line.get("inside") else fmt_length(line.get("distance_m")) if line else "None"),
        None if not line else _finite(line.get("distance_m")),
        "m" if line else None,
        note=None if not power_checked else (emi_name(line) if line else f"No power line within {fmt_length(r.get('emi_radius_m'))}"),
        tone="warn" if line and line.get("inside") else "neutral",
        tone_label="Crosses or touches the analysed geometry" if line and line.get("inside") else None,
    ))
    tables: list[BriefTable] = []
    segments = _dicts(daylight.get("window_segments"))
    if segments:
        tables.append(_table(
            "Planned window by sun state",
            [("Sun", False), ("Profile", False), ("Start", True), ("End", True), ("Duration", True)],
            [[DAYLIGHT_SEGMENT_LABEL.get(str(s.get("kind")), str(s.get("kind"))),
              DAYLIGHT_STATUS_LABEL.get(str(s.get("status")), str(s.get("status"))),
              str(s.get("start_local")), str(s.get("end_local")), fmt_duration((_finite(s.get("minutes")) or 0) * 60)] for s in segments],
        ))
    if emi:
        tables.append(_table(
            f"Power lines and masts within {fmt_length(r.get('emi_radius_m'))} (proximity evidence)",
            [("Feature", False), ("Voltage", True), ("Distance", True), ("Source", False)],
            [[emi_name(i), f"{fmt_number(i.get('voltage_kv'))} kV" if _finite(i.get("voltage_kv")) is not None else "—",
              ("Crosses" if i.get("kind") in ("power_line", "minor_line", "cable") else "Inside") if i.get("inside") else fmt_length(i.get("distance_m")),
              "Power layer" if i.get("source") == "power" else "Obstacle layer"] for i in emi],
        ))
    statement = daylight.get("statement") if isinstance(daylight.get("statement"), str) else None
    # Interference was asked for (a search radius) but its data were missing: a data gap, as an
    # airspace check without aerodromes (EMI switched off on purpose is not one).
    banners: list[str] = []
    if r.get("emi_radius_m") is not None:
        if not r.get("emi_assessed"):
            banners.append("Interference not assessed: " + (
                _issue_message(r, "emi_not_assessed") or "no data pack covers this area, so power lines and masts were not checked."
            ))
        else:
            banners += [
                f"Interference only partly assessed: {message}"
                for message in (_issue_message(r, code) for code in ("power_not_assessed", "masts_not_assessed", "emi_partial_coverage"))
                if message
            ]
    return {
        "environment": src.part(
            status="partial" if banners else "assessed",
            summary=f"{statement + ' ' if statement else ''}Evidence for planning, not a go/no-go.",
            banners=banners,
            facts=facts,
            tables=tables,
            notes=_issues(r),
            disclaimer=r.get("disclaimer") if isinstance(r.get("disclaimer"), str) else None,
        )
    }


def _line_of_sight(src: _Source) -> Contribution:
    r = src.result
    radio = _dict(r.get("radio"))
    grid = _dict(r.get("grid"))
    guidance = _dict(r.get("vlos_guidance"))
    facts = [
        _fact("Area in visual line of sight", fmt_pct(r.get("combined_visible_pct"), 1), _finite(r.get("combined_visible_pct")), "%",
              note=f"Aircraft {fmt_height(r.get('target_height_agl_m'))} above ground"),
    ]
    if r.get("radio_los_pct") is not None:
        facts.append(_fact("Area in radio line of sight", fmt_pct(r.get("radio_los_pct"), 1), _finite(r.get("radio_los_pct")), "%",
                           note=f"From {radio.get('station_name')}" if radio else None))
    if radio:
        freq = _finite(radio.get("frequency_mhz")) or 0.0
        mhz = f"{fmt_number(freq / 1000, max_decimals=3)}{NNBSP}GHz" if freq >= 1000 else f"{fmt_number(freq)}{NNBSP}MHz"
        blocked = int(radio.get("paths_blocked") or 0)
        below = int(radio.get("paths_below_threshold") or 0)
        worst = radio.get("worst_clearance_pct")
        tone: FactTone = "neutral" if worst is None else "danger" if blocked else "warn" if below else "ok"
        facts.append(_fact(
            "Worst Fresnel clearance", fmt_pct(worst, 1), _finite(worst), "%",
            note=f"First Fresnel zone at {mhz}",
            tone=tone,
            tone_label=("Line of sight cut" if tone == "danger" else f"Below {fmt_number(radio.get('clearance_threshold_pct'))}%" if tone == "warn"
                        else f"At least {fmt_number(radio.get('clearance_threshold_pct'))}%" if tone == "ok" else None),
        ))
        if radio.get("max_fspl_db") is not None:
            facts.append(_fact("Free-space path loss", f"{fmt_number(radio.get('max_fspl_db'), decimals=1)}{NNBSP}dB",
                               _finite(radio.get("max_fspl_db")), "dB", note="Longest path; geometry only, not a link budget"))
    patch = _dict(r.get("largest_hidden_patch"))
    facts += [
        _fact("Largest hidden patch", fmt_area(patch.get("area_m2")) if patch else "None", _finite(patch.get("area_m2")) if patch else None,
              "m²" if patch else None, note="Hidden from every pilot and observer"),
        _fact("VLOS guidance distance", fmt_length(guidance.get("guidance_m")), _finite(guidance.get("guidance_m")), "m",
              note="Non-regulatory guidance"),
    ]
    if r.get("beyond_guidance_pct") is not None:
        facts.append(_fact("Area beyond the guidance distance", fmt_pct(r.get("beyond_guidance_pct"), 1), _finite(r.get("beyond_guidance_pct")), "%"))
    route = _dict(r.get("route"))
    if route:
        facts.append(_fact("Route in visual line of sight", fmt_pct(route.get("visible_pct"), 1), _finite(route.get("visible_pct")), "%",
                           note=f"Longest continuous stretch {fmt_length(route.get('max_continuous_visible_m'))}"))
    if grid:
        res = _finite(grid.get("dem_resolution_m"))
        facts.append(_fact("Terrain model", str(grid.get("dem_source") or "Data-pack DEM"),
                           note=f"{fmt_length(res)} DEM, {fmt_length(grid.get('grid_m'))} analysis grid" if res is not None else f"{fmt_length(grid.get('grid_m'))} analysis grid"))
    tables = []
    observers = _dicts(r.get("observers"))
    if observers:
        tables.append(_table(
            "Pilot, observers and ground station",
            [("Name", False), ("Role", False), ("Sees of the area", True), ("Height above ground", True), ("Position", False)],
            [[str(o.get("name")) + (" (assumed)" if o.get("assumed") else ""), ROLE_LABEL.get(str(o.get("role")), str(o.get("role"))),
              fmt_pct(o.get("visible_pct"), 1), fmt_height(o.get("height_agl_m"), 1), fmt_coord(o.get("lon"), o.get("lat"))] for o in observers],
        ))
    paths = _dicts(radio.get("paths")) if radio else []
    if paths:
        tables.append(_table(
            "Radio paths",
            [("To", False), ("Distance", True), ("Worst clearance", True), ("Free-space loss", True), ("Line of sight", False)],
            [[str(pth.get("to_name")), fmt_length(pth.get("distance_m")), fmt_pct(pth.get("worst_clearance_pct"), 1),
              f"{fmt_number(pth.get('fspl_db'), decimals=1)}{NNBSP}dB", "Blocked" if pth.get("los_blocked") else "Clear"] for pth in paths],
        ))
    hidden = _dicts(route.get("hidden_segments")) if route else []
    if hidden:
        tables.append(_table("Hidden stretches of the route", [("From", True), ("To", True), ("Length", True)], _segment_rows(hidden)))
    # DEM gaps: those cells are left out of the percentages (a data gap, as in the other sections).
    gap = _issue_message(r, "dem_nodata")
    banners = [f"Line of sight only partly assessed: {gap}"] if gap else []
    return {
        "line_of_sight": src.part(
            status="partial" if banners else "assessed",
            banners=banners,
            summary="Geometric evidence from the data-pack surface model — not a VLOS approval, link budget or coverage prediction.",
            facts=facts,
            tables=tables,
            notes=_issues(r),
            disclaimer=r.get("disclaimer") if isinstance(r.get("disclaimer"), str) else None,
        )
    }


def _humanize(key: str) -> str:
    text = key.replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def _is_range(low: float | None, high: float | None) -> bool:
    """True when a GSD range is worth printing as a range (as the survey panel: ≥ 0.005 cm/px)."""
    return low is not None and high is not None and abs(high - low) >= 0.005


def _survey(src: _Source) -> Contribution:
    r, p = src.result, src.params
    spraying = r.get("mode") == "spraying"
    camera = _dict(r.get("camera"))
    alt = _finite(r.get("altitude_agl_m"))
    limit = _finite(r.get("max_altitude_agl_m")) or _finite(p.get("max_altitude_agl_m")) or 120.0
    above = alt is not None and alt > limit + 1e-6
    reference = str(r.get("height_reference") or "agl")
    gsd_min, gsd_max = _finite(r.get("gsd_min_cm")), _finite(r.get("gsd_max_cm"))
    if not _is_range(gsd_min, gsd_max):
        gsd_min = gsd_max = None  # flat ground (or AGL): one value, not "2.74–2.74"
    gsd_text = (f"{fmt_number(gsd_min, max_decimals=2)}–{fmt_number(gsd_max, max_decimals=2)}{NNBSP}cm/px"
                if gsd_min is not None and gsd_max is not None else f"{fmt_number(r.get('gsd_cm'), decimals=2)}{NNBSP}cm/px")
    facts: list[BriefFact] = []
    if not spraying:
        if gsd_min is not None:
            gsd_note = r.get("heights_note") if isinstance(r.get("heights_note"), str) else "Over the terrain relief"
        elif r.get("gsd_along_cm") != r.get("gsd_cm"):
            gsd_note = f"Along track {fmt_number(r.get('gsd_along_cm'), max_decimals=2)} cm/px"
        else:
            gsd_note = "Square pixels"
        facts.append(_fact("Ground sample distance", gsd_text, _finite(r.get("gsd_cm")), "cm/px", note=gsd_note))
    # altitude_agl_m is the height the layout uses: above ground, or above the take-off ground for
    # an above-take-off or AMSL plan (an AMSL plan's level is the take-off ground + that height).
    height_note = _cap(HEIGHT_REFERENCE_LABEL.get(reference, "above ground"))
    if reference == "amsl":
        level = _finite(r.get("flight_level_amsl_m"))
        height_note = "Above the take-off ground" + (f"; flight level {fmt_height(level)} AMSL" if level is not None else "")
    facts.append(_fact("Height", fmt_height(alt), alt, "m", note=height_note,
                       tone="warn" if above else "neutral", tone_label=f"Above the {fmt_height(limit)} limit" if above else None))
    if r.get("coverage") == "corridor":
        facts.append(_fact("Corridor", fmt_length(r.get("corridor_width_m")), _finite(r.get("corridor_width_m")), "m",
                           note=f"Wide, along a {fmt_length((_finite(r.get('centreline_km')) or 0) * 1000)} centreline"))
    if not spraying:
        facts += [
            _fact("Image footprint", f"{fmt_length(r.get('footprint_across_m'))} × {fmt_length(r.get('footprint_along_m'))}",
                  note="Across × along track"),
            _fact("Photos", fmt_number(r.get("photos"), decimals=0), _finite(r.get("photos"))),
        ]
    swaps = "including battery swaps and refills" if spraying else "including battery swaps"
    facts += [
        _fact("Line spacing", fmt_length(r.get("line_spacing_m")), _finite(r.get("line_spacing_m")), "m",
              note=f"{_plural(int(r.get('line_count') or 0), 'line')} at {fmt_bearing(r.get('direction_deg'))} (a visual aid, not a route)"),
        _fact("Flight time", fmt_duration((_finite(r.get("flight_minutes")) or 0) * 60), _finite(r.get("flight_minutes")), "min",
              note=f"{fmt_duration((_finite(r.get('total_minutes')) or 0) * 60)} {swaps}"),
        _fact("Batteries", fmt_number(r.get("batteries"), decimals=0), _finite(r.get("batteries")),
              note=f"{fmt_pct(r.get('battery_reserve_pct'), 0)} reserve kept" if _finite(r.get("battery_reserve_pct")) else None),
        _fact("Area", fmt_area((_finite(r.get("area_ha")) or 0) * 10_000), _finite(r.get("area_ha")), "ha",
              note=f"{fmt_area((_finite(r.get('ha_per_hour')) or 0) * 10_000)} per hour"),
    ]
    if _finite(r.get("transit_km")):
        facts.append(_fact("Transit", fmt_length((_finite(r.get("transit_km")) or 0) * 1000), _finite(r.get("transit_km")), "km",
                           note=f"{fmt_duration((_finite(r.get('transit_minutes')) or 0) * 60)} to and from the take-off point"))
    for key in ("worst_side_overlap_pct", "worst_front_overlap_pct"):
        if _finite(r.get(key)) is not None:
            facts.append(_fact(_humanize(key.removesuffix("_pct")), fmt_pct(r.get(key), 0), _finite(r.get(key)), "%",
                               note="At the highest ground"))
    gcp = _dict(r.get("gcp"))
    if gcp:
        facts.append(_fact("Ground control points", fmt_number(gcp.get("count"), decimals=0), _finite(gcp.get("count")),
                           note=str(gcp.get("rule") or "Rule of thumb")))
    storage = _dict(r.get("storage"))
    if storage:
        facts.append(_fact(
            "Storage", f"{fmt_number(storage.get('total_gb'), max_decimals=1)}{NNBSP}GB", _finite(storage.get("total_gb")), "GB",
            note=f"{fmt_number(storage.get('images'), decimals=0)} images × {fmt_number(storage.get('mb_per_image'), max_decimals=1)} MB",
        ))
    spray = _dict(r.get("spray"))
    if spray:
        pump = " (pump-limited)" if spray.get("flow_limited") else ""
        facts += [
            _fact("Spray mix", f"{fmt_number(spray.get('litres'), max_decimals=1)}{NNBSP}L", _finite(spray.get("litres")), "L",
                  note=f"{fmt_number(spray.get('rate_l_per_ha'))} L/ha over a {fmt_length(spray.get('swath_m'))} swath"),
            _fact("Tank loads", fmt_number(spray.get("tank_loads"), decimals=0), _finite(spray.get("tank_loads")),
                  note=(f"{fmt_number(spray.get('tank_l'))} L tank; {_plural(int(spray.get('refills') or 0), 'refill')}, "
                        f"{_plural(int(spray.get('sorties') or 0), 'sortie')}")),
            _fact("Spraying rate", f"{fmt_number(spray.get('effective_ha_per_hour'), max_decimals=2)}{NNBSP}ha/h",
                  _finite(spray.get("effective_ha_per_hour")), "ha/h",
                  note=f"{fmt_pct(spray.get('field_efficiency_pct'), 0)} field efficiency at {fmt_speed(spray.get('speed_ms'))}{pump}"),
        ]
    blocks = _dicts(r.get("blocks"))
    columns = [("Battery", False), ("Area", True), ("Line length", True), ("Flight time", True)]
    columns.append(("Spray mix", True) if spraying else ("Photos", True))
    with_gsd = not spraying and any(_is_range(_finite(b.get("gsd_min_cm")), _finite(b.get("gsd_max_cm"))) for b in blocks)
    if with_gsd:
        columns.append(("GSD", True))
    rows: list[list[str]] = []
    for b in blocks:
        row = [
            f"Battery {b.get('index')}",
            fmt_area((_finite(b.get("area_ha")) or 0) * 10_000),
            fmt_length((_finite(b.get("line_km")) or 0) * 1000),
            fmt_duration((_finite(b.get("minutes")) or 0) * 60),
            f"{fmt_number(b.get('litres'), max_decimals=1)}{NNBSP}L" if spraying else fmt_number(b.get("photos"), decimals=0),
        ]
        if with_gsd:
            row.append(f"{fmt_number(b.get('gsd_min_cm'), max_decimals=2)}–{fmt_number(b.get('gsd_max_cm'), max_decimals=2)}{NNBSP}cm/px")
        rows.append(row)
    tables = [_table("Battery blocks", columns, rows)] if len(blocks) > 1 else []
    summary = ("Spraying effort estimate only — no route or waypoints are generated." if spraying
               else f"{camera.get('name') or 'Camera'}. Estimate only — no flight plan or waypoints are generated.")
    out: Contribution = {
        "survey": src.part(
            "Spraying effort estimator" if spraying else None,
            summary=summary,
            facts=facts,
            tables=tables,
            notes=_issues(r),
        )
    }
    if r.get("dem") or r.get("relief_m") is not None or reference != "agl" or r.get("heights_assessed") is False:
        hfacts: list[BriefFact] = []
        if r.get("terrain_min_m") is not None:
            hfacts.append(_fact("Terrain under the survey", f"{fmt_number(r.get('terrain_min_m'), max_decimals=0)}–{fmt_height(r.get('terrain_max_m'))}",
                                _finite(r.get("terrain_max_m")), "m", note="Above mean sea level"))
        if r.get("relief_m") is not None:
            hfacts.append(_fact("Relief", fmt_height(r.get("relief_m")), _finite(r.get("relief_m")), "m"))
        if gsd_min is not None:
            hfacts.append(_fact("GSD over the relief", gsd_text, gsd_max, "cm/px"))
        out["heights"] = _heights_part(src, hfacts, has_terrain=r.get("relief_m") is not None or bool(r.get("dem")))
    return out


def _audit_limits(r: Mapping[str, Any]) -> tuple[float, float, float, bool, bool, str]:
    requested = _finite(r.get("ceiling_agl_m")) or 120.0
    used = [v for v in (_finite(p.get("limit_agl_m")) for p in _dicts(r.get("profile"))) if v is not None]
    used += [v for v in (_finite(e.get("limit_m")) for e in _dicts(r.get("exceedances"))) if v is not None]
    highest = max(used) if used else requested
    lowest = min(used) if used else requested
    cap_source = "the default rules outside mapped zones" if not _dicts(r.get("zones")) else "the zone rules"
    return requested, highest, lowest, highest < requested - 1e-6, lowest < highest - 1e-6, cap_source


def _describe_limit(r: Mapping[str, Any]) -> str:
    requested, highest, lowest, capped, varies, source = _audit_limits(r)
    if varies:
        return (f"Limit {fmt_height(lowest)} to {fmt_height(highest)} along the flight: {source} cap your {fmt_height(requested)}"
                if capped else f"Limit {fmt_height(requested)}, down to {fmt_height(lowest)} inside some zones")
    return f"Limit {fmt_height(highest)}: {source} cap your {fmt_height(requested)}" if capped else f"Limit {fmt_height(requested)}"


def _flight_audit(src: _Source) -> Contribution:
    r = src.result
    flight = _dict(r.get("flight"))
    heights = r.get("heights_assessed") is not False
    complete = r.get("assessment_complete", True) is not False
    exceed = _dicts(r.get("exceedances"))
    banners: list[str] = []
    if not complete:
        banners.append(_gap_banner(r, "no data pack covers this flight, so aerodrome zones were not checked."))
    if not heights:
        banners.append("Heights not assessed: the track has no altitudes, so the height limit was not checked.")
    if src.non_default:
        banners.append(_non_default_banner(src.non_default))
    requested, highest, _lowest, capped, varies, source = _audit_limits(r)
    if not heights:
        summary = "Heights not assessed: the track has no altitudes."
    elif exceed:
        summary = f"{_plural(len(exceed), 'segment')} above the limit; highest {fmt_height(r.get('max_agl_m'))} above ground."
    elif varies:
        summary = f"Stayed within the height limit everywhere; highest {fmt_height(r.get('max_agl_m'))} above ground."
    else:
        cap = f" ({source} cap your {fmt_height(requested)})" if capped else ""
        summary = f"Stayed at or below {fmt_height(highest)} above ground{cap}."
    is_track = flight.get("source") == "track"
    name = flight.get("file_name") or flight.get("report_id") or flight.get("track_name") or ("Position track" if is_track else "FlightMD flight")
    detail = [str(name)]
    if is_track and flight.get("track_format"):
        label = TRACK_FORMAT_LABEL.get(str(flight.get("track_format")), str(flight.get("track_format")))
        if label != name:  # an unnamed track of no known format is just "Position track", once
            detail.append(label)
    if flight.get("log_start_utc"):
        detail.append(fmt_utc(flight.get("log_start_utc")))
    facts = [
        _fact("Flight", " · ".join(detail), note=f"{fmt_number(flight.get('points'), decimals=0)} positions"),
        (_fact("Max height above ground", fmt_height(r.get("max_agl_m")), _finite(r.get("max_agl_m")), "m", note=_describe_limit(r),
               tone="danger" if exceed else "ok", tone_label="Above the limit" if exceed else "Within the limit")
         if heights else _fact("Max height above ground", "Not recorded", tone="warn", tone_label="Not assessed",
                               note="The track has no altitudes")),
    ]
    if heights:
        facts.append(_fact("Max height above take-off", fmt_height(r.get("max_height_above_takeoff_m")),
                           _finite(r.get("max_height_above_takeoff_m")), "m",
                           note=ALTITUDE_REFERENCE_TEXT.get(str(r.get("altitude_reference")))))
    planned = flight.get("planning_brief_sha256")
    if isinstance(planned, str) and planned:
        # the hash FlightMD got from its upload form: the brief this flight was planned with
        facts.append(_fact("Planning brief", f"SHA-256 {planned[:16]}…", note=planned))
    findings = _dicts(r.get("findings"))
    if findings:
        armed = sum(f.get("armed") is True for f in findings)
        placed = sum(f.get("placement") == "placed" for f in findings)
        facts.append(_fact("FlightMD findings", fmt_number(len(findings), decimals=0), len(findings),
                           note=f"{armed} while armed, {placed} placed on the track"))
    facts.append(_fact("Distance flown", fmt_length(r.get("distance_m")), _finite(r.get("distance_m")), "m"))
    if r.get("duration_s") is not None:
        above_s = _finite(r.get("time_above_limit_s"))
        facts.append(_fact("Flight time", fmt_duration(r.get("duration_s")), _finite(r.get("duration_s")), "s",
                           note=None if not heights else (f"{fmt_duration(above_s)} above the height limit" if above_s else "Never above the height limit")))
    far = r.get("distance_exceedances")
    far_limit = _finite(r.get("max_distance_from_home_m"))
    if r.get("max_home_distance_m") is not None:
        n_far = len(_dicts(far))
        facts.append(_fact(
            "Farthest from take-off", fmt_length(r.get("max_home_distance_m")), _finite(r.get("max_home_distance_m")), "m",
            note=None if far_limit is None else f"Limit {fmt_length(far_limit)}",
            tone="neutral" if far_limit is None else "danger" if n_far else "ok",
            tone_label=None if far_limit is None else "Beyond the distance" if n_far else "Within the distance",
        ))
    exits = r.get("area_exits")
    if isinstance(exits, list):
        n_exits = len(_dicts(exits))
        facts.append(_fact(
            "Outside the training area",
            fmt_duration(r.get("time_outside_area_s")) if r.get("time_outside_area_s") is not None else fmt_number(n_exits, decimals=0),
            _finite(r.get("time_outside_area_s")),
            "s",
            note=str(r.get("training_box_name") or "Training area"),
            tone="danger" if n_exits else "ok",
            tone_label=f"Left it {n_exits} time{'' if n_exits == 1 else 's'}" if n_exits else "Stayed inside",
        ))
    nearest = _dict(r.get("nearest_aerodrome"))
    facts += [
        _fact("Nearest aerodrome",
              fmt_length((_finite(r.get("min_aerodrome_distance_km")) or 0) * 1000) if nearest and r.get("min_aerodrome_distance_km") is not None else "None in data",
              _finite(r.get("min_aerodrome_distance_km")), "km" if nearest else None,
              note=f"{nearest.get('name')} ({nearest.get('ident')})" if nearest else None),
        _fact("People overflown", "No data" if r.get("overflown_population") is None else fmt_number(r.get("overflown_population"), decimals=0),
              _finite(r.get("overflown_population")),
              note=f"Max {fmt_density(r.get('max_density_per_km2'))}" if r.get("max_density_per_km2") else None),
        _fact("Zones crossed", fmt_number(len(_dicts(r.get("zones"))), decimals=0), len(_dicts(r.get("zones")))),
        _zone_rules_fact(r, note=("Above a band's free-flight ceiling: the yellow zone, permission required, not an exceedance"
                                  if src.params.get("band_ceiling") == "permission" else None)),
    ]

    def runs(segments: list[dict[str, Any]]) -> list[list[str]]:
        return [[fmt_length(e.get("start_distance_m"), 1), fmt_length(e.get("end_distance_m"), 1), fmt_length(e.get("length_m")),
                 fmt_height(e.get("max_agl_m")), fmt_height(e.get("limit_m")), str(e.get("reason") or "")] for e in segments]

    tables: list[BriefTable] = []
    if exceed:
        tables.append(_table(
            "Ceiling exceedances",
            [("From", True), ("To", True), ("Length", True), ("Max AGL", True), ("Limit", True), ("Reason", False)],
            runs(exceed),
        ))
    above = _dicts(r.get("above_band_ceiling"))
    if above:
        tables.append(_table(
            "Flown in yellow airspace above a band's ceiling (permission required)",
            [("From", True), ("To", True), ("Length", True), ("Max AGL", True), ("Ceiling", True), ("Reason", False)],
            runs(above),
        ))
    if isinstance(exits, list) and _dicts(exits):
        tables.append(_table(
            f"Segments outside {r.get('training_box_name') or 'the training area'}",
            [("From", True), ("To", True), ("Length", True), ("Duration", True), ("Farthest outside", True)],
            _segment_rows(_dicts(exits), "peak_m"),
        ))
    if isinstance(far, list) and _dicts(far):
        tables.append(_table(
            f"Beyond {fmt_length(far_limit)} from take-off",
            [("From", True), ("To", True), ("Length", True), ("Duration", True), ("Farthest", True)],
            _segment_rows(_dicts(far), "peak_m"),
        ))
    if _dicts(r.get("zones")):
        tables.append(_table("Zones crossed", _ZONE_COLUMNS, _zone_rows(_dicts(r.get("zones")))))
    if _dicts(r.get("band_evidence")):
        tables.append(_table("Aerodrome bands flown", _BAND_COLUMNS, _band_rows(_dicts(r.get("band_evidence")))))
    return {
        "flight_audit": src.part(
            status="assessed" if complete and heights else "partial",
            verdict=_verdict(r.get("verdict")),
            summary=summary,
            banners=banners,
            facts=facts,
            tables=tables,
            notes=_issues(r),
            disclaimer=r.get("disclaimer") if isinstance(r.get("disclaimer"), str) else None,
        )
    }


def _show(src: _Source) -> Contribution:
    r, p = src.result, src.params
    conflicts = _dicts(r.get("conflicts"))
    violations = _dicts(r.get("violations"))
    site = _dict(r.get("site"))
    pads = _dict(r.get("pads"))
    frames = _dict(r.get("frames"))
    origin_raw = frames.get("origin")
    origin: list[Any] = origin_raw if isinstance(origin_raw, list) else []
    summary = (f"No conflicts or limit violations across {fmt_number(r.get('frames_checked'), decimals=0)} frames."
               if not conflicts and not violations else f"{_plural(len(conflicts), 'conflict')} and {_plural(len(violations), 'violation')}.")
    pair = r.get("min_separation_pair")
    min_sep_limit = _finite(p.get("min_separation_m")) or 1.5
    facts = [
        _fact("Drones", fmt_number(r.get("drone_count"), decimals=0), _finite(r.get("drone_count")), note=f"{fmt_clock(r.get('duration_s'))} long"),
        _fact("Minimum separation", fmt_length(r.get("min_separation_m"), 2), _finite(r.get("min_separation_m")), "m",
              note=(f"{' – '.join(str(x) for x in pair)} at {fmt_clock(r.get('min_separation_at_s'))}; limit {fmt_length(min_sep_limit, 2)}"
                    if isinstance(pair, list) and pair else f"Limit {fmt_length(min_sep_limit, 2)}")),
        _fact("Conflicts", fmt_number(len(conflicts), decimals=0), len(conflicts), tone="danger" if conflicts else "ok",
              tone_label="Too close" if conflicts else "None"),
        _fact("Limit violations", fmt_number(len(violations), decimals=0), len(violations), tone="danger" if violations else "ok",
              tone_label="See the table" if violations else "None"),
        _fact("Checked at", f"{fmt_number(r.get('sample_rate_hz'))}{NNBSP}Hz", _finite(r.get("sample_rate_hz")), "Hz",
              note=f"{'C++' if r.get('backend') == 'cpp' else 'Python'} kernel"),
    ]
    if r.get("max_altitude_m") is not None:
        facts.append(_fact("Max height", fmt_height(r.get("max_altitude_m"), 1), _finite(r.get("max_altitude_m")), "m", note="Above the show origin"))
    if len(origin) >= 2:
        source = {"inferred": " (the mean first position)", "given": " (given)"}.get(str(r.get("origin_source")), "")
        heights = (f"heights above sea level, measured from {fmt_height(origin[2] if len(origin) > 2 else 0)} at the origin"
                   if r.get("altitude_reference") == "absolute" else "heights relative to the origin")
        facts.append(_fact("Origin", fmt_coord(origin[0], origin[1]) + source, note=_cap(heights)))
    box_area = site.get("show_box_area_m2")
    box_source = site.get("show_box_source")
    facts.append(_fact(
        "Show box",
        "Not defined" if box_area is None else fmt_area(box_area),
        _finite(box_area), "m²" if box_area is not None else None,
        note=(f"Derived from the tracks (+{fmt_length(site.get('show_box_margin_m'))} margin)" if box_source == "derived"
              else "“Show box” polygon" if box_source == "given" else None),
    ))
    audience = _dict(site.get("audience_buffer"))
    if audience:
        facts.append(_fact(
            "Audience distance",
            fmt_length(audience.get("distance_m"), 1),
            _finite(audience.get("distance_m")), "m",
            note=f"{AUDIENCE_RULE_LABEL.get(str(audience.get('rule')), str(audience.get('rule')))}: {audience.get('formula')}",
        ))
    if site.get("audience_area_m2") is not None:
        overlaps = bool(site.get("box_overlaps_audience_buffer"))
        facts.append(_fact("Box to audience", fmt_length(site.get("box_audience_distance_m")), _finite(site.get("box_audience_distance_m")), "m",
                           tone="danger" if overlaps else "ok", tone_label="Overlaps the box" if overlaps else "Clear of the box"))
    if pads:
        spacings = [v for v in (_finite(pads.get("takeoff_min_spacing_m")), _finite(pads.get("landing_min_spacing_m"))) if v is not None]
        pad_conflicts = _dicts(pads.get("conflicts"))
        facts.append(_fact(
            "Pad spacing", fmt_length(min(spacings), 2) if spacings else "—", min(spacings) if spacings else None, "m",
            note=f"{fmt_number(pads.get('takeoff_pads'), decimals=0)} take-off, {fmt_number(pads.get('landing_pads'), decimals=0)} landing pads; limit {fmt_length(pads.get('limit_m'), 2)}",
            tone="danger" if pad_conflicts else "ok",
            tone_label=f"{len(pad_conflicts)}{'+' if pads.get('conflicts_truncated') else ''} too close" if pad_conflicts else "Clear",
        ))
    endurance = _finite(p.get("endurance_s"))
    if endurance is not None:
        longest = max((_finite(d.get("airborne_s")) or 0.0 for d in _dicts(r.get("drones"))), default=0.0)
        over = [v for v in violations if v.get("kind") == "endurance"]
        facts.append(_fact(
            "Endurance", fmt_duration(longest), longest, "s",
            note=f"Longest airborne time against the {fmt_duration(endurance)} endurance",
            tone="danger" if over else "ok",
            tone_label=f"{_plural(len(over), 'drone')} over" if over else "Within",
        ))
    tables: list[BriefTable] = []
    if conflicts:
        tables.append(_table(
            "Separation conflicts",
            [("Time", True), ("Drones", False), ("Closest", True), ("Window", True)],
            [[fmt_clock(c.get("t_min_s")), f"{c.get('drone_a')} – {c.get('drone_b')}", fmt_length(c.get("min_distance_m"), 2),
              f"{fmt_clock(c.get('start_s'))} – {fmt_clock(c.get('end_s'))}"] for c in conflicts],
        ))
    if violations:
        def value(v: Mapping[str, Any], x: Any) -> str:
            kind = v.get("kind")
            if kind == "endurance":
                return fmt_duration(x)
            if kind == "acceleration":
                return f"{fmt_number(x, max_decimals=2)}{NNBSP}m/s²"
            if kind in ("speed_h", "climb", "descent", "low_altitude_motion"):
                return fmt_speed(x)
            return fmt_length(x, 2)

        tables.append(_table(
            "Dynamic and site violations",
            [("From", True), ("Drone", False), ("Check", False), ("Peak", True), ("Limit", True)],
            [[fmt_clock(v.get("start_s")), str(v.get("drone")), VIOLATION_LABEL.get(str(v.get("kind")), str(v.get("kind"))),
              value(v, v.get("peak_value")), value(v, v.get("limit"))] for v in violations],
        ))
    if pads and _dicts(pads.get("conflicts")):
        tables.append(_table(
            f"Pads closer than {fmt_length(pads.get('limit_m'), 2)}",
            [("When", False), ("Drones", False), ("Apart", True)],
            [["Take-off" if c.get("phase") == "takeoff" else "Landing", f"{c.get('drone_a')} – {c.get('drone_b')}",
              fmt_length(c.get("distance_m"), 2)] for c in _dicts(pads.get("conflicts"))],
        ))
    return {
        "show": src.part(
            verdict=_verdict(r.get("verdict")),
            summary=summary + (" More events exist than are listed (event limit reached)." if r.get("events_truncated") else ""),
            facts=facts,
            tables=tables,
            notes=_issues(r),
        )
    }


def _unverified_band_note(src: _Source) -> str | None:
    if src.params.get("unverified_band") != "exclude":
        return None
    return "Bands to verify on Digital Sky (DGCA 0–5 km) excluded like a red zone (unverified_band: exclude)"


def _evtol_siting(src: _Source) -> Contribution:
    r = src.result
    candidates = _dicts(r.get("candidates"))
    top = candidates[0] if candidates else {}
    weights = _dict(r.get("weights"))
    facts = [
        _fact("Candidate sites", fmt_number(len(candidates), decimals=0), len(candidates)),
        _fact("Best score", f"{fmt_number(top.get('score'), max_decimals=1)} / 100" if top else "—", _finite(top.get("score")) if top else None),
        _fact("Cells evaluated", fmt_number(r.get("cells_evaluated"), decimals=0), _finite(r.get("cells_evaluated")),
              note=f"{fmt_number(r.get('cells_excluded'), decimals=0)} excluded (water, red zones, steep slopes)"),
        _fact("Weights", ", ".join(f"{_humanize(k)} {fmt_number(v, max_decimals=2)}" for k, v in weights.items()) or "—"),
        _zone_rules_fact(r, note=_unverified_band_note(src)),
    ]
    rows = [
        [str(c.get("rank")), fmt_number(c.get("score"), max_decimals=1), fmt_coord(c.get("lon"), c.get("lat")),
         "Verify on Digital Sky" if c.get("zone_basis") == "verify_official_map" else ZONE_LABEL.get(str(c.get("zone")), str(c.get("zone"))),
         "—" if c.get("slope_deg") is None else f"{fmt_number(c.get('slope_deg'), max_decimals=1)}°",
         f"{c.get('nearest_poi')}" + (f" ({fmt_length((_finite(c.get('nearest_poi_km')) or 0) * 1000)})" if c.get("nearest_poi_km") is not None else "")
         if c.get("nearest_poi") else "—"]
        for c in candidates
    ]
    tables = [_table(
        "Ranked vertiport candidates",
        [("Rank", True), ("Score", True), ("Location", False), ("Zone", False), ("Slope", True), ("Nearest POI", False)],
        rows,
    )] if rows else []
    return {
        "evtol": src.part(
            title="eVTOL vertiport siting",
            summary="Planning-level screening of candidate sites, not a site approval.",
            facts=facts,
            tables=tables,
            notes=_issues(r),
            disclaimer=r.get("disclaimer") if isinstance(r.get("disclaimer"), str) else None,
        )
    }


def _evtol_corridor(src: _Source) -> Contribution:
    r, p = src.result, src.params
    noise = _dict(r.get("noise"))
    zones = _dicts(r.get("zones"))
    detour = _finite(r.get("detour_pct"))
    reference = str(p.get("cruise_reference") or "agl")
    facts = [
        _fact("Route length", fmt_length((_finite(r.get("length_km")) or 0) * 1000), _finite(r.get("length_km")), "km",
              note=f"Straight line {fmt_length((_finite(r.get('straight_km')) or 0) * 1000)}"),
        _fact("Detour", fmt_pct(detour, 1), detour, "%", tone="warn" if detour is not None and detour > 30 else "neutral",
              tone_label="Long detour" if detour is not None and detour > 30 else None),
        _fact("Flight time", fmt_duration((_finite(r.get("flight_time_min")) or 0) * 60), _finite(r.get("flight_time_min")), "min"),
        _fact("Energy", f"{fmt_number(r.get('energy_kwh'), decimals=1)}{NNBSP}kWh", _finite(r.get("energy_kwh")), "kWh"),
        # An AMSL corridor flies its level (cruise_altitude_amsl_m); cruise_altitude_agl_m is then
        # only the clearance used to size the required level.
        (_fact("Cruise height", fmt_height(r.get("cruise_altitude_amsl_m")), _finite(r.get("cruise_altitude_amsl_m")), "m",
               note="Above mean sea level")
         if reference == "amsl" and _finite(r.get("cruise_altitude_amsl_m")) is not None
         else _fact("Cruise height", fmt_height(r.get("cruise_altitude_agl_m")), _finite(r.get("cruise_altitude_agl_m")), "m",
                    note="Above the highest ground")),
        _fact("People exposed to noise", fmt_number(noise.get("exposed_population"), decimals=0), _finite(noise.get("exposed_population")),
              note=f"≥ {fmt_number(noise.get('threshold_db'))} dB within {fmt_length(noise.get('footprint_half_width_m'))} of the route"),
        _fact("Zones crossed", fmt_number(len(zones), decimals=0), len(zones), tone="warn" if zones else "neutral"),
        _zone_rules_fact(r, note=_unverified_band_note(src)),
    ]
    out: Contribution = {
        "evtol": src.part(
            title="eVTOL corridor routing",
            summary=f"{p.get('origin_name') or 'Origin'} → {p.get('destination_name') or 'destination'}: planning-level corridor evidence, not a route approval.",
            facts=facts,
            tables=[_table("Zones crossed", _ZONE_COLUMNS, _zone_rows(zones))] if zones else [],
            notes=_issues(r),
            disclaimer=r.get("disclaimer") if isinstance(r.get("disclaimer"), str) else None,
        )
    }
    hfacts = [
        _fact("Required cruise (AMSL)", fmt_height(r.get("required_cruise_amsl_m")), _finite(r.get("required_cruise_amsl_m")), "m",
              note=(f"The {fmt_height(r.get('cruise_altitude_amsl_m'))} level flown" if reference == "amsl"
                    else f"{fmt_height(r.get('cruise_altitude_agl_m'))} above the highest ground along the corridor")),
        _fact("Highest terrain along the corridor", fmt_height(r.get("max_terrain_m")), _finite(r.get("max_terrain_m")), "m",
              note="Above mean sea level"),
    ]
    out["heights"] = _heights_part(src, hfacts, has_terrain=r.get("max_terrain_m") is not None, title="eVTOL corridor routing")
    obstacles = _dicts(r.get("obstacles"))
    out["obstacles"] = _obstacle_part(src, obstacles, p.get("corridor_width_m"), "corridor centreline", "eVTOL corridor routing")
    return out


_FACT_TONES: dict[str, FactTone] = {"ok": "ok", "warn": "warn", "danger": "danger", "info": "info"}


def _ha(v: Mapping[str, Any]) -> str:
    ha = _finite(v.get("area_ha"))
    return fmt_area(ha * 10_000.0 if ha is not None else None)


# Three of each kind's per-feature figures for the table; the CSV export has all of them.
_BATCH_COLUMNS: dict[str, list[tuple[str, bool, Callable[[Mapping[str, Any]], str]]]] = {
    "airspace": [
        ("Band", False, lambda v: str(v.get("band") or "Outside mapped zones")),
        ("Ceiling AGL", True, lambda v: "Permission" if v.get("permission_required") else fmt_height(v.get("effective_ceiling_agl_m"))),
        ("Obstacle conflicts", True, lambda v: fmt_number(v.get("obstacle_conflicts"), decimals=0)),
    ],
    "ground_risk": [
        ("Density band", False, lambda v: str(v.get("band") or "—")),
        ("Highest density", True, lambda v: fmt_density(v.get("max_density_per_km2"))),
        ("People in footprint", True, lambda v: fmt_number(v.get("population"), decimals=0)),
    ],
    "survey": [
        ("Area", True, _ha),
        ("Photos", True, lambda v: fmt_number(v.get("photos"), decimals=0)),
        ("Total time", True, lambda v: fmt_duration((_finite(v.get("minutes")) or 0.0) * 60.0)),
    ],
    "environment": [
        ("Nearest power or mast", True, lambda v: fmt_length(v.get("emi_nearest_m")) if v.get("emi_nearest_m") is not None else "None"),
        ("Outside daylight", True, lambda v: fmt_duration((_finite(v.get("daylight_minutes_outside")) or 0.0) * 60.0)),
        ("Declination", True, lambda v: f"{fmt_number(v.get('declination_deg'), max_decimals=1)}°"),
    ],
    "line_of_sight": [
        ("Visible", True, lambda v: fmt_pct(v.get("visible_pct"))),
        ("Hidden area", True, lambda v: fmt_area(v.get("hidden_area_m2"))),
        ("Worst Fresnel clearance", True, lambda v: fmt_pct(v.get("radio_worst_clearance_pct"))),
    ],
}


def _batch(src: _Source) -> Contribution:
    r = src.result
    counts = _dicts(r.get("counts"))
    items = _dicts(r.get("items"))
    n = int(_finite(r.get("feature_count")) or len(items))
    errors, skipped = int(_finite(r.get("errors")) or 0), int(_finite(r.get("skipped")) or 0)
    facts = [_fact("Features", fmt_number(n, decimals=0), n, note="One result each, with the same parameters")]
    facts += [
        _fact(str(c.get("label")), fmt_number(c.get("count"), decimals=0), _finite(c.get("count")),
              tone=_FACT_TONES.get(str(c.get("tone")), "neutral"))
        for c in counts
    ]
    if errors:
        facts.append(_fact("Could not be assessed", fmt_number(errors, decimals=0), errors, tone="warn", note="Each row gives the reason"))
    if skipped:
        facts.append(_fact("Not run (time budget)", fmt_number(skipped, decimals=0), skipped, tone="warn"))

    rank = {c.get("verdict"): i for i, c in enumerate(counts)}
    labels = {c.get("verdict"): str(c.get("label")) for c in counts}
    order = {"ok": 0, "error": 1, "skipped": 2}
    ordered = sorted(items, key=lambda it: (order.get(str(it.get("status")), 3), rank.get(it.get("verdict"), len(rank))))
    columns = _BATCH_COLUMNS.get(src.kind, [])
    rows = []
    for it in ordered:
        name = str(it.get("key") or "")
        if it.get("name") and it.get("name") != it.get("key"):
            name += f" ({it['name']})"
        values = _dict(it.get("values"))
        if it.get("status") == "ok":
            rows.append([name, labels.get(it.get("verdict"), str(it.get("verdict"))), *(fmt(values) for _l, _n, fmt in columns)])
        else:
            rows.append([name, f"Not assessed: {it.get('error') or 'no reason given'}", *("—" for _ in columns)])
    table = _table("One result per feature (most severe first)", [("Feature", False), ("Result", False), *((label, num) for label, num, _f in columns)], rows)

    banners: list[str] = []
    gaps = sum(int(_finite(c.get("count")) or 0) for c in counts if c.get("verdict") in ("not_assessed", "emi_not_checked"))
    if errors or skipped or gaps:
        banners.append(f"Not every feature was assessed: {errors + skipped + gaps:,} of {n:,} have no complete result. "
                       "Their rows say why; the CSV export lists every feature.")
    if src.non_default:
        banners.append(_non_default_banner(src.non_default))
    verdict = None
    if src.kind == "airspace":
        verdict = _worst(_verdict(_dict(it.get("values")).get("check")) for it in items if it.get("status") == "ok")
    return {
        src.kind: src.part(
            f"{src.title}: one result per feature",
            status="partial" if errors or skipped or gaps else "assessed",
            verdict=verdict,
            banners=banners,
            summary=str(r.get("summary") or ""),
            facts=facts,
            tables=[table],
            notes=_issues(r),
            disclaimer=r.get("disclaimer") if isinstance(r.get("disclaimer"), str) else None,
        )
    }


_CONTRIBUTORS: dict[str, Callable[[_Source], Contribution]] = {
    "airspace": _airspace,
    "ground_risk": _ground_risk,
    "environment": _environment,
    "line_of_sight": _line_of_sight,
    "survey": _survey,
    "flight_audit": _flight_audit,
    "show": _show,
    "evtol_siting": _evtol_siting,
    "evtol_corridor": _evtol_corridor,
}

_NOT_ASSESSED_REASON: dict[str, str] = {
    "airspace": "No airspace analysis in this brief: zones, ceilings and aerodromes were not checked.",
    "heights": "No analysis in this brief checked terrain heights (airspace, eVTOL corridor or survey with terrain).",
    "obstacles": "No analysis in this brief searched for obstacles (airspace or eVTOL corridor).",
    "ground_risk": "No ground-risk analysis in this brief: population density and the SORA iGRC evidence were not assessed.",
    "environment": "No environment analysis in this brief: daylight, magnetic declination, wind and interference were not assessed.",
    "line_of_sight": "No line-of-sight analysis in this brief: visual and radio line of sight were not assessed.",
    "survey": "No survey or spray effort estimate in this brief.",
    "flight_audit": "No flight audit in this brief (it applies to a flown track).",
    "show": "No drone-show check in this brief.",
    "evtol": "No eVTOL siting or corridor analysis in this brief.",
}


def _aircraft(meta: OperationMeta) -> AircraftSummary | None:
    info = meta.aircraft
    if info is None:
        return None
    preset = UAS_PRESETS.get(info.preset) if info.preset else None
    custom = {k: getattr(info, k) for k in ("name", "takeoff_mass_kg", "max_dimension_m", "max_speed_ms") if getattr(info, k) is not None}
    if preset is None and not custom:
        return None
    base: dict[str, Any] = {}
    if preset is not None:
        base = {"name": preset.name, "takeoff_mass_kg": preset.takeoff_mass_kg, "max_dimension_m": preset.max_dimension_m,
                "max_speed_ms": preset.max_speed_ms}
    base.update(custom)
    source = "custom" if preset is None else "preset_with_overrides" if custom else "preset"
    return AircraftSummary(preset=info.preset, source=source, **base)  # type: ignore[arg-type]


def _operation_summary(meta: OperationMeta) -> OperationSummary:
    return OperationSummary(
        operator=meta.operator,
        reference=meta.reference,
        aircraft=_aircraft(meta),
        pilot_position=list(meta.pilot_position) if meta.pilot_position else None,
        planned_start_utc=_iso_utc(meta.planned_start_utc),
        planned_end_utc=_iso_utc(meta.planned_end_utc),
        utc_offset_min=meta.utc_offset_min,
        utc_offset_label=fmt_offset(meta.utc_offset_min),
        notes=meta.notes,
    )


def _operation_section(op: OperationSummary) -> BriefSection:
    not_given = "Not given"
    a = op.aircraft
    facts = [
        _fact("Operator", op.operator or not_given),
        _fact("Reference", op.reference or not_given),
        _fact(
            "Aircraft",
            (a.name or "Custom aircraft") if a else not_given,
            note=None if a is None else {"preset": "Class preset (illustrative values)", "custom": "Entered by the operator",
                                         "preset_with_overrides": "Class preset with the operator's values"}.get(a.source or ""),
        ),
    ]
    if a is not None:
        facts += [
            _fact("Take-off mass", f"{fmt_number(a.takeoff_mass_kg)}{NNBSP}kg" if a.takeoff_mass_kg is not None else not_given, a.takeoff_mass_kg, "kg"),
            _fact("Max dimension", fmt_length(a.max_dimension_m) if a.max_dimension_m is not None else not_given, a.max_dimension_m, "m"),
            _fact("Max speed", fmt_speed(a.max_speed_ms) if a.max_speed_ms is not None else not_given, a.max_speed_ms, "m/s"),
        ]
    if op.planned_start_utc:
        start, end = _parse_utc(op.planned_start_utc), _parse_utc(op.planned_end_utc)
        local = fmt_local(op.planned_start_utc, op.utc_offset_min)
        if end is not None:
            local_end = (end + timedelta(minutes=op.utc_offset_min)).strftime("%H:%M")
            text = f"{local[:16]}–{local_end} {op.utc_offset_label}"
            note = f"{fmt_utc(op.planned_start_utc)} to {fmt_utc(op.planned_end_utc)}; {fmt_duration((end - start).total_seconds()) if start else ''}".rstrip("; ")
        else:
            text = local
            note = fmt_utc(op.planned_start_utc)
        facts.append(_fact("Planned window", text, op.planned_start_utc, note=note))
    else:
        facts.append(_fact("Planned window", not_given))
    facts.append(_fact(
        "Pilot / take-off position",
        fmt_coord(op.pilot_position[0], op.pilot_position[1]) if op.pilot_position else not_given,
    ))
    if op.notes:
        facts.append(_fact("Notes", op.notes))
    return BriefSection(key="operation", title=SECTION_TITLES["operation"], status="info",
                        parts=[BriefPart(analysis_id="", kind="operation", title=SECTION_TITLES["operation"], facts=facts)])


def _overlap(a: Sequence[float], b: Sequence[float]) -> bool:
    return not (b[0] > a[2] or b[2] < a[0] or b[1] > a[3] or b[3] < a[1])


def _cross_checks(
    sources: list[_Source], op: OperationSummary, synthetic: bool, packs: Mapping[str, DataPackManifest]
) -> list[Issue]:
    warnings: list[Issue] = []
    if synthetic:
        warnings.append(Issue(code="synthetic_data", severity=Severity.CRITICAL,
                              message="Computed with a synthetic demo data pack: terrain, population and obstacles are invented. Not evidence."))
    datasets = {s.raw.dataset.id for s in sources if s.raw.dataset is not None}
    if len(datasets) > 1:
        warnings.append(Issue(code="different_datasets", severity=Severity.WARNING,
                              message=f"The analyses come from {len(datasets)} different datasets: check they describe the same operation."))
    groups = _area_groups(sources)
    if len(groups) > 1:
        listed = "; ".join(", ".join(s.title for s in group) for group in groups)
        warnings.append(Issue(code="areas_differ", severity=Severity.WARNING, message=(
            f"The analyses cover {len(groups)} separate areas that do not overlap ({listed}): "
            "check they belong to the same operation.")))
    counts: dict[str, int] = {}
    for s in sources:
        counts[s.kind] = counts.get(s.kind, 0) + 1
    for kind, n in counts.items():
        if n > 1:
            warnings.append(Issue(code="several_runs", severity=Severity.INFO,
                                  message=f"{n} {kind_title(kind)} analyses are included; each is shown with its own parameters."))
    aircraft = op.aircraft
    if aircraft is not None:
        for s in sources:
            if s.kind != "ground_risk":
                continue
            dim, speed = _finite(s.params.get("max_dimension_m")), _finite(s.params.get("max_speed_ms"))
            bigger = aircraft.max_dimension_m is not None and dim is not None and aircraft.max_dimension_m > dim + 1e-9
            faster = aircraft.max_speed_ms is not None and speed is not None and aircraft.max_speed_ms > speed + 1e-9
            if bigger or faster:
                warnings.append(Issue(code="aircraft_exceeds_assumption", severity=Severity.WARNING, message=(
                    f"The ground-risk analysis assumed {fmt_length(dim)} and {fmt_speed(speed)}, but the aircraft is "
                    f"{fmt_length(aircraft.max_dimension_m)} and {fmt_speed(aircraft.max_speed_ms)}: the iGRC column may be too low. "
                    "Run it again with the aircraft's values.")))
    start = _parse_utc(op.planned_start_utc)
    if start is not None:
        local_date = (start + timedelta(minutes=op.utc_offset_min)).date().isoformat()
        for s in sources:
            if s.kind == "environment" and isinstance(s.params.get("date"), str) and s.params["date"] != local_date:
                warnings.append(Issue(code="window_date_differs", severity=Severity.WARNING, message=(
                    f"The environment evidence is for {s.params['date']}, but the planned window starts on {local_date} (local): "
                    "sun times and the daylight check may not apply.")))
    for s in sources:
        if s.non_default:
            warnings.append(Issue(code="non_default_parameters", severity=Severity.WARNING, message=(
                f"{s.title} ran with non-default safety parameters: {'; '.join(s.non_default)}. "
                "Check they were intended: zones, obstacles and conflicts may differ from a run with the defaults.")))
        manifest = packs.get(s.datapack) if s.datapack else None
        if s.datapack_sha256 and manifest is not None and manifest.content_sha256 != s.datapack_sha256:
            warnings.append(Issue(code="datapack_changed", severity=Severity.WARNING, message=(
                f"Data pack '{s.datapack}' changed after the {s.title} analysis read it: the pack described in the "
                "provenance is the installed one, not the one used. Run the analysis again.")))
    # Several runs with the same assumptions give the same text: say it once.
    unique: dict[tuple[str, str], Issue] = {}
    for w in warnings:
        unique.setdefault((w.code, w.message), w)
    return list(unique.values())


def _area_groups(sources: list[_Source]) -> list[list[_Source]]:
    """The analyses with known bounds grouped by overlap: two analyses are in one group when a
    chain of overlapping bounding boxes links them (in the brief's order)."""
    boxed = [s for s in sources if s.raw.bbox is not None and len(s.raw.bbox) == 4]
    parent = list(range(len(boxed)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(boxed):
        for j in range(i + 1, len(boxed)):
            if _overlap(a.raw.bbox or [], boxed[j].raw.bbox or []):
                parent[root(j)] = root(i)
    groups: dict[int, list[_Source]] = {}
    for i, s in enumerate(boxed):
        groups.setdefault(root(i), []).append(s)
    return sorted(groups.values(), key=lambda g: boxed.index(g[0]))


def _layer_users(kinds: Iterable[str]) -> dict[str, list[str]]:
    users: dict[str, list[str]] = {}
    for kind in sorted(set(kinds), key=_kind_rank):
        spec = REGISTRY.get(kind)
        if spec is None:
            continue
        for layer in (*spec.requires, *spec.optional):
            users.setdefault(layer, []).append(kind)
    return users


def _pack_provenance(name: str, manifest: DataPackManifest | None, synthetic: bool, kinds: list[str]) -> PackProvenance:
    if manifest is None:
        return PackProvenance(name=name, installed=False, synthetic=synthetic)
    users = _layer_users(kinds)
    order = {key: i for i, key in enumerate(LAYER_KEYS)}
    layers = [
        PackLayerProvenance(key=key, file=entry.file, source=entry.source, licence=entry.licence, used_by=users.get(key, []),
                            sha256=entry.sha256, source_url=entry.source_url)
        for key, entry in sorted(manifest.layers.items(), key=lambda kv: (order.get(kv[0], len(order)), kv[0]))
    ]
    return PackProvenance(
        name=manifest.name,
        version=manifest.version,
        created_utc=manifest.created_utc or None,
        synthetic=manifest.synthetic,
        layers=layers,
        attribution=list(manifest.attribution),
        content_sha256=manifest.content_sha256,
    )


def _analysis_provenance(src: _Source) -> BriefAnalysisProvenance:
    env = src.raw.envelope
    dataset = src.raw.dataset
    note = None
    if dataset is None:
        note = "Inputs given in the parameters (drawn or pasted geometry, or an inline report): covered by params_sha256."
    elif not dataset.sha256:
        note = "The stored upload has no recorded SHA-256."
    return BriefAnalysisProvenance(
        analysis_id=src.id,
        kind=src.kind,
        title=src.title,
        schema_version=str(env.get("schema_version") or SCHEMA_VERSION),
        airbrief_version=str(env.get("airbrief_version") or __version__),
        datapack=src.datapack,
        datapack_synthetic=src.synthetic,
        datapack_sha256=src.datapack_sha256,
        backend=env.get("backend") if isinstance(env.get("backend"), str) else None,
        started_utc=src.started,
        finished_utc=src.finished,
        duration_ms=int(env.get("duration_ms") or 0),
        params_sha256=canonical_sha256(src.stored_params),
        result_sha256=canonical_sha256(src.result),
        dataset_id=dataset.id if dataset else None,
        dataset_filename=dataset.filename if dataset else None,
        input_sha256=dataset.sha256 if dataset else None,
        input_note=note,
    )


def brief_digest(brief: Brief) -> str:
    """SHA-256 of the canonical brief JSON without ``provenance.generated_utc`` and
    ``provenance.brief_sha256`` (see :class:`~airbrief_core.models.brief.BriefProvenance`)."""
    data = brief.model_dump(mode="json")
    data["provenance"].pop("generated_utc", None)
    data["provenance"].pop("brief_sha256", None)
    return canonical_sha256(data)


def _worst(verdicts: Iterable[Verdict | None]) -> Verdict | None:
    known = [v for v in verdicts if v is not None]
    return max(known, key=lambda v: _VERDICT_ORDER[v]) if known else None


def build_brief(
    sources: Sequence[BriefSource],
    operation: OperationMeta | None = None,
    *,
    packs: Mapping[str, DataPackManifest] | None = None,
    generated_utc: datetime | None = None,
    signing_key_id: str | None = None,
) -> Brief:
    """Build the operation evidence brief (see the module docstring).

    Args:
        sources: completed analyses (1 to :data:`~airbrief_core.models.brief.MAX_BRIEF_ANALYSES`);
            they are ordered canonically (by kind, then finish time, then id), so the order
            given does not change the brief or its hash.
        operation: what the operator says about the operation (all optional).
        packs: manifests of the installed data packs by name; a pack an analysis used that is
            missing here is listed as no longer installed.
        generated_utc: the generation time (default: now). It is the only value outside the hash.
        signing_key_id: id of the key that signs this brief's bundle (:mod:`airbrief_core.signing`),
            or None when the instance does not sign; the provenance says which in words.
    """
    if not sources:
        raise ValueError("a brief needs at least one analysis")
    if len(sources) > MAX_BRIEF_ANALYSES:
        raise ValueError(f"a brief holds at most {MAX_BRIEF_ANALYSES} analyses")
    meta = operation or OperationMeta()
    srcs = sorted((_Source(s) for s in sources), key=lambda s: (_kind_rank(s.kind), s.finished, s.id))
    op = _operation_summary(meta)
    readers = [s for s in srcs if s.kind in pack_reading_kinds()]
    mixed = any(s.synthetic for s in readers) and not all(s.synthetic for s in readers)
    for s in srcs:
        s.flag_synthetic = mixed

    parts: dict[str, list[BriefPart]] = {key: [] for key in SECTION_ORDER}
    for src in srcs:
        contributor = _batch if is_batch_result(src.result) else _CONTRIBUTORS.get(src.kind)
        if contributor is None:
            continue
        for key, part in contributor(src).items():
            parts[key].append(part)

    sections = [_operation_section(op)]
    for key in SECTION_ORDER[1:]:
        found = parts[key]
        if not found:
            sections.append(BriefSection(key=key, title=SECTION_TITLES[key], status="not_assessed", reason=_NOT_ASSESSED_REASON[key]))
            continue
        partial = any(p.status == "partial" for p in found)
        sections.append(BriefSection(
            key=key,
            title=SECTION_TITLES[key],
            status="partial" if partial else "assessed",
            verdict=_worst(p.verdict for p in found),
            reason="Some evidence could not be assessed: see the banners." if partial else None,
            parts=found,
        ))

    synthetic = any(reads_synthetic_data(s.kind, s.synthetic) for s in srcs)
    boxes = [s.raw.bbox for s in srcs if s.raw.bbox and len(s.raw.bbox) == 4]
    extent = None
    if boxes:
        extent = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]

    pack_names: dict[str, list[str]] = {}
    pack_synthetic: dict[str, bool] = {}
    for s in srcs:
        if s.datapack:
            pack_names.setdefault(s.datapack, []).append(s.kind)
            pack_synthetic[s.datapack] = pack_synthetic.get(s.datapack, False) or s.synthetic
    installed = packs or {}
    inputs: dict[str, BriefDatasetRef] = {}
    for s in srcs:
        if s.raw.dataset is not None:
            inputs.setdefault(s.raw.dataset.id, s.raw.dataset)
    started = [s.started for s in srcs if s.started]
    finished = [s.finished for s in srcs if s.finished]
    provenance = BriefProvenance(
        airbrief_version=__version__,
        schema_version=SCHEMA_VERSION,
        generated_utc=_iso_utc(generated_utc or datetime.now(UTC)) or "",
        started_utc=min(started) if started else None,
        finished_utc=max(finished) if finished else None,
        operation_sha256=canonical_sha256(meta.model_dump(mode="json")),
        analyses=[_analysis_provenance(s) for s in srcs],
        datapacks=[_pack_provenance(name, installed.get(name), pack_synthetic[name], kinds) for name, kinds in sorted(pack_names.items())],
        inputs=list(inputs.values()),
        signing_key_id=signing_key_id,
        signature_note=signed_note(signing_key_id) if signing_key_id else UNSIGNED_NOTE,
    )
    title = "Operation evidence brief" + (f" — {op.reference}" if op.reference else "")
    brief = Brief(
        title=title,
        notice=EVIDENCE_NOTICE,
        synthetic=synthetic,
        watermark=SYNTHETIC_WATERMARK if synthetic else None,
        operation=op,
        sections=sections,
        warnings=_cross_checks(srcs, op, synthetic, installed),
        extent=extent,
        provenance=provenance,
    )
    brief.provenance.brief_sha256 = brief_digest(brief)
    return brief
