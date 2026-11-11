"""``ground_risk`` analyses through the API with the SORA 2.5 operational volume: parameters
round trip, the synthetic pack's assemblies, exports (multi-table CSV, square density grid
polygons) and parameter errors; the airspace not-assessed flag on stored results."""

from __future__ import annotations

import csv
import io
import json
import math
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from airbrief_core.analyses.groundrisk import GRAVITY_MS2
from airbrief_core.models.presets import SORA_EVIDENCE_LABEL, UAS_PRESETS
from api.services.analyses import main_table_csv, merged_layers

QUAD = UAS_PRESETS["mapping_quad"]
VOLUME = {**QUAD.volume.model_dump(), "fg_height_m": 100.0}
FAIR = {
    "name": "Harvest fair",
    "geometry": {"type": "Point", "coordinates": [77.4910, 12.9120]},
    "expected_people": 12_000,
    "start_utc": "2026-10-11T04:30:00Z",
    "end_utc": "2026-10-11T12:30:00Z",
    "note": "Organiser's estimate",
}
PARAMS: dict[str, Any] = {
    "max_dimension_m": QUAD.max_dimension_m,
    "max_speed_ms": QUAD.max_speed_ms,
    "takeoff_mass_kg": QUAD.takeoff_mass_kg,
    "altitude_agl_m": 120.0,
    "volume": VOLUME,
    "assemblies": [FAIR],
}


def _rows(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))


@pytest.fixture
def volume_run(api_client: TestClient, helpers: SimpleNamespace) -> dict[str, Any]:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info: dict[str, Any] = helpers.run_analysis(
        api_client, {"kind": "ground_risk", "dataset_id": dataset["id"], "params": PARAMS}
    )
    assert info["status"] == "COMPLETED", info["error"]
    return info


def test_volume_params_round_trip(volume_run: dict[str, Any]) -> None:
    params = volume_run["params"]
    assert params["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert params["volume"]["fg_height_m"] == 100.0
    assert params["volume"]["pitch_angle_deg"] == QUAD.volume.pitch_angle_deg
    assert "grid_m" not in params  # stored as sent: the auto grid stays auto when re-run
    fair = params["assemblies"][0]
    assert fair["name"] == "Harvest fair" and fair["expected_people"] == 12_000
    assert fair["start_utc"].startswith("2026-10-11T04:30:00")

    result = volume_run["result"]
    assert result["evidence_label"] == SORA_EVIDENCE_LABEL
    volume = result["volume"]
    # S_CV = 3 + 1 + 1 + 18 + 18²/(2·g·tan 35°) with the mapping-quad terms.
    s_cm = 18.0**2 / (2 * GRAVITY_MS2 * math.tan(math.radians(QUAD.volume.pitch_angle_deg)))
    assert volume["s_cv_m"] == pytest.approx(3.0 + 1.0 + 1.0 + 18.0 + s_cm, abs=1e-3)
    assert volume["fg_height_m"] == 100.0
    assert volume["adjacent_outer_km"] == 5.0  # 18 m/s × 180 s = 3.24 km → 5 km
    assert volume["grid_m"] == 200.0 and volume["grid_source"] == "auto"
    assert result["density_grid"]["cells"]
    assert result["adjacent_mean_density_per_km2"] is not None
    names = {a["name"]: a for a in result["assemblies"]}
    assert names["Synthetic Kengeri stadium"]["source"] == "datapack"
    assert names["Synthetic Kengeri stadium"]["band"] == "40k_400k"
    assert names["Harvest fair"]["source"] == "user" and names["Harvest fair"]["band"] == "lt_40k"
    assert {"flight_geography", "contingency_volume", "ground_risk_buffer", "adjacent_area", "assemblies"} <= set(
        result["layers"]
    )


def test_volume_exports(api_client: TestClient, volume_run: dict[str, Any]) -> None:
    result = volume_run["result"]
    base = f"/api/analyses/{volume_run['id']}/export/"

    rows = _rows(api_client.get(base, params={"format": "csv"}).content)
    tables = [r["table"] for r in rows]
    assert tables[0] == "volume" and rows[0]["s_cv_m"] == str(result["volume"]["s_cv_m"])
    assert rows[0]["evidence_label"] == SORA_EVIDENCE_LABEL  # the scope label travels with the CSV
    assert tables.count("volume_term") == len(result["volume"]["terms"])
    assert tables.count("grid_cell") == len(result["density_grid"]["cells"])
    assert tables.count("assembly") == len(result["assemblies"])
    cm = next(r for r in rows if r["table"] == "volume_term" and r["term"] == "S_CM")
    assert cm["formula"].startswith("S_CM = v²/(2·g·tan θ)")
    fair = next(r for r in rows if r["table"] == "assembly" and r["name"] == "Harvest fair")
    assert fair["assembly_source"] == "user" and fair["size_band"] == "< 40,000 people"

    merged = json.loads(api_client.get(base, params={"format": "geojson"}).content)
    by_layer: dict[str, list[dict[str, Any]]] = {}
    for feature in merged["features"]:
        by_layer.setdefault(feature["properties"]["_layer"], []).append(feature)
    assert len(by_layer["density"]) == len(result["density_grid"]["cells"])
    assert all(f["geometry"]["type"] == "Polygon" and len(f["geometry"]["coordinates"][0]) == 5
               for f in by_layer["density"])
    assert [f["properties"]["rank"] for f in by_layer["hotspots"]] == list(range(1, len(by_layer["hotspots"]) + 1))
    assert {"flight_geography", "contingency_volume", "ground_risk_buffer"} <= set(by_layer)
    assert by_layer["footprint"][0]["properties"]["evidence"] == SORA_EVIDENCE_LABEL
    # Built at export time: the stored result keeps only its own layers.
    assert "density" not in result["layers"]


def test_footprint_results_keep_their_h3_exports() -> None:
    cell = {"h3": "8961892cdb3ffff", "lon": 77.6, "lat": 12.95, "density_per_km2": 900.0, "band": "< 5,000"}
    result = {"cells": [cell], "hotspots": [cell], "layers": {}}
    rows = _rows(main_table_csv("ground_risk", result, {}))
    assert [r["h3"] for r in rows] == ["8961892cdb3ffff"] and "table" not in rows[0]
    names = {f["properties"]["_layer"] for f in merged_layers(result, "ground_risk")["features"]}
    assert names == {"density", "hotspots"}


def test_volume_parameter_errors(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    bad: list[dict[str, Any]] = [
        {"assemblies": [FAIR]},  # assemblies need the operational volume
        {"grid_m": 200.0},  # so does the grid size
        {"volume": {"aircraft": "helicopter"}},
        {"volume": {"pitch_angle_deg": 89.0}},
        {"volume": {}, "grid_m": 5.0},
        {"volume": {}, "assemblies": [{**FAIR, "geometry": {"type": "LineString", "coordinates": [[77.49, 12.91], [77.5, 12.92]]}}]},
        {"volume": {}, "assemblies": [{**FAIR, "end_utc": "2026-10-11T04:00:00Z"}]},
        {"volume": {}, "assemblies": [{**FAIR, "geometry": {"type": "Point", "coordinates": [437.49, 12.91]}}]},
    ]
    for params in bad:
        response = api_client.post("/api/analyses/", json={"kind": "ground_risk", "dataset_id": dataset["id"], "params": params})
        assert response.status_code == 422, (params, response.text)
        assert response.json()["error"]["code"] == "invalid_params"


def test_meta_exposes_volume_defaults(api_client: TestClient) -> None:
    meta = api_client.get("/api/meta/").json()
    presets = meta["uas_presets"]
    assert presets["fixed_wing"]["volume"]["aircraft"] == "fixed_wing"
    assert presets["mapping_quad"]["volume"] == QUAD.volume.model_dump()
    module = next(m for m in meta["modules"] if m["kind"] == "ground_risk")
    assert module["requires"] == ["population"] and module["optional"] == ["assemblies"]
    assert "Not a GRC, ARC or SAIL." in module["description"]


def test_airspace_outside_every_pack_is_not_assessed(api_client: TestClient, helpers: SimpleNamespace) -> None:
    info = helpers.run_analysis(
        api_client,
        {"kind": "airspace", "params": {"geometry": {"type": "Point", "coordinates": [10.0, 45.0]}, "grid": False}},
    )
    assert info["status"] == "COMPLETED", info["error"]
    assert info["datapack"] is None
    result = info["result"]
    assert result["verdict"] == "caution" and result["assessment_complete"] is False
    assert result["summary"].startswith("Airspace not assessed")
    assert "not_assessed" in {i["code"] for i in result["issues"]}
