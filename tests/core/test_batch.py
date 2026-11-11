"""One result per feature (airbrief_core/batch.py) on the synthetic pack.

Ring tests use their own zone rules (0–8 km permission, 8–20 km up to 60 m around medium
airports) and points due north of VOBG (12.9499°N, 77.6682°E) at 4 km and 11 km, so a boundary
measured from the runway instead of the reference point still leaves each on its side.
"""

from __future__ import annotations

import random
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from airbrief_core import batch as batch_module
from airbrief_core.analyses.base import AnalysisContext
from airbrief_core.batch import batch_table, export_layers, is_batch_result, run_batch
from airbrief_core.data import DataPack
from airbrief_core.errors import AnalysisCancelled, AnalysisError
from airbrief_core.models.analyses import AirspaceParams, LineOfSightParams, SurveyParams
from airbrief_core.models.batch import MAX_BATCH_FEATURES, BatchParams
from airbrief_core.orchestrator import run_analysis_sync
from airbrief_core.processing import process_file
from airbrief_core.samples import build_sample

VOBG = (77.6682, 12.9499)
KM_PER_DEG_LAT = 111.32

RULES = {
    "rules": [
        {
            "aerodrome_types": ["medium_airport"],
            "assumed_radius_km": 0,
            "rings": [
                {"zone": "yellow", "inner_km": 0, "outer_km": 8, "ceiling_agl_m": None, "label": "0–8 km: permission"},
                {"zone": "green", "inner_km": 8, "outer_km": 20, "ceiling_agl_m": 60, "label": "8–20 km: up to 60 m"},
            ],
        }
    ],
    "default_zone": "green",
    "default_ceiling_agl_m": 120,
}


def _square(lon: float, lat: float, d: float = 0.001) -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]]}


def _feature(i: int, geometry: dict[str, Any] | None, **props: Any) -> dict[str, Any]:
    return {"type": "Feature", "id": i, "geometry": geometry, "properties": props}


def _collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _plots(n: int, seed: int = 7) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    return [
        _feature(i, _square(77.42 + rng.random() * 0.4, 12.82 + rng.random() * 0.35), _name=f"Plot {i}", asset_id=f"T-{i:03d}")
        for i in range(n)
    ]


def _extent() -> dict[str, Any]:
    return _square(77.4, 12.8, 0.5)


def _run(kind: str, features: list[dict[str, Any]], pack: DataPack, **params: Any) -> dict[str, Any]:
    batch = {"features": _collection(features), **params.pop("batch", {})}
    envelope = run_analysis_sync(kind, {"geometry": _extent(), "batch": batch, **params}, datapack=pack)
    return envelope.result


def test_plots_shapefile_gives_one_item_per_plot_with_its_attributes(tmp_path: Path, synthetic_pack: DataPack) -> None:
    path = tmp_path / "plots.zip"
    path.write_bytes(build_sample("plots_shapefile"))
    records = process_file(path, "plots.zip").features
    features = [
        _feature(r.index, r.geometry_wgs84, _name=r.name, **(r.properties or {})) for r in records
    ]

    result = _run("survey", features, synthetic_pack, batch={"key_property": "name"})

    assert is_batch_result(result)
    assert result["feature_count"] == 3 and result["ok"] == 3 and result["errors"] == 0
    assert [i["key"] for i in result["items"]] == ["Plot A", "Plot B", "Plot C"]
    assert [i["attributes"]["crop"] for i in result["items"]] == ["Ragi", "Tomato", "Mulberry"]
    # the grid areas of the sample: 20,000, 22,500 and 15,000 m²
    areas = [i["values"]["area_ha"] for i in result["items"]]
    assert areas == pytest.approx([2.0, 2.25, 1.5], rel=2e-3)
    assert {f["properties"]["key"] for f in result["layers"]["features"]["features"]} == {"Plot A", "Plot B", "Plot C"}


def test_items_on_either_side_of_a_ring_boundary_get_different_bands(synthetic_pack: DataPack) -> None:
    lon, lat = VOBG
    near = _feature(0, {"type": "Point", "coordinates": [lon, lat + 4 / KM_PER_DEG_LAT]}, _name="near")
    far = _feature(1, {"type": "Point", "coordinates": [lon, lat + 11 / KM_PER_DEG_LAT]}, _name="far")

    result = _run("airspace", [near, far], synthetic_pack, zone_rules=RULES, include_datapack_zones=False, grid=False)

    a, b = result["items"]
    assert a["verdict"] == "permission" and a["values"]["permission_required"] is True
    assert b["verdict"] == "limited" and b["values"]["effective_ceiling_agl_m"] == 60
    assert a["values"]["band"] != b["values"]["band"]
    assert a["values"]["band"].startswith("VOBG") and b["values"]["band"].startswith("VOBG")
    assert {c["verdict"]: c["count"] for c in result["counts"]} == {"permission": 1, "limited": 1}


def test_an_invalid_feature_records_an_error_and_the_others_go_on(synthetic_pack: DataPack) -> None:
    features = _plots(3)
    features.insert(1, _feature(99, None, _name="no geometry"))
    features.append(_feature(100, {"type": "Point", "coordinates": [77.6, 12.95]}, _name="a point"))

    result = _run("survey", features, synthetic_pack)

    assert [(i["index"], i["status"], i["error_code"]) for i in result["items"]] == [
        (0, "ok", None), (1, "error", "invalid_geometry"), (2, "ok", None), (3, "ok", None),
        (4, "error", "geometry_type_not_supported")]
    assert result["ok"] == 3 and result["errors"] == 2
    assert any(issue["code"] == "feature_errors" for issue in result["issues"])
    assert "2 could not be assessed" in result["summary"]


@pytest.mark.parametrize("kind, extra", [
    ("airspace", {"grid": False}),
    ("ground_risk", {"volume": {"aircraft": "multirotor"}}),
])
def test_a_batch_item_matches_the_single_run_of_its_feature(kind: str, extra: dict[str, Any], synthetic_pack: DataPack) -> None:
    features = _plots(30, seed=11)
    result = _run(kind, features, synthetic_pack, **dict(extra))
    for i in random.Random(3).sample(range(30), 5):
        single = run_analysis_sync(kind, {"geometry": features[i]["geometry"], **extra}, datapack=synthetic_pack).result
        values = result["items"][i]["values"]
        if kind == "airspace":
            assert values["check"] == single["verdict"]
            assert values["effective_ceiling_agl_m"] == single["effective_ceiling_agl_m"]
            assert values["permission_required"] == single["permission_required"]
            assert values["obstacle_conflicts"] == sum(1 for o in single["obstacles"] if o["conflict"])
            assert values["nearest_aerodrome"] == single["nearest_aerodromes"][0]["ident"]
        else:
            assert values["igrc"] == single["igrc"]
            assert values["band"] == single["density_band"]
            assert values["max_density_per_km2"] == pytest.approx(single["max_density_per_km2"], abs=0.05)


def test_a_hundred_plots_run_within_the_time_budget_and_build_the_zones_once(
    synthetic_pack: DataPack, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    zones = synthetic_pack.zones

    def counting() -> Any:
        nonlocal calls
        calls += 1
        return zones()

    monkeypatch.setattr(synthetic_pack, "zones", counting)
    # a thread of its own, so the zone cache starts empty
    out: dict[str, Any] = {}

    def work() -> None:
        started = time.perf_counter()
        out["result"] = _run("airspace", _plots(100), synthetic_pack)
        out["seconds"] = time.perf_counter() - started

    worker = threading.Thread(target=work)
    worker.start()
    worker.join()

    assert out["result"]["ok"] == 100
    assert out["seconds"] < 30.0
    assert calls == 1


def test_cancellation_stops_the_run_at_the_next_feature(synthetic_pack: DataPack) -> None:
    event = threading.Event()
    seen: list[float] = []

    def progress(stage: str, fraction: float) -> None:
        seen.append(fraction)
        if fraction >= 0.3:
            event.set()

    params = SurveyParams.model_validate({"geometry": _extent(), "batch": {"features": _collection(_plots(10))}})
    ctx = AnalysisContext(datapack=synthetic_pack, progress=progress, cancel_event=event)
    with pytest.raises(AnalysisCancelled):
        run_batch("survey", params, params.batch.features["features"], ctx)  # type: ignore[union-attr]
    assert max(seen) < 0.5


def test_feature_limits() -> None:
    too_many = _collection([_feature(i, {"type": "Point", "coordinates": [77.5, 12.9]}) for i in range(MAX_BATCH_FEATURES + 1)])
    with pytest.raises(ValidationError, match="at most 1,000 features"):
        BatchParams.model_validate({"features": too_many})

    los = LineOfSightParams.model_validate(
        {"geometry": _extent(), "batch": {"features": _collection([_feature(i, {"type": "Point", "coordinates": [77.5, 12.9]}) for i in range(501)])}}
    )
    with pytest.raises(AnalysisError) as excinfo:
        run_batch("line_of_sight", los, los.batch.features["features"], AnalysisContext())  # type: ignore[union-attr]
    assert excinfo.value.code == "too_many_features"


def test_kinds_without_per_feature_results_are_refused(synthetic_pack: DataPack) -> None:
    params = AirspaceParams.model_validate({"geometry": _extent()})
    with pytest.raises(AnalysisError) as excinfo:
        run_batch("show", params, _plots(2), AnalysisContext(datapack=synthetic_pack))
    assert excinfo.value.code == "invalid_params"


def test_features_after_the_time_budget_are_skipped(synthetic_pack: DataPack, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batch_module, "BATCH_TIME_BUDGET_S", -1.0)

    result = _run("survey", _plots(4), synthetic_pack)

    assert result["skipped"] == 4 and result["ok"] == 0
    assert {i["status"] for i in result["items"]} == {"skipped"}
    assert any(issue["code"] == "batch_time_budget" for issue in result["issues"])


def test_the_layer_falls_back_to_points_past_its_vertex_cap(synthetic_pack: DataPack, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batch_module, "MAX_LAYER_POSITIONS", 12)

    result = _run("survey", _plots(4), synthetic_pack)

    types = [f["geometry"]["type"] for f in result["layers"]["features"]["features"]]
    assert types == ["Polygon", "Polygon", "Point", "Point"]
    assert result["points_only"] == 2


def test_the_key_property_names_each_item_and_a_missing_one_is_reported(synthetic_pack: DataPack) -> None:
    features = _plots(3)
    del features[2]["properties"]["asset_id"]

    result = _run("environment", features, synthetic_pack, date="2026-10-19", batch={"key_property": "asset_id"})

    assert [i["key"] for i in result["items"]] == ["T-000", "T-001", "Plot 2"]
    assert any(issue["code"] == "key_property_missing" for issue in result["issues"])


def test_features_sent_with_the_request_are_numbered_by_position(synthetic_pack: DataPack) -> None:
    # ids that repeat a position (1), are missing, or point at another feature (0)
    first = _feature(1, _square(77.50, 12.90), _name="A", crop="Ragi")
    second = {"type": "Feature", "geometry": _square(77.52, 12.90), "properties": {"_name": "B", "crop": "Tomato"}}
    third = _feature(0, _square(77.54, 12.90), _name="C", crop="Mulberry")

    result = _run("survey", [first, second, third], synthetic_pack)

    assert [(i["index"], i["key"]) for i in result["items"]] == [(0, "A"), (1, "B"), (2, "C")]
    layer = export_layers(result)["features"]["features"]
    assert [(f["properties"]["index"], f["properties"]["key"], f["properties"]["crop"]) for f in layer] == [
        (0, "A", "Ragi"), (1, "B", "Tomato"), (2, "C", "Mulberry")]
    rows, _columns = batch_table(result)
    assert [(r["key"], r["crop"]) for r in rows] == [("A", "Ragi"), ("B", "Tomato"), ("C", "Mulberry")]


def test_a_datasets_features_keep_their_feature_numbers(synthetic_pack: DataPack) -> None:
    features = [_feature(i, _square(77.50 + i * 0.02, 12.90), _name=f"Plot {i}") for i in (4, 9)]
    result = _run("survey", features, synthetic_pack, batch={"from_dataset": True})
    assert [i["index"] for i in result["items"]] == [4, 9]


def test_repeated_feature_numbers_fall_back_to_positions(synthetic_pack: DataPack) -> None:
    features = [_feature(i, _square(77.50 + k * 0.02, 12.90), _name=f"Plot {k}") for k, i in enumerate((4, 4, 0))]
    result = _run("survey", features, synthetic_pack, batch={"from_dataset": True})
    assert [i["index"] for i in result["items"]] == [0, 1, 2]


def test_the_table_joins_the_attributes_and_renames_clashing_ones() -> None:
    result = {
        "kind": "survey",
        "counts": [{"verdict": "batteries_1", "label": "1 battery", "tone": "neutral", "count": 1}],
        "items": [
            {"index": 4, "key": "P4", "name": "Plot 4", "status": "ok", "verdict": "batteries_1",
             "values": {"area_ha": 1.5, "photos": 20}, "issues": ["a", "b"],
             "attributes": {"crop": "Ragi", "status": "leased", "tags": ["x"]}},
        ],
    }

    rows, columns = batch_table(result)

    assert columns[:6] == ["index", "key", "name", "status", "verdict", "verdict_label"]
    assert columns[-3:] == ["crop", "attr.status", "tags"]
    assert rows[0]["verdict_label"] == "1 battery"
    assert rows[0]["status"] == "ok" and rows[0]["attr.status"] == "leased"
    assert rows[0]["tags"] == '["x"]' and rows[0]["issues"] == "a b"


def test_the_export_layer_carries_the_attributes() -> None:
    result = {
        "batch": "per_feature",
        "items": [{"index": 2, "attributes": {"crop": "Ragi", "band": "own"}}],
        "layers": {"features": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [77.5, 12.9]},
             "properties": {"index": 2, "verdict": "free", "band": "VOBG 8–12 km"}},
        ]}},
    }

    props = export_layers(result)["features"]["features"][0]["properties"]

    assert props["crop"] == "Ragi" and props["attr.band"] == "own" and props["band"] == "VOBG 8–12 km"


def test_the_brief_shows_a_batch_and_flags_lower_cell_caps(synthetic_pack: DataPack) -> None:
    from airbrief_core.brief import build_brief
    from airbrief_core.models.brief import BriefSource

    features = _plots(3)
    envelopes = [
        run_analysis_sync(kind, {"geometry": _extent(), "batch": {"features": _collection(features)}, **extra}, datapack=synthetic_pack)
        for kind, extra in (("airspace", {"max_grid_cells": 500}), ("ground_risk", {"max_cells": 5000}))
    ]
    brief = build_brief([BriefSource(analysis_id=f"a{i}", envelope=e.model_dump(mode="json")) for i, e in enumerate(envelopes)])

    airspace = next(s for s in brief.sections if s.key == "airspace")
    assert airspace.parts[0].title.endswith("one result per feature")
    assert airspace.parts[0].tables[0].total == 3
    flagged = " ".join(w.message for w in brief.warnings if w.code == "non_default_parameters")
    assert "(max_grid_cells)" in flagged and "(max_cells)" in flagged


def test_looser_show_settings_are_flagged() -> None:
    from airbrief_core.brief import _non_default_params

    assert _non_default_params("show", {"dynamics_window_s": 1.0, "airborne_threshold_m": 1.0}) == []
    flagged = _non_default_params("show", {"dynamics_window_s": 3.0, "airborne_threshold_m": 5.0})
    assert len(flagged) == 2
    assert "(dynamics_window_s)" in flagged[0] and "(airborne_threshold_m)" in flagged[1]
