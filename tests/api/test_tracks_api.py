"""GPX uploads, flight audit from a dataset's position track, and the drone-show site and pad
checks through ``POST /api/analyses/``."""

from __future__ import annotations

import csv
import io
import zipfile
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from airbrief_core.samples import (
    TRAINING_DURATION_S,
    TRAINING_EXIT_END_S,
    TRAINING_EXIT_START_S,
    TRAINING_FARTHEST_M,
    TRAINING_MAX_HEIGHT_M,
    build_sample,
)

_FAR_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
<trk><name>Desert hop</name><trkseg>
<trkpt lat="20.0000" lon="10.0000"><ele>400</ele><time>2026-05-01T06:00:00Z</time></trkpt>
<trkpt lat="20.0005" lon="10.0000"><ele>440</ele><time>2026-05-01T06:00:20Z</time></trkpt>
<trkpt lat="20.0010" lon="10.0000"><ele>440</ele><time>2026-05-01T06:00:40Z</time></trkpt>
<trkpt lat="20.0010" lon="10.0005"><ele>400</ele><time>2026-05-01T06:01:00Z</time></trkpt>
</trkseg></trk>
</gpx>
"""


def _features(client: TestClient, dataset_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/api/files/{dataset_id}/features/")
    assert response.status_code == 200, response.text
    return list(response.json()["features"])


def test_gpx_upload_and_sample(api_client: TestClient, helpers: SimpleNamespace) -> None:
    response = helpers.upload(api_client, "training_flight.gpx", build_sample("training_flight_gpx"))
    assert response.status_code == 201, response.text
    info = response.json()
    assert info["status"] == "COMPLETED" and info["file_type"] == "gpx"
    assert info["feature_count"] == 3 and info["layer_count"] == 3
    by_name = {f["properties"]["_name"]: f for f in _features(api_client, info["id"])}
    assert by_name["Take-off"]["geometry"]["type"] == "Point"
    assert by_name["Training box"]["geometry"]["type"] == "LineString"
    assert by_name["Training flight"]["geometry"]["type"] == "LineString"
    # Timed tracks are flagged for the panels (positions with times, seconds first to last).
    assert by_name["Training flight"]["properties"]["_time_points"] == TRAINING_DURATION_S + 1
    assert by_name["Training flight"]["properties"]["_duration_s"] == float(TRAINING_DURATION_S)
    assert "_time_points" not in by_name["Training box"]["properties"]

    # The GPX sample is a dataset (the FlightMD JSON samples stay downloads only).
    sample = helpers.load_sample(api_client, "training_flight_gpx")
    assert sample["file_type"] == "gpx" and sample["sample"] == "training_flight_gpx"
    download = api_client.get("/api/samples/training_flight_gpx/download/")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/gpx+xml")
    refused = api_client.post("/api/samples/flightmd_report_v15/")
    assert refused.json()["error"]["code"] == "sample_not_dataset"


def test_zip_of_per_drone_csvs_upload(api_client: TestClient, helpers: SimpleNamespace) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for drone, east in (("D01", 0.0), ("D02", 3.0)):
            rows = "\n".join(f"{t},{east},{t * 0.5},{t}" for t in range(5))
            archive.writestr(f"show/{drone}.csv", f"# origin=77.5920,13.0005,0\nt,x,y,z\n{rows}\n")
    response = helpers.upload(api_client, "skybrush_export.zip", buffer.getvalue())
    assert response.status_code == 201, response.text
    info = response.json()
    assert info["status"] == "COMPLETED" and info["file_type"] == "csv"
    names = sorted(f["properties"]["_name"] for f in _features(api_client, info["id"]))
    assert names == ["D01", "D02"]


def test_flight_audit_from_a_dataset_track(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "training_flight_gpx")
    info = helpers.run_analysis(
        api_client,
        {"kind": "flight_audit", "dataset_id": dataset["id"], "params": {"max_distance_from_home_m": 500}},
    )
    assert info["status"] == "COMPLETED", info
    params = info["params"]
    assert params["track"]["name"] == "Training flight"
    assert params["track"]["source_format"] == "gpx"
    assert params["track"]["altitude_mode"] == "absolute"
    assert len(params["track"]["times"]) == TRAINING_DURATION_S + 1
    assert params["training_box_name"] == "Training box"
    assert info["datapack"] == "demo"

    result = info["result"]
    assert result["flight"]["source"] == "track"
    assert result["flight"]["track_format"] == "gpx"
    assert result["duration_s"] == pytest.approx(float(TRAINING_DURATION_S))
    assert result["max_height_above_takeoff_m"] == pytest.approx(TRAINING_MAX_HEIGHT_M, abs=0.01)
    assert result["max_home_distance_m"] == pytest.approx(TRAINING_FARTHEST_M, abs=0.01)
    assert result["assessment_complete"] is True
    (exit_,) = result["area_exits"]
    assert exit_["start_time_s"] == pytest.approx(TRAINING_EXIT_START_S, abs=1e-3)
    assert exit_["end_time_s"] == pytest.approx(TRAINING_EXIT_END_S, abs=1e-3)
    assert result["time_outside_area_s"] == pytest.approx(60.0, abs=1e-3)
    (far,) = result["distance_exceedances"]
    assert far["start_time_s"] == pytest.approx(197.98, abs=0.01)
    assert far["end_time_s"] == pytest.approx(TRAINING_EXIT_END_S, abs=1e-3)
    assert {"training_box", "area_exits", "home_radius", "distance_exceedances"} <= set(result["layers"])
    assert result["verdict"] == "fail"

    # The training area is a choice: an explicit null switches it off.
    off = helpers.run_analysis(
        api_client, {"kind": "flight_audit", "dataset_id": dataset["id"], "params": {"training_box": None}}
    )
    assert off["status"] == "COMPLETED", off
    assert off["result"]["area_exits"] is None and off["result"]["distance_exceedances"] is None

    # The CSV export is the height profile along the track.
    csv_export = api_client.get(f"/api/analyses/{info['id']}/export/", params={"format": "csv"})
    assert csv_export.status_code == 200
    header = next(csv.reader(io.StringIO(csv_export.content.decode("utf-8-sig"))))
    assert "agl_m" in header and "limit_agl_m" in header


def test_track_outside_every_pack_is_not_assessed(api_client: TestClient, helpers: SimpleNamespace) -> None:
    upload = helpers.upload(api_client, "desert_hop.gpx", _FAR_GPX)
    assert upload.status_code == 201, upload.text
    info = helpers.run_analysis(
        api_client, {"kind": "flight_audit", "dataset_id": upload.json()["id"], "params": {"ceiling_agl_m": 120}}
    )
    assert info["status"] == "COMPLETED", info
    assert info["datapack"] is None
    result = info["result"]
    assert result["assessment_complete"] is False
    assert result["verdict"] == "caution"  # never "pass" when zones were not checked
    assert "not_assessed" in {i["code"] for i in result["issues"]}
    assert result["max_agl_m"] == pytest.approx(40.0, abs=0.01)


def test_flight_audit_needs_a_timed_track(api_client: TestClient, helpers: SimpleNamespace) -> None:
    survey = helpers.load_sample(api_client, "bengaluru_survey")
    response = api_client.post("/api/analyses/", json={"kind": "flight_audit", "dataset_id": survey["id"], "params": {}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_tracks"


def test_show_derived_box_audience_rule_and_pad_rows(api_client: TestClient, helpers: SimpleNamespace) -> None:
    # The CSV show has no "Show box" polygon: the box is derived from the tracks.
    dataset = helpers.load_sample(api_client, "show_50_drones_csv")
    info = helpers.run_analysis(
        api_client,
        {
            "kind": "show",
            "dataset_id": dataset["id"],
            "params": {
                "audience_buffer_rule": "ballistic",
                "audience_breach_margin_pct": 50,
                "min_pad_spacing_m": 6,
                "endurance_s": 600,
            },
        },
    )
    assert info["status"] == "COMPLETED", info
    result = info["result"]
    site = result["site"]
    assert site["show_box_source"] == "derived" and site["show_box_margin_m"] == 5.0
    rule = site["audience_buffer"]
    assert rule["rule"] == "ballistic" and rule["breach_margin_pct"] == 50.0
    assert rule["distance_m"] == pytest.approx(rule["base_distance_m"] * 1.5, abs=0.02)
    assert result["max_altitude_m"] == pytest.approx(50.8, abs=0.01)
    assert result["pads"]["takeoff_pads"] == 50 and len(result["pads"]["conflicts"]) == 100
    assert result["verdict"] == "fail"

    export = api_client.get(f"/api/analyses/{info['id']}/export/", params={"format": "csv"})
    rows = list(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig"))))
    pad_rows = [r for r in rows if r["event"] == "pad_conflict"]
    assert len(pad_rows) == 100
    assert {r["kind"] for r in pad_rows} == {"takeoff", "landing"}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Training box", True),
        ("Training area", True),
        ("Flying area", True),
        ("Site boundary", True),
        ("Geofence", True),
        ("No-fly area", False),
        ("NFZ boundary", False),
        ("Restricted area", False),
        ("Prohibited area P-1", False),
        ("Audience area", False),
        ("Red zone boundary", False),
        ("Covered area", True),  # "red zone" only as words: not inside "covered"
        ("Take-off", False),
        (None, False),
    ],
)
def test_training_area_names(name: str | None, expected: bool) -> None:
    from api.services.tracks import is_training_area_name

    assert is_training_area_name(name) is expected


def test_a_no_fly_area_is_never_the_default_training_area(api_client: TestClient, helpers: SimpleNamespace) -> None:
    kml = b"""<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2"><Document>
<Placemark><name>No-fly area</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
77.4990,12.9010 77.5010,12.9010 77.5010,12.9030 77.4990,12.9030 77.4990,12.9010</coordinates></LinearRing>
</outerBoundaryIs></Polygon></Placemark>
<Placemark><name>Hop</name><gx:Track><altitudeMode>absolute</altitudeMode>
<when>2026-02-01T08:00:00Z</when><when>2026-02-01T08:00:30Z</when>
<gx:coord>77.4995 12.902 900</gx:coord><gx:coord>77.5005 12.902 950</gx:coord>
</gx:Track></Placemark></Document></kml>"""
    upload = helpers.upload(api_client, "hop.kml", kml)
    assert upload.status_code == 201, upload.text
    info = helpers.run_analysis(api_client, {"kind": "flight_audit", "dataset_id": upload.json()["id"], "params": {}})
    assert info["status"] == "COMPLETED", info
    assert "training_box" not in info["params"]
    assert info["result"]["area_exits"] is None


def test_a_closed_timed_track_is_never_its_own_training_area() -> None:
    from api.services.tracks import _training_box

    ring = {"type": "LineString", "coordinates": [[77.5, 12.9], [77.51, 12.9], [77.51, 12.91], [77.5, 12.9]]}
    box = {"type": "Polygon", "coordinates": [[[77.4, 12.8], [77.6, 12.8], [77.6, 13.0], [77.4, 13.0], [77.4, 12.8]]]}
    flight = SimpleNamespace(name="Training flight", times=[0.0, 1.0, 2.0, 3.0], geometry_wgs84=ring)
    area = SimpleNamespace(name="Training box", times=None, geometry_wgs84=box)
    found = _training_box([flight, area])
    assert found is not None and found[1] == "Training box"
    assert _training_box([flight]) is None
