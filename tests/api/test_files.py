"""``/api/files/``: upload, info, measurements, features, exports, delete and ingest attacks."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from airbrief_core.geo.crs import WGS84, crs_label


def _upload_square(client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]) -> dict[str, Any]:
    response = helpers.upload(client, "square_1km_equator.kml", sample_bytes("square_1km_equator"))
    assert response.status_code == 201, response.text
    return response.json()


def test_upload_kml_is_processed_inline(
    api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    info = _upload_square(api_client, helpers, sample_bytes)
    assert info["status"] == "COMPLETED"
    assert info["filename"] == "square_1km_equator.kml"
    assert info["file_type"] == "kml"
    assert info["feature_count"] == 1
    assert info["layer_count"] >= 1
    assert info["error"] is None
    assert info["sample"] is None
    assert info["size_bytes"] == len(sample_bytes("square_1km_equator"))
    assert len(info["bbox"]) == 4
    assert info["processed_at"] is not None


def test_file_info_has_brief_fields(
    api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    created = _upload_square(api_client, helpers, sample_bytes)
    response = api_client.get(f"/api/files/{created['id']}/")
    assert response.status_code == 200
    info = response.json()
    assert info["id"] == created["id"]
    assert info["filename"] == "square_1km_equator.kml"
    assert info["feature_count"] == 1
    assert info["crs"] == crs_label(WGS84)
    assert info["status"] == "COMPLETED"
    # the same resource without the trailing slash
    assert api_client.get(f"/api/files/{created['id']}").json()["id"] == created["id"]


def test_list_files(api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]) -> None:
    first = _upload_square(api_client, helpers, sample_bytes)
    second = _upload_square(api_client, helpers, sample_bytes)
    page = api_client.get("/api/files/", params={"limit": 1}).json()
    assert page["total"] == 2
    assert len(page["items"]) == 1
    ids = {item["id"] for item in api_client.get("/api/files").json()["items"]}
    assert ids == {first["id"], second["id"]}


def test_measurements(api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]) -> None:
    created = _upload_square(api_client, helpers, sample_bytes)
    response = api_client.get(f"/api/files/{created['id']}/measurements/")
    assert response.status_code == 200
    page = response.json()
    assert page["file_id"] == created["id"]
    assert page["total"] == 1 and page["limit"] == 100 and page["offset"] == 0
    item = page["items"][0]
    assert item["feature_index"] == 0
    assert item["geometry_type"] == "Polygon"
    assert item["crs"] == crs_label(WGS84)
    measurement = item["measurement"]
    assert measurement["status"] == "measured"
    assert measurement["area_m2"] == pytest.approx(1_000_000, rel=1e-4)
    assert measurement["perimeter_m"] == pytest.approx(4_000, rel=1e-4)
    assert measurement["method"] == "local-projection"
    assert page["totals"]["feature_count"] == 1


def test_measurements_filters_and_points(
    api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    created = helpers.upload(api_client, "mixed_features.kml", sample_bytes("mixed_features")).json()
    assert created["status"] == "COMPLETED"
    url = f"/api/files/{created['id']}/measurements/"
    everything = api_client.get(url).json()
    assert everything["total"] == created["feature_count"]
    points = api_client.get(url, params={"geometry_type": "Point"}).json()
    assert points["total"] >= 1
    for item in points["items"]:
        assert item["measurement"]["status"] == "not_applicable"
        assert item["measurement"]["reason"]
    not_applicable = api_client.get(url, params={"status": "not_applicable"}).json()
    assert not_applicable["total"] == points["total"]
    paged = api_client.get(url, params={"limit": 1, "offset": 1}).json()
    assert len(paged["items"]) == 1 and paged["items"][0]["feature_index"] == everything["items"][1]["feature_index"]
    bad = api_client.get(url, params={"status": "nonsense"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_params"


def test_features_geojson(api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]) -> None:
    created = _upload_square(api_client, helpers, sample_bytes)
    response = api_client.get(f"/api/files/{created['id']}/features/")
    assert response.status_code == 200
    collection = response.json()
    assert collection["type"] == "FeatureCollection"
    assert len(collection["features"]) == 1
    feature = collection["features"][0]
    assert feature["type"] == "Feature"
    assert feature["geometry"]["type"] == "Polygon"
    props = feature["properties"]
    for key in ("_index", "_layer", "_name", "_type", "_status", "_area_m2", "_length_m"):
        assert key in props
    assert props["_index"] == 0
    assert props["_status"] == "measured"
    assert props["_area_m2"] == pytest.approx(1_000_000, rel=1e-4)


def test_single_feature_and_missing_index(
    api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    created = _upload_square(api_client, helpers, sample_bytes)
    record = api_client.get(f"/api/files/{created['id']}/features/0/").json()
    assert record["index"] == 0
    assert record["geometry_wgs84"]["type"] == "Polygon"
    assert record["measurement"]["status"] == "measured"
    missing = api_client.get(f"/api/files/{created['id']}/features/99/")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_unknown_id_is_404_envelope(api_client: TestClient) -> None:
    for path in (
        "/api/files/00000000-0000-0000-0000-000000000000/",
        "/api/files/00000000-0000-0000-0000-000000000000/measurements/",
        "/api/files/00000000-0000-0000-0000-000000000000/features/",
    ):
        response = api_client.get(path)
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"
        assert body["error"]["message"]


def test_wrong_extension_is_415(api_client: TestClient, helpers: SimpleNamespace) -> None:
    response = helpers.upload(api_client, "report.pdf", b"%PDF-1.7\n%fake\n")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_format"
    assert api_client.get("/api/files/").json()["total"] == 0


def test_zipslip_is_rejected(
    api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes], tmp_path: Path
) -> None:
    response = helpers.upload(api_client, "attack_zipslip.zip", sample_bytes("attack_zipslip"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_archive"
    assert api_client.get("/api/files/").json()["total"] == 0
    assert not (tmp_path / "data" / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_corrupt_file_is_failed_dataset(
    api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    response = helpers.upload(api_client, "corrupt.kml", sample_bytes("corrupt"))
    assert response.status_code == 201
    info = response.json()
    assert info["status"] == "FAILED"
    assert info["error"]["code"] == "corrupt_file"
    assert info["error"]["message"]


def test_oversized_upload_is_413(make_client: Callable[..., TestClient], helpers: SimpleNamespace) -> None:
    client = make_client(max_upload_mb=0.001)  # 1,048 bytes
    response = helpers.upload(client, "big.kml", b"<kml>" + b" " * 5000 + b"</kml>")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert client.get("/api/files/").json()["total"] == 0


def test_large_upload_is_queued(
    make_client: Callable[..., TestClient], helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    client = make_client(inline_process_max_mb=0)
    response = helpers.upload(client, "square_1km_equator.kml", sample_bytes("square_1km_equator"))
    assert response.status_code == 202
    assert response.json()["status"] in ("PENDING", "PROCESSING", "COMPLETED")
    info = helpers.wait_for_file(client, response.json()["id"])
    assert info["status"] == "COMPLETED"
    assert info["feature_count"] == 1


def test_invalid_origin_and_crs_override(
    api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    bad_origin = helpers.upload(api_client, "square.kml", sample_bytes("square_1km_equator"), origin="east,north")
    assert bad_origin.status_code == 422
    assert bad_origin.json()["error"]["code"] == "invalid_params"
    bad_crs = helpers.upload(api_client, "square.kml", sample_bytes("square_1km_equator"), crs_override="EPSG:999999999")
    assert bad_crs.status_code == 422
    assert bad_crs.json()["error"]["code"] == "invalid_crs"


def test_missing_file_field_is_422(api_client: TestClient) -> None:
    response = api_client.post("/api/files/", data={"crs_override": "EPSG:4326"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_params"


def test_delete(api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]) -> None:
    created = _upload_square(api_client, helpers, sample_bytes)
    response = api_client.delete(f"/api/files/{created['id']}/")
    assert response.status_code == 204
    assert response.content == b""
    assert api_client.get(f"/api/files/{created['id']}/").status_code == 404
    assert api_client.delete(f"/api/files/{created['id']}/").status_code == 404


def test_exports(api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]) -> None:
    created = _upload_square(api_client, helpers, sample_bytes)
    base = f"/api/files/{created['id']}/export/"

    csv_response = api_client.get(base, params={"format": "csv"})
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert float(rows[0]["area_m2"]) == pytest.approx(1_000_000, rel=1e-4)

    geojson_response = api_client.get(base, params={"format": "geojson"})
    assert geojson_response.status_code == 200
    collection = json.loads(geojson_response.content)
    assert collection["type"] == "FeatureCollection"
    assert collection["features"][0]["properties"]["_index"] == 0

    kml_response = api_client.get(base, params={"format": "kml"})
    assert kml_response.status_code == 200
    text = kml_response.content.decode("utf-8")
    assert "<kml" in text and "Placemark" in text
    assert "ExtendedData" in text and 'name="area_m2"' in text

    bad = api_client.get(base, params={"format": "shp"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_params"
