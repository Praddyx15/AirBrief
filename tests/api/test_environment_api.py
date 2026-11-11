"""``environment`` analyses through the API: geometry from a dataset, EMI from the synthetic pack's
power layer, exports and parameter errors."""

from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from airbrief_core.models.environment import EmiItem

PARAMS = {
    "date": "2026-06-21",
    "window_start": "17:30",
    "window_end": "19:30",
    "utc_offset_min": 330,
    "daylight_profile": "faa_107",
    "height_agl_m": 100,
    "surface_wind_ms": 10,
    "wind_limit_ms": 12,
}


def test_environment_on_bengaluru_survey(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info = helpers.run_analysis(api_client, {"kind": "environment", "dataset_id": dataset["id"], "params": PARAMS})
    assert info["status"] == "COMPLETED", info["error"]
    assert info["params"]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert info["datapack"] == "demo" and info["datapack_synthetic"] is True
    result = info["result"]
    assert result["utc_offset_label"] == "UTC+05:30"
    assert result["sun"]["sunrise"]["local"] == "05:55"
    assert result["sun"]["sunset"]["local"] == "18:48"
    assert result["daylight"]["profile"] == "faa_107"
    assert result["daylight"]["minutes_lighting"] == pytest.approx(23.4, abs=0.5)
    assert result["magnetic"]["model"] == "WMM2025"
    assert result["wind"]["wind_at_height_ms"] == pytest.approx(13.895, abs=0.01)
    emi = {item["name"]: item for item in result["emi"]}
    assert emi["Synthetic 220 kV line (Kengeri–Peenya)"]["distance_m"] == 0.0
    assert emi["Synthetic 33 kV feeder"]["distance_m"] == pytest.approx(40.2, abs=1.5)
    assert emi["Synthetic 33 kV feeder"]["voltage_kv"] == 33.0
    assert {"input", "point", "emi", "emi_search"} <= set(result["layers"])
    assert result["disclaimer"] and result["method"]


def test_environment_exports(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info = helpers.run_analysis(api_client, {"kind": "environment", "dataset_id": dataset["id"], "params": PARAMS})
    assert info["status"] == "COMPLETED", info["error"]
    analysis_id = info["id"]

    response = api_client.get(f"/api/analyses/{analysis_id}/export/", params={"format": "csv"})
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert len(rows) == len(info["result"]["emi"])
    assert list(rows[0]) == [*EmiItem.model_fields, "notice"]  # every export ends with the evidence notice
    assert rows[0]["distance_m"] == "0.0"

    response = api_client.get(f"/api/analyses/{analysis_id}/export/", params={"format": "geojson"})
    assert response.status_code == 200
    collection = json.loads(response.content)
    layers = {f["properties"]["_layer"] for f in collection["features"]}
    assert {"input", "point", "emi", "emi_search"} <= layers

    response = api_client.get(f"/api/analyses/{analysis_id}/export/", params={"format": "json"})
    assert response.status_code == 200
    envelope = json.loads(response.content)
    assert envelope["kind"] == "environment"
    assert envelope["result"]["sun"]["solar_noon"]["local"] == info["result"]["sun"]["solar_noon"]["local"]


def test_environment_with_explicit_geometry_outside_every_pack(api_client: TestClient, helpers: SimpleNamespace) -> None:
    info = helpers.run_analysis(
        api_client,
        {
            "kind": "environment",
            "params": {"geometry": {"type": "Point", "coordinates": [15.65, 78.22]}, "date": "2026-12-21", "utc_offset_min": 60},
        },
    )
    assert info["status"] == "COMPLETED", info["error"]
    assert info["datapack"] is None
    result = info["result"]
    assert result["sun"]["polar_night"] is True
    assert result["emi_assessed"] is False
    assert "emi_not_assessed" in {i["code"] for i in result["issues"]}


def test_environment_on_a_mixed_gpx_dataset(api_client: TestClient, helpers: SimpleNamespace) -> None:
    """A GPX with a waypoint, a route (2D) and a track (3D): the union of its features is taken in
    2D (mixed dimensions used to fail the GeoJSON round trip with 422 for every geometry module)."""
    dataset = helpers.load_sample(api_client, "training_flight_gpx")
    airspace = api_client.post("/api/analyses/", json={"kind": "airspace", "dataset_id": dataset["id"], "params": {}})
    assert airspace.status_code == 202, airspace.text
    info = helpers.run_analysis(
        api_client, {"kind": "environment", "dataset_id": dataset["id"], "params": {"date": "2026-03-21", "utc_offset_min": 330}}
    )
    assert info["status"] == "COMPLETED", info["error"]
    result = info["result"]
    assert result["sun"]["sunrise"] is not None and result["daylight"]["window"] is None
    assert result["emi_assessed"] is True
    assert all(item["distance_m"] <= 300.0 for item in result["emi"])


def test_environment_parameter_errors(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    for params in (
        {},
        {"date": "2026-06-21", "window_start": "18:00"},
        {"date": "21/06/2026"},
        {"date": "2026-06-21", "window_start": "18:00", "window_end": "18:00"},
        {"date": "2026-06-21", "utc_offset_min": 900},
        {"date": "2026-06-21", "emi_radius_m": 5},
        {"date": "2026-06-21", "daylight_profile": "night"},
        {"date": "2026-06-21", "surface_wind_ms": 10, "gust_ms": 5},
    ):
        response = api_client.post("/api/analyses/", json={"kind": "environment", "dataset_id": dataset["id"], "params": params})
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_params"


def test_meta_lists_the_environment_module(api_client: TestClient) -> None:
    meta = api_client.get("/api/meta/").json()
    module = next(m for m in meta["modules"] if m["kind"] == "environment")
    assert module["requires"] == []
    assert set(module["optional"]) == {"dem", "power", "obstacles"}
    schema = module["params_schema"]
    assert "date" in schema["required"]
    assert schema["properties"]["daylight_profile"]["default"] == "india_daylight"
