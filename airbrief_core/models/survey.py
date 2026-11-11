"""Survey effort additions: spraying effort mode, ground control points and storage.

Everything here is *effort evidence* for planning a job: litres, tank loads, sorties, times,
counts and gigabytes. Survey and spray lines stay a visual aid; nothing here is a route, a
waypoint list or a mission file (airbrief_core/analyses/survey.py, steps 12–15).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import InputModel, OutputModel

__all__ = [
    "GCP_RULE_REFERENCE",
    "GcpEstimate",
    "SPRAY_HEIGHT_BAND_M",
    "SprayParams",
    "SprayResult",
    "StorageEstimate",
    "SurveyCoverage",
    "SurveyMode",
]

SurveyMode = Literal["mapping", "spraying"]
SurveyCoverage = Literal["area", "corridor"]

SPRAY_HEIGHT_BAND_M: tuple[float, float] = (2.0, 5.0)
"""Spray heights above the crop outside this band get a note (typical agricultural-drone band)."""

GCP_RULE_REFERENCE = (
    "Rule of thumb, not a standard: Pix4D recommends at least 5 ground control points, well "
    "distributed (5–10 are usually enough); more are added per area or along a corridor so that "
    "every part of the block is near one. The counts are configurable."
)


class SprayParams(InputModel):
    """Agricultural spraying inputs (effort only: no route, no waypoints).

    ``flow_l_per_min`` is the most the pump can deliver; when the application rate at the chosen
    speed needs more, the estimate slows down to what the pump allows.
    """

    swath_m: float = Field(default=5.0, gt=0, le=50)
    rate_l_per_ha: float = Field(default=20.0, gt=0, le=1000)
    tank_l: float = Field(default=10.0, gt=0, le=500)
    flow_l_per_min: float = Field(default=4.0, gt=0, le=200)
    speed_ms: float = Field(default=5.0, gt=0, le=20)
    turn_seconds: float = Field(default=5.0, ge=0, le=120)
    refill_minutes: float = Field(default=3.0, ge=0, le=120)
    height_m: float = Field(default=3.0, ge=0.5, le=30)


class SprayResult(OutputModel):
    """Spraying effort: litres, tank loads, sorties, work rates (survey step 14)."""

    swath_m: float
    rate_l_per_ha: float
    tank_l: float
    height_m: float
    requested_speed_ms: float
    speed_ms: float
    flow_l_per_min: float
    max_flow_l_per_min: float
    flow_limited: bool
    litres: float
    tank_loads: int
    sorties: int
    refills: int
    spray_minutes: float
    theoretical_ha_per_hour: float
    effective_ha_per_hour: float
    field_efficiency_pct: float
    method: str


class GcpEstimate(OutputModel):
    """Ground control points for the block (survey step 13): a rule of thumb, configurable."""

    count: int
    basis: Literal["area", "corridor"]
    rule: str
    reference: str = GCP_RULE_REFERENCE


class StorageEstimate(OutputModel):
    """Image storage for the job (survey step 13): images × megabytes per image."""

    images: int
    images_per_capture: int
    mb_per_image: float
    total_gb: float
    source: Literal["params", "camera"]
