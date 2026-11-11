"""Analysis exports: GeoJSON of every result layer, KML of the evidence layers and CSV tables.

Every format carries the evidence labels (:data:`~airbrief_core.models.brief.EVIDENCE_NOTICE`,
and :data:`~airbrief_core.models.brief.SYNTHETIC_WATERMARK` when the analysis read a synthetic
data pack): the JSON envelope in ``notice``/``watermark``, the GeoJSON in its ``airbrief``
member, the KML in the document description, each CSV in a last ``notice`` column.

* **GeoJSON** — :func:`merged_layers`: the result's own layers, the ground-risk square density
  grid and the H3 cells of each kind (:data:`H3_LAYERS`) as polygons built at export time, every
  feature tagged with ``_layer``; at most :data:`FEATURE_LIMIT` features, the cut recorded in
  ``airbrief.truncated_layers`` (later layers, the large grids, are cut first).
* **KML** — :func:`analysis_kml`: the same layers, one folder each, as 2D evidence placemarks with
  every property in ``ExtendedData`` (heights stay attributes). Never waypoints or a route file:
  the survey coverage pattern (``lines``) is a visual aid and is not exported to KML.
* **CSV** — :func:`table_csv` with ``table``: one of :func:`table_catalog` for the kind (the first
  is the default). Multi-table defaults (airspace, flight audit, line of sight and ground-risk
  operational volume) hold several tables in one file, each row naming its table in a leading
  ``table`` column; the other entries are the single tables.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import h3
from pydantic import BaseModel

from airbrief_core.batch import batch_table, export_layers, is_batch_result
from airbrief_core.brief import kind_title, reads_synthetic_data
from airbrief_core.geo.h3grid import cells_to_feature_collection
from airbrief_core.io.kml_write import KmlDocument
from airbrief_core.models.analyses import (
    AuditExceedance,
    DensityCell,
    DroneStats,
    FlyabilityCell,
    NoiseCell,
    SiteCandidate,
    SitingCell,
    SurveyBlock,
)
from airbrief_core.models.brief import EVIDENCE_NOTICE, SYNTHETIC_WATERMARK
from airbrief_core.models.common import NearestAerodrome, ObstacleHit, ProfilePoint, ZoneHit
from airbrief_core.models.environment import EmiItem
from airbrief_core.models.tracks import AuditSegment

from .ground_risk import ground_risk_csv, ground_risk_layers, has_volume
from .line_of_sight import line_of_sight_csv

__all__ = [
    "FEATURE_LIMIT",
    "H3_LAYERS",
    "EXPORT_EXCLUDED_LAYERS",
    "KML_EXCLUDED_LAYERS",
    "TableSpec",
    "analysis_kml",
    "evidence_labels",
    "geojson_document",
    "h3_layers",
    "layer_collections",
    "merged_layers",
    "table_catalog",
    "table_csv",
    "table_rows",
]

FEATURE_LIMIT = 50_000
"""Features in one GeoJSON or KML export (the stored result keeps everything)."""

EXPORT_EXCLUDED_LAYERS: dict[str, frozenset[str]] = {
    "survey": frozenset({"lines"}),
}
"""Layers never exported (GeoJSON, KML or bundle): the survey coverage pattern is a visual aid
on the map, not a route, and mission routes belong to the operations platform."""

KML_EXCLUDED_LAYERS = EXPORT_EXCLUDED_LAYERS
"""Kept under its former name (the exclusion now applies to every export format)."""


def evidence_labels(kind: str, datapack_synthetic: bool) -> tuple[str, str | None]:
    """``(notice, watermark)`` of an analysis: the watermark only when it read synthetic data."""
    return EVIDENCE_NOTICE, SYNTHETIC_WATERMARK if reads_synthetic_data(kind, datapack_synthetic) else None


def _notice_cell(kind: str, datapack_synthetic: bool) -> str:
    notice, watermark = evidence_labels(kind, datapack_synthetic)
    return f"{watermark}. {notice}" if watermark else notice


H3_LAYERS: dict[str, tuple[tuple[str, tuple[str, ...], bool], ...]] = {
    "airspace": (("grid", ("grid",), False),),
    "ground_risk": (("density", ("cells",), False), ("hotspots", ("hotspots",), True)),
    "evtol_siting": (("cells", ("cells",), False),),
    "evtol_corridor": (("noise", ("noise", "cells"), False),),
}
"""H3 results drawn as hexagons on the map: (layer name, path to the cell list in the result,
add a 1-based ``rank``). They are exported as polygon layers built at export time, so stored
results stay compact. The names match the map layers (``airspace:grid``, ``ground-risk:density``)."""

_CELL_POSITION_KEYS = frozenset({"h3", "lon", "lat"})


def _cell_rows(result: dict[str, Any], path: tuple[str, ...]) -> list[dict[str, Any]]:
    node: Any = result
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    if not isinstance(node, list):
        return []
    return [row for row in node if isinstance(row, dict) and isinstance(row.get("h3"), str)]


def h3_layers(kind: str, result: dict[str, Any], limit: int | None = None) -> dict[str, dict[str, Any]]:
    """The H3 cell lists of a result as polygon FeatureCollections (``properties.h3`` + the
    cell's values). Invalid cell ids are skipped; with ``limit``, at most that many cells per layer."""
    layers: dict[str, dict[str, Any]] = {}
    for name, path, ranked in H3_LAYERS.get(kind, ()):
        cells: list[str] = []
        props: dict[str, dict[str, Any]] = {}
        for rank, row in enumerate(_cell_rows(result, path), start=1):
            if limit is not None and len(cells) >= limit:
                break
            cell = row["h3"]
            if not h3.is_valid_cell(cell) or cell in props:
                continue
            values = {k: v for k, v in row.items() if k not in _CELL_POSITION_KEYS}
            if ranked:
                values = {"rank": rank, **values}
            cells.append(cell)
            props[cell] = values
        layers[name] = cells_to_feature_collection(cells, props)
    return layers


def _h3_count(kind: str, result: dict[str, Any], name: str) -> int:
    for layer, path, _ranked in H3_LAYERS.get(kind, ()):
        if layer == name:
            return len({row["h3"] for row in _cell_rows(result, path) if h3.is_valid_cell(row["h3"])})
    return 0


def layer_collections(
    result: dict[str, Any], kind: str | None = None, limit: int | None = None
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """The result's layers in export order and, per layer, the features left out by ``limit``.

    Order: the result's own layers, then (with ``kind``) the ground-risk square density grid and
    the kind's H3 polygon layers (a result layer of the same name wins). With ``kind``, the
    layers in :data:`EXPORT_EXCLUDED_LAYERS` are left out. With ``limit``, layers are kept in
    that order until the total reaches it; later features are cut.
    """
    batch = is_batch_result(result)
    layers = export_layers(result) if batch else (result.get("layers") or {})
    if batch:
        kind = None  # its items have no cells or grids to add
    excluded = EXPORT_EXCLUDED_LAYERS.get(kind, frozenset()) if kind is not None else frozenset()
    collections: dict[str, dict[str, Any]] = {
        name: c
        for name, c in (layers.items() if isinstance(layers, dict) else [])
        if isinstance(c, dict) and name not in excluded
    }
    if kind == "ground_risk":
        # Operational-volume results: the square density grid takes the H3 layer names.
        for name, collection in ground_risk_layers(result).items():
            collections.setdefault(name, collection)
    used = sum(len(c.get("features") or []) for c in collections.values())
    built_from_cells: set[str] = set()
    if kind is not None:
        budget = None if limit is None else max(0, limit - used)
        for name, collection in h3_layers(kind, result, budget).items():
            if name not in collections:
                collections[name] = collection
                built_from_cells.add(name)
    truncated: dict[str, int] = {}
    if limit is None:
        return collections, truncated
    remaining = limit
    cut: dict[str, dict[str, Any]] = {}
    for name, collection in collections.items():
        features = [f for f in collection.get("features") or [] if isinstance(f, dict)]
        # H3 layers were built only up to the budget: their size is the number of valid cells.
        total = max(len(features), _h3_count(kind or "", result, name) if name in built_from_cells else 0)
        kept = features[: max(0, remaining)]
        remaining -= len(kept)
        if total > len(kept):
            truncated[name] = total - len(kept)
        cut[name] = {**collection, "features": kept}
    return cut, truncated


def merged_layers(result: dict[str, Any], kind: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """All result layers as one FeatureCollection; each feature gets ``_layer``.

    With ``kind``, the H3 results of that kind (:func:`h3_layers`) are added as polygon layers
    after the result's own layers (a result layer of the same name wins). With ``limit``, at most
    that many features (see :func:`layer_collections`).
    """
    collections, _truncated = layer_collections(result, kind, limit)
    features: list[dict[str, Any]] = []
    for name, collection in collections.items():
        for feature in collection.get("features") or []:
            if not isinstance(feature, dict):
                continue
            copy = dict(feature)
            props = dict(copy.get("properties") or {})
            props["_layer"] = name
            copy["properties"] = props
            features.append(copy)
    return {"type": "FeatureCollection", "features": features}


def geojson_document(
    kind: str, result: dict[str, Any], *, analysis_id: str, datapack: str | None, datapack_synthetic: bool, name: str
) -> dict[str, Any]:
    """The GeoJSON export: :func:`merged_layers` capped at :data:`FEATURE_LIMIT`, with the evidence
    labels and the cut in the ``airbrief`` foreign member (RFC 7946 §6.1)."""
    collections, truncated = layer_collections(result, kind, FEATURE_LIMIT)
    merged = merged_layers({"layers": collections})
    notice, watermark = evidence_labels(kind, datapack_synthetic)
    merged["name"] = name
    merged["airbrief"] = {
        "kind": kind,
        "analysis_id": analysis_id,
        "notice": notice,
        "watermark": watermark,
        "datapack": datapack,
        "datapack_synthetic": bool(datapack_synthetic),
        "feature_limit": FEATURE_LIMIT,
        "feature_count": len(merged["features"]),
        "truncated": bool(truncated),
        "truncated_layers": truncated,
    }
    return merged


def _humanize(key: str) -> str:
    text = key.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "Layer"


_NAME_KEYS = ("name", "key", "label", "ident", "drone", "id", "role", "kind")


def _placemark_name(layer: str, props: dict[str, Any], index: int) -> str:
    for key in _NAME_KEYS:
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    if isinstance(props.get("rank"), int):
        return f"{_humanize(layer)} #{props['rank']}"
    if isinstance(props.get("h3"), str):
        return str(props["h3"])
    return f"{_humanize(layer)} {index + 1}"


_TONE_STYLE = {"ok": "zone-green", "warn": "zone-yellow", "danger": "zone-red"}


def _style(props: dict[str, Any]) -> str:
    if props.get("conflict") is True or props.get("agl_band") == "over":
        return "conflict"
    if props.get("tone") in _TONE_STYLE:  # per-feature results, coloured by their verdict
        return _TONE_STYLE[props["tone"]]
    zone = props.get("zone")
    if zone in ("green", "yellow", "red"):
        return f"zone-{zone}"
    return "evidence"


def _write_layers(doc: KmlDocument, parent: Any, kind: str, collections: dict[str, dict[str, Any]], extra: dict[str, Any]) -> int:
    written = 0
    excluded = EXPORT_EXCLUDED_LAYERS.get(kind, frozenset())
    for name, collection in collections.items():
        if name in excluded:
            continue
        features = [f for f in collection.get("features") or [] if isinstance(f, dict)]
        if not features:
            continue
        folder = doc.folder(_humanize(name), parent=parent)
        for i, feature in enumerate(features):
            props = dict(feature.get("properties") or {})
            data = {"_layer": name, **extra, **props}
            geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else None
            doc.placemark(folder, _placemark_name(name, props, i), data.items(), geometry, style=_style(props))
            written += 1
    return written


def analysis_kml(
    kind: str, result: dict[str, Any], *, analysis_id: str, datapack: str | None, datapack_synthetic: bool, name: str
) -> bytes:
    """The KML export of one analysis (see the module docstring)."""
    collections, truncated = layer_collections(result, kind, FEATURE_LIMIT)
    notice, watermark = evidence_labels(kind, datapack_synthetic)
    description = _kml_description(
        [f"{kind_title(kind)} evidence, analysis {analysis_id}, data pack {datapack or 'none'}."], notice, watermark, truncated, kind
    )
    doc = KmlDocument(name, description, styles=True)
    _write_layers(doc, None, kind, collections, {"_analysis_id": analysis_id})
    return doc.tobytes()


def _kml_description(lead: list[str], notice: str, watermark: str | None, truncated: dict[str, int], kind: str | None) -> str:
    parts = list(lead)
    if watermark:
        parts.insert(0, f"{watermark}.")
    parts.append(notice)
    parts.append("Geometry in WGS84, drawn 2D (clamped to the ground); heights and values are in ExtendedData (SI units).")
    if kind in EXPORT_EXCLUDED_LAYERS or kind is None:
        parts.append("Survey coverage lines are a visual aid and are not exported as routes.")
    if truncated:
        parts.append(f"Cut at {FEATURE_LIMIT:,} features: " + ", ".join(f"{k} −{v:,}" for k, v in truncated.items()) + ".")
    return " ".join(parts)


@dataclass(frozen=True)
class EvidenceItem:
    """One analysis of an evidence bundle (``prefix`` is its fixed file prefix, e.g. ``01_airspace``)."""

    prefix: str
    analysis_id: str
    kind: str
    result: dict[str, Any]
    datapack: str | None
    datapack_synthetic: bool


def _evidence_collections(items: Sequence[EvidenceItem]) -> list[tuple[EvidenceItem, dict[str, dict[str, Any]], dict[str, int]]]:
    out = []
    remaining = FEATURE_LIMIT
    for item in items:
        collections, truncated = layer_collections(item.result, item.kind, remaining)
        remaining -= sum(len(c.get("features") or []) for c in collections.values())
        out.append((item, collections, truncated))
    return out


def evidence_geojson(items: Sequence[EvidenceItem], *, notice: str, watermark: str | None, name: str) -> dict[str, Any]:
    """Every analysis' layers in one FeatureCollection (``_analysis`` + ``_layer`` on each feature)."""
    features: list[dict[str, Any]] = []
    truncated: dict[str, dict[str, int]] = {}
    for item, collections, cut in _evidence_collections(items):
        for feature in merged_layers({"layers": collections})["features"]:
            feature["properties"] = {"_analysis": item.prefix, "_analysis_id": item.analysis_id, **feature["properties"]}
            features.append(feature)
        if cut:
            truncated[item.prefix] = cut
    return {
        "type": "FeatureCollection",
        "name": name,
        "features": features,
        "airbrief": {
            "notice": notice,
            "watermark": watermark,
            "analyses": [{"prefix": i.prefix, "analysis_id": i.analysis_id, "kind": i.kind} for i in items],
            "feature_limit": FEATURE_LIMIT,
            "feature_count": len(features),
            "truncated": bool(truncated),
            "truncated_layers": truncated,
        },
    }


def evidence_kml(items: Sequence[EvidenceItem], *, notice: str, watermark: str | None, name: str) -> bytes:
    """Every analysis' layers in one KML: a folder per analysis, a sub-folder per layer."""
    built = _evidence_collections(items)
    truncated = {f"{item.prefix}/{k}": v for item, _c, cut in built for k, v in cut.items()}
    doc = KmlDocument(name, _kml_description(["Operation evidence brief layers."], notice, watermark, truncated, None), styles=True)
    for item, collections, _cut in built:
        number = item.prefix.split("_", 1)[0]
        folder = doc.folder(f"{number} {kind_title(item.kind)}", f"Analysis {item.analysis_id} ({item.prefix})")
        _write_layers(doc, folder, item.kind, collections, {"_analysis": item.prefix, "_analysis_id": item.analysis_id})
    return doc.tobytes()


Rows = tuple[list[dict[str, Any]], list[str]]


def _model_columns(model: type[BaseModel], skip: frozenset[str] = frozenset()) -> list[str]:
    return [name for name in model.model_fields if name not in skip]


def _flatten_row(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for col in columns:
        value = row.get(col)
        if isinstance(value, dict):
            for key, sub in value.items():
                flat[f"{col}.{key}"] = json.dumps(sub) if isinstance(sub, dict | list) else sub
        elif isinstance(value, list):
            flat[col] = json.dumps(value)
        else:
            flat[col] = value
    return flat


def _with_cell_centre(row: dict[str, Any]) -> dict[str, Any]:
    """``row`` plus the ``lon``/``lat`` of its H3 cell centre when it has a cell and no position."""
    cell = row.get("h3")
    if "lon" in row or not isinstance(cell, str) or not h3.is_valid_cell(cell):
        return row
    lat, lon = h3.cell_to_latlng(cell)
    return {**row, "lon": round(float(lon), 8), "lat": round(float(lat), 8)}


def _node(result: dict[str, Any], path: Sequence[str]) -> Any:
    node: Any = result
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _list_rows(result: dict[str, Any], path: Sequence[str], base_columns: list[str]) -> Rows:
    """The rows of the list at ``path`` flattened to ``base_columns`` (dict values become ``col.sub``)."""
    node = _node(result, path)
    raw_rows = [_with_cell_centre(r) for r in node if isinstance(r, dict)] if isinstance(node, list) else []
    flat_rows = [_flatten_row(r, base_columns) for r in raw_rows]
    columns: list[str] = []
    for col in base_columns:
        expanded = sorted({k for r in flat_rows for k in r if k.startswith(f"{col}.")})
        columns.extend(expanded if expanded else [col])
    return flat_rows, columns


def _lister(path: Sequence[str], columns: list[str]) -> Callable[[dict[str, Any], dict[str, Any]], Rows]:
    return lambda result, _params: _list_rows(result, path, columns)


def _combined(parts: Sequence[tuple[str, Sequence[str], list[str]]]) -> Callable[[dict[str, Any], dict[str, Any]], Rows]:
    """Several lists in one table: (row label, path, columns); the first column names the table."""

    def build(result: dict[str, Any], _params: dict[str, Any]) -> Rows:
        rows: list[dict[str, Any]] = []
        columns = ["table"]
        for label, path, base in parts:
            part_rows, part_columns = _list_rows(result, path, base)
            columns += [c for c in part_columns if c not in columns]
            rows.extend({"table": label, **r} for r in part_rows)
        return rows, columns

    return build


_SHOW_COLUMNS = ["event", "drone_a", "drone_b", "kind", "start_s", "end_s", "t_min_s", "value", "limit"]


def _show_rows(result: dict[str, Any], params: dict[str, Any]) -> Rows:
    raw = params.get("min_separation_m", 1.5)
    min_sep = float(raw) if isinstance(raw, int | float) else None
    rows: list[dict[str, Any]] = []
    for c in result.get("conflicts") or []:
        rows.append({"event": "conflict", "drone_a": c.get("drone_a"), "drone_b": c.get("drone_b"), "kind": "separation",
                     "start_s": c.get("start_s"), "end_s": c.get("end_s"), "t_min_s": c.get("t_min_s"),
                     "value": c.get("min_distance_m"), "limit": min_sep})
    for v in result.get("violations") or []:
        rows.append({"event": "violation", "drone_a": v.get("drone"), "drone_b": None, "kind": v.get("kind"),
                     "start_s": v.get("start_s"), "end_s": v.get("end_s"), "t_min_s": None,
                     "value": v.get("peak_value"), "limit": v.get("limit")})
    pads = result.get("pads")
    for p in (pads.get("conflicts") if isinstance(pads, dict) else None) or []:
        rows.append({"event": "pad_conflict", "drone_a": p.get("drone_a"), "drone_b": p.get("drone_b"), "kind": p.get("phase"),
                     "start_s": p.get("time_s"), "end_s": p.get("time_s"), "t_min_s": None,
                     "value": p.get("distance_m"), "limit": p.get("limit_m")})
    return rows, list(_SHOW_COLUMNS)


_DAYLIGHT_COLUMNS = ["part", "kind", "status", "start", "end", "start_local", "end_local", "start_minute", "end_minute", "minutes"]


def _daylight_rows(result: dict[str, Any], _params: dict[str, Any]) -> Rows:
    raw = result.get("daylight")
    daylight: dict[str, Any] = raw if isinstance(raw, dict) else {}
    rows: list[dict[str, Any]] = []
    for part, key in (("day", "segments"), ("window", "window_segments")):
        for s in daylight.get(key) or []:
            if isinstance(s, dict):
                rows.append({"part": part, **{c: s.get(c) for c in _DAYLIGHT_COLUMNS[1:]}})
    return rows, list(_DAYLIGHT_COLUMNS)


def _columns_by_table(builder: Callable[[dict[str, Any]], Rows], probe: dict[str, Any]) -> dict[str, list[str]]:
    """The columns of each table of a multi-table builder, read from one row of each table built
    from ``probe`` (a result with one empty item per list), so they follow the builder."""
    rows, _columns = builder(probe)
    by_table: dict[str, list[str]] = {}
    for row in rows:
        by_table.setdefault(str(row.get("table")), list(row))
    return by_table


_VOLUME_COLUMNS = _columns_by_table(
    ground_risk_csv, {"volume": {"terms": [{}]}, "density_grid": {"cells": [{}]}, "assemblies": [{}]}
)
_LOS_COLUMNS = _columns_by_table(
    line_of_sight_csv, {"observers": [{}], "radio": {"paths": [{"profile": [{}]}]}, "route": {"hidden_segments": [{}]}}
)


def _part_of(builder: Callable[[dict[str, Any]], Rows], label: str, columns: dict[str, list[str]]) -> Callable[[dict[str, Any], dict[str, Any]], Rows]:
    """One table of a multi-table builder, with that table's own columns."""

    def build(result: dict[str, Any], _params: dict[str, Any]) -> Rows:
        rows, _all = builder(result)
        return [r for r in rows if r.get("table") == label], list(columns[label])

    return build


_SEGMENT_COLUMNS = ["start_m", "end_m", "length_m", "peak_agl_m", "limit_m"]
_ZONES = _model_columns(ZoneHit)
_OBSTACLES = _model_columns(ObstacleHit)
_PROFILE = _model_columns(ProfilePoint)
_GRID = ["h3", "lon", "lat", *_model_columns(FlyabilityCell, frozenset({"h3"}))]
_AUDIT_SEGMENTS = _model_columns(AuditSegment)


@dataclass(frozen=True)
class TableSpec:
    """One CSV table of a kind: the ``?table=`` value, its label and how to build its rows."""

    key: str
    label: str
    build: Callable[[dict[str, Any], dict[str, Any]], Rows]
    multi: bool = False


def _airspace_tables(result: dict[str, Any]) -> list[TableSpec]:
    parts = [
        ("zone", ("zones",), _ZONES),
        ("obstacle", ("obstacles",), _OBSTACLES),
        ("grid_cell", ("grid",), _GRID),
        ("profile_point", ("profile",), _PROFILE),
        ("aerodrome", ("nearest_aerodromes",), _model_columns(NearestAerodrome)),
        ("below_min_agl", ("below_min_agl",), _SEGMENT_COLUMNS),
        ("above_ceiling", ("above_ceiling",), _SEGMENT_COLUMNS),
    ]
    return [
        TableSpec("all", "All tables (one file)", _combined(parts), multi=True),
        TableSpec("zones", "Zones", _lister(("zones",), _ZONES)),
        TableSpec("obstacles", "Obstacles", _lister(("obstacles",), _OBSTACLES)),
        TableSpec("grid", "Flyability grid cells", _lister(("grid",), _GRID)),
        TableSpec("profile", "Height profile", _lister(("profile",), _PROFILE)),
        TableSpec("aerodromes", "Nearest aerodromes", _lister(("nearest_aerodromes",), _model_columns(NearestAerodrome))),
        TableSpec("height_segments", "Below the minimum height / above the ceiling",
                  _combined([("below_min_agl", ("below_min_agl",), _SEGMENT_COLUMNS), ("above_ceiling", ("above_ceiling",), _SEGMENT_COLUMNS)]),
                  multi=True),
    ]


def _ground_risk_tables(result: dict[str, Any]) -> list[TableSpec]:
    if not has_volume(result):
        density = _model_columns(DensityCell)
        return [
            TableSpec("cells", "Density cells", _lister(("cells",), density)),
            TableSpec("hotspots", "Hotspots", _lister(("hotspots",), density)),
        ]
    return [
        TableSpec("all", "All tables (one file)", lambda r, _p: ground_risk_csv(r), multi=True),
        TableSpec("volume", "Operational volume and Step 8 figures", _part_of(ground_risk_csv, "volume", _VOLUME_COLUMNS), multi=True),
        TableSpec("terms", "Volume terms with formulas", _part_of(ground_risk_csv, "volume_term", _VOLUME_COLUMNS), multi=True),
        TableSpec("grid", "Density grid cells", _part_of(ground_risk_csv, "grid_cell", _VOLUME_COLUMNS), multi=True),
        TableSpec("assemblies", "Outdoor assemblies", _part_of(ground_risk_csv, "assembly", _VOLUME_COLUMNS), multi=True),
        TableSpec("hotspots", "Density hotspots", _lister(("density_grid", "hotspots"), ["id", "ix", "iy", "lon", "lat", "density_per_km2", "band"])),
    ]


def _flight_audit_tables(result: dict[str, Any]) -> list[TableSpec]:
    exceed = _model_columns(AuditExceedance)
    parts = [
        ("profile_point", ("profile",), _PROFILE),
        ("exceedance", ("exceedances",), exceed),
        ("above_band_ceiling", ("above_band_ceiling",), exceed),
        ("area_exit", ("area_exits",), _AUDIT_SEGMENTS),
        ("distance", ("distance_exceedances",), _AUDIT_SEGMENTS),
        ("zone", ("zones",), _ZONES),
    ]
    tables = [
        TableSpec("all", "All tables (one file)", _combined(parts), multi=True),
        TableSpec("profile", "Height profile", _lister(("profile",), _PROFILE)),
        TableSpec("exceedances", "Height-limit exceedances", _lister(("exceedances",), exceed)),
        TableSpec("area_exits", "Training-area exits", _lister(("area_exits",), _AUDIT_SEGMENTS)),
        TableSpec("distance", "Beyond the distance from take-off", _lister(("distance_exceedances",), _AUDIT_SEGMENTS)),
        TableSpec("zones", "Zones crossed", _lister(("zones",), _ZONES)),
    ]
    if result.get("above_band_ceiling") is not None:  # band_ceiling "permission" only
        tables.insert(3, TableSpec("above_band_ceiling", "In yellow airspace above a band's ceiling", _lister(("above_band_ceiling",), exceed)))
    return tables


def _line_of_sight_tables(result: dict[str, Any]) -> list[TableSpec]:
    return [
        TableSpec("all", "All tables (one file)", lambda r, _p: line_of_sight_csv(r), multi=True),
        TableSpec("observers", "Pilot, observers and ground station", _part_of(line_of_sight_csv, "observer", _LOS_COLUMNS), multi=True),
        TableSpec("radio_paths", "Radio paths", _part_of(line_of_sight_csv, "radio_path", _LOS_COLUMNS), multi=True),
        TableSpec("radio_profile", "Radio path profiles", _part_of(line_of_sight_csv, "radio_profile", _LOS_COLUMNS), multi=True),
        TableSpec("route_hidden", "Hidden stretches of the route", _part_of(line_of_sight_csv, "route_hidden", _LOS_COLUMNS), multi=True),
    ]


def _evtol_corridor_tables(result: dict[str, Any]) -> list[TableSpec]:
    return [
        TableSpec("profile", "Height profile", _lister(("profile",), _PROFILE)),
        TableSpec("obstacles", "Obstacles", _lister(("obstacles",), _OBSTACLES)),
        TableSpec("zones", "Zones crossed", _lister(("zones",), _ZONES)),
        TableSpec("noise", "Noise cells", _lister(("noise", "cells"), ["h3", "lon", "lat", *_model_columns(NoiseCell, frozenset({"h3"}))])),
        TableSpec("height_segments", "Below the minimum height / above the ceiling",
                  _combined([("below_min_agl", ("below_min_agl",), _SEGMENT_COLUMNS), ("above_ceiling", ("above_ceiling",), _SEGMENT_COLUMNS)]),
                  multi=True),
    ]


_STATIC_TABLES: dict[str, list[TableSpec]] = {
    "evtol_siting": [
        TableSpec("candidates", "Ranked candidates", _lister(("candidates",), _model_columns(SiteCandidate))),
        TableSpec("cells", "Scored cells", _lister(("cells",), ["h3", "lon", "lat", *_model_columns(SitingCell, frozenset({"h3"}))])),
    ],
    "show": [
        TableSpec("events", "Conflicts, violations and pad conflicts", _show_rows),
        TableSpec("drones", "Per-drone statistics", _lister(("drones",), _model_columns(DroneStats))),
    ],
    "survey": [
        TableSpec("blocks", "Battery blocks", _lister(("blocks",), _model_columns(SurveyBlock, frozenset({"polygon"})))),
    ],
    "environment": [
        TableSpec("emi", "Power lines and masts", _lister(("emi",), _model_columns(EmiItem))),
        TableSpec("daylight", "Daylight segments", _daylight_rows),
        TableSpec("sun", "Sun elevation track", _lister(("sun", "track"), ["t", "minute", "elevation_deg", "azimuth_deg"])),
    ],
}

_DYNAMIC_TABLES: dict[str, Callable[[dict[str, Any]], list[TableSpec]]] = {
    "airspace": _airspace_tables,
    "ground_risk": _ground_risk_tables,
    "flight_audit": _flight_audit_tables,
    "line_of_sight": _line_of_sight_tables,
    "evtol_corridor": _evtol_corridor_tables,
}


def table_catalog(kind: str, result: dict[str, Any]) -> list[TableSpec]:
    """The CSV tables of ``kind`` for ``result`` (the first is the default)."""
    if is_batch_result(result):
        return [TableSpec("features", "One row per feature, with its attributes", lambda r, _p: batch_table(r))]
    dynamic = _DYNAMIC_TABLES.get(kind)
    if dynamic is not None:
        return dynamic(result)
    return list(_STATIC_TABLES.get(kind, []))


def table_rows(kind: str, result: dict[str, Any], params: dict[str, Any], table: str | None = None) -> tuple[TableSpec, Rows]:
    """The spec and rows of ``table`` (default: the kind's first table). ``KeyError`` when the
    kind has no such table."""
    catalog = table_catalog(kind, result)
    if not catalog:
        raise KeyError(table or "")
    spec = catalog[0] if table is None else next((t for t in catalog if t.key == table), None)
    if spec is None:
        raise KeyError(table)
    return spec, spec.build(result, params)


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def write_csv(rows: Iterable[dict[str, Any]], columns: list[str], notice: str) -> bytes:
    """UTF-8 CSV with a BOM; text cells (header included) that look like formulas are neutralised;
    a last ``notice`` column carries the evidence label on every row."""
    header = [*columns, "notice"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header, extrasaction="ignore", lineterminator="\n")
    # Per-feature tables name columns after the uploaded file's attributes: the header is user text too.
    writer.writerow({c: _csv_cell(c) for c in header})
    for row in rows:
        writer.writerow({**{k: _csv_cell(row.get(k)) for k in columns}, "notice": notice})
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def table_csv(
    kind: str, result: dict[str, Any], params: dict[str, Any], table: str | None = None, *, datapack_synthetic: bool = False
) -> bytes:
    """One CSV table of an analysis (see the module docstring)."""
    _spec, (rows, columns) = table_rows(kind, result, params, table)
    return write_csv(rows, columns, _notice_cell(kind, datapack_synthetic))
