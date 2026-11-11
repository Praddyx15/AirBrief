"""``/api/samples/``: list, load as a dataset, download.

(Named ``test_samples_api.py`` because ``tests/core/test_samples.py`` exists and the test folders
have no ``__init__.py``.)
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from airbrief_core.samples import SAMPLES


def test_list_samples(api_client: TestClient) -> None:
    response = api_client.get("/api/samples/")
    assert response.status_code == 200
    samples = response.json()
    assert {s["name"] for s in samples} == set(SAMPLES)
    survey = next(s for s in samples if s["name"] == "bengaluru_survey")
    assert set(survey) == {"name", "title", "description", "filename", "module"}
    assert survey["module"] == "survey"
    assert api_client.get("/api/samples").status_code == 200


def test_load_sample_creates_completed_dataset(api_client: TestClient) -> None:
    response = api_client.post("/api/samples/square_1km_equator/")
    assert response.status_code == 201
    info = response.json()
    assert info["status"] == "COMPLETED"
    assert info["sample"] == "square_1km_equator"
    assert info["filename"] == "square_1km_equator.kml"
    assert info["feature_count"] == 1
    listed = api_client.get("/api/files/").json()
    assert [item["id"] for item in listed["items"]] == [info["id"]]


def test_load_sample_without_trailing_slash(api_client: TestClient) -> None:
    response = api_client.post("/api/samples/plots_shapefile")
    assert response.status_code == 201
    info = response.json()
    assert info["file_type"] == "shapefile"
    assert info["feature_count"] == 3


def test_unknown_sample_is_404(api_client: TestClient) -> None:
    for response in (
        api_client.post("/api/samples/nope/"),
        api_client.get("/api/samples/nope/download/"),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


def test_flightmd_sample_is_not_a_dataset(api_client: TestClient) -> None:
    response = api_client.post("/api/samples/flightmd_report_v15/")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "sample_not_dataset"


def test_attack_sample_is_rejected(api_client: TestClient) -> None:
    response = api_client.post("/api/samples/attack_zipslip/")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_archive"
    assert api_client.get("/api/files/").json()["total"] == 0


def test_download_sample(api_client: TestClient, sample_bytes: Callable[[str], bytes]) -> None:
    response = api_client.get("/api/samples/bengaluru_survey/download/")
    assert response.status_code == 200
    assert response.content == sample_bytes("bengaluru_survey")
    assert 'filename="bengaluru_survey.kml"' in response.headers["content-disposition"]
    report = api_client.get("/api/samples/flightmd_report_v15/download/")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("application/json")
