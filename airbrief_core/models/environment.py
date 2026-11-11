"""Parameters, results and presets of the environment module (sun, magnetic field, wind, EMI).

Everything here is *evidence*: times, angles, field values, estimated wind and distances. None of
it is a "fit to fly" verdict. See ``airbrief_core/analyses/environment.py`` for the methods.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .batch import BatchParams
from .common import GeoJSON, InputModel, Issue, OutputModel

__all__ = [
    "DAYLIGHT_PROFILES",
    "DISCLAIMER_ENVIRONMENT",
    "SHEAR_PRESETS",
    "WIND_LIMIT_PRESETS",
    "DaylightCheck",
    "DaylightProfile",
    "DaylightProfileSpec",
    "DaylightSegment",
    "EmiItem",
    "EmiKind",
    "EnvironmentParams",
    "EnvironmentResult",
    "MagneticSummary",
    "PlannedWindow",
    "ShearPreset",
    "SunEvent",
    "SunSummary",
    "SunTrackPoint",
    "SunWindow",
    "TimeSegment",
    "WindAtHeight",
    "WindLimitPreset",
]

DaylightProfile = Literal["india_daylight", "faa_107", "custom"]
SegmentKind = Literal["night", "civil_twilight", "day"]
SegmentStatus = Literal["allowed", "lighting", "outside"]
EmiKind = Literal["power_line", "minor_line", "cable", "substation", "plant", "comm_tower", "mast", "pylon"]

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class DaylightProfileSpec(OutputModel):
    """How a daylight profile treats day, civil twilight and night."""

    key: DaylightProfile
    label: str
    rule: str
    civil_twilight: SegmentStatus
    night: SegmentStatus


DAYLIGHT_PROFILES: dict[str, DaylightProfileSpec] = {
    "india_daylight": DaylightProfileSpec(
        key="india_daylight",
        label="Daylight only (sunrise to sunset)",
        rule=(
            "Flight only between sunrise and sunset, as Indian agricultural-spraying SOPs ask. "
            "Civil twilight and night count as outside the profile. Verify the SOP or permission "
            "that applies to your operation."
        ),
        civil_twilight="outside",
        night="outside",
    ),
    "faa_107": DaylightProfileSpec(
        key="faa_107",
        label="FAA Part 107 (US)",
        rule=(
            "14 CFR 107.29: in civil twilight the aircraft needs anti-collision lighting visible for "
            "at least 3 statute miles. Night is flagged: it also needs that lighting and the remote "
            "pilot's night knowledge test or training."
        ),
        civil_twilight="lighting",
        night="outside",
    ),
    "custom": DaylightProfileSpec(
        key="custom",
        label="Custom minimum sun elevation",
        rule="Time with the sun below the chosen elevation counts as outside the profile.",
        civil_twilight="outside",
        night="outside",
    ),
}
"""Daylight profiles (``DaylightProfile`` keys). The custom profile's boundary is
``min_sun_elevation_deg``; for the others it is sunrise/sunset (−0.833°) and civil twilight (−6°)."""


class ShearPreset(OutputModel):
    key: str
    label: str
    exponent: float
    source: str


SHEAR_PRESETS: dict[str, ShearPreset] = {
    "open_water": ShearPreset(
        key="open_water", label="Open water", exponent=0.11,
        source="Hsu, Meindl & Gilhousen (1994), J. Appl. Meteor. 33, 757–765: α ≈ 0.11 over the sea",
    ),
    "open_land": ShearPreset(
        key="open_land", label="Open land", exponent=1.0 / 7.0,
        source="The 1/7 power law for open, level terrain (Peterson & Hennessey, 1978, J. Appl. Meteor. 17, 390–394)",
    ),
    "suburbs": ShearPreset(
        key="suburbs", label="Suburbs / woodland", exponent=0.25,
        source="Terrain friction coefficients tabulated by Masters, Renewable and Efficient Electric Power Systems (Wiley, 2004): wooded/suburban ≈ 0.25",
    ),
    "city": ShearPreset(
        key="city", label="City with tall buildings", exponent=0.40,
        source="Terrain friction coefficients tabulated by Masters (2004) after Davenport (1960): large city ≈ 0.40",
    ),
}
"""Power-law shear exponents for common terrain (near-neutral stability; nights can be stronger)."""


class WindLimitPreset(OutputModel):
    key: str
    label: str
    wind_limit_ms: float


WIND_LIMIT_PRESETS: dict[str, WindLimitPreset] = {
    "sub250": WindLimitPreset(key="sub250", label="Sub-250 g multirotor (illustrative)", wind_limit_ms=10.7),
    "mapping_quad": WindLimitPreset(key="mapping_quad", label="Mapping quadcopter (illustrative)", wind_limit_ms=12.0),
    "heavy_lift": WindLimitPreset(key="heavy_lift", label="Heavy-lift multirotor (illustrative)", wind_limit_ms=12.0),
    "fixed_wing": WindLimitPreset(key="fixed_wing", label="Fixed-wing mapper (illustrative)", wind_limit_ms=15.0),
}
"""Illustrative rated wind limits, keyed like ``UAS_PRESETS``. Use the aircraft manual's value."""

DISCLAIMER_ENVIRONMENT = (
    "Evidence for planning, not a go/no-go decision. Sun times use the NOAA solar calculator "
    "equations; the magnetic field is the WMM2025 main-field model (local anomalies, magnetic "
    "storms and nearby steel are not included); wind at height is a power-law estimate from the "
    "surface value you entered, not a forecast; power lines and masts are proximity evidence, not "
    "an interference prediction."
)


def _require_geometry(value: GeoJSON) -> GeoJSON:
    if not isinstance(value, dict) or "type" not in value:
        raise ValueError("geometry must be a GeoJSON object with a 'type'")
    return value


class EnvironmentParams(InputModel):
    """Inputs of the environment analysis. Times are local wall-clock times on ``date`` in the
    zone ``utc_offset_min`` (minutes east of UTC); every computation is done in UTC."""

    geometry: GeoJSON
    # The NOAA spreadsheet equations (Meeus) are given for 1901–2099; far beyond any planned flight.
    date: dt.date = Field(ge=dt.date(1901, 1, 1), le=dt.date(2099, 12, 31))
    window_start: str | None = Field(default=None, description="Local start time HH:MM")
    window_end: str | None = Field(default=None, description="Local end time HH:MM (next day when earlier than the start)")
    utc_offset_min: int = Field(default=0, ge=-720, le=840)
    daylight_profile: DaylightProfile = "india_daylight"
    min_sun_elevation_deg: float = Field(default=0.0, ge=-18, le=60, description="Custom profile only")
    height_agl_m: float = Field(default=120.0, gt=0, le=1500)
    surface_wind_ms: float | None = Field(default=None, ge=0, le=75)
    gust_ms: float | None = Field(default=None, ge=0, le=100)
    reference_height_m: float = Field(default=10.0, gt=0, le=500)
    shear_exponent: float = Field(default=1.0 / 7.0, ge=0, le=0.6)
    wind_limit_ms: float | None = Field(default=None, gt=0, le=100)
    include_emi: bool = True
    emi_radius_m: float = Field(default=300.0, ge=10, le=5000)
    sun_min_deg: float = Field(default=30.0, ge=0, lt=90)
    sun_max_deg: float = Field(default=60.0, gt=0, le=90)
    batch: BatchParams | None = None

    _check_geometry = field_validator("geometry")(_require_geometry)

    @field_validator("window_start", "window_end")
    @classmethod
    def _hhmm(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not _HHMM.match(text):
            raise ValueError("expected a local time as HH:MM (00:00–23:59)")
        return text

    @model_validator(mode="after")
    def _consistent(self) -> EnvironmentParams:
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("give both window_start and window_end, or neither")
        if self.window_start is not None and self.window_start == self.window_end:
            raise ValueError("window_end must differ from window_start")
        if self.gust_ms is not None and self.surface_wind_ms is None:
            raise ValueError("gust_ms needs surface_wind_ms")
        if self.gust_ms is not None and self.surface_wind_ms is not None and self.gust_ms < self.surface_wind_ms:
            raise ValueError("gust_ms must be at least surface_wind_ms")
        if self.sun_min_deg >= self.sun_max_deg:
            raise ValueError("sun_min_deg must be below sun_max_deg")
        return self


class SunEvent(OutputModel):
    """An instant: UTC ISO time, local wall-clock time and its day relative to ``date``."""

    utc: str
    local: str
    day_offset: int = 0
    azimuth_deg: float | None = None
    elevation_deg: float | None = None


class SunTrackPoint(OutputModel):
    t: str
    minute: float
    """Minutes since local midnight of ``date``."""
    elevation_deg: float
    azimuth_deg: float


class SunSummary(OutputModel):
    sunrise: SunEvent | None = None
    sunset: SunEvent | None = None
    civil_dawn: SunEvent | None = None
    civil_dusk: SunEvent | None = None
    solar_noon: SunEvent
    polar_day: bool = False
    polar_night: bool = False
    max_elevation_deg: float
    daylight_minutes: float
    """Minutes of the local day with the sun above −0.833° (sunrise to sunset)."""
    track: list[SunTrackPoint] = Field(default_factory=list)


class TimeSegment(OutputModel):
    start: str
    end: str
    start_local: str
    end_local: str
    start_minute: float
    """Minutes since local midnight of ``date`` (may exceed 1440 for the next day)."""
    end_minute: float
    minutes: float


class DaylightSegment(TimeSegment):
    kind: SegmentKind
    status: SegmentStatus


class PlannedWindow(TimeSegment):
    crosses_midnight: bool = False


class DaylightCheck(OutputModel):
    profile: DaylightProfile
    profile_label: str
    rule: str
    min_elevation_deg: float | None = None
    window: PlannedWindow | None = None
    minutes_outside: float | None = None
    minutes_lighting: float | None = None
    segments: list[DaylightSegment] = Field(default_factory=list)
    """The local day (00:00–24:00) split into night / civil twilight / day."""
    window_segments: list[DaylightSegment] = Field(default_factory=list)
    statement: str


class MagneticSummary(OutputModel):
    declination_deg: float
    inclination_deg: float
    horizontal_nt: float
    total_nt: float
    north_nt: float
    east_nt: float
    down_nt: float
    declination_change_deg_per_year: float
    grid_variation_deg: float | None = None
    model: str = "WMM2025"
    model_epoch: float
    valid_until: str
    within_validity: bool
    altitude_m: float
    decimal_year: float
    blackout: bool
    caution: bool


class WindAtHeight(OutputModel):
    surface_wind_ms: float
    gust_ms: float | None = None
    reference_height_m: float
    height_agl_m: float
    shear_exponent: float
    factor: float
    wind_at_height_ms: float
    gust_at_height_ms: float | None = None
    wind_limit_ms: float | None = None
    margin_ms: float | None = None
    gust_margin_ms: float | None = None
    statement: str


class EmiItem(OutputModel):
    id: str
    kind: EmiKind
    name: str | None = None
    voltage_kv: float | None = None
    height_m: float | None = None
    distance_m: float
    lon: float
    lat: float
    """Point of the feature nearest to the analysed geometry."""
    inside: bool = False
    source: Literal["power", "obstacles"]


class SunWindow(OutputModel):
    min_deg: float
    max_deg: float
    segments: list[TimeSegment] = Field(default_factory=list)
    total_minutes: float
    reached: bool


class EnvironmentResult(OutputModel):
    point: list[float]
    """[lon, lat] where sun, magnetic field and wind are evaluated (the geometry's centroid)."""
    geometry_type: str
    date: str
    utc_offset_min: int
    utc_offset_label: str
    height_agl_m: float
    sun: SunSummary
    daylight: DaylightCheck
    magnetic: MagneticSummary
    wind: WindAtHeight | None = None
    emi: list[EmiItem] = Field(default_factory=list)
    emi_assessed: bool = False
    emi_radius_m: float | None = None
    emi_truncated: bool = False
    sun_window: SunWindow
    facts: list[str] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    method: str
    disclaimer: str
    layers: dict[str, GeoJSON] = Field(default_factory=dict)
