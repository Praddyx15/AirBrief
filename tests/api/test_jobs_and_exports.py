"""Job recovery, shutdown, data-pack bookkeeping, show altitudes and export hygiene (API level)."""

from __future__ import annotations

import json
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import Future
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from api.jobs import JobRunner, fair_order, mark_interrupted, run_analysis_job
from api.models import Analysis, Dataset, as_utc, utcnow

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[77.60, 12.95], [77.61, 12.95], [77.61, 12.96], [77.60, 12.96], [77.60, 12.95]]],
}


def _add_analysis(client: TestClient, analysis_id: str, **values: Any) -> None:
    db = client.app.state.db  # type: ignore[attr-defined]
    fields: dict[str, Any] = {"kind": "airspace", "status": "RUNNING", "params": {"geometry": SQUARE}}
    fields.update(values)
    with db.session() as session:
        session.add(Analysis(id=analysis_id, **fields))
        session.commit()


def _analysis(client: TestClient, analysis_id: str) -> Analysis:
    db = client.app.state.db  # type: ignore[attr-defined]
    with db.session() as session:
        row = session.get(Analysis, analysis_id)
        assert row is not None
        session.expunge(row)
        return row


def test_mark_interrupted_spares_jobs_with_a_fresh_heartbeat(api_client: TestClient) -> None:
    now = utcnow()
    _add_analysis(api_client, "live", heartbeat_at=now, claimed_by="other-process")
    _add_analysis(api_client, "stale", heartbeat_at=now - timedelta(minutes=5), claimed_by="dead-process")
    _add_analysis(api_client, "legacy")  # no heartbeat at all (written before heartbeats existed)

    datasets, analyses = mark_interrupted(api_client.app.state.db)  # type: ignore[attr-defined]

    assert (datasets, analyses) == (0, 2)
    assert _analysis(api_client, "live").status == "RUNNING"
    for gone in ("stale", "legacy"):
        row = _analysis(api_client, gone)
        assert row.status == "FAILED"
        assert row.error_code == "interrupted"


def test_heartbeat_touches_only_this_runners_jobs(api_client: TestClient) -> None:
    ctx = api_client.app.state.ctx  # type: ignore[attr-defined]
    old = utcnow() - timedelta(minutes=10)
    _add_analysis(api_client, "mine", heartbeat_at=old, claimed_by="runner-a")
    _add_analysis(api_client, "theirs", heartbeat_at=old, claimed_by="runner-b")
    runner = JobRunner(ctx.db, ctx.storage, ctx.settings, ctx.registry, worker_id="runner-a", threads=1)
    future: Future[None] = Future()
    runner._inflight.update({"mine": future, "theirs": future})

    assert runner.heartbeat() == 1
    mine, theirs = as_utc(_analysis(api_client, "mine").heartbeat_at), as_utc(_analysis(api_client, "theirs").heartbeat_at)
    assert mine is not None and mine > old
    assert theirs == old
    # A live job of runner-a survives another process starting up.
    mark_interrupted(ctx.db)
    assert _analysis(api_client, "mine").status == "RUNNING"
    assert _analysis(api_client, "theirs").status == "FAILED"


def _stopped_run(client: TestClient, analysis_id: str, *, user_cancelled: bool) -> Analysis:
    ctx = client.app.state.ctx  # type: ignore[attr-defined]
    _add_analysis(client, analysis_id, cancel_requested=user_cancelled, claimed_by="runner")
    cancel, shutdown = threading.Event(), threading.Event()
    cancel.set()
    shutdown.set()
    run_analysis_job(ctx.db, ctx.registry, analysis_id, cancel, shutdown=shutdown)
    return _analysis(client, analysis_id)


def test_shutdown_records_interrupted_not_cancelled(api_client: TestClient) -> None:
    stopped = _stopped_run(api_client, "restart", user_cancelled=False)
    assert stopped.status == "FAILED"
    assert stopped.error_code == "interrupted"
    assert "server stopped" in (stopped.error_message or "")

    cancelled = _stopped_run(api_client, "user", user_cancelled=True)
    assert cancelled.status == "CANCELLED"
    assert cancelled.error_code == "cancelled"


def test_missing_pack_is_not_recorded_as_used(api_client: TestClient) -> None:
    ctx = api_client.app.state.ctx  # type: ignore[attr-defined]
    _add_analysis(api_client, "packless", datapack="gone-pack", claimed_by="runner")
    run_analysis_job(ctx.db, ctx.registry, "packless", threading.Event())
    row = _analysis(api_client, "packless")
    assert row.status == "COMPLETED", row.error_message
    assert row.datapack is None
    assert any("not checked" in issue["message"] for issue in (row.result or {}).get("issues", []))


def _validate(client: TestClient, helpers: SimpleNamespace, data: bytes) -> dict[str, Any]:
    created = helpers.upload(client, "show.csv", data)
    assert created.status_code in (200, 201, 202), created.text
    info = helpers.wait_for_file(client, created.json()["id"])
    assert info["status"] == "COMPLETED", info
    return helpers.run_analysis(client, {"kind": "show", "dataset_id": info["id"], "params": {}})  # type: ignore[no-any-return]


def test_show_with_sea_level_altitudes_is_measured_from_the_pad(
    api_client: TestClient, helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    relative = sample_bytes("show_50_drones_csv")
    first, rest = relative.split(b"\n", 1)
    assert first.startswith(b"# origin=") and first.rstrip().endswith(b",0")
    absolute = first.rstrip()[:-2] + b",900\n" + rest  # same show, take-off pad 900 m above sea level

    base = _validate(api_client, helpers, relative)
    raised = _validate(api_client, helpers, absolute)
    assert base["status"] == raised["status"] == "COMPLETED", (base["error"], raised["error"])
    assert raised["params"]["altitude_reference"] == "absolute"
    assert raised["result"]["frames"]["origin"][2] == 900
    for key in ("verdict", "conflicts", "violations", "min_separation_m"):
        assert json.dumps(raised["result"][key], sort_keys=True) == json.dumps(base["result"][key], sort_keys=True), key


def test_nan_coordinates_do_not_break_the_feature_endpoints(api_client: TestClient, helpers: SimpleNamespace) -> None:
    doc = (
        b'{"type":"FeatureCollection","features":['
        b'{"type":"Feature","properties":{"name":"bad"},"geometry":{"type":"LineString","coordinates":[[77.5,12.9],[NaN,12.95],[77.6,13.0]]}},'
        b'{"type":"Feature","properties":{"name":"good"},"geometry":{"type":"Point","coordinates":[77.5,12.9]}}]}'
    )
    created = helpers.upload(api_client, "nan.geojson", doc)
    info = helpers.wait_for_file(api_client, created.json()["id"])
    assert info["status"] == "COMPLETED", info
    assert any(i["code"] == "invalid_coordinates" for i in info["issues"])

    features = api_client.get(f"/api/files/{info['id']}/features/")
    assert features.status_code == 200
    by_name = {f["properties"].get("name"): f for f in features.json()["features"]}
    assert by_name["bad"]["geometry"] is None
    measurements = api_client.get(f"/api/files/{info['id']}/measurements/").json()["items"]
    assert measurements[0]["measurement"]["status"] == "unsupported"
    assert api_client.get(f"/api/files/{info['id']}/export/", params={"format": "geojson"}).status_code == 200


def test_kml_export_drops_xml_illegal_characters(api_client: TestClient, helpers: SimpleNamespace) -> None:
    doc = (
        b'{"type":"FeatureCollection","features":[{"type":"Feature",'
        b'"properties":{"name":"Plot\\u0001A","note":"legacy\\u001fDBF\\ttext"},'
        b'"geometry":{"type":"Point","coordinates":[77.5,12.9]}}]}'
    )
    created = helpers.upload(api_client, "legacy.geojson", doc)
    info = helpers.wait_for_file(api_client, created.json()["id"])
    assert info["status"] == "COMPLETED", info
    kml = api_client.get(f"/api/files/{info['id']}/export/", params={"format": "kml"})
    assert kml.status_code == 200
    root = ET.fromstring(kml.content)  # raises if the document is not well-formed
    text = ET.tostring(root, encoding="unicode")
    assert "PlotA" in text and "legacyDBF\ttext" in text


def test_measurement_status_filter_counts_in_sql(api_client: TestClient, helpers: SimpleNamespace) -> None:
    sample = helpers.load_sample(api_client, "mixed_features")
    url = f"/api/files/{sample['id']}/measurements/"
    everything = api_client.get(url, params={"limit": 100}).json()
    for status in ("measured", "not_applicable", "unsupported"):
        expected = [i for i in everything["items"] if i["measurement"]["status"] == status]
        page = api_client.get(url, params={"status": status, "limit": 1}).json()
        assert page["total"] == len(expected)
        assert [i["feature_index"] for i in page["items"]] == [i["feature_index"] for i in expected[:1]]


def test_interrupted_dataset_rows_use_heartbeats_too(api_client: TestClient) -> None:
    db = api_client.app.state.db  # type: ignore[attr-defined]
    with db.session() as session:
        session.add(Dataset(id="ds-live", filename="a.kml", status="PROCESSING", heartbeat_at=utcnow()))
        session.add(Dataset(id="ds-dead", filename="b.kml", status="PROCESSING", heartbeat_at=utcnow() - timedelta(hours=1)))
        session.commit()
    assert mark_interrupted(db) == (1, 0)
    with db.session() as session:
        assert session.get(Dataset, "ds-live").status == "PROCESSING"  # type: ignore[union-attr]
        assert session.get(Dataset, "ds-dead").status == "FAILED"  # type: ignore[union-attr]


def test_pending_analyses_take_turns_by_dataset() -> None:
    # a run of four on dataset A, queued before one each on B and C and one with no dataset
    pending = [("a1", "A"), ("a2", "A"), ("b1", "B"), ("a3", "A"), ("x1", None), ("c1", "C"), ("a4", "A")]
    assert fair_order(pending) == ["a1", "b1", "x1", "c1", "a2", "a3", "a4"]
    assert fair_order([]) == []


def test_drain_beats_by_the_clock_while_short_jobs_keep_finishing(api_client: TestClient, monkeypatch: Any) -> None:
    from api import jobs

    ctx = api_client.app.state.ctx  # type: ignore[attr-defined]
    runner = JobRunner(ctx.db, ctx.storage, ctx.settings, ctx.registry, worker_id="drainer", threads=2)
    runner._executor = SimpleNamespace()  # type: ignore[assignment]  # drain only needs one to exist
    long_job: Future[None] = Future()
    runner._inflight["long"] = long_job
    clock = [0.0]
    beats: list[float] = []
    waits = [0]

    def a_short_job_finishes(futures: Any, timeout: float, return_when: Any) -> tuple[set[Any], set[Any]]:
        clock[0] += 4.0
        waits[0] += 1
        if waits[0] == 10:
            runner._inflight.pop("long")
        return {Future()}, set()

    monkeypatch.setattr(jobs, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(jobs, "wait", a_short_job_finishes)
    monkeypatch.setattr(runner, "dispatch_pending", lambda: 0)
    monkeypatch.setattr(runner, "heartbeat", lambda: beats.append(clock[0]) or 0)
    runner.drain(timeout=300)
    assert beats == [12.0, 24.0, 36.0]  # every 10 s or so, though every wait returned a finished job


def test_an_inline_upload_is_not_taken_for_an_interrupted_job(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    with api_client.app.state.db.session() as session:  # type: ignore[attr-defined]
        row = session.get(Dataset, dataset["id"])
        assert row is not None and row.heartbeat_at is not None
