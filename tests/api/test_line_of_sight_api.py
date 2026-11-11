"""``line_of_sight`` analyses through the API: observers resolved from named point features, the
operation geometry without them, explicit parameters, exports (CSV tables, GeoJSON layers) and
input errors."""

from __future__ import annotations

import csv
import io
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.services.line_of_sight import LOS_CSV_COLUMNS, observer_role

AREA = [[77.485, 12.900], [77.497, 12.900], [77.497, 12.904], [77.490, 12.910], [77.485, 12.910], [77.485, 12.900]]
"""The bengaluru_survey pentagon (synthetic pack)."""


def _point(name: str, lon: float, lat: float, **props: Any) -> dict[str, Any]:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": {"name": name, **props}}


def _dataset(client: TestClient, helpers: SimpleNamespace, features: list[dict[str, Any]]) -> dict[str, Any]:
    body = json.dumps({"type": "FeatureCollection", "features": features}).encode()
    response = helpers.upload(client, "operation.geojson", body)
    assert response.status_code in (201, 202), response.text
    info = helpers.wait_for_file(client, response.json()["id"])
    assert info["status"] == "COMPLETED", info
    return info


AREA_FEATURE = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [AREA]}, "properties": {"name": "Survey area"}}
OBSERVER_FEATURES = [
    _point("Remote pilot", 77.488, 12.902, height_agl_m=1.8),
    _point("GCS antenna mast", 77.4875, 12.9015, height_agl_m=6),
    _point("Visual observer 2", 77.495, 12.908),
    _point("Water tank", 77.4995, 12.905),  # not an observer: part of the geometry (east of the area)
]


@pytest.mark.parametrize(
    ("name", "props", "role"),
    [
        ("Pilot", None, "pilot"),
        ("Remote pilot (RPIC)", None, "pilot"),
        ("RPIC", None, "pilot"),
        ("Visual observer", None, "observer"),
        ("VO 2", None, "observer"),
        ("spotter north", None, "observer"),
        ("GCS", None, "gcs"),
        ("Ground control station", None, "gcs"),
        ("ground station mast", None, "gcs"),
        ("Base station", None, "gcs"),
        ("Tower", {"role": "GCS"}, "gcs"),
        ("Pilot", {"role": "observer"}, "observer"),
        ("Autopilot test", None, None),  # whole words only
        ("Voltage pole", None, None),
        ("Survey area", None, None),
        (None, None, None),
    ],
)
def test_observer_role_from_name_or_property(name: str | None, props: dict[str, Any] | None, role: str | None) -> None:
    assert observer_role(name, props) == role


def test_observers_and_geometry_from_named_points(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = _dataset(api_client, helpers, [AREA_FEATURE, *OBSERVER_FEATURES])
    info = helpers.run_analysis(api_client, {"kind": "line_of_sight", "dataset_id": dataset["id"], "params": {}})
    assert info["status"] == "COMPLETED", info["error"]
    params = info["params"]
    assert [(o["name"], o["role"], o.get("height_agl_m")) for o in params["observers"]] == [
        ("Remote pilot", "pilot", 1.8),
        ("GCS antenna mast", "gcs", 6.0),
        ("Visual observer 2", "observer", None),
    ]
    # The geometry is the area and the water tank, never the observer positions.
    assert params["geometry"]["type"] == "GeometryCollection"
    kinds = sorted(g["type"] for g in params["geometry"]["geometries"])
    assert kinds == ["Point", "Polygon"]
    assert info["datapack"] == "demo" and info["datapack_synthetic"] is True

    result = info["result"]
    by_name = {o["name"]: o for o in result["observers"]}
    assert by_name["Remote pilot"]["height_agl_m"] == 1.8 and by_name["Remote pilot"]["los_kind"] == "visual"
    assert by_name["Visual observer 2"]["height_agl_m"] == 1.7
    assert by_name["GCS antenna mast"]["los_kind"] == "radio" and by_name["GCS antenna mast"]["height_agl_m"] == 6.0
    assert result["combined_visible_pct"] >= max(by_name[n]["visible_pct"] for n in ("Remote pilot", "Visual observer 2"))
    assert result["radio"]["station_name"] == "GCS antenna mast"
    assert not any(i["code"] == "observer_assumed" for i in result["issues"])
    assert {"input", "observers", "visible", "hidden", "vlos_guidance", "radio_paths"} <= set(result["layers"])


def test_selection_keeps_dataset_observers(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = _dataset(api_client, helpers, [AREA_FEATURE, *OBSERVER_FEATURES])
    info = helpers.run_analysis(
        api_client, {"kind": "line_of_sight", "dataset_id": dataset["id"], "feature_indices": [0], "params": {}}
    )
    assert info["status"] == "COMPLETED", info["error"]
    assert info["params"]["geometry"]["type"] == "Polygon"
    assert len(info["params"]["observers"]) == 3


def test_explicit_observers_win_and_empty_list_assumes_the_pilot(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = _dataset(api_client, helpers, [AREA_FEATURE, *OBSERVER_FEATURES])
    typed = [{"name": "Pilot on the bund", "role": "pilot", "lon": 77.486, "lat": 12.9005, "height_agl_m": 2.0}]
    info = helpers.run_analysis(
        api_client,
        {"kind": "line_of_sight", "dataset_id": dataset["id"], "feature_indices": [0],
         "params": {"observers": typed, "radio_check": False, "target_height_agl_m": 60}},
    )
    assert info["status"] == "COMPLETED", info["error"]
    assert [o["name"] for o in info["result"]["observers"]] == ["Pilot on the bund"]
    assert info["result"]["radio"] is None and info["result"]["target_height_agl_m"] == 60

    empty = helpers.run_analysis(
        api_client,
        {"kind": "line_of_sight", "dataset_id": dataset["id"], "feature_indices": [0], "params": {"observers": []}},
    )
    assert empty["status"] == "COMPLETED", empty["error"]
    assert empty["result"]["observers"][0]["assumed"] is True
    assert "observer_assumed" in {i["code"] for i in empty["result"]["issues"]}


def test_bengaluru_survey_with_a_typed_observer_and_radio_target(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info = helpers.run_analysis(
        api_client,
        {
            "kind": "line_of_sight",
            "dataset_id": dataset["id"],
            "params": {
                "observers": [{"name": "Pilot", "role": "pilot", "lon": 77.488, "lat": 12.902}],
                "radio_target": {"name": "Relay mast", "lon": 77.497, "lat": 12.910, "height_agl_m": 30},
                "frequency_mhz": 5800,
            },
        },
    )
    assert info["status"] == "COMPLETED", info["error"]
    result = info["result"]
    assert 0 < result["combined_visible_pct"] <= 100
    path = result["radio"]["paths"][0]
    assert path["to_name"] == "Relay mast" and result["radio"]["frequency_mhz"] == 5800
    assert len(path["profile"]) >= 2
    # The pilot is the radio station when no ground station is given.
    assert result["radio"]["station_name"] == "Pilot" and result["radio"]["station_role"] == "pilot"


def test_line_of_sight_exports(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = _dataset(api_client, helpers, [AREA_FEATURE, *OBSERVER_FEATURES[:2]])
    info = helpers.run_analysis(api_client, {"kind": "line_of_sight", "dataset_id": dataset["id"], "params": {}})
    assert info["status"] == "COMPLETED", info["error"]
    analysis_id = info["id"]
    result = info["result"]

    response = api_client.get(f"/api/analyses/{analysis_id}/export/", params={"format": "csv"})
    assert response.status_code == 200, response.text
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert list(rows[0]) == [*LOS_CSV_COLUMNS, "notice"]  # every export ends with the evidence notice
    tables = [r["table"] for r in rows]
    assert tables.count("observer") == len(result["observers"]) == 2
    assert tables.count("radio_path") == len(result["radio"]["paths"]) == 1
    assert tables.count("radio_profile") == len(result["radio"]["paths"][0]["profile"])
    observer = next(r for r in rows if r["table"] == "observer" and r["name"] == "Remote pilot")
    assert float(observer["visible_pct"]) == next(o for o in result["observers"] if o["name"] == "Remote pilot")["visible_pct"]
    path = next(r for r in rows if r["table"] == "radio_path")
    assert path["from_name"] == "GCS antenna mast" and path["frequency_mhz"] == "2400.0"
    assert float(path["fspl_db"]) == result["radio"]["paths"][0]["fspl_db"]
    profile = [r for r in rows if r["table"] == "radio_profile"]
    assert all(r["path"] == "1" for r in profile) and profile[0]["d_m"] == "0.0"

    response = api_client.get(f"/api/analyses/{analysis_id}/export/", params={"format": "geojson"})
    assert response.status_code == 200
    layers = {f["properties"]["_layer"] for f in json.loads(response.content)["features"]}
    assert {"input", "observers", "visible", "vlos_guidance", "radio_paths"} <= layers

    response = api_client.get(f"/api/analyses/{analysis_id}/export/", params={"format": "json"})
    assert response.status_code == 200
    envelope = json.loads(response.content)
    assert envelope["kind"] == "line_of_sight"
    assert envelope["result"]["combined_visible_pct"] == result["combined_visible_pct"]


def test_line_of_sight_input_errors(api_client: TestClient, helpers: SimpleNamespace) -> None:
    only_observers = _dataset(api_client, helpers, OBSERVER_FEATURES[:2])
    response = api_client.post("/api/analyses/", json={"kind": "line_of_sight", "dataset_id": only_observers["id"], "params": {}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_features"

    no_geometry = api_client.post("/api/analyses/", json={"kind": "line_of_sight", "params": {}})
    assert no_geometry.status_code == 422
    assert no_geometry.json()["error"]["code"] == "invalid_params"

    bad_role = api_client.post(
        "/api/analyses/",
        json={"kind": "line_of_sight", "params": {
            "geometry": {"type": "Polygon", "coordinates": [AREA]},
            "observers": [{"name": "Drone", "role": "aircraft", "lon": 77.49, "lat": 12.9}],
        }},
    )
    assert bad_role.status_code == 422
    assert any("role" in ".".join(map(str, e["loc"])) for e in bad_role.json()["error"]["details"]["errors"])

    far = helpers.run_analysis(
        api_client,
        {"kind": "line_of_sight", "params": {
            "geometry": {"type": "Polygon", "coordinates": [AREA]},
            "observers": [{"name": "Pilot", "lon": 78.2, "lat": 12.9}],
        }},
    )
    assert far["status"] == "FAILED"
    assert far["error"]["code"] in ("area_too_large", "out_of_coverage")
