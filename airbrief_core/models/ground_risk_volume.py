"""Models for the SORA 2.5-aligned operational volume of the ground-risk module.

Inputs: :class:`VolumeDefaults` (per-aircraft contingency terms, also carried by every UAS preset),
:class:`OperationalVolumeParams` (those terms plus the flight-geography height) and
:class:`AssemblyInput` (a user-entered outdoor assembly or event).

Outputs: :class:`OperationalVolumeResult` (the contingency volume, ground risk buffer and
adjacent-area sizes with every term and its formula), :class:`DensityGrid` (the square
population-density grid of SORA 2.5 Main Body Table 4), :class:`AssemblyHit` (assemblies within
1 km of the operational volume) and :class:`IgrcTable` (Table 2 with the cell that applies).

Everything here is *evidence* for SORA Step #2 and Step #8; none of it is a GRC, ARC or SAIL.
Formulas and sources are in :mod:`airbrief_core.analyses.groundrisk`.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .common import GeoJSON, InputModel, OutputModel

AircraftType = Literal["multirotor", "fixed_wing"]
"""Contingency manoeuvre family: multirotors stop by pitching, fixed-wings turn by banking."""

AssemblyBand = Literal["lt_40k", "40k_400k", "gt_400k", "unknown"]
"""Size band of an outdoor assembly (SORA 2.5 Main Body §4.8.3, Tables 8–13 columns)."""

ASSEMBLY_BAND_LABELS: dict[str, str] = {
    "lt_40k": "< 40,000 people",
    "40k_400k": "40,000–400,000 people",
    "gt_400k": "> 400,000 people",
    "unknown": "Capacity unknown",
}


class VolumeDefaults(InputModel):
    """Contingency-volume terms of an aircraft (metres, seconds, degrees).

    The defaults are illustrative; replace them with the aircraft's and the operation's data.
    """

    aircraft: AircraftType = "multirotor"
    gnss_error_m: float = Field(default=3.0, ge=0, le=500)
    position_error_m: float = Field(default=1.0, ge=0, le=500)
    map_error_m: float = Field(default=1.0, ge=0, le=500)
    reaction_time_s: float = Field(default=1.0, ge=0, le=60)
    bank_angle_deg: float = Field(default=30.0, ge=5, le=75)
    pitch_angle_deg: float = Field(default=45.0, ge=5, le=75)
    altimetry_error_m: float = Field(default=4.0, ge=0, le=500)
    climb_angle_deg: float = Field(default=45.0, ge=0, le=90)


class OperationalVolumeParams(VolumeDefaults):
    """The operational volume of a ground-risk analysis.

    ``fg_height_m`` is the flight-geography height (the planned maximum height above ground);
    when left out it is the analysis' ``altitude_agl_m``.
    """

    fg_height_m: float | None = Field(default=None, gt=0, le=1500)


_POSITION_DEPTH: dict[str, int] = {"Point": 0, "MultiPoint": 1, "Polygon": 2, "MultiPolygon": 3}
"""Nesting depth of the positions in the ``coordinates`` of each accepted geometry type."""


def _check_positions(coords: Any, depth: int) -> None:
    """Every position is ``[lon, lat, ...]`` with finite numbers, lon in [-180, 180] and lat in
    [-90, 90] (a bad entry is a 422, never an assembly silently left out)."""
    if depth == 0:
        if not isinstance(coords, list | tuple) or len(coords) < 2:
            raise ValueError("an assembly position must be [longitude, latitude]")
        lon, lat = coords[0], coords[1]
        if not all(isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in (lon, lat)):
            raise ValueError("assembly coordinates must be finite numbers")
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            raise ValueError("assembly coordinates must be longitude -180..180 and latitude -90..90")
        return
    if not isinstance(coords, list | tuple) or not coords:
        raise ValueError("an assembly geometry needs coordinates")
    for item in coords:
        _check_positions(item, depth - 1)


class AssemblyInput(InputModel):
    """An outdoor assembly or event entered by the user (a stadium match, a festival, a market).

    ``start_utc``/``end_utc`` are UTC: a time without an offset is read as UTC.
    """

    name: str = Field(min_length=1, max_length=120)
    geometry: GeoJSON
    expected_people: int | None = Field(default=None, ge=0, le=100_000_000)
    start_utc: datetime | None = None
    end_utc: datetime | None = None
    note: str | None = Field(default=None, max_length=500)

    @field_validator("geometry")
    @classmethod
    def _point_or_polygon(cls, value: GeoJSON) -> GeoJSON:
        kind = value.get("type") if isinstance(value, dict) else None
        if kind not in _POSITION_DEPTH:
            raise ValueError("an assembly geometry must be a GeoJSON Point or Polygon (or Multi*)")
        _check_positions(value.get("coordinates"), _POSITION_DEPTH[kind])
        return value

    @field_validator("start_utc", "end_utc")
    @classmethod
    def _as_utc(cls, value: datetime | None) -> datetime | None:
        # The fields are UTC: a time without an offset is read as UTC, one with an offset is
        # converted (so start and end always compare, and are stored with "+00:00").
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def _window_order(self) -> AssemblyInput:
        if self.start_utc is not None and self.end_utc is not None and self.end_utc <= self.start_utc:
            raise ValueError("end_utc must be after start_utc")
        return self


class VolumeTerm(OutputModel):
    """One term of the contingency volume, ground risk buffer or adjacent area."""

    name: str  # symbol, e.g. "S_GNSS", "H_CM", "S_GRB"
    label: str
    axis: Literal["horizontal", "vertical", "buffer", "adjacent"]
    value_m: float
    formula: str  # the formula with its numbers, e.g. "v·t_R = 15 m/s × 1 s"
    source: str


class OperationalVolumeResult(OutputModel):
    """Sizes of the operational volume (flight geography + contingency volume) and around it."""

    aircraft: AircraftType
    speed_ms: float
    fg_height_m: float
    s_cv_m: float
    h_cv_m: float
    grb_m: float
    grb_rule: Literal["one_to_one", "ballistic"]
    adjacent_outer_km: float
    adjacent_distance_km: float  # v·180 s before the 5–35 km clamp
    grid_m: float
    grid_source: Literal["auto", "user", "coarsened"]
    terms: list[VolumeTerm] = Field(default_factory=list)
    reference: str


class GridDensityCell(OutputModel):
    """One square cell of the density grid (``ix``/``iy`` index the local frame grid)."""

    id: str
    ix: int
    iy: int
    lon: float
    lat: float
    density_per_km2: float
    band: str


class DensityGrid(OutputModel):
    """Mean population density per square cell over the iGRC footprint (FG + CV + GRB).

    Cell ``(ix, iy)`` spans ``[ix·grid_m, (ix+1)·grid_m] × [iy·grid_m, (iy+1)·grid_m]`` metres east
    and north of ``(origin_lon, origin_lat)`` in a local azimuthal-equidistant frame.
    """

    grid_m: float
    origin_lon: float
    origin_lat: float
    samples_per_cell: int
    cells: list[GridDensityCell] = Field(default_factory=list)
    hotspots: list[GridDensityCell] = Field(default_factory=list)
    nodata_cells: int = 0
    coarsened: bool = False


class AssemblyHit(OutputModel):
    """An outdoor assembly within 1 km of the operational volume."""

    name: str
    source: Literal["datapack", "user"]
    kind: str | None = None
    capacity: int | None = None
    band: AssemblyBand
    band_label: str
    distance_m: float  # from the outer limit of the operational volume; 0 when inside it
    lon: float
    lat: float
    start_utc: str | None = None
    end_utc: str | None = None
    note: str | None = None


class IgrcColumn(OutputModel):
    dimension_m: float
    speed_ms: float
    label: str


class IgrcRow(OutputModel):
    label: str
    values: list[int | None]


class IgrcTable(OutputModel):
    """The lookup table used, with the cell that applies (``None`` indices: beyond the table)."""

    reference: str
    columns: list[IgrcColumn]
    rows: list[IgrcRow]
    row_index: int
    column_index: int | None = None


__all__ = [
    "ASSEMBLY_BAND_LABELS",
    "AircraftType",
    "AssemblyBand",
    "AssemblyHit",
    "AssemblyInput",
    "DensityGrid",
    "GridDensityCell",
    "IgrcColumn",
    "IgrcRow",
    "IgrcTable",
    "OperationalVolumeParams",
    "OperationalVolumeResult",
    "VolumeDefaults",
    "VolumeTerm",
]
