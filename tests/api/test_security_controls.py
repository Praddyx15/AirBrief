"""Security and privacy controls: owner tokens, body-size limits, headers, production defaults."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import math
import re
from collections.abc import Iterator
from typing import Any

import pytest

from api import security
from api.config import PRODUCTION_DEFAULT_TTL_HOURS, Settings
from api.services.analyses import MAX_PARAM_VERTICES
from airbrief_core.errors import AirBriefError

OWNER = "X-Owner-Token"


def test_creation_returns_an_owner_token_once(api_client: Any) -> None:
    created = api_client.post("/api/samples/square_1km_equator/")
    assert created.status_code == 201
    token = created.json()["owner_token"]
    assert isinstance(token, str) and len(token) >= 32
    # Never repeated afterwards.
    again = api_client.get(f"/api/files/{created.json()['id']}/").json()
    assert again["owner_token"] is None


def test_delete_needs_the_owner_token_when_required(make_client: Any) -> None:
    client = make_client(require_owner_token=True)
    created = client.post("/api/samples/square_1km_equator/").json()
    file_id, token = created["id"], created["owner_token"]

    missing = client.delete(f"/api/files/{file_id}/")
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "owner_token_required"

    wrong = client.delete(f"/api/files/{file_id}/", headers={OWNER: "not-the-token"})
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "owner_token_invalid"

    assert client.get(f"/api/files/{file_id}/").status_code == 200  # still there
    assert client.delete(f"/api/files/{file_id}/", headers={OWNER: token}).status_code == 204
    assert client.get(f"/api/files/{file_id}/").status_code == 404


def test_analysis_cancel_and_delete_need_the_owner_token(make_client: Any, helpers: Any) -> None:
    client = make_client(require_owner_token=True)
    dataset = helpers.load_sample(client, "bengaluru_survey")
    created = client.post("/api/analyses/", json={"kind": "survey", "dataset_id": dataset["id"], "params": {}})
    assert created.status_code == 202
    analysis_id, token = created.json()["id"], created.json()["owner_token"]
    assert token

    assert client.post(f"/api/analyses/{analysis_id}/cancel/").status_code == 403
    assert client.delete(f"/api/analyses/{analysis_id}/").status_code == 403
    helpers.wait_for_analysis(client, analysis_id)
    assert client.delete(f"/api/analyses/{analysis_id}/", headers={OWNER: token}).status_code == 204


def test_tokens_not_required_in_development_by_default(api_client: Any) -> None:
    created = api_client.post("/api/samples/square_1km_equator/").json()
    assert api_client.delete(f"/api/files/{created['id']}/").status_code == 204


def test_oversized_json_body_is_refused(make_client: Any) -> None:
    client = make_client(max_json_body_mb=0.01)  # about 10 KB
    body = {"kind": "flight_audit", "params": {"report": {"padding": "x" * 50_000}}}
    response = client.post("/api/analyses/", json=body)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_oversized_chunked_body_keeps_its_code(make_client: Any) -> None:
    """No Content-Length: the limit is enforced while the body streams in."""
    client = make_client(max_json_body_mb=0.01)
    payload = json.dumps({"kind": "flight_audit", "params": {"report": {"padding": "x" * 50_000}}}).encode()

    def chunks() -> Iterator[bytes]:
        for start in range(0, len(payload), 4096):
            yield payload[start : start + 4096]

    response = client.post("/api/analyses/", content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_security_headers(api_client: Any) -> None:
    for path in ("/health", "/api/meta/"):
        headers = api_client.get(path).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert api_client.get("/api/meta/").headers["cache-control"] == "no-store"


def test_module_crash_details_stay_in_the_log(make_client: Any, helpers: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from airbrief_core.analyses import REGISTRY

    def crash(params: Any, ctx: Any) -> Any:
        raise RuntimeError(r"secret detail C:\srv\airbrief\data\x.tif")

    monkeypatch.setitem(REGISTRY, "survey", dataclasses.replace(REGISTRY["survey"], run=crash))
    client = make_client()
    dataset = helpers.load_sample(client, "bengaluru_survey")
    created = client.post("/api/analyses/", json={"kind": "survey", "dataset_id": dataset["id"], "params": {}})
    info = helpers.wait_for_analysis(client, created.json()["id"])
    assert info["status"] == "FAILED"
    assert info["error"]["code"] == "internal_error"
    assert "secret detail" not in info["error"]["message"]
    assert "server log" in info["error"]["message"]


def _production(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"app_env": "production", "cors_origins": "https://airbrief.example"}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_production_turns_on_safe_defaults() -> None:
    settings = _production()
    assert settings.owner_token_required is True
    assert settings.global_listing_enabled is False
    assert settings.upload_ttl_hours == PRODUCTION_DEFAULT_TTL_HOURS


def test_production_defaults_can_be_overridden_explicitly() -> None:
    settings = _production(upload_ttl_hours=0, allow_global_listing=True, require_owner_token=False)
    assert settings.upload_ttl_hours == 0
    assert settings.global_listing_enabled is True
    assert settings.owner_token_required is False


def test_development_defaults_are_permissive() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.owner_token_required is False
    assert settings.global_listing_enabled is True
    assert settings.upload_ttl_hours == 0


def test_production_requires_explicit_cors() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env="production")  # type: ignore[call-arg]


def test_flightmd_fetch_returns_only_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setattr(security, "fetch_json", lambda *a, **k: {"anything": "else"})
    with pytest.raises(AirBriefError) as info:
        security.fetch_flightmd_report("https://example.org/data.json", settings)
    assert info.value.code == "report_invalid"

    report = {"metadata": {"gps_path": [[12.9, 77.5, 900.0], [12.91, 77.51, 950.0]]}}
    monkeypatch.setattr(security, "fetch_json", lambda *a, **k: {"report": report})
    assert security.fetch_flightmd_report("https://example.org/report/1", settings) == report


# Geometry and lists inside analysis parameters (VERIFY-03)


def _post_raw(client: Any, body: str) -> Any:
    return client.post("/api/analyses/", content=body, headers={"Content-Type": "application/json"})


@pytest.mark.parametrize("depth", [40, 200, 900, 20_000])
def test_deeply_nested_geometry_is_a_422(api_client: Any, depth: int) -> None:
    # Before: 200 levels stored the analysis and then failed with a 500, 900 levels a RecursionError.
    nested = '{"type":"GeometryCollection","geometries":[' * depth + '{"type":"Point","coordinates":[77.5,12.9]}' + "]}" * depth
    response = _post_raw(api_client, '{"kind":"airspace","params":{"geometry":' + nested + "}}")
    assert response.status_code == 422, response.text[:300]
    assert response.json()["error"]["code"] == "too_complex"
    assert api_client.get("/api/analyses/").json()["total"] == 0


def test_deeply_nested_report_is_a_422(api_client: Any) -> None:
    path = "[[12.9,77.5,900],[12.91,77.51,950]]"
    body = '{"kind":"flight_audit","params":{"report":{"metadata":{"gps_path":' + path + '},"x":' + "[" * 300 + "]" * 300 + "}}}"
    response = _post_raw(api_client, body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "too_complex"


def test_geometry_with_too_many_vertices_is_a_422(api_client: Any) -> None:
    n = MAX_PARAM_VERTICES + 1
    ring = [[77.5 + 0.01 * math.cos(2 * math.pi * i / n), 12.9 + 0.01 * math.sin(2 * math.pi * i / n)] for i in range(n)]
    ring.append(ring[0])
    for params in (
        {"geometry": {"type": "Polygon", "coordinates": [ring]}},
        {"geometry": {"type": "Point", "coordinates": [77.5, 12.9]}, "user_zones": [{"name": "z", "zone": "red", "geometry": {"type": "Polygon", "coordinates": [ring]}}]},
    ):
        response = api_client.post("/api/analyses/", json={"kind": "airspace", "params": params})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "too_complex"
        assert response.json()["error"]["details"]["limit"] == MAX_PARAM_VERTICES


def test_a_detailed_dataset_polygon_copied_into_params_is_accepted(api_client: Any) -> None:
    # 20k vertices: more than can be pasted, still fine as a copied training area or show box
    n = 20_000
    ring = [[77.5 + 0.01 * math.cos(2 * math.pi * i / n), 12.9 + 0.01 * math.sin(2 * math.pi * i / n)] for i in range(n)]
    ring.append(ring[0])
    response = api_client.post("/api/analyses/", json={"kind": "airspace", "params": {"geometry": {"type": "Polygon", "coordinates": [ring]}}})
    assert response.status_code == 202, response.text[:300]


def test_rule_lists_in_parameters_are_bounded(api_client: Any) -> None:
    ring = {"zone": "red", "inner_km": 0, "outer_km": 5, "label": "r"}
    rule = {"aerodrome_types": ["large_airport"], "rings": [ring] * 21}
    params = {"geometry": {"type": "Point", "coordinates": [77.5, 12.9]}, "zone_rules": {"rules": [rule]}}
    response = api_client.post("/api/analyses/", json={"kind": "airspace", "params": params})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_params"
    assert "rings" in response.json()["error"]["message"]


PRODUCTION = {"app_env": "production", "cors_origins": "https://airbrief.example"}


def test_api_docs_setting_follows_the_environment() -> None:
    assert Settings(_env_file=None, api_docs="").api_docs_enabled is True  # type: ignore[call-arg]
    assert _production().api_docs_enabled is False
    assert _production(api_docs="true").api_docs_enabled is True
    assert Settings(_env_file=None, api_docs="false").api_docs_enabled is False  # type: ignore[call-arg]


def test_api_docs_load_pinned_assets_under_a_csp(api_client: Any) -> None:
    from api.headers import REDOC_JS, SWAGGER_UI_DIST

    docs = api_client.get("/docs")
    assert docs.status_code == 200
    assert f'src="{SWAGGER_UI_DIST}/swagger-ui-bundle.js"' in docs.text
    assert "fastapi.tiangolo.com" not in docs.text
    csp = docs.headers["content-security-policy"]
    script_src = next(d for d in csp.split("; ") if d.startswith("script-src "))
    inline = re.search(r"<script>(.*?)</script>", docs.text, re.S)
    assert inline is not None
    digest = base64.b64encode(hashlib.sha256(inline.group(1).encode()).digest()).decode()
    assert script_src == f"script-src {SWAGGER_UI_DIST}/swagger-ui-bundle.js 'sha256-{digest}'"
    assert "frame-ancestors 'none'" in csp
    assert "default-src 'none'" in csp

    redoc = api_client.get("/redoc")
    assert redoc.status_code == 200
    assert f'src="{REDOC_JS}"' in redoc.text
    assert "fonts.googleapis.com" not in redoc.text
    assert f"script-src {REDOC_JS};" in redoc.headers["content-security-policy"]

    assert api_client.get("/openapi.json").status_code == 200


def test_api_docs_off_in_production_by_default(make_client: Any) -> None:
    client = make_client(**PRODUCTION)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 200


def test_api_docs_can_be_turned_on_in_production(make_client: Any) -> None:
    client = make_client(api_docs=True, **PRODUCTION)
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "script-src https://cdn.jsdelivr.net/npm/swagger-ui-dist@" in docs.headers["content-security-policy"]
    assert client.get("/redoc").status_code == 200


@pytest.mark.parametrize(
    "overrides, expected",
    [({}, None), (PRODUCTION, "max-age=31536000; includeSubDomains")],
)
def test_hsts_only_in_production(make_client: Any, overrides: dict[str, str], expected: str | None) -> None:
    client = make_client(**overrides)
    for path in ("/health", "/api/meta/", "/openapi.json"):
        assert client.get(path).headers.get("strict-transport-security") == expected


def test_allowed_hosts_refuses_other_host_headers(make_client: Any) -> None:
    client = make_client(allowed_hosts="testserver, AirBrief.example")
    assert client.get("/health").status_code == 200
    assert client.get("/health", headers={"Host": "airbrief.example:8000"}).status_code == 200
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/meta/", headers={"Host": "evil.example"}).status_code == 400


def test_any_host_is_accepted_without_allowed_hosts(api_client: Any) -> None:
    assert api_client.get("/health", headers={"Host": "anything.example"}).status_code == 200


def test_production_wants_tls_to_a_database_on_the_internet() -> None:
    from api.config import database_tls_ok

    assert database_tls_ok("sqlite:///./data/airbrief.db")
    for local in ("localhost:5432", "127.0.0.1", "db:5432", "10.0.0.7", "pg.internal"):
        assert database_tls_ok(f"postgresql+psycopg://u:p@{local}/airbrief"), local
    assert not database_tls_ok("postgresql+psycopg://u:p@ep-x.neon.tech/airbrief")
    assert not database_tls_ok("postgresql+psycopg://u:p@8.8.8.8/airbrief?sslmode=prefer")
    assert database_tls_ok("postgresql+psycopg://u:p@ep-x.neon.tech/airbrief?sslmode=require")
    assert not database_tls_ok("postgresql+psycopg://u:p@[2a05:d014::10]:5432/airbrief")  # no dot, still public
    assert database_tls_ok("postgresql+psycopg://u:p@[::1]:5432/airbrief")
    with pytest.raises(ValueError, match="sslmode=require"):
        Settings(_env_file=None, app_env="production", cors_origins="https://airbrief.example",  # type: ignore[call-arg]
                 database_url="postgresql+psycopg://u:p@ep-x.neon.tech/airbrief")
