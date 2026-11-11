"""Ground-risk exports for results of the SORA 2.5 operational-volume method.

* :func:`ground_risk_layers` — the square density grid (``density``) and its hotspots
  (``hotspots``, ranked) as polygon FeatureCollections, the same layer names as the H3 cells of
  the earlier footprint method, so the GeoJSON export and the map's "Layers as table" agree.
* :func:`ground_risk_csv` — one CSV holding several tables, each row naming its table in the
  first column: ``volume`` (one row of sizes and Step #8 figures), ``volume_term`` (every
  contingency-volume, buffer and adjacent-area term with its formula and source), ``grid_cell``
  (the averaged density cells) and ``assembly`` (outdoor assemblies within 1 km).

Results of the footprint method (no ``volume``) keep their single H3 ``cells`` table.
"""

from __future__ import annotations

from typing import Any

from airbrief_core.analyses.groundrisk import density_grid_collection

__all__ = ["VOLUME_TABLES", "ground_risk_csv", "ground_risk_layers", "has_volume"]

_VOLUME_FIELDS = (
    "aircraft", "speed_ms", "fg_height_m", "s_cv_m", "h_cv_m", "grb_m", "grb_rule",
    "adjacent_distance_km", "adjacent_outer_km", "grid_m", "grid_source",
)
_RESULT_FIELDS = (
    "igrc", "density_band", "max_density_per_km2", "p95_density_per_km2", "adjacent_mean_density_per_km2",
    "adjacent_people", "adjacent_area_km2", "adjacent_coverage", "evidence_label",
)

VOLUME_TABLES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("volume_term", (
        ("term", "name"), ("label", "label"), ("axis", "axis"), ("value_m", "value_m"),
        ("formula", "formula"), ("source", "source"),
    )),
    ("grid_cell", (
        ("cell_id", "id"), ("ix", "ix"), ("iy", "iy"), ("lon", "lon"), ("lat", "lat"),
        ("density_per_km2", "density_per_km2"), ("density_band", "band"),
    )),
    ("assembly", (
        ("name", "name"), ("assembly_source", "source"), ("kind", "kind"), ("capacity", "capacity"),
        ("size_band", "band_label"), ("distance_m", "distance_m"), ("lon", "lon"), ("lat", "lat"),
        ("start_utc", "start_utc"), ("end_utc", "end_utc"), ("note", "note"),
    )),
)
"""(``table`` value, ((CSV column, result field), ...)) for the list tables of the CSV.
Columns shared by several tables (``lon``/``lat``) mean the same thing in each."""


def has_volume(result: dict[str, Any]) -> bool:
    """True for a result of the operational-volume method."""
    return isinstance(result.get("volume"), dict)


def _rows_of(result: dict[str, Any], table: str) -> list[dict[str, Any]]:
    if table == "volume_term":
        node: Any = (result.get("volume") or {}).get("terms")
    elif table == "grid_cell":
        node = (result.get("density_grid") or {}).get("cells") if isinstance(result.get("density_grid"), dict) else None
    else:
        node = result.get("assemblies")
    return [row for row in node if isinstance(row, dict)] if isinstance(node, list) else []


def ground_risk_csv(result: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows and columns of the operational-volume CSV (see the module docstring)."""
    volume = result.get("volume") or {}
    columns: list[str] = ["table", *_VOLUME_FIELDS, *_RESULT_FIELDS]
    rows: list[dict[str, Any]] = [{
        "table": "volume",
        **{key: volume.get(key) for key in _VOLUME_FIELDS},
        **{key: result.get(key) for key in _RESULT_FIELDS},
    }]
    for table, mapping in VOLUME_TABLES:
        columns += [column for column, _field in mapping if column not in columns]
        for row in _rows_of(result, table):
            rows.append({"table": table, **{column: row.get(field) for column, field in mapping}})
    return rows, columns


def ground_risk_layers(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The density grid and its hotspots as square polygons; empty for the footprint method."""
    cells = density_grid_collection(result, "cells")
    if cells is None:
        return {}
    hotspots = density_grid_collection(result, "hotspots", ranked=True) or {"type": "FeatureCollection", "features": []}
    return {"density": cells, "hotspots": hotspots}
