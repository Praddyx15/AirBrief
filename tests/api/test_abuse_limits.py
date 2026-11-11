"""Abuse limits: storage quota, job time limit, request origin, development-mode exposure,
always-queued uploads in production, worker memory limit, security log, SQLite secure_delete."""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import logging
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from api import worker
from api.clients import client_key, from_public_network, is_public
from api.config import MB, PRODUCTION_JOB_TIMEOUT_S, PRODUCTION_STORAGE_QUOTA_MB, Settings
from api.db import Database
from api.errors import ApiError
from api.storage import Storage, TTLSweeper

PRODUCTION = {"app_env": "production", "cors_origins": "https://airbrief.example"}
PARSE = {"text": "12.97, 77.59"}


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _security_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "airbrief.security"]


def test_defaults_are_open_in_development_and_limited_in_production() -> None:
    dev = _settings()
    assert dev.storage_quota_bytes == 0
    assert dev.min_free_disk_bytes == 500 * MB
    assert dev.job_timeout == 0
    assert dev.inline_process_max_bytes == 5 * MB
    assert dev.worker_memory_limit_mb is None

    prod = _settings(**PRODUCTION)
    assert prod.storage_quota_bytes == int(PRODUCTION_STORAGE_QUOTA_MB * MB) == 5000 * MB
    assert prod.min_free_disk_bytes == 500 * MB
    assert prod.job_timeout == PRODUCTION_JOB_TIMEOUT_S == 900
    assert prod.inline_process_max_bytes == 0


def test_explicit_values_win_and_empty_means_default() -> None:
    prod = _settings(
        storage_quota_mb=0, min_free_disk_mb=0, job_timeout_s=0, inline_process_max_mb=5, **PRODUCTION
    )
    assert prod.storage_quota_bytes == 0
    assert prod.min_free_disk_bytes == 0
    assert prod.job_timeout == 0
    assert prod.inline_process_max_bytes == 5 * MB

    empty = _settings(
        storage_quota_mb="", job_timeout_s="", inline_process_max_mb="", worker_memory_limit_mb="", **PRODUCTION
    )
    assert empty.storage_quota_bytes == 5000 * MB
    assert empty.job_timeout == 900
    assert empty.inline_process_max_bytes == 0
    assert empty.worker_memory_limit_mb is None
    assert _settings(storage_quota_mb=12).storage_quota_bytes == 12 * MB


def test_upload_is_refused_when_the_quota_is_used_up(
    make_client: Callable[..., TestClient], helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    data = sample_bytes("square_1km_equator")
    client = make_client(storage_quota_mb=2 * len(data) / MB)

    first = helpers.upload(client, "square.kml", data)
    assert first.status_code == 201, first.text
    full = helpers.upload(client, "square.kml", data)
    assert full.status_code == 507
    error = full.json()["error"]
    assert error["code"] == "storage_full"
    assert error["details"] == {"reason": "quota"}
    assert "Try again later" in error["message"]
    assert client.get("/api/files/").json()["total"] == 1

    # Deleting frees the space again.
    assert client.delete(f"/api/files/{first.json()['id']}/").status_code == 204
    assert helpers.upload(client, "square.kml", data).status_code == 201


def test_samples_go_through_the_same_check(make_client: Callable[..., TestClient]) -> None:
    client = make_client(storage_quota_mb=100 / MB)
    response = client.post("/api/samples/square_1km_equator/")
    assert response.status_code == 507
    assert response.json()["error"]["code"] == "storage_full"


def test_upload_is_refused_when_the_disk_is_nearly_full(
    make_client: Callable[..., TestClient], helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    client = make_client(min_free_disk_mb=10**12)  # more than any disk has free
    response = helpers.upload(client, "square.kml", sample_bytes("square_1km_equator"))
    assert response.status_code == 507
    assert response.json()["error"]["details"] == {"reason": "disk"}
    sample = client.post("/api/samples/square_1km_equator/")
    assert sample.status_code == 507
    assert sample.json()["error"]["details"] == {"reason": "disk"}


def test_received_bytes_are_checked_while_streaming(tmp_path: Path) -> None:
    storage = Storage(tmp_path, quota_bytes=1500)
    upload = UploadFile(io.BytesIO(b"x" * 4000), filename="big.kml")
    with pytest.raises(ApiError) as info:
        asyncio.run(storage.save_upload("a1", upload, max_bytes=10_000))
    assert info.value.code == "storage_full"
    assert info.value.http_status == 507
    assert not (tmp_path / "a1").exists()
    assert storage.used_bytes() == 0


def test_usage_follows_writes_and_deletes(tmp_path: Path) -> None:
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "source.kml").write_bytes(b"y" * 300)
    storage = Storage(tmp_path, quota_bytes=10_000)
    stored = asyncio.run(storage.save_upload("a1", UploadFile(io.BytesIO(b"x" * 1000), filename="a.kml"), 10_000))
    assert stored.size_bytes == 1000
    assert storage.used_bytes() == 1300
    storage.write_options("a1", {"crs_override": None})
    options_size = (tmp_path / "a1" / "options.json").stat().st_size
    assert storage.used_bytes() == 1300 + options_size
    (tmp_path / "a1" / "work").mkdir()
    (tmp_path / "a1" / "work" / "scratch.shp").write_bytes(b"z" * 5000)  # extraction scratch
    storage.refresh_usage()
    assert storage.used_bytes() == 1300 + options_size
    storage.delete("a1")
    assert storage.used_bytes() == 300


def test_quota_is_not_tracked_without_one(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    storage.save_bytes("a1", "a.kml", b"x" * 100)
    storage.refresh_usage()
    assert storage.used_bytes() is None


def test_ttl_sweeper_re_measures_the_usage(make_client: Callable[..., TestClient]) -> None:
    client = make_client(storage_quota_mb=1)  # no TTL: the app runs no sweeper of its own
    ctx = client.app.state.ctx  # type: ignore[attr-defined]
    ctx.storage.save_bytes("drift", "a.kml", b"x" * 100)
    before = ctx.storage.used_bytes()
    (Path(ctx.settings.upload_dir) / "drift" / "extra.bin").write_bytes(b"e" * 4000)  # written behind its back
    assert ctx.storage.used_bytes() == before
    TTLSweeper(ctx.db, ctx.storage, ctx.settings.model_copy(update={"upload_ttl_hours": 1})).sweep()
    assert ctx.storage.used_bytes() == before + 4000


def test_slow_analysis_is_stopped_with_job_timeout(
    make_client: Callable[..., TestClient], helpers: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from airbrief_core.analyses import REGISTRY

    def slow(params: Any, ctx: Any) -> Any:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ctx.check_cancelled()
            time.sleep(0.02)
        raise AssertionError("the job timeout never fired")

    monkeypatch.setitem(REGISTRY, "survey", dataclasses.replace(REGISTRY["survey"], run=slow))
    client = make_client(job_timeout_s=0.3)
    dataset = helpers.load_sample(client, "bengaluru_survey")
    created = client.post("/api/analyses/", json={"kind": "survey", "dataset_id": dataset["id"], "params": {}})
    assert created.status_code == 202
    info = helpers.wait_for_analysis(client, created.json()["id"], timeout=30)
    assert info["status"] == "FAILED"
    assert info["error"]["code"] == "job_timeout"
    assert "0.3 s" in info["error"]["message"]
    assert info["duration_ms"] < 20_000


def test_analyses_run_without_a_time_limit_in_development(
    make_client: Callable[..., TestClient], helpers: SimpleNamespace
) -> None:
    client = make_client()
    assert client.app.state.settings.job_timeout == 0  # type: ignore[attr-defined]
    dataset = helpers.load_sample(client, "bengaluru_survey")
    info = helpers.run_analysis(client, {"kind": "survey", "dataset_id": dataset["id"], "params": {}})
    assert info["status"] == "COMPLETED"


def test_foreign_origin_cannot_change_state(api_client: TestClient) -> None:
    foreign = {"Origin": "https://evil.example"}
    response = api_client.post("/api/geometry/parse/", json=PARSE, headers=foreign)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "cross_site_request"
    assert api_client.post("/api/samples/square_1km_equator/", headers=foreign).status_code == 403
    assert api_client.delete("/api/files/nope/", headers=foreign).status_code == 403
    assert api_client.post("/api/geometry/parse/", json=PARSE, headers={"Origin": "null"}).status_code == 403
    # Reads are not affected.
    assert api_client.get("/api/samples/", headers=foreign).status_code == 200


def test_allowed_origin_and_no_origin_pass(api_client: TestClient) -> None:
    assert api_client.post("/api/geometry/parse/", json=PARSE, headers={"Origin": "http://localhost:3000"}).status_code == 200
    assert api_client.post("/api/geometry/parse/", json=PARSE, headers={"Origin": "http://localhost:3000/"}).status_code == 200
    assert api_client.post("/api/geometry/parse/", json=PARSE).status_code == 200


def test_development_default_origins_cover_the_web_app() -> None:
    origins = _settings().cors_origin_list
    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins


def test_public_client_is_refused_in_development(make_client: Callable[..., TestClient]) -> None:
    client = make_client(trusted_proxy_hops=1)
    response = client.get("/health", headers={"X-Forwarded-For": "8.8.8.8"})
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "development_mode_local_only"
    assert "APP_ENV=production" in error["message"]
    assert client.get("/api/meta/", headers={"X-Forwarded-For": "2a00:1450:4001:80b::1"}).status_code == 403
    for local in ("127.0.0.1", "192.168.1.20", "10.1.2.3", "172.17.0.2", "::1", "fd00::5"):
        assert client.get("/health", headers={"X-Forwarded-For": local}).status_code == 200, local


def test_public_peer_is_refused_in_development(make_client: Callable[..., TestClient]) -> None:
    app = make_client().app
    assert TestClient(app, client=("8.8.8.8", 50000)).get("/health").status_code == 403
    assert TestClient(app, client=("192.168.0.7", 50000)).get("/health").status_code == 200


def test_forwarded_public_address_counts_without_trusted_hops(make_client: Callable[..., TestClient]) -> None:
    # A hosted proxy has a private address; the address it forwarded for is the real client.
    client = make_client(trusted_proxy_hops=0)
    assert client.get("/health", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 403
    assert client.get("/health", headers={"X-Forwarded-For": "10.0.0.4"}).status_code == 200


def test_public_client_passes_in_production(make_client: Callable[..., TestClient]) -> None:
    client = make_client(trusted_proxy_hops=1, **PRODUCTION)
    assert client.get("/health", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 200
    assert TestClient(client.app, client=("8.8.8.8", 50000)).get("/health").status_code == 200


def test_public_address_classification() -> None:
    assert is_public("8.8.8.8")
    assert is_public("::ffff:8.8.8.8")
    assert is_public("2a00:1450:4001:80b::200e")
    for address in ("127.0.0.1", "::1", "10.0.0.1", "192.168.1.1", "172.17.0.2", "100.64.0.1", "fe80::1", "::ffff:10.0.0.1", "testclient", "unknown"):
        assert not is_public(address), address
    scope = {"type": "http", "client": ("10.0.0.2", 1), "headers": [(b"x-forwarded-for", b"6.6.6.6, 10.0.0.9")]}
    assert not from_public_network(scope)  # only the nearest proxy's entry counts
    assert not from_public_network(scope, 1)


def test_development_start_up_warning(make_client: Callable[..., TestClient], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="airbrief.api")
    make_client()
    warnings = [r.getMessage() for r in caplog.records if r.name == "airbrief.api" and r.levelno == logging.WARNING]
    assert any("APP_ENV=development" in m for m in warnings)
    line = next(m for m in warnings if "APP_ENV=development" in m)
    assert "owner tokens" in line
    assert "scoped listings" in line
    assert "storage quota" in line
    assert "job time limit" in line


def test_no_start_up_warning_in_production(make_client: Callable[..., TestClient], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="airbrief.api")
    make_client(**PRODUCTION)
    assert not any("APP_ENV=development" in r.getMessage() for r in caplog.records)


def test_meta_reports_the_environment(make_client: Callable[..., TestClient]) -> None:
    assert make_client().get("/api/meta/").json()["app_env"] == "development"


def test_meta_reports_production(make_client: Callable[..., TestClient]) -> None:
    meta = make_client(inline_process_max_mb=None, **PRODUCTION).get("/api/meta/").json()
    assert meta["app_env"] == "production"
    assert meta["limits"]["inline_process_max_mb"] == 0


def test_production_queues_every_upload(
    make_client: Callable[..., TestClient], helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    client = make_client(inline_process_max_mb=None, **PRODUCTION)
    response = helpers.upload(client, "square.kml", sample_bytes("square_1km_equator"))
    assert response.status_code == 202
    assert helpers.wait_for_file(client, response.json()["id"])["status"] == "COMPLETED"


def test_development_still_processes_small_uploads_inline(
    make_client: Callable[..., TestClient], helpers: SimpleNamespace, sample_bytes: Callable[[str], bytes]
) -> None:
    client = make_client(inline_process_max_mb=None)
    response = helpers.upload(client, "square.kml", sample_bytes("square_1km_equator"))
    assert response.status_code == 201
    assert response.json()["status"] == "COMPLETED"


class _FakeResource:
    RLIMIT_AS = 9
    RLIM_INFINITY = -1

    def __init__(self, hard: int) -> None:
        self.hard = hard
        self.calls: list[tuple[int, tuple[int, int]]] = []

    def getrlimit(self, which: int) -> tuple[int, int]:
        assert which == self.RLIMIT_AS
        return self.RLIM_INFINITY, self.hard

    def setrlimit(self, which: int, limits: tuple[int, int]) -> None:
        self.calls.append((which, limits))


def test_worker_memory_limit_sets_rlimit_as(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeResource(hard=_FakeResource.RLIM_INFINITY)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "resource", fake)
    worker.apply_memory_limit(256)
    assert fake.calls == [(9, (256 * MB, -1))]


def test_worker_memory_limit_stays_under_the_hard_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeResource(hard=100 * MB)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "resource", fake)
    worker.apply_memory_limit(256)
    assert fake.calls == [(9, (100 * MB, 100 * MB))]


def test_worker_memory_limit_off_or_unsupported(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    fake = _FakeResource(hard=-1)
    monkeypatch.setitem(sys.modules, "resource", fake)
    monkeypatch.setattr(sys, "platform", "linux")
    worker.apply_memory_limit(None)
    worker.apply_memory_limit(0)
    assert fake.calls == []

    monkeypatch.setattr(sys, "platform", "win32")
    caplog.set_level(logging.WARNING, logger="airbrief.worker")
    worker.apply_memory_limit(256)
    assert fake.calls == []
    assert any("not supported on Windows" in r.getMessage() for r in caplog.records)


def test_rate_limit_refusal_is_logged_without_address_or_query(
    make_client: Callable[..., TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="airbrief.security")
    client = make_client(rate_limit_per_hour=1)
    assert client.post("/api/geometry/parse/?token=s3cret", json=PARSE).status_code == 200
    assert client.post("/api/geometry/parse/?token=s3cret", json=PARSE).status_code == 429
    lines = _security_lines(caplog)
    assert lines == [f"rate_limited 429 client={client_key('testclient')} path=/api/geometry/parse/"]
    assert re.fullmatch(r"[0-9a-f]{12}", client_key("testclient"))
    assert "s3cret" not in lines[0]
    assert "testclient" not in lines[0]


def test_owner_token_failures_are_logged(make_client: Callable[..., TestClient], caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="airbrief.security")
    client = make_client(require_owner_token=True)
    file_id = client.post("/api/samples/square_1km_equator/").json()["id"]
    assert client.delete(f"/api/files/{file_id}/").status_code == 403
    assert client.delete(f"/api/files/{file_id}/", headers={"X-Owner-Token": "guess-123"}).status_code == 403
    lines = _security_lines(caplog)
    assert [line.split(" client=")[0] for line in lines] == ["owner_token_required 403", "owner_token_invalid 403"]
    assert all(line.endswith(f"path=/api/files/{file_id}/") for line in lines)
    assert not any("guess-123" in line for line in lines)


def test_origin_and_local_only_refusals_are_logged(
    make_client: Callable[..., TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="airbrief.security")
    client = make_client(trusted_proxy_hops=1)
    client.post("/api/geometry/parse/", json=PARSE, headers={"Origin": "https://evil.example"})
    client.get("/health", headers={"X-Forwarded-For": "8.8.8.8"})
    lines = _security_lines(caplog)
    assert lines[0].startswith("cross_site_request 403 client=")
    assert lines[1] == f"development_mode_local_only 403 client={client_key('8.8.8.8')} path=/health"


def test_size_and_storage_refusals_are_logged(
    make_client: Callable[..., TestClient],
    helpers: SimpleNamespace,
    sample_bytes: Callable[[str], bytes],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="airbrief.security")
    client = make_client(max_json_body_mb=0.01, storage_quota_mb=10 / MB)
    big = {"kind": "flight_audit", "params": {"report": {"padding": "x" * 50_000}}}
    assert client.post("/api/analyses/", json=big).status_code == 413

    def chunks() -> Any:
        payload = b'{"padding": "' + b"x" * 50_000 + b'"}'
        for start in range(0, len(payload), 4096):
            yield payload[start : start + 4096]

    assert client.post("/api/analyses/", content=chunks(), headers={"Content-Type": "application/json"}).status_code == 413
    assert helpers.upload(client, "square.kml", sample_bytes("square_1km_equator")).status_code == 507
    assert client.post("/api/samples/square_1km_equator/").status_code == 507
    codes = [line.split(" client=")[0] for line in _security_lines(caplog)]
    assert codes == ["request_too_large 413", "request_too_large 413", "storage_full 507", "storage_full 507"]


def test_url_not_allowed_is_logged(api_client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="airbrief.security")
    response = api_client.post("/api/flightmd/fetch/", json={"url": "http://127.0.0.1/report/1"})
    assert response.json()["error"]["code"] == "url_not_allowed"
    lines = _security_lines(caplog)
    assert len(lines) == 1
    assert lines[0].startswith(f"url_not_allowed {response.status_code} client=")
    assert "127.0.0.1" not in lines[0]


def test_logged_path_stays_on_one_line(api_client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="airbrief.security")
    api_client.post("/api/x%0Afake%20line/", json=PARSE, headers={"Origin": "https://evil.example"})
    (line,) = _security_lines(caplog)
    assert "\n" not in line
    assert line.endswith("path=/api/x\\nfake line/")


def test_successful_requests_are_not_logged(api_client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="airbrief.security")
    assert api_client.post("/api/geometry/parse/", json=PARSE).status_code == 200
    assert api_client.get("/api/files/nope/").status_code == 404
    assert _security_lines(caplog) == []


def test_sqlite_connections_use_secure_delete(tmp_path: Path) -> None:
    from sqlalchemy import text

    db = Database(f"sqlite:///{(tmp_path / 'secure.db').as_posix()}")
    try:
        with db.engine.connect() as conn:
            assert conn.execute(text("PRAGMA secure_delete")).scalar() == 1
    finally:
        db.dispose()


def test_upload_budget_counts_bytes_per_day_in_hourly_buckets() -> None:
    from api.ratelimit import ByteBudget

    now = [7200.0]
    budget = ByteBudget(1000, clock=lambda: now[0])
    assert budget.take("a", 600) == (True, 0)
    allowed, retry = budget.take("a", 500)
    assert not allowed and retry == 24 * 3600
    assert budget.take("b", 500) == (True, 0)  # another client has its own
    now[0] += 23 * 3600
    assert not budget.take("a", 500)[0]
    now[0] += 3600  # the first hour's 600 bytes leave the window
    assert budget.take("a", 500) == (True, 0)
    assert ByteBudget(0).take("a", 10**12) == (True, 0)


def test_uploads_past_the_daily_budget_are_refused(make_client: Callable[..., TestClient]) -> None:
    client = make_client(upload_mb_per_day=0.01)  # 10,485 bytes
    half = json.dumps({"type": "FeatureCollection", "features": []}).encode() + b" " * 6_000
    sent = client.post("/api/files/", files={"file": ("a.geojson", half, "application/geo+json")})
    assert sent.status_code in (201, 202), sent.text
    refused = client.post("/api/files/", files={"file": ("b.geojson", half, "application/geo+json")})
    assert refused.status_code == 429
    body = refused.json()["error"]
    assert body["code"] == "upload_budget_exceeded" and body["details"]["limit_bytes_per_day"] == 10_485
    assert int(refused.headers["retry-after"]) > 0


def test_a_refused_upload_does_not_count_against_the_day(make_client: Callable[..., TestClient]) -> None:
    client = make_client(upload_mb_per_day=0.01)  # 10,485 bytes
    junk = b"not a geodata file " * 300  # about 5.7 kB, refused after it was counted
    for _ in range(3):
        refused = client.post("/api/files/", files={"file": ("notes.txt", junk, "text/plain")})
        assert 400 <= refused.status_code < 500 and refused.status_code != 429, refused.text
    small = json.dumps({"type": "FeatureCollection", "features": []}).encode()
    assert client.post("/api/files/", files={"file": ("a.geojson", small, "application/geo+json")}).status_code != 429
    too_big = client.post("/api/files/", files={"file": ("b.geojson", small + b" " * 20_000, "application/geo+json")})
    assert too_big.status_code == 413 and too_big.json()["error"]["details"]["limit_bytes_per_day"] == 10_485


def test_upload_budget_defaults_off_in_development_and_on_in_production() -> None:
    from api.config import PRODUCTION_UPLOAD_MB_PER_DAY

    assert Settings(_env_file=None).upload_bytes_per_day == 0  # type: ignore[call-arg]
    assert Settings(_env_file=None, **PRODUCTION).upload_bytes_per_day == int(PRODUCTION_UPLOAD_MB_PER_DAY * 1024 * 1024)  # type: ignore[arg-type]
    assert Settings(_env_file=None, upload_mb_per_day=0, **PRODUCTION).upload_bytes_per_day == 0  # type: ignore[arg-type]
