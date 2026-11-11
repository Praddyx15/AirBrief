"""One result per feature through the API: ``params.batch`` resolved from the dataset, the stored
result, the CSV joined with the attributes (formula cells neutralised), GeoJSON/KML with verdict
properties, the brief and its bundle, and the refusals."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from airbrief_core.io.kml import parse_kml


def _rows(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))


def _square(lon: float, lat: float, d: float = 0.001) -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]]}


def _plots_batch(client: TestClient, helpers: SimpleNamespace, kind: str = "airspace", **params: Any) -> dict[str, Any]:
    dataset = helpers.load_sample(client, "plots_shapefile")
    body = {"kind": kind, "dataset_id": dataset["id"], "params": {"batch": {"mode": "per_feature", "key_property": "name"}, **params}}
    info = helpers.run_analysis(client, body)
    assert info["status"] == "COMPLETED", info["error"]
    return info


def test_batch_params_round_trip(api_client: TestClient, helpers: SimpleNamespace) -> None:
    info = _plots_batch(api_client, helpers)

    batch = info["params"]["batch"]
    assert batch["mode"] == "per_feature" and batch["key_property"] == "name" and batch["feature_count"] == 3
    envelope = api_client.get(f"/api/analyses/{info['id']}/export/", params={"format": "json"}).json()
    stored = envelope["params"]["batch"]["features"]["features"]
    assert [f["properties"]["crop"] for f in stored] == ["Ragi", "Tomato", "Mulberry"]
    assert [f["id"] for f in stored] == [0, 1, 2]
    assert info["params"]["geometry"]["type"] == "Polygon"  # the extent, used to pick the pack

    result = info["result"]
    assert result["batch"] == "per_feature" and result["kind"] == "airspace"
    assert [i["key"] for i in result["items"]] == ["Plot A", "Plot B", "Plot C"]
    assert sum(c["count"] for c in result["counts"]) == 3
    assert len(result["layers"]["features"]["features"]) == 3

    again = api_client.get(f"/api/analyses/{info['id']}/").json()
    assert again["result"]["items"] == result["items"]


def test_listings_carry_a_feature_count_instead_of_the_features(api_client: TestClient, helpers: SimpleNamespace) -> None:
    points = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.48 + i * 0.0004, 12.905]},
               "properties": {"name": f"Mast {i}"}} for i in range(50)]
    body = {"kind": "airspace", "params": {"grid": False, "batch": {"key_property": "name",
                                                                       "features": {"type": "FeatureCollection", "features": points}}}}
    created = api_client.post("/api/analyses/", json=body)
    assert created.status_code == 202, created.text
    info = helpers.wait_for_analysis(api_client, created.json()["id"])
    assert info["status"] == "COMPLETED", info["error"]

    listed = api_client.get("/api/analyses/", params={"ids": info["id"]}).json()["items"]
    single = api_client.get(f"/api/analyses/{info['id']}/").json()
    for shown in (created.json(), listed[0], single):
        batch = shown["params"]["batch"]
        assert "features" not in batch and batch["feature_count"] == 50 and batch["key_property"] == "name"
    assert len(single["result"]["items"]) == 50

    # the JSON envelope keeps what the run was given
    envelope = api_client.get(f"/api/analyses/{info['id']}/export/", params={"format": "json"}).json()
    assert len(envelope["params"]["batch"]["features"]["features"]) == 50
    assert "feature_count" not in envelope["params"]["batch"]


def test_features_sent_with_the_request_are_numbered_by_position(api_client: TestClient, helpers: SimpleNamespace) -> None:
    features = [{"type": "Feature", "id": 7, "geometry": _square(77.49, 12.905), "properties": {"name": n}} for n in ("A", "B")]
    batch = {"from_dataset": True, "features": {"type": "FeatureCollection", "features": features}}
    info = helpers.run_analysis(api_client, {"kind": "survey", "params": {"batch": batch}})
    assert info["status"] == "COMPLETED", info["error"]
    assert info["params"]["batch"]["from_dataset"] is False
    assert [(i["index"], i["key"]) for i in info["result"]["items"]] == [(0, "A"), (1, "B")]


def test_selected_features_only(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "plots_shapefile")
    body = {"kind": "survey", "dataset_id": dataset["id"], "feature_indices": [0, 2], "params": {"batch": {"mode": "per_feature"}}}
    info = helpers.run_analysis(api_client, body)
    assert info["status"] == "COMPLETED", info["error"]
    assert [i["index"] for i in info["result"]["items"]] == [0, 2]


def test_csv_joins_each_feature_with_its_attributes(api_client: TestClient, helpers: SimpleNamespace) -> None:
    info = _plots_batch(api_client, helpers)

    tables = api_client.get(f"/api/analyses/{info['id']}/export/tables/").json()
    assert [t["key"] for t in tables] == ["features"]
    response = api_client.get(f"/api/analyses/{info['id']}/export/?format=csv")
    assert response.status_code == 200
    rows = _rows(response.content)
    assert [r["key"] for r in rows] == ["Plot A", "Plot B", "Plot C"]
    assert [r["crop"] for r in rows] == ["Ragi", "Tomato", "Mulberry"]
    assert [r["owner"] for r in rows] == ["K. Ramaiah", "S. Lakshmi", "Anand Farms Pvt Ltd"]
    # the attribute "name" clashes with the result's own column
    assert [r["attr.name"] for r in rows] == ["Plot A", "Plot B", "Plot C"]
    assert all(r["verdict_label"] and r["check"] in ("pass", "caution", "fail") for r in rows)
    assert all(r["notice"] for r in rows)


def test_csv_neutralises_formula_attributes_and_column_names(api_client: TestClient, helpers: SimpleNamespace) -> None:
    features = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": _square(77.49, 12.905), "properties": {"name": "=HYPERLINK(1)", "=cmd|' /c calc'!A0": "+1+1"}},
    ]}
    info = helpers.run_analysis(api_client, {"kind": "survey", "params": {"batch": {"features": features}}})
    assert info["status"] == "COMPLETED", info["error"]

    content = api_client.get(f"/api/analyses/{info['id']}/export/?format=csv").content.decode("utf-8-sig")
    header, row = list(csv.reader(io.StringIO(content)))[:2]
    assert "'=cmd|' /c calc'!A0" in header and "=cmd|' /c calc'!A0" not in header
    assert row[header.index("'=cmd|' /c calc'!A0")] == "'+1+1"
    assert row[header.index("key")] == "'=HYPERLINK(1)"


def test_geojson_and_kml_carry_the_verdicts(api_client: TestClient, helpers: SimpleNamespace) -> None:
    info = _plots_batch(api_client, helpers)

    doc = api_client.get(f"/api/analyses/{info['id']}/export/?format=geojson").json()
    assert {f["properties"]["_layer"] for f in doc["features"]} == {"features"}
    props = [f["properties"] for f in doc["features"]]
    assert [p["key"] for p in props] == ["Plot A", "Plot B", "Plot C"]
    assert all(p["verdict"] and p["label"] for p in props)
    assert [p["crop"] for p in props] == ["Ragi", "Tomato", "Mulberry"]

    kml = api_client.get(f"/api/analyses/{info['id']}/export/?format=kml")
    assert kml.status_code == 200
    placemarks = [f for layer in parse_kml(kml.content) for f in layer.features]
    assert [p.name for p in placemarks] == ["Plot A", "Plot B", "Plot C"]
    assert all("verdict" in p.properties for p in placemarks)


def test_brief_and_bundle_render_a_batch(api_client: TestClient, helpers: SimpleNamespace) -> None:
    info = _plots_batch(api_client, helpers)

    brief = api_client.post("/api/briefs/", json={"analysis_ids": [info["id"]], "operation": {}}).json()
    section = next(s for s in brief["sections"] if s["key"] == "airspace")
    part = section["parts"][0]
    assert part["title"].endswith("one result per feature")
    assert part["facts"][0]["value"] == 3
    table = part["tables"][0]
    assert table["total"] == 3 and table["columns"][:2] == ["Feature", "Result"]

    bundle = api_client.post("/api/briefs/bundle/", json={"analysis_ids": [info["id"]]})
    assert bundle.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(bundle.content))
    rows = _rows(archive.read("tables/01_airspace_features.csv"))
    assert [r["crop"] for r in rows] == ["Ragi", "Tomato", "Mulberry"]


def test_line_of_sight_batch_leaves_the_observers_out(api_client: TestClient, helpers: SimpleNamespace) -> None:
    collection = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": _square(77.486, 12.901), "properties": {"name": "Block 1"}},
        {"type": "Feature", "geometry": _square(77.492, 12.905), "properties": {"name": "Block 2"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.488, 12.902]}, "properties": {"name": "Remote pilot"}},
    ]}
    upload = helpers.upload(api_client, "blocks.geojson", json.dumps(collection).encode())
    dataset = helpers.wait_for_file(api_client, upload.json()["id"])

    info = helpers.run_analysis(api_client, {"kind": "line_of_sight", "dataset_id": dataset["id"], "params": {"batch": {"mode": "per_feature"}}})
    assert info["status"] == "COMPLETED", info["error"]
    assert [i["key"] for i in info["result"]["items"]] == ["Block 1", "Block 2"]
    assert [o["name"] for o in info["params"]["observers"]] == ["Remote pilot"]


def test_refusals(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "plots_shapefile")
    wrong_kind = api_client.post("/api/analyses/", json={"kind": "evtol_siting", "dataset_id": dataset["id"], "params": {"batch": {"mode": "per_feature"}}})
    assert wrong_kind.status_code == 422
    assert wrong_kind.json()["error"]["code"] == "invalid_params"

    no_features = api_client.post("/api/analyses/", json={"kind": "airspace", "params": {"batch": {"mode": "per_feature"}}})
    assert no_features.status_code == 422

    points = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.5, 12.9]}, "properties": {}} for _ in range(501)]
    too_many = api_client.post("/api/analyses/", json={
        "kind": "line_of_sight", "params": {"batch": {"features": {"type": "FeatureCollection", "features": points}}},
    })
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "too_many_features"
