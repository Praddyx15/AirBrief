"""The assessment choices through the API: flight audits and eVTOL runs record ``band_ceiling`` /
``unverified_band`` (stored runs without them read as the defaults), the ED-318 exports take
``ed318_limits``, and the brief flags ``band_ceiling: permission``."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping

from airbrief_core.geo.geodesic import GEOD
from airbrief_core.io.geozones import parse_ed318

VOBG = (77.6682, 12.9499)
LIMIT_KEYS = ("lower", "lowerReference", "upper", "upperReference", "uom")


def _track() -> dict[str, Any]:
    # 10.2 to 11.8 km east of HAL's synthetic boundary (its 8-12 km band), at 75 m above ground
    points = [GEOD.fwd(VOBG[0], VOBG[1], 90.0, d)[:2] for d in range(12_000, 13_601, 200)]
    return {"coordinates": [[float(lon), float(lat), 75.0] for lon, lat in points], "altitude_mode": "relativeToGround"}


def _post(client: TestClient, kind: str, params: dict[str, Any]) -> Any:
    return client.post("/api/analyses/", json={"kind": kind, "params": params})


@pytest.mark.parametrize(
    ("kind", "params", "key", "default", "other"),
    [
        ("flight_audit", {"track": _track()}, "band_ceiling", "exceedance", "permission"),
        ("evtol_siting", {"geometry": mapping(box(77.66, 12.85, 77.67, 12.86))}, "unverified_band", "permission", "exclude"),
        ("evtol_corridor", {"origin": [77.66, 12.85], "destination": [77.70, 12.86]}, "unverified_band", "permission", "exclude"),
    ],
)
def test_runs_record_their_choice(
    api_client: TestClient, kind: str, params: dict[str, Any], key: str, default: str, other: str
) -> None:
    plain = _post(api_client, kind, params)
    assert plain.status_code == 202, plain.text
    assert plain.json()["params"][key] == default
    chosen = _post(api_client, kind, {**params, key: other})
    assert chosen.status_code == 202, chosen.text
    assert chosen.json()["params"][key] == other
    refused = _post(api_client, kind, {**params, key: "maybe"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_params"


def test_airspace_records_no_choice(api_client: TestClient) -> None:
    line = {"type": "LineString", "coordinates": [c[:2] for c in _track()["coordinates"]]}
    response = _post(api_client, "airspace", {"geometry": line, "grid": False})
    assert response.status_code == 202, response.text
    assert "band_ceiling" not in response.json()["params"] and "unverified_band" not in response.json()["params"]
    assert _post(api_client, "airspace", {"geometry": line, "band_ceiling": "permission"}).status_code == 422


def test_an_audit_round_trips_its_choice(api_client: TestClient, helpers: SimpleNamespace) -> None:
    done = helpers.run_analysis(api_client, {"kind": "flight_audit", "params": {"track": _track(), "band_ceiling": "permission"}})
    assert done["status"] == "COMPLETED", done["error"]
    result = done["result"]
    assert done["params"]["band_ceiling"] == "permission" and result["band_ceiling"] == "permission"
    assert result["verdict"] == "caution" and result["exceedances"] == []
    (run,) = result["above_band_ceiling"]
    assert run["limit_m"] == 60.0 and run["max_agl_m"] == 75.0
    again = api_client.get(f"/api/analyses/{done['id']}/").json()
    assert again["params"]["band_ceiling"] == "permission"

    tables = {t["key"]: t["rows"] for t in api_client.get(f"/api/analyses/{done['id']}/export/tables/").json()}
    assert tables["above_band_ceiling"] == 1 and tables["exceedances"] == 0
    csv = api_client.get(f"/api/analyses/{done['id']}/export/", params={"format": "csv", "table": "above_band_ceiling"})
    assert csv.status_code == 200 and "Flown in yellow airspace above the 60 m ceiling" in csv.text
    strict = helpers.run_analysis(api_client, {"kind": "flight_audit", "params": {"track": _track()}})
    assert "above_band_ceiling" not in {t["key"] for t in api_client.get(f"/api/analyses/{strict['id']}/export/tables/").json()}


def test_evtol_runs_round_trip_their_choice(api_client: TestClient, helpers: SimpleNamespace) -> None:
    # 2 km south of HAL's reference point: its DGCA 0-5 km band
    lon, lat, _ = GEOD.fwd(VOBG[0], VOBG[1], 180.0, 2_000.0)
    area = mapping(box(lon - 0.004, lat - 0.004, lon + 0.004, lat + 0.004))
    done = helpers.run_analysis(api_client, {"kind": "evtol_siting", "params": {
        "geometry": area, "h3_resolution": 9, "max_slope_deg": 45, "exclude_water": False, "unverified_band": "exclude"}})
    assert done["status"] == "COMPLETED", done["error"]
    assert done["params"]["unverified_band"] == "exclude" and done["result"]["unverified_band"] == "exclude"
    assert done["result"]["candidates"] == []
    assert {c["reason"] for c in done["result"]["cells"]} == {"unverified_band"}


def _export(client: TestClient, path: str, **params: str) -> Any:
    return client.get(path, params={"format": "ed318", **params})


def test_an_analysis_exports_both_layouts(api_client: TestClient, helpers: SimpleNamespace) -> None:
    done = helpers.run_analysis(api_client, {"kind": "flight_audit", "params": {"track": _track()}})
    path = f"/api/analyses/{done['id']}/export/"
    default = _export(api_client, path)
    by_geometry = _export(api_client, path, ed318_limits="geometry")
    by_properties = _export(api_client, path, ed318_limits="properties")
    assert default.status_code == by_geometry.status_code == by_properties.status_code == 200
    assert default.json() == by_geometry.json()

    geometry_doc, properties_doc = by_geometry.json(), by_properties.json()
    assert all("layer" in f["geometry"] for f in geometry_doc["features"])
    assert all("layer" not in f["geometry"] and all(k in f["properties"] for k in LIMIT_KEYS) for f in properties_doc["features"])
    assert properties_doc["airbrief"] == geometry_doc["airbrief"]
    assert parse_ed318(properties_doc) == parse_ed318(geometry_doc)
    assert {"VOBG 8–12 km up to 60 m", "VOBG 8–12 km above 60 m"} <= {z.name for z in parse_ed318(properties_doc)}

    refused = api_client.get(path, params={"format": "json", "ed318_limits": "properties"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_params"
    assert _export(api_client, path, ed318_limits="layer").status_code == 422


def test_a_dataset_exports_both_layouts(api_client: TestClient, helpers: SimpleNamespace) -> None:
    feature = {"type": "Feature", "properties": {"name": "Event", "zone": "yellow", "ceiling_agl_m": 45},
               "geometry": mapping(box(77.485, 12.902, 77.495, 12.908))}
    response = helpers.upload(api_client, "event.geojson", json.dumps({"type": "FeatureCollection", "features": [feature]}).encode())
    info = helpers.wait_for_file(api_client, response.json()["id"])
    assert info["status"] == "COMPLETED", info
    path = f"/api/files/{info['id']}/export/"

    by_geometry = _export(api_client, path).json()
    by_properties = _export(api_client, path, ed318_limits="properties").json()
    (g,), (p,) = by_geometry["features"], by_properties["features"]
    assert "layer" in g["geometry"] and "layer" not in p["geometry"]
    assert {k: p["properties"][k] for k in LIMIT_KEYS} == g["geometry"]["layer"]
    assert parse_ed318(by_properties) == parse_ed318(by_geometry)

    refused = api_client.get(path, params={"format": "geojson", "ed318_limits": "geometry"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_params"


def _flags(client: TestClient, analysis_id: str) -> str:
    response = client.post("/api/briefs/", json={"analysis_ids": [analysis_id], "operation": {}})
    assert response.status_code == 200, response.text
    return " ".join(w["message"] for w in response.json()["warnings"] if w["code"] == "non_default_parameters")


def test_the_brief_flags_band_ceiling_permission(api_client: TestClient, helpers: SimpleNamespace) -> None:
    loose = helpers.run_analysis(api_client, {"kind": "flight_audit", "params": {"track": _track(), "band_ceiling": "permission"}})
    strict = helpers.run_analysis(api_client, {"kind": "flight_audit", "params": {"track": _track()}})
    assert "(band_ceiling: permission)" in _flags(api_client, loose["id"])
    assert "band_ceiling" not in _flags(api_client, strict["id"])
