"""Orchestrator: parameter validation, envelope fields, cancellation and end-to-end runs."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime

import pytest
from shapely.geometry import box

import airbrief_core
from airbrief_core.analyses import REGISTRY
from airbrief_core.data import DataPack
from airbrief_core.errors import AnalysisCancelled, AnalysisError
from airbrief_core.geo.transform import geometry_to_geojson
from airbrief_core.models import ANALYSIS_KINDS, PARAMS_MODELS, RESULT_MODELS, SCHEMA_VERSION
from airbrief_core.models.analyses import GroundRiskResult, SurveyParams, SurveyResult
from airbrief_core.orchestrator import run_analysis, run_analysis_sync, validate_params

SURVEY_AREA = geometry_to_geojson(box(77.48, 12.90, 77.49, 12.905))
CITY = geometry_to_geojson(box(77.5916, 12.9686, 77.5976, 12.9746))


def _parse_utc(text: str) -> datetime:
    assert text.endswith("Z")
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_registry_covers_every_kind() -> None:
    assert set(REGISTRY) == set(ANALYSIS_KINDS)
    for kind, spec in REGISTRY.items():
        assert spec.kind == kind
        assert spec.params_model is PARAMS_MODELS[kind]
        assert spec.result_model is RESULT_MODELS[kind]
        assert callable(spec.run)
        assert spec.title and spec.description
    assert REGISTRY["ground_risk"].requires == ("population",)
    assert set(REGISTRY["evtol_siting"].requires) == {"population", "dem"}
    assert REGISTRY["evtol_corridor"].requires == ("population",)
    for kind in ("airspace", "show", "survey", "flight_audit"):
        assert REGISTRY[kind].requires == ()


def test_package_exports() -> None:
    assert airbrief_core.__version__ == "0.1.0"
    assert airbrief_core.SCHEMA_VERSION == SCHEMA_VERSION
    assert airbrief_core.run_analysis_sync is run_analysis_sync
    assert airbrief_core.run_analysis is run_analysis
    assert airbrief_core.REGISTRY is REGISTRY
    assert callable(airbrief_core.process_file)
    assert airbrief_core.errors.AnalysisError is AnalysisError


@pytest.mark.parametrize(
    "params",
    [
        {"geometry": SURVEY_AREA, "front_overlap_pct": 150},
        {"geometry": SURVEY_AREA, "unknown_field": 1},
        {"front_overlap_pct": 80},
        {"geometry": SURVEY_AREA, "altitude_agl_m": 100, "target_gsd_cm": 2},
    ],
)
def test_invalid_params(params: dict) -> None:
    with pytest.raises(AnalysisError) as exc:
        run_analysis_sync("survey", params)
    assert exc.value.code == "invalid_params"
    errors = exc.value.details["errors"]
    assert errors and {"loc", "msg", "type"} <= set(errors[0])


def test_unknown_kind() -> None:
    with pytest.raises(AnalysisError) as exc:
        run_analysis_sync("teleport", {})
    assert exc.value.code == "invalid_params"
    assert "survey" in exc.value.details["known"]


def test_validate_params_accepts_model() -> None:
    model = SurveyParams(geometry=SURVEY_AREA)
    assert validate_params("survey", model) is model


def test_survey_end_to_end_envelope() -> None:
    stages: list[tuple[str, float]] = []
    envelope = run_analysis_sync(
        "survey",
        {"geometry": SURVEY_AREA, "altitude_agl_m": 100},
        progress=lambda stage, fraction: stages.append((stage, fraction)),
    )
    assert envelope.kind == "survey"
    assert envelope.schema_version == SCHEMA_VERSION
    assert envelope.airbrief_version == airbrief_core.__version__
    assert envelope.backend == "python"
    assert envelope.datapack is None
    assert envelope.datapack_synthetic is False
    started = _parse_utc(envelope.started_utc)
    finished = _parse_utc(envelope.finished_utc)
    assert finished >= started
    assert envelope.duration_ms >= 0
    assert envelope.params["altitude_agl_m"] == 100
    assert envelope.params["camera"] == "p4rtk"

    result = SurveyResult.model_validate(envelope.result)
    assert result.gsd_cm == pytest.approx(2.74, abs=0.005)
    assert result.line_count > 0
    assert set(result.layers) == {"input", "blocks", "lines"}

    assert stages[-1] == ("done", 1.0)
    fractions = [f for _, f in stages]
    assert all(0.0 <= f <= 1.0 for f in fractions)


def test_envelope_records_datapack(synthetic_pack: DataPack) -> None:
    envelope = run_analysis_sync("ground_risk", {"geometry": CITY}, datapack=synthetic_pack)
    assert envelope.datapack == synthetic_pack.name
    assert envelope.datapack_synthetic is True
    result = GroundRiskResult.model_validate(envelope.result)
    assert result.igrc is not None


def test_missing_datapack_propagates() -> None:
    with pytest.raises(AnalysisError) as exc:
        run_analysis_sync("ground_risk", {"geometry": CITY})
    assert exc.value.code == "datapack_missing"


def test_cancel_event_set_before_start() -> None:
    event = threading.Event()
    event.set()
    with pytest.raises(AnalysisCancelled) as exc:
        run_analysis_sync("survey", {"geometry": SURVEY_AREA}, cancel_event=event)
    assert exc.value.code == "cancelled"


def test_cancel_during_run() -> None:
    event = threading.Event()

    def progress(stage: str, fraction: float) -> None:
        event.set()  # request cancellation at the first progress report

    with pytest.raises(AnalysisCancelled):
        run_analysis_sync("survey", {"geometry": SURVEY_AREA}, progress=progress, cancel_event=event)


def test_async_run_analysis() -> None:
    envelope = asyncio.run(run_analysis("survey", {"geometry": SURVEY_AREA}))
    assert envelope.kind == "survey"
    assert envelope.result["batteries"] >= 1


def test_async_invalid_params() -> None:
    with pytest.raises(AnalysisError) as exc:
        asyncio.run(run_analysis("survey", {"geometry": SURVEY_AREA, "speed_ms": -1}))
    assert exc.value.code == "invalid_params"
