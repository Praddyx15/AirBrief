"""API regression tests where the modules meet.

* Line of sight on a GPX dataset: a 2D route and a 3D track in one selection (input resolution).
* Drone show from tracks without heights (a CSV without a height column): never read as 0 m.
* A CSV track whose height column is an ``elevation`` (above sea level) in the flight audit.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api import jobs
from api.jobs import JobRunner
from api.models import Analysis, utcnow

SQUARE = {"type": "Polygon", "coordinates": [[[77.60, 12.95], [77.61, 12.95], [77.61, 12.96], [77.60, 12.96], [77.60, 12.95]]]}


def test_worker_once_keeps_the_heartbeat_of_a_long_job(
    make_client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    # `python -m api.worker --once` runs jobs with JobRunner.drain(). Another process fails RUNNING
    # jobs whose heartbeat is older than STALE_AFTER_S, so drain must refresh it while it waits.
    client = make_client(job_mode="external")
    ctx = client.app.state.ctx  # type: ignore[attr-defined]
    with ctx.db.session() as session:
        session.add(Analysis(id="long-job", kind="airspace", status="PENDING", params={"geometry": SQUARE}, created_at=utcnow()))
        session.commit()
    monkeypatch.setattr(jobs, "HEARTBEAT_INTERVAL_S", 0.05)
    runner = JobRunner(ctx.db, ctx.storage, ctx.settings, ctx.registry, worker_id="worker-once", threads=1)
    beats: list[int] = []
    original = runner.heartbeat

    def counting() -> int:
        touched = original()
        beats.append(touched)
        return touched

    release = threading.Event()

    def slow_job(analysis_id: str, cancel_event: threading.Event) -> None:
        release.wait(0.5)

    monkeypatch.setattr(runner, "heartbeat", counting)
    monkeypatch.setattr(runner, "_run_analysis", slow_job)
    started = time.monotonic()
    assert runner.drain(timeout=10.0) == 1
    assert time.monotonic() - started >= 0.4
    assert sum(1 for touched in beats if touched == 1) >= 2  # the running job's row, several times
    runner.stop(wait=True)


def test_line_of_sight_resolves_a_gpx_route_and_track_of_mixed_dimensions(
    api_client: TestClient, helpers: SimpleNamespace
) -> None:
    # The GPX sample holds a 2D route (the training box), a 3D track and a 3D waypoint: their
    # union must not mix 2D and 3D positions (that gives an invalid MultiLineString).
    dataset = helpers.load_sample(api_client, "training_flight_gpx")
    response = api_client.post("/api/analyses/", json={"kind": "line_of_sight", "dataset_id": dataset["id"], "params": {}})
    assert response.status_code == 202, response.text
    info = helpers.wait_for_analysis(api_client, response.json()["id"])
    assert info["status"] == "COMPLETED", info["error"]
    assert info["params"]["geometry"]["type"] in ("GeometryCollection", "MultiLineString", "LineString", "Polygon")
    assert info["result"]["grid"]["area_cells"] > 0


def test_corridor_survey_of_lines_that_do_not_join_is_refused_at_creation(
    api_client: TestClient, helpers: SimpleNamespace
) -> None:
    # The GPX sample's route (a closed box) and track (out and back) cannot form one centreline:
    # a 422 at creation, as for a selection without lines, rather than a job that fails later.
    dataset = helpers.load_sample(api_client, "training_flight_gpx")
    body = {"kind": "survey", "dataset_id": dataset["id"], "params": {"coverage": "corridor", "corridor_width_m": 60}}
    response = api_client.post("/api/analyses/", json=body)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "geometry_type_not_supported" and "one connected centreline" in error["message"]
    # One of them selected alone is fine.
    features = api_client.get(f"/api/files/{dataset['id']}/features/").json()["features"]
    track = next(f["properties"]["_index"] for f in features if f["properties"]["_name"] == "Training flight")
    info = helpers.run_analysis(api_client, {**body, "feature_indices": [track]})
    assert info["status"] == "COMPLETED", info["error"]


def _two_drone_csv(with_heights: bool) -> bytes:
    header = "id,time,lon,lat,alt" if with_heights else "id,time,lon,lat"
    rows = [header]
    for t in range(0, 40, 2):
        for drone, lat in (("D1", 12.95), ("D2", 12.9502)):
            lon = 77.60 + t * 1e-5
            rows.append(f"{drone},{t},{lon:.6f},{lat},{min(t, 20)}" if with_heights else f"{drone},{t},{lon:.6f},{lat}")
    return ("\n".join(rows) + "\n").encode("utf-8")


def test_show_refuses_tracks_without_heights(api_client: TestClient, helpers: SimpleNamespace) -> None:
    # A CSV without a height column is read as 2D: the show checks (separation, speeds, ceiling)
    # cannot run on it, and reading it as 0 m would leave every drone "on the ground" (no checks).
    flat = helpers.upload(api_client, "show_2d.csv", _two_drone_csv(with_heights=False))
    assert flat.status_code == 201, flat.text
    response = api_client.post("/api/analyses/", json={"kind": "show", "dataset_id": flat.json()["id"], "params": {}})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "no_tracks"
    assert "D1" in error["message"] and "height" in error["message"]

    # The same tracks with heights run as before.
    tall = helpers.upload(api_client, "show_3d.csv", _two_drone_csv(with_heights=True))
    info = helpers.run_analysis(api_client, {"kind": "show", "dataset_id": tall.json()["id"], "params": {}})
    assert info["status"] == "COMPLETED", info["error"]
    assert info["result"]["drone_count"] == 2


def test_csv_elevation_column_is_read_above_sea_level_by_the_flight_audit(
    api_client: TestClient, helpers: SimpleNamespace
) -> None:
    # "elevation" is a height above sea level (as GPX <ele>): the audit must not read 905 m as
    # 905 m above the ground. The synthetic DEM is about 905–925 m here.
    rows = ["time,lon,lat,elevation"]
    for k in range(41):
        rows.append(f"{k},{77.488 + k * 2e-5:.6f},12.902,{905.0 + min(k, 40 - k) * 5.0}")
    upload = helpers.upload(api_client, "flight_ele.csv", ("\n".join(rows) + "\n").encode("utf-8"))
    assert upload.status_code == 201, upload.text
    record = api_client.get(f"/api/files/{upload.json()['id']}/features/0/").json()
    assert record["altitude_mode"] == "absolute"
    info = helpers.run_analysis(api_client, {"kind": "flight_audit", "dataset_id": upload.json()["id"], "params": {}})
    assert info["status"] == "COMPLETED", info["error"]
    result = info["result"]
    assert result["altitude_reference"] == "terrain_calibrated"
    assert result["max_height_above_takeoff_m"] == 100.0
    assert result["max_agl_m"] < 150.0
