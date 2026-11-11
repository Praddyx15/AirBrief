"""Analysis exports are complete: airspace CSV tables beyond zones, H3 results as GeoJSON polygons."""

from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace
from typing import Any

import h3
import pytest
from fastapi.testclient import TestClient

from api.services.analyses import h3_layers, main_table_csv, merged_layers

CELL_A = h3.latlng_to_cell(12.955, 77.605, 9)
CELL_B = h3.latlng_to_cell(12.958, 77.608, 9)


def _rows(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))


def _airspace_result() -> dict[str, Any]:
    return {
        "zones": [],
        "obstacles": [
            {"id": "o1", "kind": "tower", "name": "Mast", "lon": 77.6, "lat": 12.95, "height_m": 60.0,
             "distance_m": 120.0, "clearance_m": 30.0, "conflict": True},
        ],
        "grid": [
            {"h3": CELL_A, "zone": "green", "ceiling_agl_m": 120.0, "terrain_m": 910.0, "status": "free"},
            {"h3": CELL_B, "zone": "yellow", "ceiling_agl_m": None, "status": "permission"},
            {"h3": "not-a-cell", "zone": "green", "status": "free"},
        ],
        "layers": {"input": {"type": "FeatureCollection", "features": []}},
    }


def test_airspace_csv_holds_obstacles_and_grid_without_zones() -> None:
    rows = _rows(main_table_csv("airspace", _airspace_result(), {}))
    assert [r["table"] for r in rows] == ["obstacle", "grid_cell", "grid_cell", "grid_cell"]
    obstacle = rows[0]
    assert obstacle["name"] == "Mast" and obstacle["conflict"] == "True"
    cell = rows[1]
    assert cell["h3"] == CELL_A and cell["status"] == "free" and cell["ceiling_agl_m"] == "120.0"
    lat, lon = h3.cell_to_latlng(CELL_A)
    assert float(cell["lon"]) == pytest.approx(lon) and float(cell["lat"]) == pytest.approx(lat)
    assert list(rows[0])[0] == "table"
    # Zone columns stay in the header for a run that touched zones.
    assert {"source", "permission_required", "overlap_area_m2"} <= set(rows[0])


def test_h3_results_become_polygon_layers() -> None:
    layers = h3_layers("airspace", _airspace_result())
    grid = layers["grid"]["features"]
    assert [f["properties"]["h3"] for f in grid] == [CELL_A, CELL_B]  # the invalid id is skipped
    assert grid[0]["geometry"]["type"] == "Polygon"
    ring = grid[0]["geometry"]["coordinates"][0]
    assert len(ring) == 7 and ring[0] == ring[-1]
    assert grid[1]["properties"]["status"] == "permission"

    merged = merged_layers(_airspace_result(), "airspace")
    assert {f["properties"]["_layer"] for f in merged["features"]} == {"grid"}
    assert len(merged["features"]) == 2
    # Without a kind only the stored result layers are exported.
    assert merged_layers(_airspace_result())["features"] == []


def test_ground_risk_density_and_hotspots_layers() -> None:
    cells = [
        {"h3": CELL_A, "lon": 77.605, "lat": 12.955, "density_per_km2": 9000.0, "band": "< 50,000"},
        {"h3": CELL_B, "lon": 77.608, "lat": 12.958, "density_per_km2": 300.0, "band": "< 500"},
    ]
    result = {"cells": cells, "hotspots": cells[:1], "layers": {}}
    layers = h3_layers("ground_risk", result)
    assert len(layers["density"]["features"]) == 2
    hotspot = layers["hotspots"]["features"][0]["properties"]
    assert hotspot == {"h3": CELL_A, "rank": 1, "density_per_km2": 9000.0, "band": "< 50,000"}
    names = {f["properties"]["_layer"] for f in merged_layers(result, "ground_risk")["features"]}
    assert names == {"density", "hotspots"}


def test_airspace_exports_through_the_api(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info = helpers.run_analysis(
        api_client,
        {"kind": "airspace", "dataset_id": dataset["id"], "params": {"altitude_agl_m": 60, "h3_resolution": 9}},
    )
    assert info["status"] == "COMPLETED", info["error"]
    result = info["result"]
    base = f"/api/analyses/{info['id']}/export/"

    rows = _rows(api_client.get(base, params={"format": "csv"}).content)
    counts = {t: sum(1 for r in rows if r["table"] == t) for t in ("zone", "obstacle", "grid_cell")}
    assert counts == {"zone": len(result["zones"]), "obstacle": len(result["obstacles"]), "grid_cell": len(result["grid"])}
    assert counts["grid_cell"] > 0

    merged = json.loads(api_client.get(base, params={"format": "geojson"}).content)
    grid = [f for f in merged["features"] if f["properties"]["_layer"] == "grid"]
    assert len(grid) == len(result["grid"])
    assert all(f["geometry"]["type"] == "Polygon" for f in grid)
    # The stored result is unchanged: the polygons are built at export time.
    assert "grid" not in result["layers"]
