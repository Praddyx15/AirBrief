"""Pydantic models of the HTTP API (docs/SPEC.md §4; mirrored in ``frontend/lib/types.ts``).

Domain fragments (layers, totals, issues, measurements, presets, zone rules) reuse the core
models from :mod:`airbrief_core.models` so the contract has a single source of truth.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from airbrief_core.models.analyses import AnalysisKind
from airbrief_core.models.common import AnalysisStatus, FileStatus, Issue, ZoneRules
from airbrief_core.models.features import LayerInfo, Measurement, MeasurementTotals
from airbrief_core.models.presets import CameraSpec, UasPreset


class ApiModel(BaseModel):
    """Base of response models."""

    model_config = ConfigDict(extra="ignore")


class ErrorInfo(ApiModel):
    """Error object inside ``{"error": ...}`` and on FileInfo / AnalysisInfo."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ApiModel):
    """Body of every error response."""

    error: ErrorInfo


class FileInfo(ApiModel):
    """A dataset (uploaded or sample file) and its processing state."""

    id: str
    filename: str
    file_type: str | None = None
    size_bytes: int
    status: FileStatus
    feature_count: int = 0
    layer_count: int = 0
    crs: str | None = None
    crs_source: str | None = None
    bbox: list[float] | None = Field(default=None, description="[min_lon, min_lat, max_lon, max_lat]")
    layers: list[LayerInfo] = Field(default_factory=list)
    totals: MeasurementTotals | None = None
    issues: list[Issue] = Field(default_factory=list)
    error: ErrorInfo | None = None
    created_at: datetime
    processed_at: datetime | None = None
    processing_ms: int | None = None
    sample: str | None = None
    # Only in the creation response: keep it to delete/cancel later (see api/ownership.py).
    owner_token: str | None = None


class FilesPage(ApiModel):
    items: list[FileInfo]
    total: int


class MeasurementItem(ApiModel):
    feature_index: int
    layer: str
    name: str | None = None
    geometry_type: str
    crs: str | None = None
    measurement: Measurement


class MeasurementsPage(ApiModel):
    file_id: str
    items: list[MeasurementItem]
    total: int
    limit: int
    offset: int
    totals: MeasurementTotals


class SampleInfo(ApiModel):
    name: str
    title: str
    description: str
    filename: str
    module: str | None = None


class AnalysisRequest(BaseModel):
    """Body of ``POST /api/analyses/``. Missing inputs are resolved from the dataset."""

    model_config = ConfigDict(extra="forbid")

    kind: AnalysisKind
    dataset_id: str | None = Field(default=None, max_length=64)
    feature_indices: list[int] | None = Field(default=None, max_length=100_000)
    params: dict[str, Any] = Field(default_factory=dict)


class AnalysisInfo(ApiModel):
    id: str
    kind: str
    status: AnalysisStatus
    progress: float = 0.0
    stage: str | None = None
    dataset_id: str | None = None
    by_dataset_owner: bool = Field(
        default=False,
        description="Created with the dataset's owner token (always false without a dataset). "
        "Where owner tokens are enforced, a dataset's listing holds only these and the ids the caller names.",
    )
    feature_indices: list[int] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: ErrorInfo | None = None
    datapack: str | None = None
    datapack_synthetic: bool = False
    backend: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    # Only in the creation response: keep it to delete/cancel later (see api/ownership.py).
    owner_token: str | None = None


class AnalysesPage(ApiModel):
    items: list[AnalysisInfo]
    total: int


class ModuleMeta(ApiModel):
    kind: str
    title: str
    description: str
    requires: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)
    params_schema: dict[str, Any] = Field(default_factory=dict)


class DatapackInfo(ApiModel):
    name: str
    bbox: list[float]
    synthetic: bool
    layers: list[str]
    version: str | None = None
    created_utc: str | None = None
    attribution: list[str] = Field(default_factory=list)
    integrity: Literal["verified", "unverified", "mismatch"] = Field(
        default="unverified",
        description="verified: the files match the manifest's SHA-256 values; unverified: an older pack "
        "without hashes; mismatch: a file changed or is missing, so analyses refuse the pack",
    )
    content_sha256: str | None = Field(default=None, description="SHA-256 over the layer hashes, as the manifest states it")


class SigningMeta(ApiModel):
    """The key that signs brief bundles (``signature.json``): publish it so recipients can check."""

    algorithm: Literal["Ed25519"] = "Ed25519"
    key_id: str = Field(description="First 16 hex characters of the SHA-256 of the raw public key")
    public_key: str = Field(description="The raw 32-byte public key, base64")


class FlightmdMeta(ApiModel):
    configured: bool
    url: str | None = None


class MetaResponse(ApiModel):
    version: str
    schema_version: str
    modules: list[ModuleMeta]
    cameras: dict[str, CameraSpec]
    uas_presets: dict[str, UasPreset]
    zone_rules: ZoneRules = Field(description="The rules of default_zone_profile (what every module uses unless told otherwise)")
    zone_profiles: dict[str, ZoneRules] = Field(
        default_factory=dict,
        description="Named airspace rule sets for zone_profile (each says its source in 'reference'); 'custom' means the analysis' own zone_rules",
    )
    default_zone_profile: str = "dgca_2021"
    datapacks: list[DatapackInfo]
    limits: dict[str, float]
    flightmd: FlightmdMeta
    global_listing: bool = True
    owner_tokens_required: bool = False
    app_env: Literal["development", "production"]
    signing: SigningMeta | None = Field(default=None, description="null: brief bundles are not signed")


class HealthResponse(ApiModel):
    status: Literal["ok", "degraded"]
    version: str
    schema_version: str
    db: Literal["ok", "error"]
    datapacks: list[str]
    native_backend: str
    refusals: dict[str, int] | None = Field(
        None,
        description="Refused requests per error code since the process started, for monitoring. "
        "Only for a request from the server itself (loopback, not through a proxy); null otherwise.",
    )


class FlightmdFetchRequest(BaseModel):
    """Body of ``POST /api/flightmd/fetch/``."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2000)


class ContextResponse(ApiModel):
    """``GET /api/context/``: context layers of the data pack that covers a map view."""

    datapack: str | None = Field(description="Name of the data pack used; null when no pack covers the view")
    synthetic: bool = False
    bbox: list[float] = Field(description="[min_lon, min_lat, max_lon, max_lat] of the request")
    layers: dict[str, dict[str, Any]] = Field(description="Layer name → GeoJSON FeatureCollection")
    truncated: dict[str, bool] = Field(description="Layer name → true when it was cut at the feature or vertex limit")
    counts: dict[str, int] = Field(description="Layer name → features that matched before the cut")
    limit: int
    attribution: list[str] = Field(default_factory=list)


class PlaceResult(ApiModel):
    """One ``GET /api/places/`` match: typed coordinates or a data-pack aerodrome."""

    label: str
    kind: Literal["coordinates", "aerodrome"]
    ident: str | None = None
    lon: float
    lat: float
    detail: str = ""
    datapack: str | None = None


class GeometryParseRequest(BaseModel):
    """Body of ``POST /api/geometry/parse``."""

    model_config = ConfigDict(extra="forbid")

    # The core caps the text at 100 kB with a clear message; this only bounds the request.
    text: str = Field(max_length=400_000)
    shape: Literal["auto", "point", "line", "polygon"] = "auto"


class GeometryParseResponse(ApiModel):
    """Result of ``POST /api/geometry/parse``: one WGS84 GeoJSON geometry and how it was read."""

    geometry: dict[str, Any]
    kind: str = Field(description="GeoJSON geometry type of `geometry`")
    source: Literal["geojson", "wkt", "coordinates"]
    vertices: int
    issues: list[Issue] = Field(default_factory=list)


__all__ = [
    "AnalysesPage",
    "AnalysisInfo",
    "AnalysisRequest",
    "ApiModel",
    "ContextResponse",
    "DatapackInfo",
    "ErrorInfo",
    "ErrorResponse",
    "FileInfo",
    "FilesPage",
    "FlightmdFetchRequest",
    "FlightmdMeta",
    "GeometryParseRequest",
    "GeometryParseResponse",
    "HealthResponse",
    "MeasurementItem",
    "MeasurementsPage",
    "MetaResponse",
    "ModuleMeta",
    "PlaceResult",
    "SampleInfo",
    "SigningMeta",
]
