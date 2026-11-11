"""User-facing wording of API errors."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

from fastapi.testclient import TestClient

MISSING = "00000000-0000-0000-0000-000000000000"


def test_missing_dataset_message_says_dataset_without_the_id(api_client: TestClient) -> None:
    response = api_client.get(f"/api/files/{MISSING}/")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert "dataset" in error["message"]
    assert MISSING not in error["message"]
    assert error["details"]["id"] == MISSING


def test_missing_analysis_message_leaves_the_id_to_details(api_client: TestClient) -> None:
    response = api_client.get(f"/api/analyses/{MISSING}/")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert "analysis" in error["message"]
    assert MISSING not in error["message"]
    assert error["details"]["id"] == MISSING


def test_missing_feature_message_leaves_the_ids_to_details(api_client: TestClient, helpers: SimpleNamespace) -> None:
    info = helpers.load_sample(api_client, "square_1km_equator")
    response = api_client.get(f"/api/files/{info['id']}/features/999/")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert info["id"] not in error["message"]
    assert error["details"]["id"] == f"{info['id']}/999"


def test_shapefile_without_prj_completes_with_nothing_measured(api_client: TestClient, helpers: SimpleNamespace) -> None:
    """The web app shows "Needs CRS" (not "Ready") for exactly this shape."""
    response = api_client.post("/api/samples/no_prj/")
    assert response.status_code == 201, response.text
    info = response.json()
    assert info["status"] == "COMPLETED"
    assert info["crs"] is None
    assert info["feature_count"] > 0
    assert info["totals"]["measured"] == 0


def test_flightmd_report_upload_names_the_flight_audit(
    api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    response = helpers.upload(api_client, "flightmd_report_v15.json", sample_bytes("flightmd_report_v15"))
    assert response.status_code == 201, response.text
    info = response.json()
    assert info["status"] == "FAILED"
    assert info["error"]["code"] == "flightmd_report"
    assert "Flight audit" in info["error"]["message"]
