"""Fixtures for the API tests.

``make_client(**overrides)`` builds an isolated app: temp data dir, temp SQLite (or
``TEST_DATABASE_URL``), the synthetic ``demo`` pack, in-process jobs, every upload processed
inline, no rate limit. Keyword arguments override any Settings field.

Test modules here need basenames distinct from ``tests/core`` ones (no ``__init__.py``).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from api.config import Settings
from api.main import create_app
from api.models import Analysis, Dataset, Feature

ClientFactory = Callable[..., TestClient]


def _database_url(tmp_path: Path) -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if url:
        return url
    return f"sqlite:///{(tmp_path / 'airbrief-test.db').as_posix()}"


@pytest.fixture
def make_client(tmp_path: Path, synthetic_pack_dir: Path) -> Iterator[ClientFactory]:
    """Factory of isolated, started TestClients (closed at the end of the test)."""
    clients: list[TestClient] = []

    def _make(**overrides: Any) -> TestClient:
        data_dir = tmp_path / "data"
        values: dict[str, Any] = {
            "app_env": "development",
            "log_level": "WARNING",
            "database_url": _database_url(tmp_path),
            "auto_migrate": True,
            "data_dir": data_dir,
            "upload_dir": data_dir / "uploads",
            "datapack_dir": synthetic_pack_dir,
            "default_datapack": "demo",
            "max_upload_mb": 50,
            "inline_process_max_mb": 100,
            "job_mode": "inprocess",
            "worker_threads": 2,
            "worker_poll_interval_s": 0.2,
            "cors_origins": "http://localhost:3000",
            "rate_limit_per_hour": 0,
            "upload_ttl_hours": 0,
            "flightmd_api_url": None,
            "flightmd_fetch_timeout_s": 5,
        }
        values.update(overrides)
        settings = Settings(_env_file=None, **values)  # type: ignore[call-arg]
        app = create_app(settings)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        if len(clients) == 1:
            _clear_tables(app)
        return client

    yield _make
    for client in reversed(clients):
        client.__exit__(None, None, None)


def _clear_tables(app: Any) -> None:
    """Start every test from empty tables (matters for a shared TEST_DATABASE_URL)."""
    db = app.state.db
    with db.session() as session:
        session.execute(delete(Feature))
        session.execute(delete(Analysis))
        session.execute(delete(Dataset))
        session.commit()


@pytest.fixture
def api_client(make_client: ClientFactory) -> TestClient:
    return make_client()


def upload(
    client: TestClient,
    filename: str,
    data: bytes,
    *,
    crs_override: str | None = None,
    origin: str | None = None,
) -> Any:
    form: dict[str, str] = {}
    if crs_override is not None:
        form["crs_override"] = crs_override
    if origin is not None:
        form["origin"] = origin
    return client.post(
        "/api/files/",
        files={"file": (filename, data, "application/octet-stream")},
        data=form,
    )


def load_sample(client: TestClient, name: str) -> dict[str, Any]:
    response = client.post(f"/api/samples/{name}/")
    assert response.status_code == 201, response.text
    info = response.json()
    assert info["status"] == "COMPLETED", info
    return info


def wait_for_analysis(client: TestClient, analysis_id: str, timeout: float = 120.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/api/analyses/{analysis_id}/")
        assert response.status_code == 200, response.text
        info = response.json()
        if info["status"] not in ("PENDING", "RUNNING"):
            return info
        if time.monotonic() > deadline:
            raise AssertionError(f"analysis {analysis_id} still {info['status']} after {timeout} s")
        time.sleep(0.05)


def wait_for_file(client: TestClient, file_id: str, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(f"/api/files/{file_id}/")
        assert response.status_code == 200, response.text
        info = response.json()
        if info["status"] not in ("PENDING", "PROCESSING"):
            return info
        if time.monotonic() > deadline:
            raise AssertionError(f"file {file_id} still {info['status']} after {timeout} s")
        time.sleep(0.05)


def run_analysis(client: TestClient, body: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    """Queue an analysis (expects 202) and wait for it to finish."""
    response = client.post("/api/analyses/", json=body)
    assert response.status_code == 202, response.text
    return wait_for_analysis(client, response.json()["id"], timeout=timeout)


@pytest.fixture
def helpers() -> SimpleNamespace:
    """The helpers above, as a fixture: test modules never import conftest."""
    return SimpleNamespace(
        upload=upload,
        load_sample=load_sample,
        wait_for_analysis=wait_for_analysis,
        wait_for_file=wait_for_file,
        run_analysis=run_analysis,
    )
