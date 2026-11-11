"""Analysis modules and their registry.

Every module exposes ``run(params, ctx) -> Result`` (eVTOL has two: siting and corridor).
:data:`REGISTRY` maps each analysis kind to an :class:`AnalysisSpec` describing its parameter and
result models, its runner and the data-pack layers it requires or can use (docs/SPEC.md §3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..models.analyses import (
    AirspaceParams,
    AirspaceResult,
    EvtolCorridorParams,
    EvtolCorridorResult,
    EvtolSitingParams,
    EvtolSitingResult,
    FlightAuditParams,
    FlightAuditResult,
    GroundRiskParams,
    GroundRiskResult,
    ShowParams,
    ShowResult,
    SurveyParams,
    SurveyResult,
)
from ..models.common import InputModel, OutputModel
from ..models.environment import EnvironmentParams, EnvironmentResult
from . import airspace, environment, evtol, flight_audit, groundrisk, shows, survey
from ..models.line_of_sight import LineOfSightParams, LineOfSightResult
from . import line_of_sight
from .base import AnalysisContext

__all__ = ["REGISTRY", "AnalysisContext", "AnalysisSpec", "get_spec"]

Runner = Callable[[Any, AnalysisContext], Any]


@dataclass(frozen=True)
class AnalysisSpec:
    """Static description of one analysis kind."""

    kind: str
    title: str
    description: str
    params_model: type[InputModel]
    result_model: type[OutputModel]
    run: Runner
    requires: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()

    def meta(self) -> dict[str, Any]:
        """JSON-friendly summary for ``/api/meta`` (includes the parameter JSON schema)."""
        return {
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "requires": list(self.requires),
            "optional": list(self.optional),
            "params_schema": self.params_model.model_json_schema(),
        }


REGISTRY: dict[str, AnalysisSpec] = {
    spec.kind: spec
    for spec in (
        AnalysisSpec(
            kind="airspace",
            title="Airspace & terrain",
            description=(
                "Indicative airspace zones around aerodromes, user and data-pack zones, terrain "
                "profile, obstacles and an H3 flyability grid for a planned operation."
            ),
            params_model=AirspaceParams,
            result_model=AirspaceResult,
            run=airspace.run,
            requires=(),
            optional=("aerodromes", "dem", "obstacles", "zones", "aerodrome_perimeters", "runways"),
        ),
        AnalysisSpec(
            kind="ground_risk",
            title="Ground risk (SORA 2.5 evidence)",
            description=(
                "SORA 2.5 Step 2 / Step 8 evidence: flight geography, contingency volume and ground "
                "risk buffer, the highest gridded population density in that footprint with its "
                "Table 2 cell, the adjacent-area average density and outdoor assemblies within 1 km. "
                "Not a GRC, ARC or SAIL."
            ),
            params_model=GroundRiskParams,
            result_model=GroundRiskResult,
            run=groundrisk.run,
            requires=("population",),
            optional=("assemblies",),
        ),
        AnalysisSpec(
            kind="evtol_siting",
            title="eVTOL vertiport siting",
            description=(
                "Scores H3 cells for vertiport suitability from demand, access, airspace, "
                "obstacles and slope, and picks well-separated top candidates."
            ),
            params_model=EvtolSitingParams,
            result_model=EvtolSitingResult,
            run=evtol.run_siting,
            requires=("population", "dem"),
            optional=("water", "pois", "obstacles", "aerodromes", "zones", "aerodrome_perimeters", "runways"),
        ),
        AnalysisSpec(
            kind="evtol_corridor",
            title="eVTOL corridor routing",
            description=(
                "Least-cost corridor between two points that weighs ground risk, airspace and "
                "obstacles, with terrain profile, noise exposure, time and energy."
            ),
            params_model=EvtolCorridorParams,
            result_model=EvtolCorridorResult,
            run=evtol.run_corridor,
            requires=("population",),
            optional=("dem", "obstacles", "aerodromes", "zones", "aerodrome_perimeters", "runways"),
        ),
        AnalysisSpec(
            kind="show",
            title="Drone-show validator",
            description=(
                "Checks drone-show trajectories for separation conflicts, speed, climb, descent "
                "and acceleration limits, geofence, audience buffer and ceiling."
            ),
            params_model=ShowParams,
            result_model=ShowResult,
            run=shows.run,
            requires=(),
            optional=(),
        ),
        AnalysisSpec(
            kind="survey",
            title="Survey effort estimator",
            description=(
                "GSD, flight-line layout, photo count, flight time, batteries and battery blocks "
                "for a mapping survey over an area or a corridor, with the GSD range over relief, "
                "transit legs, GCP and storage estimates, or the effort of a spraying job. Lines "
                "are a visual aid only and never exported as waypoints."
            ),
            params_model=SurveyParams,
            result_model=SurveyResult,
            run=survey.run,
            requires=(),
            optional=("dem",),
        ),
        AnalysisSpec(
            kind="flight_audit",
            title="Flight audit (FlightMD or position track)",
            description=(
                "Audits a flown FlightMD track against an AGL ceiling, terrain, airspace zones "
                "and overflown population."
            ),
            params_model=FlightAuditParams,
            result_model=FlightAuditResult,
            run=flight_audit.run,
            requires=(),
            optional=("dem", "aerodromes", "zones", "population", "aerodrome_perimeters", "runways"),
        ),
        AnalysisSpec(
            kind="environment",
            title="Environment (sun, magnetic, wind, interference)",
            description=(
                "Sunrise, sunset and civil twilight with a daylight-profile check of the planned "
                "window, sun elevation, WMM2025 declination, wind at height and proximity to power "
                "lines, substations and masts. Evidence only, never a go/no-go."
            ),
            params_model=EnvironmentParams,
            result_model=EnvironmentResult,
            run=environment.run,
            requires=(),
            optional=("dem", "power", "obstacles"),
        ),
        AnalysisSpec(
            kind="line_of_sight",
            title="Line of sight (VLOS and radio path)",
            description=(
                "Terrain viewshed of the operation area from the pilot, observers and ground station "
                "at the planned height, with Earth curvature, plus C2 radio path profiles with first "
                "Fresnel zone clearance and free-space loss. Geometric evidence only."
            ),
            params_model=LineOfSightParams,
            result_model=LineOfSightResult,
            run=line_of_sight.run,
            requires=("dem",),
            optional=("obstacles",),
        ),
    )
}


def get_spec(kind: str) -> AnalysisSpec | None:
    """The registered spec for ``kind`` (``None`` if unknown)."""
    return REGISTRY.get(kind)
