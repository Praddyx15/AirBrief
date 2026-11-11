"""Models for uploaded files: features, measurements, layers and the processed dataset."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from .common import SCHEMA_VERSION, BBox, GeoJSON, Issue, OutputModel

FileType = Literal["kml", "kmz", "shapefile", "geojson", "csv", "gpx"]
CrsSource = Literal["prj", "kml", "geojson", "csv", "gpx", "override", "assumed"]


class MeasurementStatus(StrEnum):
    MEASURED = "measured"
    NOT_APPLICABLE = "not_applicable"  # points: no measurement required
    UNSUPPORTED = "unsupported"  # e.g. KML Model, shapefile MultiPatch
    SKIPPED = "skipped"  # empty geometry or unknown CRS
    ERROR = "error"  # unexpected failure for this one feature (others continue)


class Measurement(OutputModel):
    """Measurement of one feature.

    ``area_m2`` / ``perimeter_m`` / ``length_m`` / ``length_3d_m`` are computed in local
    projections centred on the feature (LAEA for area, Transverse Mercator k0=1 for lengths).
    ``geodesic_*`` values are the ellipsoidal ground truth (Karney, WGS84).
    ``deviation_pct`` is the largest absolute relative difference between the two.
    ``grid_*`` values are only present for projected inputs: the size in the source grid.
    """

    status: MeasurementStatus
    reason: str | None = None
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    length_3d_m: float | None = None
    geodesic_area_m2: float | None = None
    geodesic_perimeter_m: float | None = None
    geodesic_length_m: float | None = None
    deviation_pct: float | None = None
    grid_area_m2: float | None = None
    grid_length_m: float | None = None
    method: str | None = None
    area_crs: str | None = None
    length_crs: str | None = None
    utm_epsg: int | None = None
    utm_zone: str | None = None
    repaired: bool = False
    has_z: bool = False
    parts: dict[str, int] = Field(default_factory=dict)


class FeatureRecord(OutputModel):
    """One feature as read from the file, with its measurement."""

    index: int
    layer: str
    fid: str | None = None
    name: str | None = None
    geometry_type: str
    crs: str | None = None
    geometry: GeoJSON | None = None
    geometry_wgs84: GeoJSON | None = None
    bbox_wgs84: BBox | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    altitude_mode: str | None = None
    times: list[float] | None = None
    time_origin_utc: str | None = None
    measurement: Measurement


class LayerInfo(OutputModel):
    name: str
    feature_count: int
    crs: str | None = None
    geometry_types: dict[str, int] = Field(default_factory=dict)


class MeasurementTotals(OutputModel):
    feature_count: int = 0
    measured: int = 0
    not_applicable: int = 0
    unsupported: int = 0
    skipped: int = 0
    error: int = 0
    area_m2: float = 0.0
    perimeter_m: float = 0.0
    length_m: float = 0.0
    length_3d_m: float = 0.0


class DatasetResult(OutputModel):
    """Everything produced by processing one uploaded file."""

    schema_version: str = SCHEMA_VERSION
    filename: str
    file_type: FileType
    crs: str | None = None
    crs_source: CrsSource | None = None
    feature_count: int
    layers: list[LayerInfo]
    features: list[FeatureRecord]
    bbox_wgs84: BBox | None = None
    totals: MeasurementTotals
    issues: list[Issue] = Field(default_factory=list)
