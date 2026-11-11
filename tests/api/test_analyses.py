"""``/api/analyses/``: input resolution, known answers through the API, errors, cancel, exports."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import api.security as security


def test_survey_on_bengaluru_sample_k6(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info = helpers.run_analysis(
        api_client,
        {
            "kind": "survey",
            "dataset_id": dataset["id"],
            "params": {
                "camera": "p4rtk",
                "altitude_agl_m": 100,
                "front_overlap_pct": 80,
                "side_overlap_pct": 70,
            },
        },
    )
    assert info["status"] == "COMPLETED", info["error"]
    assert info["progress"] == pytest.approx(1.0)
    assert info["params"]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert info["datapack"] == "demo"
    assert info["datapack_synthetic"] is True
    assert info["backend"] in ("cpp", "python")
    assert info["started_at"] and info["finished_at"]
    result = info["result"]
    assert result["gsd_cm"] == pytest.approx(2.74, abs=0.01)
    assert result["footprint_across_m"] == pytest.approx(150.0, rel=5e-3)
    assert result["footprint_along_m"] == pytest.approx(100.0, rel=5e-3)
    assert result["line_spacing_m"] == pytest.approx(45.0, rel=5e-3)
    assert result["trigger_distance_m"] == pytest.approx(20.0, rel=5e-3)

    listed = api_client.get("/api/analyses/", params={"dataset_id": dataset["id"]}).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == info["id"]


def test_survey_exports(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info = helpers.run_analysis(api_client, {"kind": "survey", "dataset_id": dataset["id"], "params": {}})
    assert info["status"] == "COMPLETED", info["error"]
    base = f"/api/analyses/{info['id']}/export/"

    envelope = json.loads(api_client.get(base, params={"format": "json"}).content)
    assert envelope["kind"] == "survey"
    assert envelope["schema_version"]
    assert envelope["airbrief_version"]
    assert envelope["result"]["gsd_cm"] == pytest.approx(info["result"]["gsd_cm"])
    assert envelope["datapack"] == "demo"

    merged = json.loads(api_client.get(base, params={"format": "geojson"}).content)
    assert merged["type"] == "FeatureCollection"
    layers = {f["properties"]["_layer"] for f in merged["features"]}
    assert layers <= set(info["result"]["layers"])
    assert layers

    table = api_client.get(base, params={"format": "csv"})
    assert table.status_code == 200
    rows = list(csv.DictReader(io.StringIO(table.content.decode("utf-8-sig"))))
    assert len(rows) == len(info["result"]["blocks"])
    assert "polygon" not in (rows[0] if rows else {})


def test_show_sample_has_the_injected_conflict_k8(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "show_50_drones")
    info = helpers.run_analysis(api_client, {"kind": "show", "dataset_id": dataset["id"], "params": {}})
    assert info["status"] == "COMPLETED", info["error"]
    assert len(info["params"]["tracks"]) == 50
    assert info["params"]["show_box"]["type"] == "Polygon"
    assert info["params"]["audience"]["type"] == "Polygon"
    result = info["result"]
    assert result["drone_count"] == 50
    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert {conflict["drone_a"], conflict["drone_b"]} == {"D07", "D32"}
    assert conflict["t_min_s"] == pytest.approx(48.0, abs=0.05)
    assert conflict["min_distance_m"] == pytest.approx(0.80, abs=0.05)
    assert result["verdict"] == "fail"

    table = api_client.get(f"/api/analyses/{info['id']}/export/", params={"format": "csv"})
    rows = list(csv.DictReader(io.StringIO(table.content.decode("utf-8-sig"))))
    assert sum(1 for r in rows if r["event"] == "conflict") == 1


def test_flight_audit_with_inline_report_k7(
    api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    report = json.loads(sample_bytes("flightmd_report_v15"))
    info = helpers.run_analysis(
        api_client,
        {"kind": "flight_audit", "params": {"report": report, "use_terrain": False}},
    )
    assert info["status"] == "COMPLETED", info["error"]
    assert info["dataset_id"] is None
    result = info["result"]
    assert result["altitude_reference"] == "takeoff_relative"
    assert result["max_height_above_takeoff_m"] == pytest.approx(135.0, abs=0.5)
    assert len(result["exceedances"]) == 1
    exceedance = result["exceedances"][0]
    assert exceedance["start_distance_m"] == pytest.approx(1157.1, abs=1.0)
    assert exceedance["end_distance_m"] == pytest.approx(1642.9, abs=1.0)
    assert result["verdict"] == "fail"


def test_flight_audit_report_url_is_fetched_server_side(
    api_client: TestClient,
    helpers: SimpleNamespace,
    sample_bytes: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = json.loads(sample_bytes("flightmd_report_v12"))
    seen: list[str] = []

    def fake_fetch(url: str, settings: Any, **kwargs: Any) -> dict[str, Any]:
        seen.append(url)
        return report

    monkeypatch.setattr(security, "fetch_flightmd_report", fake_fetch)
    url = "https://flightmd.example.org/report/abc123"
    info = helpers.run_analysis(
        api_client,
        {"kind": "flight_audit", "params": {"report_url": url, "use_terrain": False}},
    )
    assert seen == [url]
    assert info["status"] == "COMPLETED", info["error"]
    assert "report_url" not in info["params"]
    assert info["params"]["flightmd_url"] == url
    assert info["result"]["max_height_above_takeoff_m"] == pytest.approx(135.0, abs=0.5)


def test_airspace_on_survey_area(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info = helpers.run_analysis(
        api_client,
        {"kind": "airspace", "dataset_id": dataset["id"], "feature_indices": [0], "params": {"altitude_agl_m": 120}},
    )
    assert info["status"] == "COMPLETED", info["error"]
    assert info["feature_indices"] == [0]
    assert info["datapack"] == "demo"
    result = info["result"]
    assert result["verdict"] in ("pass", "caution", "fail")
    assert result["disclaimer"]
    assert "zones" in result["layers"]
    assert result["nearest_aerodromes"]


def test_invalid_kind_is_422(api_client: TestClient) -> None:
    response = api_client.post("/api/analyses/", json={"kind": "teleport", "params": {}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_params"


def test_invalid_params_are_422(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    response = api_client.post(
        "/api/analyses/",
        json={"kind": "survey", "dataset_id": dataset["id"], "params": {"altitude_agl_m": -5}},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_params"
    assert any("altitude_agl_m" in ".".join(map(str, e["loc"])) for e in error["details"]["errors"])

    unknown_field = api_client.post(
        "/api/analyses/",
        json={"kind": "survey", "dataset_id": dataset["id"], "params": {"warp_factor": 9}},
    )
    assert unknown_field.status_code == 422


def test_missing_inputs_and_bad_references(api_client: TestClient, helpers: SimpleNamespace) -> None:
    no_geometry = api_client.post("/api/analyses/", json={"kind": "airspace", "params": {}})
    assert no_geometry.status_code == 422
    assert no_geometry.json()["error"]["code"] == "invalid_params"

    missing = api_client.post(
        "/api/analyses/",
        json={"kind": "airspace", "dataset_id": "00000000-0000-0000-0000-000000000000", "params": {}},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    out_of_range = api_client.post(
        "/api/analyses/",
        json={"kind": "airspace", "dataset_id": dataset["id"], "feature_indices": [5], "params": {}},
    )
    assert out_of_range.status_code == 422

    points_only = helpers.load_sample(api_client, "mixed_features")
    points = api_client.get(
        f"/api/files/{points_only['id']}/measurements/", params={"geometry_type": "Point"}
    ).json()["items"]
    response = api_client.post(
        "/api/analyses/",
        json={
            "kind": "survey",
            "dataset_id": points_only["id"],
            "feature_indices": [points[0]["feature_index"]],
            "params": {},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "geometry_type_not_supported"

    no_tracks = api_client.post("/api/analyses/", json={"kind": "show", "dataset_id": dataset["id"], "params": {}})
    assert no_tracks.status_code == 422
    assert no_tracks.json()["error"]["code"] == "no_tracks"


def test_unknown_analysis_is_404(api_client: TestClient) -> None:
    for response in (
        api_client.get("/api/analyses/nope/"),
        api_client.post("/api/analyses/nope/cancel/"),
        api_client.delete("/api/analyses/nope/"),
        api_client.get("/api/analyses/nope/export/"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_cancel_pending_and_delete(make_client: Callable[..., TestClient], helpers: SimpleNamespace) -> None:
    client = make_client(job_mode="external")  # nothing runs the queue, so it stays PENDING
    dataset = helpers.load_sample(client, "bengaluru_survey")
    created = client.post("/api/analyses/", json={"kind": "survey", "dataset_id": dataset["id"], "params": {}})
    assert created.status_code == 202
    analysis = created.json()
    assert analysis["status"] == "PENDING"
    assert analysis["progress"] == 0

    not_finished = client.get(f"/api/analyses/{analysis['id']}/export/")
    assert not_finished.status_code == 409
    assert not_finished.json()["error"]["code"] == "analysis_not_finished"

    cancelled = client.post(f"/api/analyses/{analysis['id']}/cancel/")
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["error"]["code"] == "cancelled"
    assert client.get(f"/api/analyses/{analysis['id']}").json()["status"] == "CANCELLED"

    deleted = client.delete(f"/api/analyses/{analysis['id']}/")
    assert deleted.status_code == 204
    assert client.get(f"/api/analyses/{analysis['id']}/").status_code == 404


def test_deleting_dataset_removes_its_analyses(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info = helpers.run_analysis(api_client, {"kind": "survey", "dataset_id": dataset["id"], "params": {}})
    assert api_client.delete(f"/api/files/{dataset['id']}/").status_code == 204
    assert api_client.get(f"/api/analyses/{info['id']}/").status_code == 404


def test_pending_dataset_is_not_ready(make_client: Callable[..., TestClient], helpers: SimpleNamespace,
                                      sample_bytes: Callable[[str], bytes]) -> None:
    client = make_client(job_mode="external", inline_process_max_mb=0)
    queued = helpers.upload(client, "survey.kml", sample_bytes("bengaluru_survey")).json()
    assert queued["status"] == "PENDING"
    response = client.post("/api/analyses/", json={"kind": "survey", "dataset_id": queued["id"], "params": {}})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "dataset_not_ready"
