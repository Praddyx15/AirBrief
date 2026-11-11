"""A backup made as DEPLOYMENT.md §3.3 says (database file and ``data/``) restores to a working
instance: same datasets, features and analyses, nothing to re-run."""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.mark.skipif(bool(os.environ.get("TEST_DATABASE_URL")), reason="the SQLite file backup; pg_dump is PostgreSQL's")
def test_a_restored_backup_serves_the_same_data(
    make_client: Callable[..., TestClient], helpers: SimpleNamespace, tmp_path: Path
) -> None:
    client = make_client()
    dataset = helpers.load_sample(client, "bengaluru_survey")
    analysis = helpers.run_analysis(client, {"kind": "survey", "dataset_id": dataset["id"]})
    features = client.get(f"/api/files/{dataset['id']}/features/").json()

    backup = tmp_path / "restored"
    backup.mkdir()
    # sqlite's own backup copies a consistent file while the API runs
    with closing(sqlite3.connect(tmp_path / "airbrief-test.db")) as src, closing(sqlite3.connect(backup / "airbrief.db")) as dst:
        src.backup(dst)
    shutil.copytree(tmp_path / "data" / "uploads", backup / "uploads")

    restored = make_client(
        database_url=f"sqlite:///{(backup / 'airbrief.db').as_posix()}",
        data_dir=backup,
        upload_dir=backup / "uploads",
    )
    assert restored.get("/health").json()["db"] == "ok"
    assert restored.get(f"/api/files/{dataset['id']}/").json()["status"] == "COMPLETED"
    assert restored.get(f"/api/files/{dataset['id']}/features/").json() == features
    again = restored.get(f"/api/analyses/{analysis['id']}/").json()
    assert again["status"] == "COMPLETED" and again["result"] == analysis["result"]
    assert restored.get(f"/api/files/{dataset['id']}/export/", params={"format": "geojson"}).status_code == 200
