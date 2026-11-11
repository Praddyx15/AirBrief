"""Shared model primitives used across datasets and analyses.

Conventions (see docs/SPEC.md §1):
* Coordinates are GeoJSON order: ``[lon, lat]`` or ``[lon, lat, z]``, WGS84 unless stated.
* Units are SI: metres, square metres, seconds, metres per second. Angles in degrees.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"
"""Version of the AirBrief result schema. Bump the minor for additive changes, the major for breaking ones."""

GeoJSON = dict[str, Any]
"""A GeoJSON object (Geometry, Feature or FeatureCollection) as a plain dict."""

ZoneKind = Literal["green", "yellow", "red"]
VerticalReference = Literal["AGL", "AMSL", "WGS84"]
ZoneBasis = Literal["rule", "verify_official_map"]
DistanceFrom = Literal["perimeter", "runway_hull", "reference_point"]
WeekDay = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN", "ANY"]

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")


class InputModel(BaseModel):
    """Base class for user-supplied parameters. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class OutputModel(BaseModel):
    """Base class for results. Unknown fields are ignored when parsing."""

    model_config = ConfigDict(extra="ignore")


class FileStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AnalysisStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Verdict(StrEnum):
    PASS = "pass"
    CAUTION = "caution"
    FAIL = "fail"


class Issue(OutputModel):
    """A non-fatal problem or note attached to a dataset, feature or analysis."""

    code: str
    message: str
    severity: Severity = Severity.WARNING
    feature_index: int | None = None


class BBox(OutputModel):
    """Bounding box in WGS84 degrees."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @classmethod
    def from_bounds(cls, bounds: tuple[float, float, float, float] | list[float]) -> BBox:
        """Build from ``(min_lon, min_lat, max_lon, max_lat)`` (shapely ``.bounds`` order)."""
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bounds)
        return cls(min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)

    def as_list(self) -> list[float]:
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

    def union(self, other: BBox) -> BBox:
        return BBox(
            min_lon=min(self.min_lon, other.min_lon),
            min_lat=min(self.min_lat, other.min_lat),
            max_lon=max(self.max_lon, other.max_lon),
            max_lat=max(self.max_lat, other.max_lat),
        )

    def contains(self, other: BBox) -> bool:
        return (
            self.min_lon <= other.min_lon
            and self.min_lat <= other.min_lat
            and self.max_lon >= other.max_lon
            and self.max_lat >= other.max_lat
        )

    def intersects(self, other: BBox) -> bool:
        return not (
            other.min_lon > self.max_lon
            or other.max_lon < self.min_lon
            or other.min_lat > self.max_lat
            or other.max_lat < self.min_lat
        )

    def expanded(self, degrees: float) -> BBox:
        return BBox(
            min_lon=self.min_lon - degrees,
            min_lat=max(-90.0, self.min_lat - degrees),
            max_lon=self.max_lon + degrees,
            max_lat=min(90.0, self.max_lat + degrees),
        )


class ZoneRing(InputModel):
    """A distance band around an aerodrome, ``inner_km <= distance < outer_km``.

    The distance is measured as ``ZoneRules.distance_from`` says: from the aerodrome perimeter
    (its mapped boundary, else the hull of its runways, else the reference point minus
    ``AerodromeZoneRule.assumed_radius_km``), or from the reference point minus that radius.
    ``ceiling_agl_m = None`` means flight needs permission (no free-flight ceiling). With
    ``ceiling_exceeded_zone`` the band is ``zone`` up to the ceiling and that zone above it
    (e.g. green to 60 m, yellow above). ``verify_official_map`` marks a band the published rule
    does not define: it always needs permission and the official map decides.
    """

    zone: ZoneKind
    inner_km: float = Field(ge=0)
    outer_km: float = Field(gt=0)
    ceiling_agl_m: float | None = Field(default=None, ge=0)
    label: str
    ceiling_exceeded_zone: ZoneKind | None = None
    verify_official_map: bool = False

    @model_validator(mode="after")
    def _check_order(self) -> ZoneRing:
        if self.outer_km <= self.inner_km:
            raise ValueError("outer_km must be greater than inner_km")
        if self.ceiling_exceeded_zone is not None and self.ceiling_agl_m is None:
            raise ValueError("ceiling_exceeded_zone needs ceiling_agl_m")
        return self


class AerodromeZoneRule(InputModel):
    """Rings applied to aerodromes of the listed OurAirports ``type`` values.

    At most 20 types and 20 rings (OurAirports has 7 types; the defaults use 2 or 3 rings).
    ``scheduled_service_only``: only aerodromes with scheduled service count as operational
    (the OurAirports ``scheduled_service`` column), unless the analysis says otherwise.
    """

    aerodrome_types: list[str] = Field(min_length=1, max_length=20)
    assumed_radius_km: float = Field(default=0.0, ge=0, le=10)
    rings: list[ZoneRing] = Field(min_length=1, max_length=20)
    scheduled_service_only: bool = False


class ZoneRules(InputModel):
    """Configurable, illustrative airspace rules. Not an official airspace map.

    At most 50 rules (the presets have 2 to 4). ``distance_from`` says where the bands are
    measured from (see :class:`ZoneRing`); ``profile`` names a preset (``custom`` otherwise).
    Outside every zone ``default_zone`` applies up to ``default_ceiling_agl_m``, and
    ``default_ceiling_exceeded_zone`` (if set) above it.
    """

    rules: list[AerodromeZoneRule] = Field(max_length=50)
    default_zone: ZoneKind = "green"
    default_ceiling_agl_m: float = Field(default=120.0, ge=0)
    default_ceiling_exceeded_zone: ZoneKind | None = None
    reference: str = ""
    distance_from: Literal["perimeter", "reference_point"] = "perimeter"
    profile: str = Field(default="custom", max_length=40)


class ZoneSchedule(InputModel):
    """A daily period in which a zone is active, ``HH:MM`` in UTC. An ``end`` at or before
    ``start`` runs past midnight; ``ANY`` is every day."""

    days: list[WeekDay] = Field(min_length=1, max_length=8)
    start: str = "00:00"
    end: str = "24:00"

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        if not _HHMM.match(value):
            raise ValueError("expected a UTC time as HH:MM (00:00–24:00)")
        return value


class UserZone(InputModel):
    """A zone supplied by the user, a dataset or a data pack: restricted area, geofence, etc.

    Optional, all off by default: a validity period (``valid_from``/``valid_to``, UTC, ``valid_to``
    exclusive) with daily ``schedule`` periods, and a vertical band (``lower_limit_m`` up to
    ``upper_limit_m``, ``None`` = from the ground / unlimited) in ``vertical_reference``.
    ``restriction``, ``authority`` and ``reason`` keep what an ED-269/ED-318 file said.
    """

    name: str = Field(min_length=1, max_length=120)
    zone: ZoneKind
    geometry: GeoJSON
    ceiling_agl_m: float | None = Field(default=None, ge=0, le=10000)
    note: str | None = None
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    schedule: list[ZoneSchedule] = Field(default_factory=list, max_length=20)
    lower_limit_m: float | None = Field(default=None, ge=-1000, le=60_000)
    upper_limit_m: float | None = Field(default=None, ge=-1000, le=60_000)
    vertical_reference: VerticalReference = "AGL"
    restriction: str | None = Field(default=None, max_length=40)
    source_format: Literal["user", "ed269", "ed318"] = "user"
    authority: str | None = Field(default=None, max_length=300)
    reason: str | None = Field(default=None, max_length=300)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def _ordered(self) -> UserZone:
        if self.vertical_reference == "AGL" and self.lower_limit_m is not None and self.lower_limit_m <= 0:
            self.lower_limit_m = None  # from the ground
        if self.valid_from is not None and self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        if self.lower_limit_m is not None and self.upper_limit_m is not None and self.upper_limit_m <= self.lower_limit_m:
            raise ValueError("upper_limit_m must be above lower_limit_m")
        return self


class ZoneHit(OutputModel):
    """A zone touched by the analysed geometry.

    ``active``: in the planned window (``None`` when no window was given, or the zone has no
    validity limits and no window applies). ``agl_margin_m`` is the free-flight ceiling (or the
    zone's lower limit) minus the planned height above ground: negative means above it.
    ``basis`` is ``verify_official_map`` for a band the published rule does not define;
    ``distance_from`` says how an aerodrome band was measured.
    """

    source: Literal["aerodrome", "user", "datapack"]
    name: str
    ident: str | None = None
    zone: ZoneKind
    ring: str | None = None
    ceiling_agl_m: float | None = None
    permission_required: bool
    distance_km: float | None = None
    overlap_area_m2: float | None = None
    overlap_length_m: float | None = None
    first_index: int | None = None
    note: str | None = None
    active: bool | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    agl_margin_m: float | None = None
    basis: ZoneBasis = "rule"
    distance_from: DistanceFrom | None = None
    inactive_reason: str | None = None


class NearestAerodrome(OutputModel):
    ident: str
    name: str
    type: str
    lon: float
    lat: float
    distance_km: float
    bearing_deg: float
    elevation_m: float | None = None
    # Whether the zone rules treat it as operational (None: no rule for its type, so no bands).
    operational: bool | None = None
    scheduled_service: bool | None = None


class ObstacleHit(OutputModel):
    id: str
    kind: str
    name: str | None = None
    lon: float
    lat: float
    height_m: float
    base_m: float | None = None
    top_amsl_m: float | None = None
    distance_m: float
    clearance_m: float | None = None
    conflict: bool
    # How the height is known: "tag" (mapped height), "levels" (building levels × 3.2 m) or
    # "default" (no height mapped: a per-kind assumption, ``height_assumed`` is then True).
    height_source: str | None = None
    height_assumed: bool = False


class TerrainSummary(OutputModel):
    min_m: float
    max_m: float
    mean_m: float
    relief_m: float
    samples: int
    source: str


class ProfilePoint(OutputModel):
    """One sample along a line: distance from the start, position, terrain and heights."""

    distance_m: float
    lon: float
    lat: float
    terrain_m: float | None = None
    altitude_amsl_m: float | None = None
    agl_m: float | None = None
    limit_agl_m: float | None = None
