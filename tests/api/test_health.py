"""``/health``, ``/api/meta/``, the error envelope for unknown routes, and slash aliases."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from airbrief_core import __version__
from airbrief_core.models.common import SCHEMA_VERSION


def test_health_reports_db_datapacks_and_backend(api_client: TestClient) -> None:
    response = api_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "db": "ok",
        "datapacks": ["demo"],
        "native_backend": body["native_backend"],
        "refusals": None,  # the test client is not on loopback
    }
    assert body["native_backend"] in ("cpp", "python")


def test_health_counts_refusals_for_the_server_itself(api_client: TestClient) -> None:
    refused = api_client.post("/api/geometry/parse/", json={"text": "1, 2"}, headers={"Origin": "https://elsewhere.example"})
    assert refused.status_code == 403
    local = TestClient(api_client.app, client=("127.0.0.1", 50000))
    assert local.get("/health").json()["refusals"]["cross_site_request"] >= 1
    proxied = local.get("/health", headers={"X-Forwarded-For": "203.0.113.9"})
    assert proxied.json()["refusals"] is None


def test_health_slash_alias(api_client: TestClient) -> None:
    assert api_client.get("/health/").status_code == 200


def test_unknown_route_uses_error_envelope(api_client: TestClient) -> None:
    response = api_client.get("/api/does-not-exist/")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert set(error) == {"code", "message", "details"}


def test_method_not_allowed_uses_error_envelope(api_client: TestClient) -> None:
    response = api_client.put("/api/files/")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_meta_lists_modules_presets_and_limits(api_client: TestClient) -> None:
    response = api_client.get("/api/meta/")
    assert response.status_code == 200
    meta = response.json()
    assert meta["version"] == __version__
    assert meta["schema_version"] == SCHEMA_VERSION
    kinds = {m["kind"] for m in meta["modules"]}
    assert {"airspace", "ground_risk", "evtol_siting", "evtol_corridor", "show", "survey", "flight_audit"} <= kinds
    survey = next(m for m in meta["modules"] if m["kind"] == "survey")
    assert "properties" in survey["params_schema"]
    assert "geometry" in survey["params_schema"]["properties"]
    assert "p4rtk" in meta["cameras"]
    assert meta["cameras"]["p4rtk"]["focal_length_mm"] == 8.8
    assert "sub250" in meta["uas_presets"]
    assert meta["zone_rules"]["rules"]
    assert [d["name"] for d in meta["datapacks"]] == ["demo"]
    assert meta["datapacks"][0]["synthetic"] is True
    assert len(meta["datapacks"][0]["bbox"]) == 4
    assert meta["limits"]["max_upload_mb"] == 50
    assert meta["flightmd"] == {"configured": False, "url": None}
    # without the trailing slash too
    assert api_client.get("/api/meta").status_code == 200


@pytest.mark.parametrize(
    ("api_url", "shown"),
    [
        ("https://flightmd.example.org/api/", "https://flightmd.example.org"),
        ("http://flightmd.internal:8000/private/v1", "http://flightmd.internal:8000"),
    ],
)
def test_meta_reports_configured_flightmd(make_client: Callable[..., TestClient], api_url: str, shown: str) -> None:
    # Only the scheme and host are published, never the path (VERIFY-02).
    client = make_client(flightmd_api_url=api_url)
    meta = client.get("/api/meta/").json()
    assert meta["flightmd"] == {"configured": True, "url": shown}


def test_cors_allows_configured_origin(api_client: TestClient) -> None:
    response = api_client.options(
        "/api/files/",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
