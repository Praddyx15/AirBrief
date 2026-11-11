"""Operation evidence brief and bundle (``/api/briefs/``) and the complete analysis exports.

* The brief has every section, in order; a topic without an analysis is "not assessed" with a
  reason; synthetic data is watermarked; cross-checks catch an aircraft bigger or faster than
  the ground-risk analysis assumed.
* Provenance is reproducible: SHA-256 of the canonical parameters (recomputed here from the
  bundled envelopes), of the uploaded file (recomputed from the sample bytes) and of the brief
  itself; identical inputs give identical hashes whatever the order of the ids.
* The bundle holds exactly the expected, safe entry names within the size caps.
* Every kind exports KML that re-parses with :func:`airbrief_core.io.kml.parse_kml`, GeoJSON with
  its H3 polygons and evidence labels, and each CSV table of its catalogue.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import h3
import pytest
from fastapi.testclient import TestClient

from airbrief_core.brief import canonical_json
from airbrief_core.io.kml import parse_kml
from airbrief_core.models.brief import EVIDENCE_NOTICE, SECTION_ORDER, SYNTHETIC_WATERMARK
from api.models import Analysis, utcnow
from api.services import analyses as analysis_service
from api.services import briefs as brief_service
from api.services import exports as export_service
from api.services.exports import KML_EXCLUDED_LAYERS, table_catalog, table_csv, write_csv

OPERATION = {
    "operator": "Example Aerial Surveys",
    "reference": "OPS-2026-0142",
    "aircraft": {"preset": "mapping_quad"},
    "pilot_position": [77.5925, 12.9725],
    "planned_start_utc": "2026-06-21T03:30:00Z",
    "planned_end_utc": "2026-06-21T04:30:00Z",
    "utc_offset_min": 330,
    "notes": "Survey of the campus blocks.",
}

ENVIRONMENT_PARAMS = {"date": "2026-06-21", "window_start": "09:00", "window_end": "10:00", "utc_offset_min": 330}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))


def _run(client: TestClient, helpers: SimpleNamespace, kind: str, dataset_id: str | None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    info = helpers.run_analysis(client, {"kind": kind, "dataset_id": dataset_id, "params": params or {}})
    assert info["status"] == "COMPLETED", (kind, info["error"])
    return info  # type: ignore[no-any-return]


@pytest.fixture
def survey_runs(api_client: TestClient, helpers: SimpleNamespace) -> SimpleNamespace:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    airspace = _run(api_client, helpers, "airspace", dataset["id"], {"altitude_agl_m": 60})
    ground_risk = _run(api_client, helpers, "ground_risk", dataset["id"])
    return SimpleNamespace(dataset=dataset, airspace=airspace, ground_risk=ground_risk)


def _brief(client: TestClient, ids: list[str], operation: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post("/api/briefs/", json={"analysis_ids": ids, "operation": operation or {}})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _section(brief: dict[str, Any], key: str) -> dict[str, Any]:
    return next(s for s in brief["sections"] if s["key"] == key)


def _fact(part: dict[str, Any], label: str) -> dict[str, Any]:
    return next(f for f in part["facts"] if f["label"] == label)


def test_brief_sections_provenance_and_watermark(
    api_client: TestClient, survey_runs: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    ids = [survey_runs.airspace["id"], survey_runs.ground_risk["id"]]
    brief = _brief(api_client, ids, OPERATION)

    assert [s["key"] for s in brief["sections"]] == list(SECTION_ORDER)
    assert brief["title"] == "Operation evidence brief — OPS-2026-0142"
    assert brief["notice"] == EVIDENCE_NOTICE
    # The demo pack is synthetic: the brief says so on every page and in the cross-checks.
    assert brief["synthetic"] is True and brief["watermark"] == SYNTHETIC_WATERMARK
    codes = {w["code"] for w in brief["warnings"]}
    assert "synthetic_data" in codes
    # A mapping quad flies 18 m/s; the ground-risk run assumed the default 15 m/s.
    assert "aircraft_exceeds_assumption" in codes

    status = {s["key"]: s["status"] for s in brief["sections"]}
    assert status["operation"] == "info"
    assert status["airspace"] in ("assessed", "partial") and status["ground_risk"] == "assessed"
    assert status["heights"] == "assessed" and status["obstacles"] == "assessed"
    for key in ("environment", "line_of_sight", "survey", "flight_audit", "show", "evtol"):
        section = _section(brief, key)
        assert section["status"] == "not_assessed" and section["parts"] == []
        assert section["reason"].startswith("No ")

    operation = _section(brief, "operation")["parts"][0]
    assert _fact(operation, "Operator")["text"] == "Example Aerial Surveys"
    assert _fact(operation, "Aircraft")["text"] == "Mapping quadcopter (≈4 kg)"
    assert _fact(operation, "Max speed")["value"] == 18.0
    assert _fact(operation, "Planned window")["text"].startswith("2026-06-21 09:00–10:00")

    airspace = _section(brief, "airspace")["parts"][0]
    assert airspace["verdict"] == survey_runs.airspace["result"]["verdict"]
    assert _fact(airspace, "Planned height")["value"] == 60.0
    # Every analysis read the synthetic pack: the watermark says it once, not every part.
    assert not any("synthetic demo data pack" in b for b in airspace["banners"])
    ground = _section(brief, "ground_risk")["parts"][0]
    assert _fact(ground, "Intrinsic ground risk class")["value"] == survey_runs.ground_risk["result"]["igrc"]

    prov = brief["provenance"]
    assert [a["analysis_id"] for a in prov["analyses"]] == ids  # canonical order: airspace, then ground risk
    for entry, run in zip(prov["analyses"], (survey_runs.airspace, survey_runs.ground_risk), strict=True):
        assert entry["params_sha256"] == _sha(canonical_json(run["params"]))
        assert entry["result_sha256"] == _sha(canonical_json(run["result"]))
        assert entry["input_sha256"] == _sha(sample_bytes("bengaluru_survey"))
        assert entry["dataset_filename"] == survey_runs.dataset["filename"]
    (pack,) = prov["datapacks"]
    assert pack["name"] == "demo" and pack["synthetic"] is True and pack["installed"] is True
    layers = {layer["key"]: layer for layer in pack["layers"]}
    assert layers["dem"]["source"] and layers["dem"]["licence"]
    assert "airspace" in layers["aerodromes"]["used_by"] and "ground_risk" in layers["population"]["used_by"]
    assert re.fullmatch(r"[0-9a-f]{64}", prov["brief_sha256"])
    assert prov["operation_sha256"] == _sha(canonical_json(brief_service.BriefRequest.model_validate(
        {"analysis_ids": ids, "operation": OPERATION}).operation.model_dump(mode="json")))


def test_identical_inputs_give_identical_hashes(api_client: TestClient, helpers: SimpleNamespace, survey_runs: SimpleNamespace) -> None:
    ids = [survey_runs.airspace["id"], survey_runs.ground_risk["id"]]
    first = _brief(api_client, ids, OPERATION)
    second = _brief(api_client, list(reversed(ids)), OPERATION)  # the order given does not matter
    assert first["provenance"]["brief_sha256"] == second["provenance"]["brief_sha256"]
    assert first["provenance"]["operation_sha256"] == second["provenance"]["operation_sha256"]

    other = _brief(api_client, ids, {**OPERATION, "notes": "Another note."})
    assert other["provenance"]["operation_sha256"] != first["provenance"]["operation_sha256"]
    assert other["provenance"]["brief_sha256"] != first["provenance"]["brief_sha256"]

    # The same analysis run again: same parameters and input file, same hashes.
    again = _run(api_client, helpers, "airspace", survey_runs.dataset["id"], {"altitude_agl_m": 60})
    a = _brief(api_client, [survey_runs.airspace["id"]])["provenance"]["analyses"][0]
    b = _brief(api_client, [again["id"]])["provenance"]["analyses"][0]
    assert a["params_sha256"] == b["params_sha256"] and a["input_sha256"] == b["input_sha256"]
    assert a["analysis_id"] != b["analysis_id"]


def test_missing_modules_are_not_assessed_and_real_data_has_no_watermark(api_client: TestClient, helpers: SimpleNamespace) -> None:
    # An environment check of an area outside every data pack: no pack, so no synthetic data.
    area = {"type": "Polygon", "coordinates": [[[10.0, 50.0], [10.01, 50.0], [10.01, 50.01], [10.0, 50.01], [10.0, 50.0]]]}
    info = _run(api_client, helpers, "environment", None, {**ENVIRONMENT_PARAMS, "geometry": area, "utc_offset_min": 120})
    assert info["datapack"] is None
    brief = _brief(api_client, [info["id"]], {"planned_start_utc": "2026-06-22T07:00:00Z", "utc_offset_min": 120})
    assert brief["synthetic"] is False and brief["watermark"] is None
    status = {s["key"]: s["status"] for s in brief["sections"]}
    assert status == {
        "operation": "info", "airspace": "not_assessed", "heights": "not_assessed", "obstacles": "not_assessed",
        # No pack: power lines and masts were not checked, a data gap inside the environment check.
        "ground_risk": "not_assessed", "environment": "partial", "line_of_sight": "not_assessed", "survey": "not_assessed",
        "flight_audit": "not_assessed", "show": "not_assessed", "evtol": "not_assessed",
    }
    env = _section(brief, "environment")["parts"][0]
    assert env["banners"] and env["banners"][0].startswith("Interference not assessed: No data pack covers this area")
    assert _fact(env, "Sunrise")["text"].endswith("UTC+02:00")
    # The environment evidence is for 21 June; the planned window starts on 22 June.
    assert "window_date_differs" in {w["code"] for w in brief["warnings"]}
    prov = brief["provenance"]["analyses"][0]
    assert prov["input_sha256"] is None and "params_sha256" in prov["input_note"]
    # Operation fields left out are said to be "Not given", never blank.
    operation = _section(brief, "operation")["parts"][0]
    assert _fact(operation, "Operator")["text"] == "Not given"


def test_brief_request_errors(api_client: TestClient, survey_runs: SimpleNamespace) -> None:
    ok_id = survey_runs.airspace["id"]
    missing = api_client.post("/api/briefs/", json={"analysis_ids": [ok_id, "no-such-analysis"]})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"

    db = api_client.app.state.db  # type: ignore[attr-defined]
    with db.session() as session:
        session.add(Analysis(id="still-running", kind="airspace", status="RUNNING", params={}, created_at=utcnow()))
        session.commit()
    running = api_client.post("/api/briefs/", json={"analysis_ids": [ok_id, "still-running"]})
    assert running.status_code == 409 and running.json()["error"]["code"] == "analysis_not_finished"

    bad_bodies: list[dict[str, Any]] = [
        {"analysis_ids": []},
        {"analysis_ids": [f"id-{i}" for i in range(13)]},
        {"analysis_ids": [ok_id], "operation": {"aircraft": {"preset": "jumbo"}}},
        {"analysis_ids": [ok_id], "operation": {"planned_start_utc": "2026-06-21T05:00:00Z", "planned_end_utc": "2026-06-21T04:00:00Z"}},
        {"analysis_ids": [ok_id], "operation": {"pilot_position": [200, 12]}},
        {"analysis_ids": [ok_id], "extra": True},
    ]
    for body in bad_bodies:
        response = api_client.post("/api/briefs/", json=body)
        assert response.status_code == 422, (body, response.text)
        assert response.json()["error"]["code"] == "invalid_params"


def test_airspace_run_without_pack_zones_is_flagged_in_the_brief(
    api_client: TestClient, helpers: SimpleNamespace, survey_runs: SimpleNamespace
) -> None:
    # Threat-model AS-1: the same area checked without data-pack zones and without aerodrome rings.
    rules = api_client.get("/api/meta/").json()["zone_rules"]
    weakened = _run(api_client, helpers, "airspace", survey_runs.dataset["id"],
                    {"altitude_agl_m": 60, "include_datapack_zones": False, "zone_rules": {**rules, "rules": []}})
    brief = _brief(api_client, [weakened["id"], survey_runs.ground_risk["id"]])
    (warning,) = [w for w in brief["warnings"] if w["code"] == "non_default_parameters"]
    assert warning["severity"] == "warning"
    assert "include_datapack_zones: false" in warning["message"] and "(zone_rules)" in warning["message"]
    banners = _section(brief, "airspace")["parts"][0]["banners"]
    assert any(b.startswith("Run with non-default safety parameters") and "include_datapack_zones" in b for b in banners)
    html = _bundle(api_client, [weakened["id"]]).read("brief.html").decode("utf-8")
    assert html.count("include_datapack_zones: false") == 2  # the cross-check and the airspace banner

    normal = _brief(api_client, [survey_runs.airspace["id"]])
    assert "non_default_parameters" not in {w["code"] for w in normal["warnings"]}
    assert not any("non-default" in b for b in _section(normal, "airspace")["parts"][0]["banners"])


_SAFE_NAME = re.compile(r"^(?:[a-z]+\.(?:html|json|geojson|kml)|zones\.ed318\.geojson|analyses/\d{2}_[a-z_]+\.json|tables/\d{2}_[a-z_]+\.csv)$")


def _bundle(client: TestClient, ids: list[str], operation: dict[str, Any] | None = None) -> zipfile.ZipFile:
    response = client.post("/api/briefs/bundle/", json={"analysis_ids": ids, "operation": operation or {}})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert re.fullmatch(r'attachment; filename="airbrief_brief_[0-9a-f]{12}\.zip"', response.headers["content-disposition"])
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_bundle_entries_are_fixed_safe_names_with_reproducible_hashes(
    api_client: TestClient, survey_runs: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    ids = [survey_runs.airspace["id"], survey_runs.ground_risk["id"]]
    archive = _bundle(api_client, ids, OPERATION)
    assert archive.testzip() is None
    names = archive.namelist()
    expected = {"brief.html", "brief.json", "provenance.json", "evidence.geojson", "evidence.kml", "zones.ed318.geojson",
                "analyses/01_airspace.json", "analyses/02_ground_risk.json"}
    for prefix, run in (("01_airspace", survey_runs.airspace), ("02_ground_risk", survey_runs.ground_risk)):
        expected |= {f"tables/{prefix}_{t.key}.csv" for t in table_catalog(run["kind"], run["result"]) if t.key != "all"}
    assert set(names) == expected and len(names) == len(expected)
    for name in names:
        assert _SAFE_NAME.match(name), name
        assert ".." not in name and not name.startswith("/") and "\\" not in name
    total = sum(info.file_size for info in archive.infolist())
    assert total <= brief_service.MAX_BUNDLE_BYTES

    provenance = json.loads(archive.read("provenance.json"))
    entries = {e["name"]: e for e in provenance["bundle"]["entries"]}
    assert set(entries) == expected - {"provenance.json"}
    for name, entry in entries.items():
        data = archive.read(name)
        assert entry["bytes"] == len(data) and entry["sha256"] == _sha(data)
    # Anyone can recompute the parameter hashes from the bundled envelopes (canonical JSON).
    for i, entry in enumerate(provenance["analyses"], start=1):
        envelope = json.loads(archive.read(f"analyses/{i:02d}_{entry['kind']}.json"))
        assert envelope["notice"] == EVIDENCE_NOTICE and envelope["watermark"] == SYNTHETIC_WATERMARK
        dumped = json.dumps(envelope["params"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert _sha(dumped) == entry["params_sha256"]
        assert entry["input_sha256"] == _sha(sample_bytes("bengaluru_survey"))
    brief = json.loads(archive.read("brief.json"))
    assert brief["provenance"]["brief_sha256"] == provenance["brief_sha256"]

    html = archive.read("brief.html").decode("utf-8")
    assert "default-src 'none'" in html and "<script" not in html.lower()
    assert not re.search(r"""(?:src|href)\s*=\s*["']?https?:""", html)
    assert "Evidence, not authorisation" in html and SYNTHETIC_WATERMARK in html and "@page" in html
    assert "Not assessed" in html and provenance["brief_sha256"] in html

    geojson = json.loads(archive.read("evidence.geojson"))
    analyses = {f["properties"]["_analysis"] for f in geojson["features"]}
    assert analyses == {"01_airspace", "02_ground_risk"}
    assert geojson["airbrief"]["watermark"] == SYNTHETIC_WATERMARK
    grid = [f for f in geojson["features"] if f["properties"]["_analysis"] == "01_airspace" and f["properties"]["_layer"] == "grid"]
    assert len(grid) == len(survey_runs.airspace["result"]["grid"]) and grid[0]["geometry"]["type"] == "Polygon"
    layers = parse_kml(archive.read("evidence.kml"))
    kml_count = sum(len(layer.features) for layer in layers)
    assert kml_count == len(geojson["features"])
    assert any(name.endswith("01 Airspace & terrain / Grid") for name in (layer.name for layer in layers))

    zones_csv = _rows(archive.read("tables/01_airspace_zones.csv"))
    assert len(zones_csv) == len(survey_runs.airspace["result"]["zones"])
    terms = _rows(archive.read("tables/02_ground_risk_cells.csv"))
    assert all(r["notice"].startswith(SYNTHETIC_WATERMARK) for r in terms)


def test_bundle_caps(api_client: TestClient, survey_runs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = [survey_runs.airspace["id"]]
    monkeypatch.setattr(brief_service, "MAX_TABLE_ROWS", 3)
    archive = _bundle(api_client, ids)
    grid = _rows(archive.read("tables/01_airspace_grid.csv"))
    assert len(grid) == 3
    truncated = json.loads(archive.read("provenance.json"))["bundle"]["truncated_tables"]
    assert truncated["tables/01_airspace_grid.csv"] == len(survey_runs.airspace["result"]["grid"]) - 3

    monkeypatch.setattr(brief_service, "MAX_BUNDLE_BYTES", 20_000)
    too_big = api_client.post("/api/briefs/bundle/", json={"analysis_ids": ids})
    assert too_big.status_code == 413 and too_big.json()["error"]["code"] == "bundle_too_large"


def test_csv_neutralises_formula_text_in_every_table() -> None:
    formula = ["=HYPERLINK(\"http://x\")", "+1+1", "-2+3", "@SUM(A1)", "\t=1", "\r=1"]
    rows = [{"name": text, "value": 1.5} for text in formula] + [{"name": "Plain", "value": -4.0}]
    parsed = _rows(write_csv(rows, ["name", "value"], "notice text"))
    assert [r["name"] for r in parsed[:-1]] == ["'" + text for text in formula]
    assert parsed[-1] == {"name": "Plain", "value": "-4.0", "notice": "notice text"}  # numbers stay numbers

    # Through a kind's catalogue: a zone name from the data, in the single and the combined table.
    zone = {"name": "=cmd|' /C calc'!A0", "zone": "red", "ceiling_agl_m": 0.0, "permission_required": True}
    result = {"zones": [zone], "obstacles": [], "grid": [], "profile": [], "nearest_aerodromes": []}
    for table in ("zones", "all"):
        (row,) = _rows(table_csv("airspace", result, {}, table, datapack_synthetic=True))
        assert row["name"] == "'" + zone["name"]
        assert row["notice"] == f"{SYNTHETIC_WATERMARK}. {EVIDENCE_NOTICE}"


def test_geojson_h3_cells_are_the_true_hexagons(api_client: TestClient, survey_runs: SimpleNamespace) -> None:
    collection = api_client.get(f"/api/analyses/{survey_runs.airspace['id']}/export/", params={"format": "geojson"}).json()
    grid = [f for f in collection["features"] if f["properties"]["_layer"] == "grid"]
    assert grid and len(grid) == len(survey_runs.airspace["result"]["grid"])
    for feature in grid:
        cell = feature["properties"]["h3"]
        ring = feature["geometry"]["coordinates"][0]
        assert feature["geometry"]["type"] == "Polygon" and ring[0] == ring[-1]  # closed ring, RFC 7946
        expected = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]  # h3 gives (lat, lng)
        assert len(ring) == len(expected) + 1
        for (x, y), (ex, ey) in zip(ring[:-1], expected, strict=True):
            assert x == pytest.approx(ex, abs=1e-7) and y == pytest.approx(ey, abs=1e-7)
        # The cell's values travel with it; its position is the polygon, not a duplicate lon/lat.
        assert "status" in feature["properties"] and "lon" not in feature["properties"]


def test_geojson_and_kml_are_capped_with_the_cut_recorded(
    api_client: TestClient, survey_runs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = f"/api/analyses/{survey_runs.airspace['id']}/export/"
    full = api_client.get(base, params={"format": "geojson"}).json()
    total = len(full["features"])
    assert total > 5 and full["airbrief"]["truncated"] is False
    monkeypatch.setattr(export_service, "FEATURE_LIMIT", 5)
    cut = api_client.get(base, params={"format": "geojson"}).json()
    assert len(cut["features"]) == 5 and cut["airbrief"]["feature_count"] == 5
    assert cut["airbrief"]["truncated"] is True and sum(cut["airbrief"]["truncated_layers"].values()) == total - 5
    kml = parse_kml(api_client.get(base, params={"format": "kml"}).content)
    assert sum(len(layer.features) for layer in kml) == 5


def test_csv_of_a_kind_without_tables_is_a_clear_error(
    api_client: TestClient, survey_runs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A kind added later without a CSV table: the error envelope, never a 500.
    monkeypatch.setattr(analysis_service, "table_catalog", lambda _kind, _result: [])
    response = api_client.get(f"/api/analyses/{survey_runs.airspace['id']}/export/", params={"format": "csv"})
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_params"


def _all_kinds(client: TestClient, helpers: SimpleNamespace) -> list[dict[str, Any]]:
    survey = helpers.load_sample(client, "bengaluru_survey")
    evtol = helpers.load_sample(client, "bengaluru_evtol")
    show = helpers.load_sample(client, "show_50_drones")
    gpx = helpers.load_sample(client, "training_flight_gpx")
    return [
        _run(client, helpers, "airspace", survey["id"], {"altitude_agl_m": 60}),
        _run(client, helpers, "ground_risk", survey["id"]),
        _run(client, helpers, "ground_risk", survey["id"], {"volume": {}}),
        _run(client, helpers, "evtol_siting", evtol["id"]),
        _run(client, helpers, "evtol_corridor", evtol["id"]),
        _run(client, helpers, "show", show["id"]),
        _run(client, helpers, "survey", survey["id"]),
        _run(client, helpers, "flight_audit", gpx["id"], {"max_distance_from_home_m": 500}),
        _run(client, helpers, "environment", survey["id"], ENVIRONMENT_PARAMS),
        _run(client, helpers, "line_of_sight", survey["id"]),
    ]


@pytest.mark.slow
def test_every_kind_exports_kml_geojson_csv_and_appears_in_the_brief(api_client: TestClient, helpers: SimpleNamespace) -> None:
    runs = _all_kinds(api_client, helpers)
    assert {r["kind"] for r in runs} == {
        "airspace", "ground_risk", "evtol_siting", "evtol_corridor", "show", "survey", "flight_audit", "environment", "line_of_sight",
    }
    for run in runs:
        base = f"/api/analyses/{run['id']}/export/"
        kind = run["kind"]

        geo = api_client.get(base, params={"format": "geojson"})
        assert geo.status_code == 200, (kind, geo.text)
        collection = geo.json()
        assert collection["airbrief"]["notice"] == EVIDENCE_NOTICE and collection["airbrief"]["kind"] == kind
        by_layer: dict[str, int] = {}
        for f in collection["features"]:
            by_layer[f["properties"]["_layer"]] = by_layer.get(f["properties"]["_layer"], 0) + 1

        kml = api_client.get(base, params={"format": "kml"})
        assert kml.status_code == 200 and kml.headers["content-type"].startswith("application/vnd.google-earth.kml+xml"), kind
        assert kml.headers["content-disposition"].endswith('.kml"')
        layers = parse_kml(kml.content)  # round trip through the hardened reader
        excluded = KML_EXCLUDED_LAYERS.get(kind, frozenset())
        kml_by_layer = {layer.name.rsplit(" / ", 1)[-1]: len(layer.features) for layer in layers}
        expected = {name.replace("_", " ").capitalize(): n for name, n in by_layer.items() if name not in excluded}
        assert kml_by_layer == expected, kind
        assert all(f.geometry is not None for layer in layers for f in layer.features), kind
        for layer in layers:
            for feature in layer.features:
                assert feature.properties.get("_analysis_id") == run["id"]
                # Evidence placemarks are 2D: no altitudes that could pass for a flight route.
                assert not feature.geometry.has_z  # type: ignore[union-attr]

        tables = api_client.get(f"{base}tables/")
        assert tables.status_code == 200, kind
        catalog = tables.json()
        assert catalog and catalog[0]["default"] is True and [t["key"] for t in catalog] == [t.key for t in table_catalog(kind, run["result"])]
        for entry in catalog:
            response = api_client.get(base, params={"format": "csv", "table": entry["key"]})
            assert response.status_code == 200, (kind, entry["key"], response.text)
            content = response.content.decode("utf-8-sig")
            header = next(csv.reader(io.StringIO(content)))
            assert header[-1] == "notice", (kind, entry["key"])
            rows = _rows(response.content)
            assert len(rows) == entry["rows"], (kind, entry["key"])
            assert all(r["notice"].endswith(EVIDENCE_NOTICE) for r in rows)
        unknown = api_client.get(base, params={"format": "csv", "table": "nope"})
        assert unknown.status_code == 422 and unknown.json()["error"]["details"]["known"] == [t["key"] for t in catalog]

        envelope = api_client.get(base, params={"format": "json"}).json()
        assert envelope["notice"] == EVIDENCE_NOTICE

    # Flight audit: the default CSV holds the profile, exceedances, training-area exits and
    # distance-from-take-off segments in one file.
    audit = next(r for r in runs if r["kind"] == "flight_audit")
    rows = _rows(api_client.get(f"/api/analyses/{audit['id']}/export/", params={"format": "csv"}).content)
    tables = {r["table"] for r in rows}
    assert {"profile_point", "area_exit", "distance"} <= tables
    assert sum(1 for r in rows if r["table"] == "area_exit") == len(audit["result"]["area_exits"])
    assert sum(1 for r in rows if r["table"] == "distance") == len(audit["result"]["distance_exceedances"])

    # One brief with every kind: every section assessed (or partly), none blank.
    brief = _brief(api_client, [r["id"] for r in runs if not (r["kind"] == "ground_risk" and r["params"].get("volume") is None)])
    status = {s["key"]: s["status"] for s in brief["sections"]}
    assert all(v in ("info", "assessed", "partial") for v in status.values()), status
    show = _section(brief, "show")["parts"][0]
    assert {"Show box", "Audience distance", "Pad spacing", "Origin"} <= {f["label"] for f in show["facts"]}
    flight = _section(brief, "flight_audit")["parts"][0]
    assert {"Outside the training area", "Farthest from take-off"} <= {f["label"] for f in flight["facts"]}
    volume = _section(brief, "ground_risk")["parts"][0]
    assert {"Contingency volume", "Ground risk buffer", "Adjacent area density"} <= {f["label"] for f in volume["facts"]}
    assert any(t["caption"].startswith("Operational volume terms") for t in volume["tables"])
    los = _section(brief, "line_of_sight")["parts"][0]
    assert _fact(los, "Area in visual line of sight")["unit"] == "%"
    assert len(_section(brief, "evtol")["parts"]) == 2


def test_survey_coverage_lines_are_never_exported() -> None:
    from api.services.exports import geojson_document, layer_collections

    line = {"type": "Feature", "properties": {}, "geometry": {"type": "LineString", "coordinates": [[77.5, 12.9], [77.51, 12.9]]}}
    block = {"type": "Feature", "properties": {"index": 1}, "geometry": {"type": "Polygon", "coordinates": [[[77.5, 12.9], [77.51, 12.9], [77.51, 12.91], [77.5, 12.9]]]}}
    result = {"layers": {"lines": {"type": "FeatureCollection", "features": [line]}, "blocks": {"type": "FeatureCollection", "features": [block]}}}
    collections, _ = layer_collections(result, "survey")
    assert "lines" not in collections and "blocks" in collections
    doc = geojson_document("survey", result, analysis_id="a", datapack=None, datapack_synthetic=False, name="s")
    assert {f["properties"]["_layer"] for f in doc["features"]} == {"blocks"}
    # Other kinds keep a layer called "lines".
    assert "lines" in layer_collections(result, "airspace")[0]
