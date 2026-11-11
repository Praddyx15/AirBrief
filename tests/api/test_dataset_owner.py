"""Evidence on a dataset: only analyses run with its owner token are the owner's (VULN-01, WEAK-12)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from api.db import ALEMBIC_DIR, run_migrations

OWNER = "X-Owner-Token"


def _run(client: Any, helpers: Any, body: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    headers = {OWNER: token} if token is not None else {}
    response = client.post("/api/analyses/", json=body, headers=headers)
    assert response.status_code == 202, response.text
    created = response.json()
    helpers.wait_for_analysis(client, created["id"])
    return created


def _airspace(dataset_id: str, **params: Any) -> dict[str, Any]:
    return {"kind": "airspace", "dataset_id": dataset_id, "params": {"altitude_agl_m": 120, **params}}


def _listed(client: Any, **query: str) -> list[str]:
    response = client.get("/api/analyses/", params=query)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == len(page["items"])
    return sorted(item["id"] for item in page["items"])


def test_only_the_datasets_token_marks_the_owners_analyses(api_client: Any, helpers: Any) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    token = dataset["owner_token"]

    owner = _run(api_client, helpers, _airspace(dataset["id"]), token)
    visitor = _run(api_client, helpers, _airspace(dataset["id"], include_datapack_zones=False))
    wrong = _run(api_client, helpers, _airspace(dataset["id"]), "not-the-token")
    # An analysis' own token is not the dataset's.
    other = _run(api_client, helpers, _airspace(dataset["id"]), owner["owner_token"])

    assert owner["by_dataset_owner"] is True
    assert visitor["by_dataset_owner"] is False
    assert wrong["by_dataset_owner"] is False
    assert other["by_dataset_owner"] is False
    stored = api_client.get(f"/api/analyses/{owner['id']}/").json()
    assert stored["by_dataset_owner"] is True
    assert api_client.get(f"/api/analyses/{visitor['id']}/").json()["by_dataset_owner"] is False


def test_an_analysis_without_a_dataset_is_never_the_owners(api_client: Any, helpers: Any) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    geometry = _run(api_client, helpers, _airspace(dataset["id"]), dataset["owner_token"])["params"]["geometry"]

    loose = _run(
        api_client,
        helpers,
        {"kind": "airspace", "params": {"geometry": geometry, "altitude_agl_m": 120}},
        dataset["owner_token"],
    )
    assert loose["dataset_id"] is None
    assert loose["by_dataset_owner"] is False


def test_production_lists_only_the_owners_analyses_of_a_dataset(make_client: Any, helpers: Any) -> None:
    client = make_client(app_env="production")
    dataset = helpers.load_sample(client, "bengaluru_survey")
    owner = _run(client, helpers, _airspace(dataset["id"]), dataset["owner_token"])
    _run(client, helpers, _airspace(dataset["id"], include_datapack_zones=False))

    assert _listed(client, dataset_id=dataset["id"]) == [owner["id"]]


def test_dataset_listing_adds_the_callers_own_ids_on_that_dataset(make_client: Any, helpers: Any) -> None:
    client = make_client(require_owner_token=True)
    dataset = helpers.load_sample(client, "bengaluru_survey")
    elsewhere = helpers.load_sample(client, "bengaluru_survey")
    owner = _run(client, helpers, _airspace(dataset["id"]), dataset["owner_token"])
    mine = _run(client, helpers, _airspace(dataset["id"], altitude_agl_m=60))
    theirs = _run(client, helpers, _airspace(dataset["id"], include_datapack_zones=False))
    other_dataset = _run(client, helpers, _airspace(elsewhere["id"]), elsewhere["owner_token"])

    listed = _listed(client, dataset_id=dataset["id"], ids=f"{mine['id']},{other_dataset['id']}")
    assert listed == sorted([owner["id"], mine["id"]])
    assert theirs["id"] not in listed
    # ids alone still list exactly those ids, wherever they belong
    assert _listed(client, ids=f"{mine['id']},{other_dataset['id']}") == sorted([mine["id"], other_dataset["id"]])


def test_development_lists_every_analysis_on_a_dataset(api_client: Any, helpers: Any) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    owner = _run(api_client, helpers, _airspace(dataset["id"]), dataset["owner_token"])
    visitor = _run(api_client, helpers, _airspace(dataset["id"], include_datapack_zones=False))
    everything = sorted([owner["id"], visitor["id"]])

    assert _listed(api_client, dataset_id=dataset["id"]) == everything
    assert _listed(api_client, dataset_id=dataset["id"], ids=visitor["id"]) == everything
    flags = {a["id"]: a["by_dataset_owner"] for a in api_client.get("/api/analyses/", params={"dataset_id": dataset["id"]}).json()["items"]}
    assert flags == {owner["id"]: True, visitor["id"]: False}


def test_list_limit_is_capped_at_100(api_client: Any) -> None:
    assert api_client.get("/api/analyses/", params={"limit": 100}).status_code == 200
    response = api_client.get("/api/analyses/", params={"limit": 101})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_params"

    params = api_client.app.openapi()["paths"]["/api/analyses/"]["get"]["parameters"]
    limit = next(p for p in params if p["name"] == "limit")
    assert limit["schema"]["maximum"] == 100
    assert "full result" in limit["description"]


def _alembic(url: str, out: io.StringIO | None = None) -> Config:
    config = Config(output_buffer=out) if out is not None else Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    return config


def test_migration_marks_existing_analyses_as_the_owners(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    run_migrations(url, "0002_job_heartbeat")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO datasets (id, filename, created_at) VALUES ('d1', 'a.kml', '2026-10-01 00:00:00')"))
        conn.execute(
            text(
                "INSERT INTO analyses (id, kind, dataset_id, created_at) "
                "VALUES ('old', 'airspace', 'd1', '2026-10-01 00:00:00')"
            )
        )

    run_migrations(url, "0003_analysis_dataset_owner")
    with engine.begin() as conn:
        column = next(c for c in inspect(conn).get_columns("analyses") if c["name"] == "by_dataset_owner")
        assert column["nullable"] is False
        conn.execute(
            text("INSERT INTO analyses (id, kind, dataset_id, created_at) VALUES ('new', 'airspace', 'd1', '2026-10-19 00:00:00')")
        )
        flags = dict(conn.execute(text("SELECT id, by_dataset_owner FROM analyses")).all())
    assert flags == {"old": 1, "new": 0}

    command.downgrade(_alembic(url), "0002_job_heartbeat")
    with engine.connect() as conn:
        assert "by_dataset_owner" not in {c["name"] for c in inspect(conn).get_columns("analyses")}
        assert conn.execute(text("SELECT count(*) FROM analyses")).scalar() == 2
    engine.dispose()


def test_migration_sql_for_postgresql() -> None:
    out = io.StringIO()
    command.upgrade(_alembic("postgresql://u@localhost/airbrief", out), "0002_job_heartbeat:0003_analysis_dataset_owner", sql=True)
    sql = out.getvalue()
    assert "ALTER TABLE analyses ADD COLUMN by_dataset_owner BOOLEAN DEFAULT false NOT NULL" in sql
    assert "UPDATE analyses SET by_dataset_owner=true" in sql
