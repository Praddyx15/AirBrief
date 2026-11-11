"""Height reference and survey upgrades through the API: input resolution and stored results."""

from __future__ import annotations

import json
import math
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient


def test_corridor_survey_takes_the_line_of_the_dataset(api_client: TestClient, helpers: SimpleNamespace) -> None:
    # climb_line_z: a 1,000 m west-to-east line. 100 m wide at 100 m (45 m spacing): 3 lines.
    dataset = helpers.load_sample(api_client, "climb_line_z")
    info = helpers.run_analysis(api_client, {
        "kind": "survey",
        "dataset_id": dataset["id"],
        "params": {"coverage": "corridor", "corridor_width_m": 100, "transit_legs": True},
    })
    assert info["status"] == "COMPLETED", info["error"]
    assert info["params"]["geometry"]["type"] in ("LineString", "MultiLineString")
    result = info["result"]
    assert result["coverage"] == "corridor"
    assert result["line_count"] == 3
    assert result["centreline_km"] == pytest.approx(1.0, rel=5e-3)
    assert result["takeoff"]["source"] == "first_vertex"
    assert result["transit_km"] is not None
    assert result["gcp"]["basis"] == "corridor"
    assert result["dem"]["synthetic"] is True
    assert result["relief_m"] is not None


def test_area_survey_still_needs_a_polygon(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "climb_line_z")
    response = api_client.post("/api/analyses/", json={"kind": "survey", "dataset_id": dataset["id"], "params": {}})
    assert response.status_code == 422
    assert "polygon" in response.text.lower()


def test_corridor_survey_needs_a_line(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    response = api_client.post("/api/analyses/", json={
        "kind": "survey", "dataset_id": dataset["id"], "params": {"coverage": "corridor", "corridor_width_m": 50},
    })
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "geometry_type_not_supported" and "line" in error["message"].lower()


def test_corridor_survey_keeps_the_whole_line_across_a_polygon(api_client: TestClient, helpers: SimpleNamespace) -> None:
    # A 0.01° (≈ 1,085 m) east-west line crossing a polygon: a union with the polygon would cut the
    # part inside it; the corridor keeps the line whole.
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"name": "Block"}, "geometry": {"type": "Polygon", "coordinates": [[
                [77.4930, 12.9020], [77.4970, 12.9020], [77.4970, 12.9080], [77.4930, 12.9080], [77.4930, 12.9020]]]}},
            {"type": "Feature", "properties": {"name": "Pipeline"},
             "geometry": {"type": "LineString", "coordinates": [[77.4900, 12.9050], [77.5000, 12.9050]]}},
        ],
    }
    uploaded = helpers.upload(api_client, "pipeline.geojson", json.dumps(fc).encode())
    assert uploaded.status_code == 201, uploaded.text
    info = helpers.run_analysis(api_client, {
        "kind": "survey", "dataset_id": uploaded.json()["id"], "params": {"coverage": "corridor", "corridor_width_m": 40},
    })
    assert info["status"] == "COMPLETED", info["error"]
    assert info["params"]["geometry"]["type"] == "LineString"
    # 0.01° of longitude at 12.905° N on WGS84 ≈ 1,085 m.
    assert info["result"]["centreline_km"] == pytest.approx(1.085, rel=5e-3)


def test_height_and_survey_params_are_validated(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")

    def post(kind: str, params: dict[str, object]) -> Any:
        return api_client.post("/api/analyses/", json={"kind": kind, "dataset_id": dataset["id"], "params": params})

    cases: list[tuple[str, dict[str, object], str]] = [
        ("airspace", {"height_reference": "sea"}, "height_reference"),
        ("airspace", {"height_reference": "takeoff", "takeoff": [200.0, 12.9]}, "takeoff"),
        ("airspace", {"altitude_agl_m": 2000}, "at most 1500"),  # above ground: ≤ 1,500 m
        ("airspace", {"min_agl_m": -1}, "min_agl_m"),
        ("survey", {"coverage": "corridor", "geometry": {"type": "LineString", "coordinates": [[77.49, 12.905], [77.5, 12.905]]}},
         "corridor_width_m"),
        ("survey", {"mode": "spraying", "height_reference": "takeoff"}, "height_reference 'agl'"),
        ("survey", {"battery_reserve_pct": 95}, "battery_reserve_pct"),
        ("survey", {"mode": "spraying", "spray": {"tank_l": 0}}, "tank_l"),
    ]
    for kind, params, needle in cases:
        response = post(kind, params)
        assert response.status_code == 422, (params, response.text)
        assert response.json()["error"]["code"] == "invalid_params", params
        assert needle in response.text, (params, response.text)
    # The same 2,000 m is a valid AMSL level.
    assert post("airspace", {"altitude_agl_m": 2000, "height_reference": "amsl", "grid": False}).status_code == 202
    corridor = api_client.post("/api/analyses/", json={
        "kind": "evtol_corridor",
        "params": {"origin": [77.75, 12.97], "destination": [77.67, 12.845], "cruise_reference": "amsl"},
    })
    assert corridor.status_code == 422 and "cruise_altitude_amsl_m" in corridor.text


def test_amsl_survey_without_a_ground_reference_fails_cleanly(api_client: TestClient, helpers: SimpleNamespace) -> None:
    # No data pack covers this area and no take-off elevation is typed: the layout height above the
    # ground is unknown, so the analysis fails with heights_not_assessed (never a crash).
    info = api_client.post("/api/analyses/", json={
        "kind": "survey",
        "params": {
            "geometry": {"type": "Polygon", "coordinates": [[[10.0, 50.0], [10.01, 50.0], [10.01, 50.005], [10.0, 50.005], [10.0, 50.0]]]},
            "height_reference": "amsl", "altitude_agl_m": 400,
        },
    })
    assert info.status_code == 202, info.text
    done = helpers.wait_for_analysis(api_client, info.json()["id"])
    assert done["status"] == "FAILED"
    assert done["error"]["code"] == "heights_not_assessed"


def test_airspace_above_take_off_on_the_survey_area(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info = helpers.run_analysis(api_client, {
        "kind": "airspace",
        "dataset_id": dataset["id"],
        "params": {"altitude_agl_m": 100, "height_reference": "takeoff", "grid": False},
    })
    assert info["status"] == "COMPLETED", info["error"]
    result = info["result"]
    assert result["height_reference"] == "takeoff"
    assert result["takeoff"]["source"] == "centroid"
    takeoff_elevation = result["takeoff"]["elevation_m"]
    assert result["flight_level_amsl_m"] == pytest.approx(takeoff_elevation + 100.0, abs=0.01)
    terrain = result["terrain"]
    assert result["agl_min_m"] == pytest.approx(takeoff_elevation + 100.0 - terrain["max_m"], abs=0.02)
    assert result["agl_max_m"] == pytest.approx(takeoff_elevation + 100.0 - terrain["min_m"], abs=0.02)
    assert result["heights_assessed"] is True
    assert result["dem"]["caveat"].startswith("Synthetic demo elevation")


def test_spraying_survey_through_the_api(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    info = helpers.run_analysis(api_client, {
        "kind": "survey",
        "dataset_id": dataset["id"],
        "params": {"mode": "spraying", "spray": {"swath_m": 6, "rate_l_per_ha": 15, "tank_l": 16}},
    })
    assert info["status"] == "COMPLETED", info["error"]
    result = info["result"]
    assert result["mode"] == "spraying"
    assert result["camera"] is None and result["photos"] == 0
    spray = result["spray"]
    assert spray["litres"] == pytest.approx(result["area_ha"] * 15.0, abs=0.01)
    assert spray["tank_loads"] >= 1 and spray["sorties"] >= spray["tank_loads"]
    csv_export = api_client.get(f"/api/analyses/{info['id']}/export/", params={"format": "csv"})
    assert csv_export.status_code == 200


# The payloads the web panels send (frontend/components/modules/{airspace,survey,evtol}), nulls
# included: the API must accept them as they are.


def test_airspace_panel_payload_amsl_route(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "climb_line_z")
    info = helpers.run_analysis(api_client, {
        "kind": "airspace",
        "dataset_id": dataset["id"],
        "params": {
            "altitude_agl_m": 950, "height_reference": "amsl", "takeoff": None, "takeoff_elevation_m": None,
            "min_agl_m": 30, "grid": True, "include_datapack_zones": True, "h3_resolution": 9,
            "obstacle_search_m": 300, "vertical_clearance_m": 30, "profile_spacing_m": 30,
        },
    })
    assert info["status"] == "COMPLETED", info["error"]
    result = info["result"]
    # The synthetic ground under the line is about 920 m: 950 m AMSL is 28–30 m above it.
    assert result["flight_level_amsl_m"] == pytest.approx(950.0)
    assert result["agl_min_m"] < 30.0 < result["agl_max_m"] + 1.0
    assert result["below_min_agl"], "a stretch below the 30 m minimum"
    assert result["layers"]["heights"]["features"][0]["properties"]["kind"] == "below_min_agl"
    assert any(i["code"] == "vertical_reference_approximated" for i in result["issues"])


def test_survey_panel_payloads(api_client: TestClient, helpers: SimpleNamespace) -> None:
    dataset = helpers.load_sample(api_client, "bengaluru_survey")
    common = {
        "mode": "mapping", "coverage": "area", "battery_minutes": 25, "battery_swap_minutes": 5,
        "battery_reserve_pct": 20, "transit_legs": True, "transit_speed_ms": None, "direction_deg": None,
    }
    mapping = helpers.run_analysis(api_client, {
        "kind": "survey",
        "dataset_id": dataset["id"],
        "params": {
            **common, "camera": "p4m", "height_reference": "takeoff", "takeoff": None, "takeoff_elevation_m": None,
            "front_overlap_pct": 80, "side_overlap_pct": 70, "speed_ms": 10, "turn_seconds": 8,
            "min_trigger_interval_s": 2, "max_altitude_agl_m": 120, "orientation": "landscape", "gcp_min": 5,
            "gcp_ha_per_extra": 10, "gcp_corridor_spacing_m": 300, "photo_mb": None, "altitude_agl_m": 100,
        },
    })
    assert mapping["status"] == "COMPLETED", mapping["error"]
    r = mapping["result"]
    assert r["camera"]["kind"] == "multispectral" and r["storage"]["images_per_capture"] == 6
    assert r["usable_battery_minutes"] == pytest.approx(20.0)
    assert r["transit_km"] is not None and r["takeoff"]["source"] == "centroid"
    assert r["gsd_min_cm"] <= r["gsd_cm"] <= r["gsd_max_cm"]

    spraying = helpers.run_analysis(api_client, {
        "kind": "survey",
        "dataset_id": dataset["id"],
        "params": {
            **common, "mode": "spraying", "takeoff": [77.49, 12.905],
            "spray": {"swath_m": 5, "rate_l_per_ha": 20, "tank_l": 10, "flow_l_per_min": 4, "speed_ms": 5,
                      "turn_seconds": 5, "refill_minutes": 3, "height_m": 3},
        },
    })
    assert spraying["status"] == "COMPLETED", spraying["error"]
    spray = spraying["result"]["spray"]
    # Breakpoint resume: with lines longer than a tank's worth, the sorties are the tank loads.
    assert spray["sorties"] == spray["tank_loads"] == math.ceil(spray["litres"] / 10.0 - 1e-3)
    assert spraying["result"]["takeoff"]["source"] == "given"


def test_corridor_panel_payload_amsl(api_client: TestClient, helpers: SimpleNamespace) -> None:
    info = helpers.run_analysis(api_client, {
        "kind": "evtol_corridor",
        "params": {
            "origin": [77.750, 12.970], "destination": [77.670, 12.845], "origin_name": "Whitefield",
            "destination_name": "Electronic City", "corridor_width_m": 300, "cruise_altitude_agl_m": 300,
            "cruise_speed_kmh": 150, "energy_wh_per_km": 1000, "obstacle_clearance_m": 50,
            "noise_level_db_at_ref": 75, "noise_threshold_db": 55, "avoid_red_zones": True,
            "cruise_reference": "amsl", "cruise_altitude_amsl_m": 1000, "origin_elevation_m": None,
            "min_agl_m": 100, "ceiling_agl_m": None,
        },
    })
    assert info["status"] == "COMPLETED", info["error"]
    result = info["result"]
    assert result["cruise_reference"] == "amsl" and result["cruise_altitude_amsl_m"] == 1000
    assert result["below_min_agl"] and result["above_ceiling"] == []
    assert result["takeoff"]["source"] == "origin"
