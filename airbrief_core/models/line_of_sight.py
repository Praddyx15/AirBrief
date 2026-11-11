"""Parameter and result models of the line-of-sight module (VLOS viewshed and radio path).

Heights are metres: ``*_agl_m`` above the DEM surface, ``*_amsl_m``/``*_m`` on profiles above
mean sea level (the data-pack DEM datum). Distances are horizontal metres unless named ``slant``.
See :mod:`airbrief_core.analyses.line_of_sight` for the method and its sources.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .batch import BatchParams
from .common import GeoJSON, InputModel, Issue, OutputModel
from .heights import DemInfo

__all__ = [
    "DEFAULT_HEIGHT_BY_ROLE",
    "DISCLAIMER_LINE_OF_SIGHT",
    "EARTH_RADIUS_M",
    "FREQUENCY_PRESETS_MHZ",
    "K_RADIO",
    "K_VISIBLE",
    "RADIO_GEOMETRY_NOTE",
    "UK_CAA_VLOS_M",
    "VISIBLE_REFRACTION_COEFFICIENT",
    "LineOfSightParams",
    "LineOfSightResult",
    "LosBelowThreshold",
    "LosGridInfo",
    "LosHiddenPatch",
    "LosObserver",
    "LosProfileSample",
    "LosRadioPath",
    "LosRadioSummary",
    "LosRouteSegment",
    "LosRouteSummary",
    "ObserverInput",
    "ObserverRole",
    "RadioTargetInput",
    "VlosGuidance",
]

EARTH_RADIUS_M = 6_371_000.0
"""Mean Earth radius R (m) used for the curvature drop d²/(2·k·R)."""

VISIBLE_REFRACTION_COEFFICIENT = 1.0 / 7.0
"""Refraction coefficient for visible light (≈ 0.143). GDAL ``gdal_viewshed`` corrects heights
with the curvature coefficient 1 − 1/7 = 0.85714 (its default ``-cc``), i.e. k = 1/(1 − 1/7)."""

K_VISIBLE = 1.0 / (1.0 - VISIBLE_REFRACTION_COEFFICIENT)
"""Effective Earth-radius factor for visible light, 7/6 ≈ 1.1667."""

K_RADIO = 4.0 / 3.0
"""Standard-atmosphere effective Earth-radius factor for radio (ITU-R P.453 / P.530 k = 4/3)."""

FREQUENCY_PRESETS_MHZ: tuple[float, ...] = (433.0, 868.0, 915.0, 2400.0, 5800.0)
"""Common licence-exempt command-and-control (C2) bands, MHz."""

UK_CAA_VLOS_M = 500.0
"""Commonly cited UK CAA (CAP 722) figure: VLOS normally accepted to 500 m horizontally."""

ObserverRole = Literal["pilot", "observer", "gcs"]

DEFAULT_HEIGHT_BY_ROLE: dict[str, float] = {"pilot": 1.7, "observer": 1.7, "gcs": 2.0}
"""Eye height (pilot, observer) or antenna height (ground station) above ground when not given."""

RADIO_GEOMETRY_NOTE = "Geometry only — not a link budget or coverage prediction."

DISCLAIMER_LINE_OF_SIGHT = (
    "Geometric line-of-sight evidence from the data-pack DEM, a coarse surface model (about 30 m, "
    "90 m in lite packs; the method states the resolution) that includes roofs and tree canopy and "
    "misses thin obstructions such as wires, poles and single trees. It does not show that visual line of sight can be kept in practice (eyesight, "
    "haze, contrast, aircraft size and lighting), and the radio figures are geometry only — not a "
    "link budget or coverage prediction. VLOS distances are non-regulatory guidance."
)


def _require_geometry(value: GeoJSON) -> GeoJSON:
    if not isinstance(value, dict) or "type" not in value:
        raise ValueError("geometry must be a GeoJSON object with a 'type'")
    return value


class ObserverInput(InputModel):
    """A remote pilot, a visual observer or a ground (control) station antenna."""

    name: str = Field(min_length=1, max_length=80)
    role: ObserverRole = "pilot"
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    height_agl_m: float | None = Field(default=None, ge=0, le=500)
    """Eye or antenna height above the ground; ``None`` = the role default (1.7 m, GCS 2.0 m)."""

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("name must not be blank")
        return text

    def eye_height_m(self) -> float:
        """The height used: ``height_agl_m`` or the role default."""
        return float(self.height_agl_m) if self.height_agl_m is not None else DEFAULT_HEIGHT_BY_ROLE[self.role]


class RadioTargetInput(InputModel):
    """A fixed far end for the radio path (a relay mast, a tower, the far end of a corridor)."""

    name: str = Field(default="Radio target", min_length=1, max_length=80)
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    height_agl_m: float | None = Field(default=None, ge=0, le=1500)
    """Antenna height above the ground; ``None`` = the target height of the aircraft."""


class LineOfSightParams(InputModel):
    geometry: GeoJSON
    observers: list[ObserverInput] = Field(default_factory=list, max_length=10)
    target_height_agl_m: float = Field(default=120.0, ge=0, le=1500)
    grid_m: float = Field(default=30.0, ge=5, le=500)
    max_range_m: float | None = Field(default=None, gt=0, le=50_000)
    k_visible: float = Field(default=K_VISIBLE, ge=0.5, le=100)
    k_radio: float = Field(default=K_RADIO, ge=0.5, le=100)
    frequency_mhz: float = Field(default=2400.0, ge=30, le=100_000)
    fresnel_clearance_pct: float = Field(default=60.0, ge=0, le=100)
    radio_target: RadioTargetInput | None = None
    radio_check: bool = True
    include_obstacles: bool = True
    vlos_aircraft: Literal["multirotor", "fixed_wing"] = "multirotor"
    characteristic_dimension_m: float | None = Field(default=None, gt=0, le=30)
    ground_visibility_m: float = Field(default=5000.0, ge=500, le=100_000)
    batch: BatchParams | None = None

    _check_geometry = field_validator("geometry")(_require_geometry)


class LosObserver(OutputModel):
    """One viewpoint and what it sees of the operation area at the target height."""

    name: str
    role: ObserverRole
    lon: float
    lat: float
    height_agl_m: float
    ground_m: float | None = None
    eye_amsl_m: float | None = None
    k_factor: float
    los_kind: Literal["visual", "radio"]
    visible_pct: float | None = None
    visible_area_m2: float | None = None
    max_visible_m: float | None = None
    assumed: bool = False


class LosHiddenPatch(OutputModel):
    """The largest connected group of area cells hidden from every pilot and observer."""

    area_m2: float
    cells: int
    lon: float
    lat: float


class LosRouteSegment(OutputModel):
    start_m: float
    end_m: float
    length_m: float
    visible: bool


class LosRouteSummary(OutputModel):
    """Visibility along the route (lineal parts of the geometry) at the target height."""

    length_m: float
    visible_length_m: float
    visible_pct: float
    max_continuous_visible_m: float
    hidden_segments: list[LosRouteSegment] = Field(default_factory=list)
    hidden_segments_truncated: bool = False


class LosProfileSample(OutputModel):
    """One sample of a radio path profile. Heights are metres above sea level."""

    d_m: float
    terrain_m: float | None = None
    obstacle_m: float | None = None
    los_m: float
    fresnel_lower_m: float
    fresnel_radius_m: float
    clearance_pct: float | None = None


class LosBelowThreshold(OutputModel):
    """A stretch of a radio path where the first Fresnel zone clearance is below the threshold."""

    start_m: float
    end_m: float
    min_clearance_pct: float


class LosRadioPath(OutputModel):
    from_name: str
    to_name: str
    from_lon: float
    from_lat: float
    to_lon: float
    to_lat: float
    from_height_agl_m: float
    to_height_agl_m: float
    from_amsl_m: float
    to_amsl_m: float
    distance_m: float
    slant_m: float
    fspl_db: float
    worst_clearance_pct: float | None = None
    worst_at_m: float | None = None
    max_fresnel_radius_m: float
    los_blocked: bool = False
    below_threshold: bool = False
    segments_below: list[LosBelowThreshold] = Field(default_factory=list)
    obstacles_on_path: int = 0
    nodata_samples: int = 0
    profile: list[LosProfileSample] = Field(default_factory=list)


class LosRadioSummary(OutputModel):
    frequency_mhz: float
    k_factor: float
    clearance_threshold_pct: float
    station_name: str
    station_role: ObserverRole
    paths: list[LosRadioPath] = Field(default_factory=list)
    paths_below_threshold: int = 0
    paths_blocked: int = 0
    worst_clearance_pct: float | None = None
    max_fspl_db: float | None = None
    note: str = RADIO_GEOMETRY_NOTE


class VlosGuidance(OutputModel):
    """Non-regulatory VLOS distance guidance (air-line distance pilot → aircraft)."""

    guidance_m: float
    basis: Literal["easa_alos_dlos", "uk_caa_500m"]
    aircraft: Literal["multirotor", "fixed_wing"]
    characteristic_dimension_m: float | None = None
    alos_m: float | None = None
    dlos_m: float | None = None
    ground_visibility_m: float
    uk_caa_m: float = UK_CAA_VLOS_M
    ring_radius_m: float
    reference: str


class LosGridInfo(OutputModel):
    grid_m: float
    requested_grid_m: float
    cols: int
    rows: int
    coarsened: bool = False
    area_cells: int
    nodata_cells: int = 0
    dem_source: str
    dem_resolution_m: float | None = None
    obstacles_used: int = 0


class LineOfSightResult(OutputModel):
    geometry_type: str
    target_height_agl_m: float
    observers: list[LosObserver] = Field(default_factory=list)
    combined_visible_pct: float | None = None
    visible_area_m2: float | None = None
    hidden_area_m2: float | None = None
    radio_los_pct: float | None = None
    largest_hidden_patch: LosHiddenPatch | None = None
    route: LosRouteSummary | None = None
    beyond_guidance_pct: float | None = None
    grid: LosGridInfo
    radio: LosRadioSummary | None = None
    vlos_guidance_m: float
    vlos_guidance: VlosGuidance
    method: str
    disclaimer: str = DISCLAIMER_LINE_OF_SIGHT
    issues: list[Issue] = Field(default_factory=list)
    layers: dict[str, GeoJSON] = Field(default_factory=dict)
    dem: DemInfo | None = None
