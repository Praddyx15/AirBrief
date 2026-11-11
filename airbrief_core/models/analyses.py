"""Parameter and result models for every analysis module.

Each module has a ``*Params`` (input, strict) and a ``*Result`` (output) model. Geometries are
GeoJSON dicts in WGS84. See docs/SPEC.md §3.6 for the algorithms behind each field.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .common import (
    SCHEMA_VERSION,
    GeoJSON,
    InputModel,
    Issue,
    NearestAerodrome,
    ObstacleHit,
    OutputModel,
    ProfilePoint,
    TerrainSummary,
    UserZone,
    Verdict,
    ZoneBasis,
    ZoneHit,
    ZoneKind,
    ZoneRules,
)
from .ground_risk_volume import (
    AssemblyHit,
    AssemblyInput,
    DensityGrid,
    IgrcTable,
    OperationalVolumeParams,
    OperationalVolumeResult,
)
from .batch import BatchParams
from .brief import EVIDENCE_NOTICE
from .environment import EnvironmentParams, EnvironmentResult
from .heights import CruiseReference, DemInfo, HeightReference, HeightSegment, TakeoffPoint
from .line_of_sight import LineOfSightParams, LineOfSightResult
from .presets import CAMERA_PRESETS, CameraSpec, ZoneProfile
from .survey import GcpEstimate, SprayParams, SprayResult, StorageEstimate, SurveyCoverage, SurveyMode
from .tracks import AuditSegment, AuditTrackInput, ShowAudienceBuffer, ShowPadSpacing, TrackFormat
from .zones import (
    MAX_DATASET_ZONES,
    MAX_ZONE_DATASETS,
    BandCeiling,
    BandEvidence,
    UnverifiedBand,
    ZoneLayerMapping,
    check_zone_inputs,
    check_zone_profile,
)

AnalysisKind = Literal[
    "airspace",
    "ground_risk",
    "evtol_siting",
    "evtol_corridor",
    "show",
    "survey",
    "flight_audit",
    "environment",
    "line_of_sight",
]
ANALYSIS_KINDS: tuple[str, ...] = (
    "airspace",
    "ground_risk",
    "evtol_siting",
    "evtol_corridor",
    "show",
    "survey",
    "flight_audit",
    "environment",
    "line_of_sight",
)


def _require_geometry(value: GeoJSON) -> GeoJSON:
    if not isinstance(value, dict) or "type" not in value:
        raise ValueError("geometry must be a GeoJSON object with a 'type'")
    return value


MAX_HEIGHT_ABOVE_GROUND_M = 1500.0
"""Largest planned height above ground or above take-off; AMSL levels may go higher (hills)."""


def _lonlat_or_none(value: tuple[float, float] | None) -> tuple[float, float] | None:
    """A ``[lon, lat]`` take-off point in WGS84 degrees (or None)."""
    if value is None:
        return None
    lon, lat = value
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise ValueError("expected [lon, lat] in WGS84 degrees")
    return value


def _check_height_for_reference(value: float | None, reference: str, field: str) -> None:
    """Heights above ground or take-off stay within MAX_HEIGHT_ABOVE_GROUND_M (AMSL may not)."""
    if value is not None and reference != "amsl" and value > MAX_HEIGHT_ABOVE_GROUND_M:
        raise ValueError(
            f"{field} is a height {'above take-off' if reference == 'takeoff' else 'above ground'} "
            f"here and must be at most {MAX_HEIGHT_ABOVE_GROUND_M:g} m (an AMSL level may be higher)"
        )


class AirspaceParams(InputModel):
    geometry: GeoJSON
    # The planned height, read in ``height_reference`` (above ground by default).
    altitude_agl_m: float = Field(default=120.0, gt=0, le=9000)
    zone_rules: ZoneRules | None = None
    user_zones: list[UserZone] = Field(default_factory=list, max_length=200)
    include_datapack_zones: bool = True
    grid: bool = True
    h3_resolution: int = Field(default=9, ge=6, le=11)
    max_grid_cells: int = Field(default=20_000, ge=100, le=200_000)
    profile_spacing_m: float = Field(default=30.0, ge=5, le=1000)
    obstacle_search_m: float = Field(default=300.0, ge=0, le=5000)
    vertical_clearance_m: float = Field(default=30.0, ge=0, le=500)
    # Height reference (airbrief_core/analyses/heights.py). "agl" keeps earlier results unchanged;
    # "takeoff"/"amsl" give terrain-true heights above ground along the flight.
    height_reference: HeightReference = "agl"
    takeoff: tuple[float, float] | None = None  # default: first vertex of a line, area centroid
    takeoff_elevation_m: float | None = Field(default=None, ge=-500, le=9000)
    min_agl_m: float = Field(default=30.0, ge=0, le=1500)
    # One result per feature (airbrief_core/batch.py); ``geometry`` is then only their extent.
    batch: BatchParams | None = None
    # Zone rules (airbrief_core/analyses/zones.py). Results stored without zone_profile were
    # computed with the conservative illustrative rules. dataset_zones is filled by the API from
    # zone_dataset_ids (whatever a request sends there is replaced).
    zone_profile: ZoneProfile = "dgca_2021"
    planned_start_utc: AwareDatetime | None = None
    planned_end_utc: AwareDatetime | None = None
    aerodrome_operational: dict[str, bool] = Field(default_factory=dict, max_length=200)
    zone_dataset_ids: list[str] = Field(default_factory=list, max_length=MAX_ZONE_DATASETS)
    zone_mappings: dict[str, ZoneLayerMapping] = Field(default_factory=dict, max_length=MAX_ZONE_DATASETS)
    dataset_zones: list[UserZone] = Field(default_factory=list, max_length=MAX_DATASET_ZONES)

    _check_geometry = field_validator("geometry")(_require_geometry)
    _check_takeoff = field_validator("takeoff")(_lonlat_or_none)

    @model_validator(mode="after")
    def _height_in_reference(self) -> AirspaceParams:
        _check_height_for_reference(self.altitude_agl_m, self.height_reference, "altitude_agl_m")
        return self

    @model_validator(mode="after")
    def _zone_inputs(self) -> AirspaceParams:
        check_zone_inputs(self)
        return self


class FlyabilityCell(OutputModel):
    h3: str
    zone: ZoneKind
    ceiling_agl_m: float | None = None
    terrain_m: float | None = None
    obstacle_max_height_m: float | None = None
    status: Literal["free", "limited", "permission", "restricted"]
    # Height above ground at the cell centre (above take-off / AMSL plans only; None for AGL plans).
    agl_m: float | None = None


class AirspaceResult(OutputModel):
    verdict: Verdict
    summary: str
    geometry_type: str
    area_m2: float | None = None
    length_m: float | None = None
    altitude_agl_m: float
    effective_ceiling_agl_m: float | None = None
    permission_required: bool
    max_terrain_m: float | None = None
    max_altitude_amsl_m: float | None = None
    nearest_aerodromes: list[NearestAerodrome] = Field(default_factory=list)
    zones: list[ZoneHit] = Field(default_factory=list)
    terrain: TerrainSummary | None = None
    profile: list[ProfilePoint] = Field(default_factory=list)
    obstacles: list[ObstacleHit] = Field(default_factory=list)
    grid: list[FlyabilityCell] = Field(default_factory=list)
    grid_resolution: int | None = None
    grid_truncated: bool = False
    # False when aerodrome zones could not be checked (no data pack, no aerodrome layer, or the
    # geometry outside the pack): the verdict is then never "pass". Stored results default to True.
    assessment_complete: bool = True
    issues: list[Issue] = Field(default_factory=list)
    disclaimer: str
    layers: dict[str, GeoJSON] = Field(default_factory=dict)
    # Terrain-true heights (airbrief_core/analyses/heights.py). ``altitude_agl_m`` above is the
    # planned value in ``height_reference``; the AGL range and stretches are along the flight.
    height_reference: HeightReference = "agl"
    min_agl_m: float | None = None
    takeoff: TakeoffPoint | None = None
    flight_level_amsl_m: float | None = None
    agl_min_m: float | None = None
    agl_max_m: float | None = None
    below_min_agl: list[HeightSegment] = Field(default_factory=list)
    above_ceiling: list[HeightSegment] = Field(default_factory=list)
    cells_below_min_agl: int | None = None
    cells_above_ceiling: int | None = None
    # False when a take-off/AMSL plan could not be related to the ground (no DEM): never "pass".
    heights_assessed: bool = True
    dem: DemInfo | None = None
    # The zone rule set used (None: stored before profiles, the conservative rules), the
    # aerodrome bands the flight reaches, zones out of the planned window or vertically clear,
    # and the notice every airspace result carries.
    zone_profile: str | None = None
    band_evidence: list[BandEvidence] = Field(default_factory=list)
    inactive_zones: list[ZoneHit] = Field(default_factory=list)
    assessment_banner: str | None = None


class GroundRiskParams(InputModel):
    geometry: GeoJSON
    max_dimension_m: float = Field(default=1.0, gt=0, le=60)
    max_speed_ms: float = Field(default=15.0, gt=0, le=250)
    takeoff_mass_kg: float | None = Field(default=None, gt=0, le=600_000)
    altitude_agl_m: float = Field(default=120.0, gt=0, le=1500)
    buffer_rule: Literal["one_to_one", "ballistic"] = "one_to_one"
    density_statistic: Literal["max", "p95"] = "max"
    controlled_ground_area: bool = False
    h3_resolution: int = Field(default=8, ge=6, le=10)
    max_cells: int = Field(default=20_000, ge=100, le=200_000)
    # SORA 2.5 operational volume (airbrief_core/models/ground_risk_volume.py). None keeps the
    # earlier footprint method (geometry + 1:1/ballistic buffer, H3 sampling) for old clients.
    volume: OperationalVolumeParams | None = None
    adjacent_area: bool = True
    assemblies: list[AssemblyInput] = Field(default_factory=list, max_length=100)
    grid_m: float | None = Field(default=None, ge=25, le=10_000)
    batch: BatchParams | None = None

    _check_geometry = field_validator("geometry")(_require_geometry)

    @model_validator(mode="after")
    def _volume_inputs(self) -> GroundRiskParams:
        if self.volume is None and self.assemblies:
            raise ValueError("assemblies are checked against the operational volume: give 'volume' too")
        if self.volume is None and self.grid_m is not None:
            raise ValueError("grid_m sets the operational-volume density grid: give 'volume' too")
        return self


class DensityCell(OutputModel):
    h3: str
    lon: float
    lat: float
    density_per_km2: float
    band: str


class GroundRiskResult(OutputModel):
    igrc: int | None = None
    in_scope: bool
    small_uas_rule: bool = False
    column: str
    density_band: str
    density_statistic: str
    max_density_per_km2: float
    p95_density_per_km2: float
    mean_density_per_km2: float
    population_in_footprint: float
    footprint_area_km2: float
    buffer_m: float
    hotspots: list[DensityCell] = Field(default_factory=list)
    cells: list[DensityCell] = Field(default_factory=list)
    cells_truncated: bool = False
    table_reference: str
    method: str
    disclaimer: str
    issues: list[Issue] = Field(default_factory=list)
    layers: dict[str, GeoJSON] = Field(default_factory=dict)
    # SORA 2.5 operational-volume evidence (absent in results of the earlier footprint method).
    evidence_label: str | None = None
    volume: OperationalVolumeResult | None = None
    density_grid: DensityGrid | None = None
    igrc_table: IgrcTable | None = None
    adjacent_mean_density_per_km2: float | None = None
    adjacent_people: float | None = None
    adjacent_area_km2: float | None = None
    adjacent_coverage: float | None = None  # covered fraction of the adjacent ring (0..1)
    assemblies: list[AssemblyHit] = Field(default_factory=list)


class SitingWeights(InputModel):
    demand: float = Field(default=0.35, ge=0, le=1)
    access: float = Field(default=0.20, ge=0, le=1)
    airspace: float = Field(default=0.20, ge=0, le=1)
    obstacles: float = Field(default=0.15, ge=0, le=1)
    slope: float = Field(default=0.10, ge=0, le=1)

    @model_validator(mode="after")
    def _non_zero(self) -> SitingWeights:
        if self.demand + self.access + self.airspace + self.obstacles + self.slope <= 0:
            raise ValueError("at least one weight must be greater than zero")
        return self


class EvtolSitingParams(InputModel):
    geometry: GeoJSON
    h3_resolution: int = Field(default=8, ge=6, le=9)
    weights: SitingWeights = Field(default_factory=SitingWeights)
    top_n: int = Field(default=10, ge=1, le=50)
    min_separation_km: float = Field(default=2.0, ge=0, le=50)
    demand_radius_km: float = Field(default=2.0, gt=0, le=20)
    access_radius_km: float = Field(default=3.0, gt=0, le=20)
    obstacle_radius_m: float = Field(default=500.0, gt=0, le=5000)
    max_slope_deg: float = Field(default=5.0, gt=0, le=45)
    exclude_water: bool = True
    exclude_red_zones: bool = True
    zone_rules: ZoneRules | None = None
    max_cells: int = Field(default=20_000, ge=100, le=200_000)
    # As airspace: stored without it, a run used the conservative rules.
    zone_profile: ZoneProfile = "dgca_2021"
    # the 0–5 km band to verify on Digital Sky: scored 0 (default) or excluded like a red zone
    unverified_band: UnverifiedBand = "permission"

    _check_geometry = field_validator("geometry")(_require_geometry)

    @model_validator(mode="after")
    def _zone_profile(self) -> EvtolSitingParams:
        check_zone_profile(self)
        return self


class SiteCandidate(OutputModel):
    rank: int
    h3: str
    lon: float
    lat: float
    score: float  # 0..100
    breakdown: dict[str, float]  # criterion -> 0..1
    elevation_m: float | None = None
    slope_deg: float | None = None
    population_nearby: float | None = None
    nearest_poi: str | None = None
    nearest_poi_km: float | None = None
    zone: ZoneKind
    notes: list[str] = Field(default_factory=list)
    # "verify_official_map": inside a band the published rule does not define (may be red)
    zone_basis: ZoneBasis = "rule"


class SitingCell(OutputModel):
    h3: str
    score: float | None = None  # 0..100, None when excluded
    excluded: bool = False
    reason: str | None = None


class EvtolSitingResult(OutputModel):
    candidates: list[SiteCandidate]
    cells: list[SitingCell] = Field(default_factory=list)
    cells_evaluated: int
    cells_excluded: int
    cells_truncated: bool = False
    weights: SitingWeights
    criteria: dict[str, str]  # criterion -> human description
    disclaimer: str
    issues: list[Issue] = Field(default_factory=list)
    layers: dict[str, GeoJSON] = Field(default_factory=dict)
    zone_profile: str | None = None  # None: stored before profiles (conservative rules)
    unverified_band: str | None = None  # None: stored before the choice ("permission")


class CorridorWeights(InputModel):
    ground_risk: float = Field(default=0.5, ge=0, le=5)
    airspace: float = Field(default=0.3, ge=0, le=5)
    obstacles: float = Field(default=0.2, ge=0, le=5)


class EvtolCorridorParams(InputModel):
    origin: tuple[float, float]
    destination: tuple[float, float]
    origin_name: str | None = None
    destination_name: str | None = None
    study_area: GeoJSON | None = None
    study_margin_km: float = Field(default=3.0, ge=0, le=50)
    h3_resolution: int = Field(default=8, ge=6, le=9)
    corridor_width_m: float = Field(default=300.0, gt=0, le=5000)
    cruise_altitude_agl_m: float = Field(default=300.0, gt=0, le=3000)
    obstacle_clearance_m: float = Field(default=50.0, ge=0, le=1000)
    cruise_speed_kmh: float = Field(default=150.0, gt=0, le=1000)
    energy_wh_per_km: float = Field(default=1000.0, gt=0, le=100_000)
    weights: CorridorWeights = Field(default_factory=CorridorWeights)
    avoid_red_zones: bool = True
    zone_rules: ZoneRules | None = None
    noise_level_db_at_ref: float = Field(default=75.0, ge=0, le=200)
    noise_ref_distance_m: float = Field(default=100.0, gt=0, le=10_000)
    noise_threshold_db: float = Field(default=55.0, ge=0, le=200)
    profile_spacing_m: float = Field(default=100.0, ge=10, le=2000)
    max_cells: int = Field(default=60_000, ge=100, le=300_000)
    # Cruise reference (airbrief_core/analyses/heights.py): "agl" (default) keeps earlier results
    # unchanged — the level is set by the highest ground; "amsl" flies the given level.
    cruise_reference: CruiseReference = "agl"
    cruise_altitude_amsl_m: float | None = Field(default=None, gt=0, le=12_000)
    origin_elevation_m: float | None = Field(default=None, ge=-500, le=9000)
    min_agl_m: float = Field(default=30.0, ge=0, le=3000)
    ceiling_agl_m: float | None = Field(default=None, gt=0, le=12_000)
    zone_profile: ZoneProfile = "dgca_2021"
    # the 0–5 km band: costed 1.0 (default) or impassable like a red zone
    unverified_band: UnverifiedBand = "permission"

    @field_validator("origin", "destination")
    @classmethod
    def _lonlat(cls, value: tuple[float, float]) -> tuple[float, float]:
        lon, lat = value
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            raise ValueError("expected [lon, lat] in WGS84 degrees")
        return value

    @model_validator(mode="after")
    def _amsl_level(self) -> EvtolCorridorParams:
        if self.cruise_reference == "amsl" and self.cruise_altitude_amsl_m is None:
            raise ValueError("cruise_reference 'amsl' needs cruise_altitude_amsl_m (the level above sea level)")
        check_zone_profile(self)
        return self


class NoiseCell(OutputModel):
    h3: str
    level_db: float
    population: float


class NoiseSummary(OutputModel):
    threshold_db: float
    footprint_half_width_m: float
    exposed_population: float
    exposed_area_km2: float
    cells: list[NoiseCell] = Field(default_factory=list)


class EvtolCorridorResult(OutputModel):
    route: GeoJSON  # LineString
    corridor: GeoJSON  # Polygon
    length_km: float
    straight_km: float
    detour_pct: float
    flight_time_min: float
    energy_kwh: float
    cruise_altitude_agl_m: float
    required_cruise_amsl_m: float | None = None
    max_terrain_m: float | None = None
    profile: list[ProfilePoint] = Field(default_factory=list)
    obstacles: list[ObstacleHit] = Field(default_factory=list)
    zones: list[ZoneHit] = Field(default_factory=list)
    noise: NoiseSummary
    max_density_per_km2: float | None = None
    mean_density_per_km2: float | None = None
    cells_considered: int
    disclaimer: str
    issues: list[Issue] = Field(default_factory=list)
    layers: dict[str, GeoJSON] = Field(default_factory=dict)
    # Terrain-true heights along the route (airbrief_core/analyses/heights.py).
    cruise_reference: CruiseReference = "agl"
    cruise_altitude_amsl_m: float | None = None  # the level flown with cruise_reference "amsl"
    min_agl_m: float | None = None
    ceiling_agl_m: float | None = None
    takeoff: TakeoffPoint | None = None  # the origin and its ground elevation
    agl_min_m: float | None = None
    agl_max_m: float | None = None
    below_min_agl: list[HeightSegment] = Field(default_factory=list)
    above_ceiling: list[HeightSegment] = Field(default_factory=list)
    heights_assessed: bool = True
    dem: DemInfo | None = None
    zone_profile: str | None = None  # None: stored before profiles (conservative rules)
    unverified_band: str | None = None  # None: stored before the choice ("permission")


class TrackInput(InputModel):
    """One drone's trajectory. ``coordinates`` are ``[lon, lat, alt_m]`` aligned with ``times``
    (at most 500,000 samples, as for an audited track)."""

    id: str = Field(min_length=1, max_length=80)
    times: list[float] = Field(min_length=2, max_length=500_000)
    coordinates: list[list[float]] = Field(min_length=2, max_length=500_000)

    @model_validator(mode="after")
    def _aligned(self) -> TrackInput:
        if len(self.times) != len(self.coordinates):
            raise ValueError("times and coordinates must have the same length")
        for c in self.coordinates:
            if len(c) < 3:
                raise ValueError("each coordinate must be [lon, lat, alt_m]")
        return self


class ShowParams(InputModel):
    tracks: list[TrackInput] = Field(min_length=1, max_length=5000)
    # lon, lat[, ground altitude AMSL]; without the altitude, absolute tracks are measured from the
    # lowest take-off position (airbrief_core.analyses.shows, step 1).
    origin: tuple[float, float, float] | tuple[float, float] | None = None
    altitude_reference: Literal["relative", "absolute"] = "relative"
    min_separation_m: float = Field(default=1.5, gt=0, le=100)
    max_horizontal_speed_ms: float = Field(default=8.0, gt=0, le=100)
    max_climb_rate_ms: float = Field(default=4.0, gt=0, le=50)
    max_descent_rate_ms: float = Field(default=3.0, gt=0, le=50)
    max_acceleration_ms2: float = Field(default=4.0, gt=0, le=100)
    sample_rate_hz: float = Field(default=10.0, ge=1, le=50)
    dynamics_window_s: float = Field(default=1.0, gt=0, le=10)
    airborne_threshold_m: float = Field(default=1.0, ge=0, le=50)
    ceiling_m: float = Field(default=120.0, gt=0, le=2000)
    show_box: GeoJSON | None = None
    audience: GeoJSON | None = None
    audience_buffer_m: float = Field(default=50.0, ge=0, le=2000)
    playback_rate_hz: float = Field(default=4.0, ge=0.5, le=20)
    max_events: int = Field(default=200, ge=1, le=10_000)
    # Site and pad checks (airbrief_core.analyses.shows, steps 6–7)
    show_box_margin_m: float = Field(default=5.0, gt=0, le=500)
    audience_buffer_rule: Literal["fixed", "one_to_one", "ballistic"] = "fixed"
    audience_breach_margin_pct: float = Field(default=0.0, ge=0, le=500)
    min_pad_spacing_m: float = Field(default=1.5, gt=0, le=100)
    endurance_s: float | None = Field(default=None, gt=0, le=36_000)
    min_navigation_altitude_m: float | None = Field(default=None, gt=0, le=500)
    low_altitude_speed_tolerance_ms: float = Field(default=0.5, gt=0, le=20)

    @field_validator("origin")
    @classmethod
    def _origin(cls, value: tuple[float, ...] | None) -> tuple[float, ...] | None:
        # JSON bodies may carry NaN/Infinity: the position must be WGS84 and the altitude real.
        if value is None:
            return None
        _lonlat_or_none((value[0], value[1]))
        if len(value) > 2 and not -500.0 <= value[2] <= 9000.0:
            raise ValueError("the origin's ground altitude must be -500 to 9000 m AMSL")
        return value


class ShowConflict(OutputModel):
    drone_a: str
    drone_b: str
    start_s: float
    end_s: float
    t_min_s: float
    min_distance_m: float
    position_enu: list[float]  # midpoint of the pair at closest approach


class ShowViolation(OutputModel):
    kind: Literal[
        "speed_h", "climb", "descent", "acceleration", "geofence", "audience", "ceiling",
        "endurance", "low_altitude_motion",
    ]
    drone: str
    start_s: float
    end_s: float
    peak_value: float
    limit: float


class DroneStats(OutputModel):
    drone: str
    airborne_s: float
    distance_m: float
    max_altitude_m: float
    max_horizontal_speed_ms: float
    max_climb_rate_ms: float
    max_descent_rate_ms: float
    max_acceleration_ms2: float


class ShowFrames(OutputModel):
    """Downsampled positions for playback, in metres east/north/up from ``origin``."""

    origin: list[float]  # [lon, lat, alt]
    times: list[float]
    drone_ids: list[str]
    positions: list[list[list[float]]]  # [frame][drone] -> [e, n, u]


class ShowSiteChecks(OutputModel):
    show_box_area_m2: float | None = None
    audience_area_m2: float | None = None
    box_audience_distance_m: float | None = None
    box_overlaps_audience_buffer: bool = False
    show_box_source: Literal["given", "derived"] | None = None
    show_box_margin_m: float | None = None  # only for a box derived from the tracks
    audience_buffer: ShowAudienceBuffer | None = None


class ShowResult(OutputModel):
    verdict: Verdict
    drone_count: int
    duration_s: float
    sample_rate_hz: float
    frames_checked: int
    min_separation_m: float | None = None
    min_separation_at_s: float | None = None
    min_separation_pair: list[str] | None = None
    conflicts: list[ShowConflict] = Field(default_factory=list)
    violations: list[ShowViolation] = Field(default_factory=list)
    events_truncated: bool = False
    drones: list[DroneStats] = Field(default_factory=list)
    site: ShowSiteChecks
    frames: ShowFrames
    backend: Literal["cpp", "python"]
    issues: list[Issue] = Field(default_factory=list)
    layers: dict[str, GeoJSON] = Field(default_factory=dict)
    altitude_reference: Literal["relative", "absolute"] = "relative"
    origin_source: Literal["given", "inferred"] | None = None
    max_altitude_m: float | None = None  # greatest height above the origin, any drone
    pads: ShowPadSpacing | None = None


class SurveyParams(InputModel):
    geometry: GeoJSON
    camera: str | CameraSpec = "p4rtk"
    # The planned height, read in ``height_reference`` (above ground by default).
    altitude_agl_m: float | None = Field(default=None, gt=0, le=9000)
    target_gsd_cm: float | None = Field(default=None, gt=0, le=100)
    front_overlap_pct: float = Field(default=80.0, ge=0, lt=100)
    side_overlap_pct: float = Field(default=70.0, ge=0, lt=100)
    speed_ms: float = Field(default=10.0, gt=0, le=60)
    battery_minutes: float = Field(default=25.0, gt=0, le=600)
    battery_swap_minutes: float = Field(default=5.0, ge=0, le=120)
    turn_seconds: float = Field(default=8.0, ge=0, le=300)
    min_trigger_interval_s: float = Field(default=2.0, ge=0, le=60)
    max_altitude_agl_m: float = Field(default=120.0, gt=0, le=1500)
    direction_deg: float | None = Field(default=None, ge=0, lt=180)
    orientation: Literal["landscape", "portrait"] = "landscape"
    # Height reference and take-off (airbrief_core/analyses/survey.py, step 10). "agl" assumes
    # terrain following and keeps earlier results unchanged.
    height_reference: HeightReference = "agl"
    takeoff: tuple[float, float] | None = None  # default: the area centroid / corridor start
    takeoff_elevation_m: float | None = Field(default=None, ge=-500, le=9000)
    # Area or corridor along a centreline (step 11)
    coverage: SurveyCoverage = "area"
    corridor_width_m: float | None = Field(default=None, gt=0, le=5000)
    # Battery reserve and transit legs from the take-off point (step 12)
    battery_reserve_pct: float = Field(default=0.0, ge=0, le=90)
    transit_legs: bool = False
    transit_speed_ms: float | None = Field(default=None, gt=0, le=60)
    # Ground control points and storage (step 13)
    gcp_min: int = Field(default=5, ge=0, le=1000)
    gcp_ha_per_extra: float = Field(default=10.0, gt=0, le=100_000)
    gcp_corridor_spacing_m: float = Field(default=300.0, gt=0, le=100_000)
    photo_mb: float | None = Field(default=None, gt=0, le=2000)
    # Spraying effort mode (step 14): effort only, the lines stay a visual aid
    mode: SurveyMode = "mapping"
    spray: SprayParams | None = None
    batch: BatchParams | None = None

    _check_geometry = field_validator("geometry")(_require_geometry)
    _check_takeoff = field_validator("takeoff")(_lonlat_or_none)

    @model_validator(mode="after")
    def _modes(self) -> SurveyParams:
        if self.coverage == "corridor" and self.corridor_width_m is None:
            raise ValueError("coverage 'corridor' needs corridor_width_m (the full width mapped along the line)")
        if self.mode == "spraying" and self.spray is None:
            self.spray = SprayParams()
        if self.mode == "spraying" and self.height_reference != "agl":
            raise ValueError("spraying is planned at a height above the crop: use height_reference 'agl'")
        _check_height_for_reference(self.altitude_agl_m, self.height_reference, "altitude_agl_m")
        return self

    @field_validator("camera")
    @classmethod
    def _known_camera(cls, value: str | CameraSpec) -> str | CameraSpec:
        if isinstance(value, str) and value not in CAMERA_PRESETS:
            raise ValueError(f"unknown camera preset '{value}'; known: {', '.join(CAMERA_PRESETS)}")
        return value

    @model_validator(mode="after")
    def _one_height_mode(self) -> SurveyParams:
        if self.altitude_agl_m is not None and self.target_gsd_cm is not None:
            raise ValueError("give either altitude_agl_m or target_gsd_cm, not both")
        if self.altitude_agl_m is None and self.target_gsd_cm is None:
            self.altitude_agl_m = 100.0
        return self


class SurveyBlock(OutputModel):
    index: int
    area_ha: float
    line_km: float
    minutes: float
    photos: int
    polygon: GeoJSON
    # Transit from / back to the take-off point (with ``transit_legs``), metres.
    transit_in_m: float | None = None
    transit_out_m: float | None = None
    # Ground under the block (with a DEM) and what it does to the height and GSD.
    terrain_min_m: float | None = None
    terrain_max_m: float | None = None
    agl_min_m: float | None = None
    agl_max_m: float | None = None
    gsd_min_cm: float | None = None
    gsd_max_cm: float | None = None
    litres: float | None = None  # spraying: the block's share of the spray mix


class SurveyResult(OutputModel):
    # The camera fields are None in spraying mode (``mode == "spraying"``: no photos are taken).
    camera: CameraSpec | None = None
    gsd_cm: float | None = None
    gsd_along_cm: float | None = None
    altitude_agl_m: float
    max_altitude_agl_m: float | None = None  # the height limit checked (``altitude_above_limit``)
    footprint_across_m: float | None = None
    footprint_along_m: float | None = None
    line_spacing_m: float
    trigger_distance_m: float | None = None
    trigger_interval_s: float | None = None
    direction_deg: float
    line_count: int
    segment_count: int
    line_km: float
    photos: int
    flight_minutes: float
    batteries: int
    total_minutes: float
    area_ha: float
    ha_per_hour: float
    blocks: list[SurveyBlock] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    layers: dict[str, GeoJSON] = Field(default_factory=dict)
    # Mode and coverage (airbrief_core/analyses/survey.py, steps 11 and 14)
    mode: SurveyMode = "mapping"
    coverage: SurveyCoverage = "area"
    corridor_width_m: float | None = None
    centreline_km: float | None = None
    # Terrain-true heights (step 10). ``altitude_agl_m`` is the height above the take-off ground
    # the layout uses; the ranges come from the DEM under each block.
    height_reference: HeightReference = "agl"
    takeoff: TakeoffPoint | None = None
    flight_level_amsl_m: float | None = None
    heights_assessed: bool = True
    dem: DemInfo | None = None
    terrain_min_m: float | None = None
    terrain_max_m: float | None = None
    relief_m: float | None = None
    agl_min_m: float | None = None
    agl_max_m: float | None = None
    gsd_min_cm: float | None = None
    gsd_max_cm: float | None = None
    worst_side_overlap_pct: float | None = None
    worst_front_overlap_pct: float | None = None
    heights_note: str | None = None
    # Batteries, transit, GCPs, storage and spraying (steps 12–14)
    battery_reserve_pct: float = 0.0
    usable_battery_minutes: float | None = None
    transit_km: float | None = None
    transit_minutes: float | None = None
    gcp: GcpEstimate | None = None
    storage: StorageEstimate | None = None
    spray: SprayResult | None = None
    method: str | None = None


class FlightAuditParams(InputModel):
    """A flight to audit: a FlightMD ``report`` or a plain position ``track`` (exactly one).

    ``track_heights`` says how a track's altitudes are read (``auto``: from its altitude mode).
    The extra checks run only when their parameter is given: ``training_box`` (a polygon the
    flight should stay inside) and ``max_distance_from_home_m``.
    """

    report: dict[str, Any] | None = None
    track: AuditTrackInput | None = None
    track_heights: Literal["auto", "amsl", "above_ground", "above_takeoff"] = "auto"
    training_box: GeoJSON | None = None
    training_box_name: str | None = Field(default=None, max_length=200)
    max_distance_from_home_m: float | None = Field(default=None, gt=0, le=100_000)
    ceiling_agl_m: float = Field(default=120.0, gt=0, le=1500)
    calibration: Literal["takeoff", "none"] = "takeoff"
    use_terrain: bool = True
    zone_rules: ZoneRules | None = None
    user_zones: list[UserZone] = Field(default_factory=list, max_length=200)
    include_datapack_zones: bool = True
    footprint_buffer_m: float = Field(default=50.0, ge=0, le=2000)
    flightmd_url: str | None = Field(default=None, max_length=2000)
    # The airspace rule sets (airbrief_core/analyses/zones.py); stored without it, the conservative rules.
    zone_profile: ZoneProfile = "dgca_2021"
    # above a band's ceiling with yellow above it: an exceedance (default) or permission required
    band_ceiling: BandCeiling = "exceedance"

    @field_validator("flightmd_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        """Only absolute http(s) links: the UI renders this as a link ("Open in FlightMD")."""
        if value is None or not value.strip():
            return None
        text = value.strip()
        parts = urlsplit(text)
        if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
            raise ValueError("flightmd_url must be an absolute http(s) URL with a host")
        if parts.username or parts.password or any(ch.isspace() for ch in text):
            raise ValueError("flightmd_url must not contain credentials or spaces")
        return text

    @field_validator("training_box")
    @classmethod
    def _training_box_geometry(cls, value: GeoJSON | None) -> GeoJSON | None:
        return None if value is None else _require_geometry(value)

    @model_validator(mode="after")
    def _one_source(self) -> FlightAuditParams:
        if self.report is None and self.track is None:
            raise ValueError("give a FlightMD report (report or report_url) or a position track (track or a dataset)")
        if self.report is not None and self.track is not None:
            raise ValueError("give either a FlightMD report or a position track, not both")
        check_zone_profile(self)
        return self


class FlightInfo(OutputModel):
    """What was audited. FlightMD fields stay ``None`` for a position track and vice versa."""

    report_id: str | None = None
    file_name: str | None = None
    schema_version: str | None = None
    location_name: str | None = None
    log_start_utc: str | None = None
    overall_score: float | None = None
    letter_grade: str | None = None
    points: int
    flightmd_url: str | None = None
    source: Literal["flightmd", "track"] = "flightmd"
    track_name: str | None = None
    track_format: TrackFormat | None = None
    altitude_mode: str | None = None
    feature_index: int | None = None
    duration_s: float | None = None
    # FlightMD schema 1.6/1.7 (None or empty for older reports and for tracks)
    armed_windows: list[list[float]] | None = None
    """``[start_s, end_s]`` pairs in log-relative seconds, from the arming state."""
    parse_warnings: list[str] = Field(default_factory=list)
    """What FlightMD's parser had to skip or guess."""
    planning_brief_sha256: str | None = None
    """``provenance.brief_sha256`` of the AirBrief brief the flight was planned with, as typed
    into FlightMD's upload form."""
    log_sha256: str | None = None
    source_format: str | None = None
    airframe_name: str | None = None
    gps_times: bool = False
    """True when the report gave a time per GPS point (``metadata.gps_path_t_s``), so findings
    can be placed on the track."""


class AuditExceedance(OutputModel):
    start_index: int
    end_index: int
    start_distance_m: float
    end_distance_m: float
    length_m: float
    max_agl_m: float
    limit_m: float
    reason: str
    start_time_s: float | None = None  # tracks with times only
    end_time_s: float | None = None
    duration_s: float | None = None


FindingPlacement = Literal["placed", "no_time", "no_track_times", "outside_track"]
FindingSeverity = Literal["critical", "warning", "info", "good"]


class AuditFinding(OutputModel):
    """A FlightMD finding with its time window (schema 1.6+) and, when the report has GPS times,
    where on the track it started: position, height above ground, the limit and the zone there.

    ``armed`` is None when the report has no armed windows or the finding has no time.
    """

    id: str
    title: str
    severity: FindingSeverity = "info"
    category: str | None = None
    kind: str | None = None
    params: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    t_start_s: float | None = None
    t_end_s: float | None = None
    armed: bool | None = None
    placement: FindingPlacement = "no_time"
    lon: float | None = None
    lat: float | None = None
    distance_m: float | None = None
    agl_m: float | None = None
    limit_agl_m: float | None = None
    zone: ZoneKind | None = None
    zone_name: str | None = None


class FlightAuditResult(OutputModel):
    verdict: Verdict
    flight: FlightInfo
    altitude_reference: Literal["terrain_calibrated", "terrain_raw", "takeoff_relative", "recorded_agl"]
    calibration_offset_m: float | None = None
    distance_m: float
    max_agl_m: float
    max_height_above_takeoff_m: float
    ceiling_agl_m: float
    profile: list[ProfilePoint] = Field(default_factory=list)
    exceedances: list[AuditExceedance] = Field(default_factory=list)
    zones: list[ZoneHit] = Field(default_factory=list)
    nearest_aerodrome: NearestAerodrome | None = None
    min_aerodrome_distance_km: float | None = None
    overflown_population: float | None = None
    max_density_per_km2: float | None = None
    footprint_area_km2: float | None = None
    disclaimer: str
    issues: list[Issue] = Field(default_factory=list)
    layers: dict[str, GeoJSON] = Field(default_factory=dict)
    # False when no data pack (or one without aerodromes) covers every point: zones "not assessed".
    assessment_complete: bool = True
    # False for a 2D position track (no altitudes): heights and the height limit were not checked.
    heights_assessed: bool = True
    duration_s: float | None = None
    time_above_limit_s: float | None = None
    max_home_distance_m: float | None = None
    training_box_name: str | None = None
    area_exits: list[AuditSegment] | None = None  # None: the check was not asked for
    time_outside_area_s: float | None = None
    max_distance_from_home_m: float | None = None  # the limit checked
    distance_exceedances: list[AuditSegment] | None = None
    time_beyond_distance_s: float | None = None
    dem: DemInfo | None = None  # the terrain model the heights were read from (None: no terrain used)
    # As airspace: the rule set (None: stored before profiles, the conservative rules) and the
    # aerodrome bands the track's fixes fall in, with the highest height flown in each.
    zone_profile: str | None = None
    band_evidence: list[BandEvidence] = Field(default_factory=list)
    # The band_ceiling choice (None: stored before it, an exceedance) and, with "permission", the
    # runs of fixes flown in the yellow zone above a band's ceiling (limit_m is that ceiling).
    band_ceiling: str | None = None
    above_band_ceiling: list[AuditExceedance] | None = None
    # FlightMD findings with times (schema 1.6+), placed on the track when it has GPS times.
    findings: list[AuditFinding] = Field(default_factory=list)


PARAMS_MODELS: dict[str, type[InputModel]] = {
    "airspace": AirspaceParams,
    "ground_risk": GroundRiskParams,
    "evtol_siting": EvtolSitingParams,
    "evtol_corridor": EvtolCorridorParams,
    "show": ShowParams,
    "survey": SurveyParams,
    "flight_audit": FlightAuditParams,
    "environment": EnvironmentParams,
    "line_of_sight": LineOfSightParams,
}

RESULT_MODELS: dict[str, type[OutputModel]] = {
    "airspace": AirspaceResult,
    "ground_risk": GroundRiskResult,
    "evtol_siting": EvtolSitingResult,
    "evtol_corridor": EvtolCorridorResult,
    "show": ShowResult,
    "survey": SurveyResult,
    "flight_audit": FlightAuditResult,
    "environment": EnvironmentResult,
    "line_of_sight": LineOfSightResult,
}


class AnalysisEnvelope(OutputModel):
    """Stable wrapper around any analysis result, suitable for export and for UAOP ingestion."""

    schema_version: str = SCHEMA_VERSION
    airbrief_version: str
    kind: AnalysisKind
    params: dict[str, Any]
    result: dict[str, Any]
    datapack: str | None = None
    datapack_synthetic: bool = False
    # content_sha256 of the pack the run read, when its files matched their hashes
    datapack_sha256: str | None = None
    backend: Literal["cpp", "python"] = "python"
    started_utc: str
    finished_utc: str
    duration_ms: int
    # Evidence labels carried by every export (airbrief_core/models/brief.py): the notice always,
    # the watermark when the analysis read a synthetic data pack (airbrief_core.brief).
    notice: str = EVIDENCE_NOTICE
    watermark: str | None = None
