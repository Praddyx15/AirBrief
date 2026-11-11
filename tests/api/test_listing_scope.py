"""Listing scope: public instances refuse unscoped listings; ``?ids=`` lists only what you name."""

from __future__ import annotations

from typing import Any


def test_global_listing_allowed_by_default_in_development(api_client: Any, helpers: Any) -> None:
    dataset = helpers.load_sample(api_client, "square_1km_equator")
    files = api_client.get("/api/files/")
    assert files.status_code == 200
    assert any(item["id"] == dataset["id"] for item in files.json()["items"])
    assert api_client.get("/api/analyses/").status_code == 200
    assert api_client.get("/api/meta/").json()["global_listing"] is True


def test_listing_disabled_refuses_unscoped_requests(make_client: Any, helpers: Any) -> None:
    client = make_client(allow_global_listing=False)
    mine = helpers.load_sample(client, "square_1km_equator")
    other = helpers.load_sample(client, "square_1km_60N")

    for path in ("/api/files/", "/api/analyses/"):
        response = client.get(path)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "listing_disabled"

    scoped = client.get("/api/files/", params={"ids": mine["id"]})
    assert scoped.status_code == 200
    assert [item["id"] for item in scoped.json()["items"]] == [mine["id"]]
    assert other["id"] not in {item["id"] for item in scoped.json()["items"]}

    by_dataset = client.get("/api/analyses/", params={"dataset_id": mine["id"]})
    assert by_dataset.status_code == 200
    assert client.get("/api/analyses/", params={"ids": "does-not-exist"}).json()["items"] == []
    assert client.get("/api/meta/").json()["global_listing"] is False


def test_too_many_ids_is_rejected(api_client: Any) -> None:
    ids = ",".join(f"id-{i}" for i in range(101))
    response = api_client.get("/api/files/", params={"ids": ids})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_params"
