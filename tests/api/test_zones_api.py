"""The zone engine through the API: zones from datasets, ED-318 exports, zone profiles in
/api/meta/ and /api/context/, and the brief's cross-check."""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping, shape

from airbrief_core.io.geozones import parse_ed269, parse_ed318

SURVEY = mapping(box(77.48, 12.90, 77.50, 12.91))  # the bengaluru_survey sample area
NEAR_VOBL = {"type": "LineString", "coordinates": [[77.7066, 13.2950], [77.7066, 13.2995]]}  # 8.3 to 8.8 km past its boundary


def _zones_file(client: TestClient, helpers: SimpleNamespace, features: list[dict[str, Any]], name: str = "zones.geojson") -> dict[str, Any]:
    body = json.dumps({"type": "FeatureCollection", "features": features}).encode()
    response = helpers.upload(client, name, body)
    assert response.status_code in (201, 202), response.text
    info = helpers.wait_for_file(client, response.json()["id"])
    assert info["status"] == "COMPLETED", info
    return info  # type: ignore[no-any-return]


def _polygon(geom: Any, **props: Any) -> dict[str, Any]:
    return {"type": "Feature", "properties": props, "geometry": geom}


def _post(client: TestClient, params: dict[str, Any]) -> Any:
    return client.post("/api/analyses/", json={"kind": "airspace", "params": {"grid": False, **params}})


def test_zones_from_a_dataset(api_client: TestClient, helpers: SimpleNamespace) -> None:
    zones = _zones_file(api_client, helpers, [
        _polygon(mapping(box(77.485, 12.902, 77.495, 12.908)), name="Event", colour="red",
                 valid_from="2026-10-20T08:00:00Z", valid_to="2026-10-20T12:00:00Z"),
        _polygon(mapping(box(70.0, 20.0, 70.1, 20.1)), name="Far away", colour="red"),
        {"type": "Feature", "properties": {"name": "A point"}, "geometry": {"type": "Point", "coordinates": [77.49, 12.905]}},
    ])
    params = {"geometry": SURVEY, "altitude_agl_m": 60, "zone_dataset_ids": [zones["id"]],
              "planned_start_utc": "2026-10-20T09:00:00Z", "planned_end_utc": "2026-10-20T10:00:00Z"}
    done = helpers.run_analysis(api_client, {"kind": "airspace", "params": params})
    assert done["status"] == "COMPLETED", done["error"]
    stored = done["params"]
    assert [z["name"] for z in stored["dataset_zones"]] == ["Event"]  # the far one is filtered by bbox
    assert stored["zone_profile"] == "dgca_2021"
    result = done["result"]
    assert result["verdict"] == "fail" and result["zones"][0]["name"] == "Event"
    assert result["zones"][0]["active"] is True

    later = helpers.run_analysis(api_client, {"kind": "airspace", "params": {
        **params, "planned_start_utc": "2026-10-20T12:00:00Z", "planned_end_utc": "2026-10-20T13:00:00Z"}})
    assert later["result"]["verdict"] != "fail"
    assert [z["name"] for z in later["result"]["inactive_zones"]] == ["Event"]

    # a column mapping: no kind column, so every polygon is the default kind
    mapped = helpers.run_analysis(api_client, {"kind": "airspace", "params": {
        **params, "zone_mappings": {zones["id"]: {"zone_field": "nothing", "default_zone": "yellow"}}}})
    assert mapped["result"]["zones"][0]["zone"] == "yellow" and mapped["result"]["permission_required"] is True


def test_a_requests_own_dataset_zones_are_replaced(api_client: TestClient, helpers: SimpleNamespace) -> None:
    planted = [{"name": "Planted", "zone": "red", "geometry": SURVEY}]
    done = helpers.run_analysis(api_client, {"kind": "airspace", "params": {"geometry": SURVEY, "grid": False,
                                                                            "dataset_zones": planted}})
    assert "dataset_zones" not in done["params"]
    assert done["result"]["verdict"] != "fail"


def test_zone_dataset_limits(api_client: TestClient, helpers: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.services import zones as zone_service

    zones = _zones_file(api_client, helpers, [
        _polygon(mapping(box(77.481 + i * 0.002, 12.901, 77.482 + i * 0.002, 12.902)), name=f"Z{i}", zone="red")
        for i in range(3)
    ])
    monkeypatch.setattr(zone_service, "MAX_DATASET_ZONES", 2)
    response = _post(api_client, {"geometry": SURVEY, "zone_dataset_ids": [zones["id"]]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "too_many_zones"
    monkeypatch.undo()

    missing = _post(api_client, {"geometry": SURVEY, "zone_dataset_ids": ["00000000-0000-0000-0000-000000000000"]})
    assert missing.status_code == 404
    too_many = _post(api_client, {"geometry": SURVEY, "zone_dataset_ids": [zones["id"]] * 11})
    assert too_many.status_code == 422
    unknown_mapping = _post(api_client, {"geometry": SURVEY, "zone_dataset_ids": [zones["id"]],
                                         "zone_mappings": {"other": {}}})
    assert unknown_mapping.status_code == 422

    points = _zones_file(api_client, helpers, [
        {"type": "Feature", "properties": {"name": "P"}, "geometry": {"type": "Point", "coordinates": [77.49, 12.905]}}])
    no_polygons = _post(api_client, {"geometry": SURVEY, "zone_dataset_ids": [points["id"]]})
    assert no_polygons.status_code == 422 and "no polygons" in no_polygons.json()["error"]["message"]


def test_profile_and_window_validation(api_client: TestClient) -> None:
    custom = _post(api_client, {"geometry": SURVEY, "zone_profile": "custom"})
    assert custom.status_code == 422
    half_window = _post(api_client, {"geometry": SURVEY, "planned_start_utc": "2026-10-20T09:00:00Z"})
    assert half_window.status_code == 422
    naive = _post(api_client, {"geometry": SURVEY, "planned_start_utc": "2026-10-20T09:00:00",
                               "planned_end_utc": "2026-10-20T10:00:00"})
    assert naive.status_code == 422
    month = _post(api_client, {"geometry": SURVEY, "planned_start_utc": "2026-10-01T00:00:00Z",
                               "planned_end_utc": "2026-11-15T00:00:00Z"})
    assert month.status_code == 422
    rules = api_client.get("/api/meta/").json()["zone_profiles"]["conservative_illustrative"]
    ok = _post(api_client, {"geometry": SURVEY, "zone_rules": rules})
    assert ok.status_code == 202 and ok.json()["params"]["zone_profile"] == "custom"


def test_ed318_exports(api_client: TestClient, helpers: SimpleNamespace) -> None:
    done = helpers.run_analysis(api_client, {"kind": "airspace", "params": {
        "geometry": NEAR_VOBL, "altitude_agl_m": 75, "grid": False}})
    assert done["status"] == "COMPLETED", done["error"]
    bands = {b["band"].split(":")[0]: b for b in done["result"]["band_evidence"]}
    assert set(bands) == {"8–12 km"}
    assert bands["8–12 km"]["agl_margin_m"] == pytest.approx(-15.0)

    response = api_client.get(f"/api/analyses/{done['id']}/export/", params={"format": "ed318"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/geo+json")
    assert ".ed318.geojson" in response.headers["content-disposition"]
    assert response.json()["airbrief"]["notice"].startswith("Evidence, not authorisation")
    assert response.json()["airbrief"]["watermark"] == "SYNTHETIC DEMO DATA — not evidence"
    zones = parse_ed318(response.json())
    names = [z.name for z in zones]
    # the band that turns yellow above 60 m comes out as two volumes
    assert "VOBL 8–12 km up to 60 m" in names and "VOBL 8–12 km above 60 m" in names
    above = next(z for z in zones if z.name == "VOBL 8–12 km above 60 m")
    assert above.zone == "yellow" and above.lower_limit_m == 60.0

    survey = helpers.load_sample(api_client, "bengaluru_survey")
    ground_risk = helpers.run_analysis(api_client, {"kind": "ground_risk", "dataset_id": survey["id"], "params": {}})
    refused = api_client.get(f"/api/analyses/{ground_risk['id']}/export/", params={"format": "ed318"})
    assert refused.status_code == 422


def test_zone_file_upload_and_dataset_export(api_client: TestClient, helpers: SimpleNamespace) -> None:
    doc = {"title": "Test", "features": [{
        "identifier": "LUR01", "country": "LUX", "name": "Test area", "type": "COMMON", "restriction": "REQ_AUTHORISATION",
        "applicability": [{"startDateTime": "2026-10-20T08:00:00Z", "endDateTime": "2026-10-20T12:00:00Z"}],
        "geometry": [{"uomDimensions": "FT", "lowerLimit": 0, "lowerVerticalReference": "AGL", "upperLimit": 400,
                      "upperVerticalReference": "AGL",
                      "horizontalProjection": {"type": "Circle", "center": [77.49, 12.905], "radius": 500}}],
    }]}
    response = helpers.upload(api_client, "lu-zones.json", json.dumps(doc).encode())
    info = helpers.wait_for_file(api_client, response.json()["id"])
    assert info["status"] == "COMPLETED", info
    assert any(i["code"] == "geozone_file" for i in info["issues"])

    exported = api_client.get(f"/api/files/{info['id']}/export/", params={"format": "ed318"})
    assert exported.status_code == 200 and ".ed318.geojson" in exported.headers["content-disposition"]
    (back,) = parse_ed318(exported.json())
    (original,) = parse_ed269(doc)
    assert back.model_dump(exclude={"geometry"}) == original.model_dump(exclude={"geometry"})
    assert shape(back.geometry).equals_exact(shape(original.geometry), 1e-7)

    done = helpers.run_analysis(api_client, {"kind": "airspace", "params": {
        "geometry": SURVEY, "grid": False, "zone_dataset_ids": [info["id"]], "altitude_agl_m": 100,
        "planned_start_utc": "2026-10-20T09:00:00Z", "planned_end_utc": "2026-10-20T09:30:00Z"}})
    hit = next(z for z in done["result"]["zones"] if z["name"].startswith("LUR01"))
    assert hit["zone"] == "yellow" and hit["permission_required"] is True

    plain = _zones_file(api_client, helpers, [
        {"type": "Feature", "properties": {"name": "P"}, "geometry": {"type": "Point", "coordinates": [77.49, 12.905]}}])
    refused = api_client.get(f"/api/files/{plain['id']}/export/", params={"format": "ed318"})
    # its own code, apart from invalid_params (a feature that is not a valid zone, a bad ed318_limits)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "no_zone_polygons"
    assert refused.json()["error"]["message"] == "This dataset has no polygons to export as zones."

    # a polygon without a zone column is read as zones from layers read it: red, PROHIBITED
    unmapped = _zones_file(api_client, helpers, [_polygon(mapping(box(77.485, 12.902, 77.495, 12.908)), name="Plot")])
    (feature,) = api_client.get(f"/api/files/{unmapped['id']}/export/", params={"format": "ed318"}).json()["features"]
    assert feature["properties"]["type"] == "PROHIBITED"
    assert feature["properties"]["extendedProperties"]["airbrief"]["zone"] == "red"


def test_an_analysis_export_keeps_what_the_zone_file_said(api_client: TestClient, helpers: SimpleNamespace) -> None:
    doc = {"title": "Weekdays", "features": [{
        "identifier": "WK01", "country": "IND", "name": "Weekday works", "type": "COMMON", "restriction": "CONDITIONAL",
        "reason": ["SENSITIVE"], "zoneAuthority": [{"name": "Site office", "email": "ops@example.org"}],
        "applicability": [{"schedule": [{"day": ["MON", "TUE", "WED", "THU", "FRI"], "startTime": "08:00", "endTime": "17:00"}]}],
        "geometry": [{"uomDimensions": "M", "lowerLimit": 0, "lowerVerticalReference": "AGL", "upperLimit": 120,
                      "upperVerticalReference": "AGL",
                      "horizontalProjection": {"type": "Circle", "center": [77.49, 12.905], "radius": 300}}],
    }]}
    (original,) = parse_ed269(doc)
    upload = helpers.upload(api_client, "weekdays.json", json.dumps(doc).encode())
    dataset = helpers.wait_for_file(api_client, upload.json()["id"])
    done = helpers.run_analysis(api_client, {"kind": "airspace", "params": {
        "geometry": SURVEY, "grid": False, "zone_dataset_ids": [dataset["id"]]}})
    assert done["status"] == "COMPLETED", done["error"]

    exported = api_client.get(f"/api/analyses/{done['id']}/export/", params={"format": "ed318"}).json()
    bundle = api_client.post("/api/briefs/bundle/", json={"analysis_ids": [done["id"]]})
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as zf:
        bundled = json.loads(zf.read("zones.ed318.geojson"))
    for written in (exported, bundled):
        (back,) = [z for z in parse_ed318(written) if z.name.startswith("WK01")]
        assert back.schedule == original.schedule and back.schedule[0].days == ["MON", "TUE", "WED", "THU", "FRI"]
        assert back.authority == original.authority == "Site office · ops@example.org"
        assert back.restriction == "CONDITIONAL" and back.reason == "SENSITIVE" and back.source_format == "ed269"
        (feature,) = [f for f in written["features"] if f["properties"]["name"].startswith("WK01")]
        assert feature["properties"]["type"] == "CONDITIONAL"


def test_meta_and_context_profiles(api_client: TestClient) -> None:
    meta = api_client.get("/api/meta/").json()
    assert set(meta["zone_profiles"]) == {"dgca_2021", "conservative_illustrative"}
    assert meta["zone_profiles"]["dgca_2021"]["profile"] == "dgca_2021"
    assert meta["default_zone_profile"] == "dgca_2021"

    view = {"bbox": "77.6,13.1,77.8,13.3", "layers": "rings"}
    dgca = api_client.get("/api/context/", params=view).json()["layers"]["rings"]["features"]
    old = api_client.get("/api/context/", params={**view, "profile": "conservative_illustrative"}).json()["layers"]["rings"]["features"]
    assert {f["properties"]["ring"] for f in dgca if f["properties"]["ident"] == "VOBL"} == {
        "0–5 km: not defined by the published rule — verify on Digital Sky (permission)",
        "5–8 km: yellow from the ground (permission required)",
        "8–12 km: green up to 60 m (200 ft) AGL, yellow above",
    }
    assert all(f["properties"]["distance_from"] == "reference_point" for f in old)
    assert api_client.get("/api/context/", params={**view, "profile": "custom"}).status_code == 422


def test_the_brief_flags_zones_from_datasets(api_client: TestClient, helpers: SimpleNamespace) -> None:
    zones = _zones_file(api_client, helpers, [_polygon(mapping(box(77.485, 12.902, 77.495, 12.908)), name="Event", zone="green")])
    done = helpers.run_analysis(api_client, {"kind": "airspace", "params": {
        "geometry": SURVEY, "grid": False, "zone_dataset_ids": [zones["id"]], "aerodrome_operational": {"VOBG": False}}})
    response = api_client.post("/api/briefs/", json={"analysis_ids": [done["id"]], "operation": {}})
    assert response.status_code == 200, response.text
    (warning,) = [w for w in response.json()["warnings"] if w["code"] == "non_default_parameters"]
    assert "(zone_dataset_ids)" in warning["message"] and "(aerodrome_operational)" in warning["message"]
