"""Models for checks on flown or choreographed position tracks.

* Flight audit from a plain position track (a dataset's KML ``gx:Track``, GPX ``trk`` or CSV
  track): :class:`AuditTrackInput` carries the track, :class:`AuditSegment` reports the parts of a
  flight outside a training area or beyond a distance from take-off.
* Drone-show site checks: :class:`ShowAudienceBuffer` (the audience distance used and how it was
  derived) and :class:`ShowPadSpacing` (take-off and landing pad spacing).

They are imported into :mod:`airbrief_core.models.analyses`, which holds the module params and
results that use them.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .common import InputModel, OutputModel

__all__ = [
    "AuditSegment",
    "AuditTrackInput",
    "ShowAudienceBuffer",
    "ShowPadConflict",
    "ShowPadSpacing",
    "TrackFormat",
]

TrackFormat = Literal["kml", "gpx", "csv", "other"]


class AuditTrackInput(InputModel):
    """A recorded position track to audit (the API fills it from a dataset's timed track).

    ``coordinates`` are ``[lon, lat]`` or ``[lon, lat, alt_m]`` in flight order; ``times`` (seconds,
    aligned with the coordinates, never decreasing) are optional. ``altitude_mode`` is the file's
    (``absolute``, ``relativeToGround``, ``clampToGround``…) and decides how heights are read
    when ``FlightAuditParams.track_heights`` is ``auto``.
    """

    name: str | None = Field(default=None, max_length=200)
    coordinates: list[list[float]] = Field(min_length=2, max_length=500_000)
    times: list[float] | None = Field(default=None, max_length=500_000)
    time_origin_utc: str | None = Field(default=None, max_length=64)
    altitude_mode: str | None = Field(default=None, max_length=32)
    source_format: TrackFormat = "other"
    feature_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> AuditTrackInput:
        for c in self.coordinates:
            if len(c) < 2:
                raise ValueError("each coordinate must be [lon, lat] or [lon, lat, alt_m]")
            if not all(math.isfinite(v) for v in c[:3]):
                raise ValueError("coordinates must be finite numbers")
            if not (-180.0 <= c[0] <= 180.0 and -90.0 <= c[1] <= 90.0):
                raise ValueError("coordinates must be [lon, lat] in WGS84 degrees")
        if self.times is not None:
            if len(self.times) != len(self.coordinates):
                raise ValueError("times and coordinates must have the same length")
            if not all(math.isfinite(t) for t in self.times):
                raise ValueError("times must be finite numbers")
            if any(b < a for a, b in zip(self.times, self.times[1:], strict=False)):
                raise ValueError("times must never decrease")
        return self


class AuditSegment(OutputModel):
    """A run of consecutive track points beyond a horizontal limit.

    ``kind="area_exit"``: outside the training area; ``peak_m`` is the farthest distance outside
    its boundary and ``limit_m`` is 0. ``kind="distance"``: farther from the take-off point than
    ``limit_m``; ``peak_m`` is the farthest horizontal distance from take-off. Start and end are
    the linearly interpolated crossings between fixes (distance along the track, and time when
    the track has times).
    """

    kind: Literal["area_exit", "distance"]
    start_index: int
    end_index: int
    start_distance_m: float
    end_distance_m: float
    length_m: float
    start_time_s: float | None = None
    end_time_s: float | None = None
    duration_s: float | None = None
    peak_m: float
    limit_m: float


class ShowAudienceBuffer(OutputModel):
    """The audience distance a show was checked against and how it was obtained.

    ``rule``: ``fixed`` (the distance given), ``one_to_one`` (the show's greatest height above
    the origin) or ``ballistic`` (fall-out distance ``v·√(2h/g)`` with the show's greatest height
    and horizontal speed). ``distance_m = base_distance_m × (1 + breach_margin_pct / 100)``.
    """

    rule: Literal["fixed", "one_to_one", "ballistic"]
    base_distance_m: float
    breach_margin_pct: float = 0.0
    distance_m: float
    height_m: float | None = None
    speed_ms: float | None = None
    formula: str


class ShowPadConflict(OutputModel):
    """Two drones whose take-off (or landing) positions are closer than the pad spacing."""

    phase: Literal["takeoff", "landing"]
    drone_a: str
    drone_b: str
    distance_m: float
    limit_m: float
    time_s: float


class ShowPadSpacing(OutputModel):
    """Horizontal spacing of the drones on the ground at the start and at the end of the show.

    Only drones at or below the airborne threshold at their first (last) position count as
    standing on a take-off (landing) pad. ``*_min_spacing_m`` is ``None`` with fewer than two.
    """

    limit_m: float
    takeoff_pads: int
    landing_pads: int
    takeoff_min_spacing_m: float | None = None
    takeoff_pair: list[str] | None = None
    landing_min_spacing_m: float | None = None
    landing_pair: list[str] | None = None
    conflicts: list[ShowPadConflict] = Field(default_factory=list)
    conflicts_truncated: bool = False
