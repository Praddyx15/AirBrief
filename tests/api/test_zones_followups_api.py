"""Zone profiles beyond airspace through the API: flight audits and eVTOL runs store their
``zone_profile``; a brief bundle with an airspace check or flight audit carries their zones as
``zones.ed318.geojson``, listed and hashed in ``provenance.json`` and so signed with the rest."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from shapely.geometry import box, mapping

from airbrief_core.geo.geodesic import GEOD
from airbrief_core.io.geozones import parse_ed318
from airbrief_core.models.brief import EVIDENCE_NOTICE
from airbrief_core.signing import write_new_key
from airbrief_core.verify import verify_bundle

VOBG = (77.6682, 12.9499)


def _track() -> dict[str, Any]:
    # 10.2 to 11.8 km east of HAL's synthetic boundary (its 8-12 km band), at 75 m above ground
    points = [GEOD.fwd(VOBG[0], VOBG[1], 90.0, d)[:2] for d in range(12_000, 13_601, 200)]
    return {"coordinates": [[float(lon), float(lat), 75.0] for lon, lat in points], "altitude_mode": "relativeToGround"}


def _line() -> dict[str, Any]:
    return {"type": "LineString", "coordinates": [c[:2] for c in _track()["coordinates"]]}


def _run(client: TestClient, helpers: SimpleNamespace, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    info = helpers.run_analysis(client, {"kind": kind, "params": params})
    assert info["status"] == "COMPLETED", info["error"]
    return info  # type: ignore[no-any-return]


def test_audits_and_evtol_runs_store_their_zone_profile(api_client: TestClient) -> None:
    area = mapping(box(77.66, 12.85, 77.67, 12.86))
    bodies = [
        ("flight_audit", {"track": _track()}),
        ("evtol_siting", {"geometry": area}),
        ("evtol_corridor", {"origin": [77.66, 12.85], "destination": [77.70, 12.86]}),
    ]
    for kind, params in bodies:
        default = api_client.post("/api/analyses/", json={"kind": kind, "params": params})
        assert default.status_code == 202, default.text
        assert default.json()["params"]["zone_profile"] == "dgca_2021"
        chosen = api_client.post("/api/analyses/", json={"kind": kind, "params": {**params, "zone_profile": "conservative_illustrative"}})
        assert chosen.json()["params"]["zone_profile"] == "conservative_illustrative"
        rules = api_client.get("/api/meta/").json()["zone_profiles"]["conservative_illustrative"]
        custom = api_client.post("/api/analyses/", json={"kind": kind, "params": {**params, "zone_rules": rules}})
        assert custom.json()["params"]["zone_profile"] == "custom"
        refused = api_client.post("/api/analyses/", json={"kind": kind, "params": {**params, "zone_profile": "custom"}})
        assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_params"


def test_meta_zone_rules_are_the_default_profile(api_client: TestClient) -> None:
    meta = api_client.get("/api/meta/").json()
    assert meta["default_zone_profile"] == "dgca_2021"
    assert meta["zone_rules"] == meta["zone_profiles"]["dgca_2021"]


def test_the_bundle_carries_the_zones_as_ed318_signed_with_the_rest(
    make_client: Any, helpers: SimpleNamespace, tmp_path: Path
) -> None:
    key = tmp_path / "keys" / "brief-signing.pem"
    write_new_key(key)
    client = make_client(brief_signing_key_file=key)
    public_key = base64.b64decode(client.get("/api/meta/").json()["signing"]["public_key"])
    airspace = _run(client, helpers, "airspace", {"geometry": _line(), "altitude_agl_m": 75, "grid": False})
    audit = _run(client, helpers, "flight_audit", {"track": _track()})

    response = client.post("/api/briefs/bundle/", json={"analysis_ids": [airspace["id"], audit["id"]]})
    assert response.status_code == 200, response.text
    path = tmp_path / "bundle.zip"
    path.write_bytes(response.content)
    with zipfile.ZipFile(path) as zf:
        data = zf.read("zones.ed318.geojson")
        provenance = json.loads(zf.read("provenance.json"))
    (entry,) = [e for e in provenance["bundle"]["entries"] if e["name"] == "zones.ed318.geojson"]
    assert entry["sha256"] == hashlib.sha256(data).hexdigest() and entry["bytes"] == len(data)

    doc = json.loads(data)
    assert doc["airbrief"]["notice"] == EVIDENCE_NOTICE
    assert doc["airbrief"]["analyses"] == ["01_airspace", "02_flight_audit"]
    owners = {f["properties"]["name"]: f["properties"]["extendedProperties"]["airbrief"]["analyses"] for f in doc["features"]}
    # both runs were judged against the same HAL bands: each band is written once and names both
    assert owners["VOBG 8–12 km up to 60 m"] == ["01_airspace", "02_flight_audit"]
    assert owners["VOBG 8–12 km above 60 m"] == ["01_airspace", "02_flight_audit"]
    zones = parse_ed318(doc)
    assert len(zones) == len(doc["features"]) and {z.zone for z in zones} >= {"green", "yellow"}

    report = verify_bundle(path, public_key)
    assert report.ok and report.signature == "valid"

    # A band removed from the zone file is caught: its hash no longer matches the signed list.
    trimmed = {**doc, "features": [f for f in doc["features"] if "8–12 km" not in f["properties"]["name"]]}
    edited = tmp_path / "edited.zip"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(edited, "w") as dst:
        for info in src.infolist():
            content = src.read(info)
            if info.filename == "zones.ed318.geojson":
                content = json.dumps(trimmed).encode("utf-8")
            dst.writestr(info, content)
    assert not verify_bundle(edited, public_key).ok


def test_a_bundle_without_airspace_or_audit_has_no_zone_file(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info = helpers.run_analysis(api_client, {"kind": "ground_risk", "dataset_id": dataset["id"], "params": {}})
    assert info["status"] == "COMPLETED", info["error"]
    response = api_client.post("/api/briefs/bundle/", json={"analysis_ids": [info["id"]]})
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert "zones.ed318.geojson" not in zf.namelist()


def test_a_flight_audit_exports_its_zones_as_ed318(api_client: TestClient, helpers: SimpleNamespace) -> None:
    audit = _run(api_client, helpers, "flight_audit", {"track": _track()})
    response = api_client.get(f"/api/analyses/{audit['id']}/export/", params={"format": "ed318"})
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].endswith('_zones.ed318.geojson"')
    names = {z.name for z in parse_ed318(response.json())}
    assert {"VOBG 8–12 km up to 60 m", "VOBG 8–12 km above 60 m"} <= names

    corridor = _run(api_client, helpers, "evtol_corridor", {"origin": [77.66, 12.85], "destination": [77.70, 12.86]})
    refused = api_client.get(f"/api/analyses/{corridor['id']}/export/", params={"format": "ed318"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_params"
