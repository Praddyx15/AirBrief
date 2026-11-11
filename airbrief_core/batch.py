"""One result per feature: an analysis run on each feature of a selection on its own.

Every item gets the request's parameters with only the geometry replaced (a survey keeps the
polygonal part, or the line of a corridor survey). An error on one feature stays on its item and
never stops the run; a cancel stops it at the next checkpoint. The items share the parameter
objects, so what the modules prepare once (the zone polygons in the cache of
:mod:`.analyses.zones`, the pack's aerodrome index, DEM and population readers) serves every item.

Limits: :data:`~airbrief_core.models.batch.BATCH_FEATURE_LIMITS` features, and no feature is
started after :data:`BATCH_TIME_BUDGET_S` (the rest are ``skipped``), so a run ends well inside
the server's job time limit (``JOB_TIMEOUT_S``, 900 s in production).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import shapely

from .analyses import REGISTRY
from .analyses.base import AnalysisContext, ProgressCallback
from .errors import AirBriefError, AnalysisCancelled, AnalysisError
from .geo.transform import geojson_to_geometry, geometry_to_geojson
from .geo.validity import lineal_part, polygonal_part
from .models.batch import (
    BATCH_FEATURE_LIMITS,
    BATCH_KINDS,
    MAX_BATCH_FEATURES,
    BatchCount,
    BatchItem,
    BatchResult,
    BatchTone,
)
from .models.brief import EVIDENCE_NOTICE
from .models.common import GeoJSON, InputModel, Issue, Severity

__all__ = [
    "BATCH_FIELDS",
    "BATCH_TIME_BUDGET_S",
    "batch_table",
    "check_batch_size",
    "export_layers",
    "is_batch_result",
    "run_batch",
]

log = logging.getLogger(__name__)

BATCH_TIME_BUDGET_S = 600.0
"""No feature is started after this long; the rest are recorded as ``skipped``."""

MAX_LAYER_POSITIONS = 200_000
"""Vertices in the result's ``features`` layer; features past it are drawn as a point."""

STAGE = "One result per feature"

BATCH_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "airspace": (
        ("check", "Check"),
        ("zone", "Zone"),
        ("band", "Band"),
        ("effective_ceiling_agl_m", "Free-flight ceiling (m AGL)"),
        ("permission_required", "Permission required"),
        ("obstacle_conflicts", "Obstacle conflicts"),
        ("nearest_aerodrome", "Nearest aerodrome"),
        ("nearest_aerodrome_km", "Nearest aerodrome (km)"),
        ("agl_min_m", "Lowest height above ground (m)"),
        ("agl_max_m", "Highest height above ground (m)"),
        ("area_m2", "Area (m²)"),
        ("length_m", "Length (m)"),
    ),
    "ground_risk": (
        ("igrc", "iGRC"),
        ("band", "Density band"),
        ("max_density_per_km2", "Highest density (people/km²)"),
        ("population", "People in footprint"),
        ("footprint_km2", "Footprint (km²)"),
        ("adjacent_density_per_km2", "Adjacent area density (people/km²)"),
        ("assemblies", "Assemblies within 1 km"),
    ),
    "survey": (
        ("area_ha", "Area (ha)"),
        ("photos", "Photos"),
        ("minutes", "Total time (min)"),
        ("flight_minutes", "Flight time (min)"),
        ("batteries", "Batteries"),
        ("gsd_cm", "GSD (cm/px)"),
        ("line_km", "Flight lines (km)"),
        ("litres", "Spray mix (L)"),
        ("sorties", "Spray sorties"),
    ),
    "environment": (
        ("emi_count", "Power lines and masts nearby"),
        ("emi_nearest_m", "Nearest (m)"),
        ("emi_nearest_kind", "Nearest feature"),
        ("emi_nearest_kv", "Its voltage (kV)"),
        ("daylight_minutes_outside", "Outside the daylight profile (min)"),
        ("declination_deg", "Magnetic declination (°)"),
        ("wind_at_height_ms", "Wind at height (m/s)"),
        ("wind_margin_ms", "Wind margin (m/s)"),
    ),
    "line_of_sight": (
        ("visible_pct", "Visible (%)"),
        ("visible_area_m2", "Visible area (m²)"),
        ("hidden_area_m2", "Hidden area (m²)"),
        ("radio_worst_clearance_pct", "Worst Fresnel clearance (%)"),
        ("beyond_guidance_pct", "Beyond VLOS guidance (%)"),
    ),
}
"""The figures each item keeps per kind (``values`` keys, SI units as in their names), with labels."""

Vocabulary = list[tuple[str, str, BatchTone]]
Summary = Callable[[Any, Any], tuple[str, dict[str, Any]]]

_AIRSPACE: Vocabulary = [
    ("restricted", "Red zone", "danger"),
    ("permission", "Permission required", "warn"),
    ("limited", "Limited", "warn"),
    ("not_assessed", "Not assessed", "neutral"),
    ("free", "Free", "ok"),
]
_LINE_OF_SIGHT: Vocabulary = [
    ("hidden", "Not visible", "danger"),
    ("partly_hidden", "Partly hidden", "warn"),
    ("visible", "Fully visible", "ok"),
    ("not_assessed", "Not assessed", "neutral"),
]


def _round(value: Any, digits: int = 2) -> float | None:
    return round(float(value), digits) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _airspace(r: Any, _p: Any) -> tuple[str, dict[str, Any]]:
    conflicts = sum(1 for o in r.obstacles if o.conflict)
    planned = r.agl_max_m if r.height_reference != "agl" else r.altitude_agl_m
    ceiling = r.effective_ceiling_agl_m
    heights = bool(r.below_min_agl or r.above_ceiling or r.cells_below_min_agl or r.cells_above_ceiling)
    limited = conflicts > 0 or heights or (ceiling is not None and planned is not None and ceiling < planned)
    if any(z.zone == "red" for z in r.zones):
        verdict = "restricted"
    elif r.permission_required:
        verdict = "permission"
    elif limited or r.verdict == "fail":
        verdict = "limited"
    elif r.verdict != "pass":
        # caution with nothing found: a data gap (no pack, outside it, no DEM), never "free"
        verdict = "not_assessed"
    else:
        verdict = "free"
    top = r.zones[0] if r.zones else None
    band = None
    if top is not None:
        band = f"{top.ident or top.name} {top.ring.split(':')[0]}" if top.ring else top.name
    nearest = r.nearest_aerodromes[0] if r.nearest_aerodromes else None
    return verdict, {
        "check": str(r.verdict.value),
        "zone": top.zone if top is not None else None,
        "band": band,
        "effective_ceiling_agl_m": ceiling,
        "permission_required": r.permission_required,
        "obstacle_conflicts": conflicts,
        "nearest_aerodrome": nearest.ident if nearest is not None else None,
        "nearest_aerodrome_km": nearest.distance_km if nearest is not None else None,
        "agl_min_m": r.agl_min_m if r.height_reference != "agl" else None,
        "agl_max_m": r.agl_max_m if r.height_reference != "agl" else None,
        "area_m2": r.area_m2,
        "length_m": r.length_m,
    }


def _ground_risk(r: Any, _p: Any) -> tuple[str, dict[str, Any]]:
    verdict = f"igrc_{r.igrc}" if r.igrc is not None else "out_of_scope"
    return verdict, {
        "igrc": r.igrc,
        "band": r.density_band,
        "max_density_per_km2": _round(r.max_density_per_km2, 1),
        "population": _round(r.population_in_footprint, 1),
        "footprint_km2": _round(r.footprint_area_km2, 4),
        "adjacent_density_per_km2": _round(r.adjacent_mean_density_per_km2, 1),
        "assemblies": len(r.assemblies),
    }


def _survey(r: Any, _p: Any) -> tuple[str, dict[str, Any]]:
    batteries = max(1, int(r.batteries))
    spray = r.spray
    return f"batteries_{min(batteries, 4)}", {
        "area_ha": _round(r.area_ha, 3),
        "photos": r.photos if r.mode != "spraying" else None,  # spraying doesn't take photos
        "minutes": _round(r.total_minutes, 1),
        "flight_minutes": _round(r.flight_minutes, 1),
        "batteries": r.batteries,
        "gsd_cm": _round(r.gsd_cm),
        "line_km": _round(r.line_km, 3),
        "litres": _round(spray.litres, 1) if spray is not None else None,
        "sorties": spray.sorties if spray is not None else None,
    }


def _environment(r: Any, _p: Any) -> tuple[str, dict[str, Any]]:
    # sun, daylight and wind hardly differ between neighbouring features, so the verdict is the interference
    nearest = min(r.emi, key=lambda e: e.distance_m, default=None)
    if not r.emi_assessed:
        verdict = "emi_not_checked"
    elif any(e.inside for e in r.emi):
        verdict = "emi_inside"
    else:
        verdict = "emi_near" if r.emi else "emi_none"
    wind = r.wind
    return verdict, {
        "emi_count": len(r.emi) if r.emi_assessed else None,
        "emi_nearest_m": _round(nearest.distance_m, 1) if nearest is not None else None,
        "emi_nearest_kind": nearest.kind if nearest is not None else None,
        "emi_nearest_kv": nearest.voltage_kv if nearest is not None else None,
        "daylight_minutes_outside": _round(r.daylight.minutes_outside, 1),
        "declination_deg": _round(r.magnetic.declination_deg),
        "wind_at_height_ms": _round(wind.wind_at_height_ms, 1) if wind is not None else None,
        "wind_margin_ms": _round(wind.margin_ms, 1) if wind is not None else None,
    }


def _line_of_sight(r: Any, _p: Any) -> tuple[str, dict[str, Any]]:
    route = r.route
    if route is not None:
        pct, hidden = route.visible_pct, bool(route.hidden_segments)
    else:
        pct, hidden = r.combined_visible_pct, (r.hidden_area_m2 or 0.0) > 0.0
    if pct is None:
        verdict = "not_assessed"
    elif not hidden:
        verdict = "visible"
    else:
        verdict = "hidden" if pct <= 0.0 else "partly_hidden"
    radio = r.radio
    return verdict, {
        "visible_pct": pct,
        "visible_area_m2": r.visible_area_m2 if route is None else None,
        "hidden_area_m2": r.hidden_area_m2 if route is None else None,
        "radio_worst_clearance_pct": radio.worst_clearance_pct if radio is not None else None,
        "beyond_guidance_pct": r.beyond_guidance_pct,
    }


_SUMMARIES: dict[str, Summary] = {
    "airspace": _airspace,
    "ground_risk": _ground_risk,
    "survey": _survey,
    "environment": _environment,
    "line_of_sight": _line_of_sight,
}


def _igrc_tone(igrc: int) -> BatchTone:
    # same bands as the ground-risk tile and the brief
    return "danger" if igrc >= 7 else "warn" if igrc >= 5 else "ok"


# every verdict of a kind, most severe first (survey: fewest batteries first)
def _vocabulary(kind: str, params: Any) -> Vocabulary:
    if kind == "airspace":
        return _AIRSPACE
    if kind == "ground_risk":
        igrc: Vocabulary = [("out_of_scope", "Beyond Table 2", "danger")]
        return igrc + [(f"igrc_{g}", f"iGRC {g}", _igrc_tone(g)) for g in range(10, 0, -1)]
    if kind == "survey":
        return [("batteries_1", "1 battery", "neutral"), ("batteries_2", "2 batteries", "neutral"),
                ("batteries_3", "3 batteries", "neutral"), ("batteries_4", "4 or more batteries", "neutral")]
    if kind == "environment":
        radius = f"{float(params.emi_radius_m):g} m"
        return [
            ("emi_inside", "Power line or mast inside", "warn"),
            ("emi_near", f"Power line or mast within {radius}", "info"),
            ("emi_none", f"None within {radius}", "ok"),
            ("emi_not_checked", "Interference not checked", "neutral"),
        ]
    return _LINE_OF_SIGHT


def is_batch_result(result: Mapping[str, Any] | None) -> bool:
    """True for the stored result of a per-feature run (:class:`~airbrief_core.models.batch.BatchResult`)."""
    return isinstance(result, Mapping) and result.get("batch") == "per_feature"


def check_batch_size(kind: str, count: int) -> None:
    """Raises ``too_many_features`` when ``count`` is over the per-feature limit of ``kind``."""
    limit = BATCH_FEATURE_LIMITS.get(kind, MAX_BATCH_FEATURES)
    if count > limit:
        raise AnalysisError(
            "too_many_features",
            f"One result per feature takes at most {limit:,} features in this module; the selection has {count:,}. "
            "Select fewer features and run the rest as another batch.",
            details={"feature_count": count, "limit": limit},
        )


def _item_geometry(kind: str, params: Any, geometry: Any) -> GeoJSON:
    if not isinstance(geometry, dict) or "type" not in geometry:
        raise AnalysisError("invalid_geometry", "This feature has no geometry (unknown CRS or empty).")
    if kind != "survey":
        return geometry
    corridor = params.coverage == "corridor"
    geom = geojson_to_geometry(geometry)
    part = lineal_part(geom) if corridor else polygonal_part(geom)
    if part is None or part.is_empty:
        raise AnalysisError(
            "geometry_type_not_supported",
            "Corridor mapping needs a line: this feature has none." if corridor else "The survey needs an area: this feature is not a polygon.",
        )
    return geometry_to_geojson(shapely.force_2d(part), decimals=8)


def _feature_name(props: Mapping[str, Any]) -> str | None:
    for key in ("_name", "name", "Name", "NAME"):
        value = props.get(key)
        if isinstance(value, str | int | float) and not isinstance(value, bool) and str(value).strip():
            return str(value).strip()[:200]
    return None


def _key(props: Mapping[str, Any], key_property: str | None, name: str | None, index: int) -> tuple[str, bool]:
    value = props.get(key_property) if key_property else None
    if value is not None and not isinstance(value, dict | list) and str(value).strip():
        return str(value).strip()[:200], True
    fid = props.get("_fid")
    return name or (str(fid)[:200] if fid not in (None, "") else f"#{index}"), False


def _progress(ctx: AnalysisContext, i: int, n: int) -> ProgressCallback:
    # one stage name for the whole run: the job writes a stage change at once, a fraction at most twice a second
    return lambda _stage, fraction: ctx.progress(STAGE, (i + fraction) / n)


def _positions(geometry: GeoJSON) -> int:
    try:
        return int(shapely.get_num_coordinates(geojson_to_geometry(geometry)))
    except AirBriefError:
        return 0


def _layer(features: Sequence[Mapping[str, Any]], items: list[BatchItem], labels: dict[str, tuple[str, BatchTone]]) -> tuple[GeoJSON, int]:
    out: list[GeoJSON] = []
    used = 0
    points = 0
    for feature, item in zip(features, items, strict=True):
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or "type" not in geometry:
            continue
        size = _positions(geometry)
        if size == 0:
            continue
        if used + size > MAX_LAYER_POSITIONS:
            point = geojson_to_geometry(geometry).representative_point()
            geometry, size = geometry_to_geojson(point, decimals=8), 1
            points += 1
        used += size
        if item.status == "ok" and item.verdict is not None:
            label, tone = labels.get(item.verdict, (item.verdict, "neutral"))
        else:
            label, tone = ("Not run (time budget)" if item.status == "skipped" else "Could not be assessed"), "neutral"
        props = {"index": item.index, "key": item.key, "name": item.name, "status": item.status,
                 "verdict": item.verdict, "label": label, "tone": tone, **item.values}
        out.append({"type": "Feature", "id": item.index, "geometry": geometry, "properties": props})
    return {"type": "FeatureCollection", "features": out}, points


def run_batch(
    kind: str,
    base_params: InputModel,
    features: Sequence[Mapping[str, Any]],
    ctx: AnalysisContext,
    key_property: str | None = None,
    from_dataset: bool = False,
) -> BatchResult:
    """Run ``kind`` on every feature on its own (see the module docstring).

    ``features`` are GeoJSON Features. An item's ``index`` is unique per item: with
    ``from_dataset`` (the API read them from a dataset) the feature's integer ``id``, its dataset
    feature number; otherwise, or when those ids are not all different integers, its position in
    ``features``. ``properties._name`` or ``name`` names it, and the other properties are kept as
    its ``attributes``. Raises ``invalid_params`` for a kind without per-feature results,
    ``too_many_features`` above its limit, and the module's ``datapack_missing`` /
    ``layer_missing`` once (not on every item) when the pack lacks a required layer.
    """
    spec = REGISTRY.get(kind)
    if spec is None or kind not in BATCH_KINDS:
        raise AnalysisError(
            "invalid_params",
            f"One result per feature is not available for '{kind}'.",
            details={"kind": kind, "known": list(BATCH_KINDS)},
        )
    check_batch_size(kind, len(features))
    if spec.requires:
        ctx.require(*spec.requires)
    summarise = _SUMMARIES[kind]
    n = len(features)
    # a client's ids can repeat or match another feature's position: only a dataset's are kept,
    # and only while every one is a different number
    indices = list(range(n))
    if from_dataset:
        ids = [fid for fid in (f.get("id") for f in features) if isinstance(fid, int) and not isinstance(fid, bool)]
        if len(ids) == n and len(set(ids)) == n:
            indices = ids
    deadline = time.monotonic() + BATCH_TIME_BUDGET_S
    items: list[BatchItem] = []
    notes: dict[str, tuple[Issue, int]] = {}
    disclaimer: str | None = None
    unkeyed = 0

    for i, feature in enumerate(features):
        ctx.step(STAGE, i / n)
        raw_props = feature.get("properties")
        props: Mapping[str, Any] = raw_props if isinstance(raw_props, dict) else {}
        index = indices[i]
        name = _feature_name(props)
        key, keyed = _key(props, key_property, name, index)
        if key_property and not keyed:
            unkeyed += 1
        item = BatchItem(index=index, key=key, name=name, attributes={k: v for k, v in props.items() if not str(k).startswith("_")})
        items.append(item)
        if time.monotonic() > deadline:
            item.status, item.error_code = "skipped", "batch_time_budget"
            item.error = "Not run: the batch reached its time budget."
            continue
        child = AnalysisContext(datapack=ctx.datapack, progress=_progress(ctx, i, n), cancel_event=ctx.cancel_event)
        try:
            geometry = _item_geometry(kind, base_params, feature.get("geometry"))
            # a shallow copy: the items share zone rules and user zones, so the zone cache hits
            result = spec.run(base_params.model_copy(update={"geometry": geometry, "batch": None}), child)
        except AnalysisCancelled:
            raise
        except AirBriefError as exc:
            item.status, item.error_code, item.error = "error", exc.code, exc.message
            continue
        except Exception:  # a module bug on one feature must not lose the others
            log.exception("%s failed on feature %s of a per-feature run", kind, index)
            item.status, item.error_code = "error", "internal_error"
            item.error = "The analysis failed unexpectedly for this feature."
            continue
        item.verdict, item.values = summarise(result, base_params)
        item.issues = sorted({issue.code for issue in result.issues})
        for issue in result.issues:
            first, count = notes.get(issue.code, (issue, 0))
            notes[issue.code] = (first, count + 1)
        disclaimer = disclaimer or getattr(result, "disclaimer", None)

    ctx.step("Summary", 1.0)
    vocabulary = _vocabulary(kind, base_params)
    tally: dict[str, int] = {}
    for item in items:
        if item.status == "ok" and item.verdict is not None:
            tally[item.verdict] = tally.get(item.verdict, 0) + 1
    counts = [BatchCount(verdict=v, label=label, tone=tone, count=tally[v]) for v, label, tone in vocabulary if tally.get(v)]
    errors = sum(1 for item in items if item.status == "error")
    skipped = sum(1 for item in items if item.status == "skipped")
    layer, points = _layer(features, items, {v: (label, tone) for v, label, tone in vocabulary})

    issues = list(ctx.issues)
    if errors:
        issues.append(Issue(code="feature_errors", severity=Severity.WARNING,
                            message=f"{errors:,} of {n:,} features could not be assessed: their rows give the reason."))
    if skipped:
        issues.append(Issue(code="batch_time_budget", severity=Severity.WARNING, message=(
            f"No new feature was started after {BATCH_TIME_BUDGET_S / 60:g} minutes: {skipped:,} features were not run. "
            "Select them and run them as another batch.")))
    if unkeyed:
        issues.append(Issue(code="key_property_missing", severity=Severity.INFO,
                            message=f"{unkeyed:,} features have no '{key_property}': their name or number is used as the key."))
    if points:
        issues.append(Issue(code="layer_points", severity=Severity.INFO, message=(
            f"{points:,} features are drawn as a point on the map: the result layer holds at most "
            f"{MAX_LAYER_POSITIONS:,} vertices. Every feature is in the CSV export.")))
    for first, count in notes.values():
        message = first.message if count == 1 else f"{first.message} ({count:,} of {n:,} features)"
        issues.append(first.model_copy(update={"message": message, "feature_index": None}))

    parts = [f"{c.label}: {c.count:,}" for c in counts]
    summary = f"{n:,} feature{'' if n == 1 else 's'}, one result each." + (f" {'; '.join(parts)}." if parts else "")
    if errors:
        summary += f" {errors:,} could not be assessed."
    if skipped:
        summary += f" {skipped:,} were not run (time budget)."
    keyed_by = f"the '{key_property}' property" if key_property else "the feature name, or its number"
    method = (
        f"One result per feature: each of the {n:,} features ran through the {spec.title} module on its own, with the "
        f"same parameters; only the geometry changes. Features are keyed by {keyed_by}. A feature that cannot be "
        f"assessed records its error and the others go on; no feature starts after {BATCH_TIME_BUDGET_S / 60:g} minutes."
    )
    return BatchResult(
        kind=kind,
        key_property=key_property,
        feature_count=n,
        ok=n - errors - skipped,
        errors=errors,
        skipped=skipped,
        counts=counts,
        items=items,
        summary=summary,
        method=method,
        disclaimer=disclaimer or EVIDENCE_NOTICE,
        issues=issues,
        layers={"features": layer},
        points_only=points,
    )


_RESERVED = frozenset({"index", "key", "name", "status", "verdict", "verdict_label", "label", "tone", "issues",
                       "error", "error_code", "notice", "table"})


def _attribute_column(name: str, taken: set[str]) -> str:
    # an attribute named like one of our columns keeps its value under attr.<name>
    return f"attr.{name}" if name in _RESERVED or name in taken else name


def batch_table(result: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """One row per feature: its result, then its original attributes (``attr.<name>`` when the
    name is already a result column). Dict and list values are JSON text."""
    kind = str(result.get("kind"))
    fields = [key for key, _label in BATCH_FIELDS.get(kind, ())]
    base = ["index", "key", "name", "status", "verdict", "verdict_label", *fields, "issues", "error_code", "error"]
    labels = {c.get("verdict"): c.get("label") for c in result.get("counts") or [] if isinstance(c, dict)}
    attributes: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        raw = item.get("values")
        values: dict[str, Any] = raw if isinstance(raw, dict) else {}
        row: dict[str, Any] = {
            "index": item.get("index"),
            "key": item.get("key"),
            "name": item.get("name"),
            "status": item.get("status"),
            "verdict": item.get("verdict"),
            "verdict_label": labels.get(item.get("verdict")),
            **{k: values.get(k) for k in fields},
            "issues": " ".join(str(code) for code in item.get("issues") or []),
            "error_code": item.get("error_code"),
            "error": item.get("error"),
        }
        for name, value in (item.get("attributes") or {}).items():
            column = attributes.get(str(name))
            if column is None:
                column = attributes[str(name)] = _attribute_column(str(name), set(base) | set(fields))
            row[column] = json.dumps(value, ensure_ascii=False) if isinstance(value, dict | list) else value
        rows.append(row)
    return rows, base + list(attributes.values())


def export_layers(result: Mapping[str, Any]) -> dict[str, GeoJSON]:
    """The ``features`` layer with each feature's attributes added to its properties (named as in
    :func:`batch_table`), for the GeoJSON and KML exports. Matched on ``index``, unique per item
    (see :func:`run_batch`)."""
    layer = (result.get("layers") or {}).get("features")
    if not isinstance(layer, dict):
        return {}
    by_index = {item.get("index"): item.get("attributes") or {} for item in result.get("items") or [] if isinstance(item, dict)}
    features = []
    for feature in layer.get("features") or []:
        if not isinstance(feature, dict):
            continue
        props = dict(feature.get("properties") or {})
        taken = set(props)
        for name, value in by_index.get(props.get("index"), {}).items():
            props[_attribute_column(str(name), taken)] = value
        features.append({**feature, "properties": props})
    return {"features": {"type": "FeatureCollection", "features": features}}
