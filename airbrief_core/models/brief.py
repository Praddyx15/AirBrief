"""Models of the operation evidence brief (:mod:`airbrief_core.brief`).

Inputs: :class:`OperationMeta` (operator, aircraft, planned window, pilot / take-off position,
notes) and one :class:`BriefSource` per completed analysis (its envelope, the dataset it read and
the bounds of its inputs).

Output: :class:`Brief` — the operation, one :class:`BriefSection` per evidence topic (always all of
them, in :data:`SECTION_ORDER`; a topic without an analysis is ``not_assessed``), cross-checks
between the analyses and the operation (``warnings``) and :class:`BriefProvenance` (versions, data
packs with per-layer source, licence and SHA-256, SHA-256 of every input file, of the canonical
parameters and results, and of the brief itself, and the key that signs the bundle, if any).

A brief is evidence for planning, never an authorisation: :data:`EVIDENCE_NOTICE` is on every page
it is printed on, and briefs built from a synthetic data pack carry :data:`SYNTHETIC_WATERMARK`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .common import InputModel, Issue, OutputModel, Verdict
from .presets import UAS_PRESETS

__all__ = [
    "BRIEF_SCHEMA_VERSION",
    "CANONICAL_JSON",
    "EVIDENCE_NOTICE",
    "MAX_BRIEF_ANALYSES",
    "SECTION_ORDER",
    "SECTION_TITLES",
    "SYNTHETIC_WATERMARK",
    "UNSIGNED_NOTE",
    "AircraftInfo",
    "AircraftSummary",
    "Brief",
    "BriefAnalysisProvenance",
    "BriefDatasetRef",
    "BriefFact",
    "BriefPart",
    "BriefProvenance",
    "BriefSection",
    "BriefSource",
    "BriefTable",
    "FactTone",
    "OperationMeta",
    "OperationSummary",
    "PackLayerProvenance",
    "PackProvenance",
    "SectionKey",
    "signed_note",
]

BRIEF_SCHEMA_VERSION = "1.1"
"""Version of the brief JSON (``Brief``). Minor for additive changes, major for breaking ones."""

UNSIGNED_NOTE = (
    "Not signed: these SHA-256 values detect accidental corruption, not deliberate edits; anyone "
    "who changes a file can recompute them."
)
"""What the provenance says when the instance has no signing key (``BRIEF_SIGNING_KEY_FILE``)."""


def signed_note(key_id: str) -> str:
    """What the provenance says when the instance signs bundles. Worded to be true in the bundle
    and on the page alike, since the brief hash covers it."""
    return (
        f"Bundles from this instance are signed (key {key_id}): a downloaded bundle's signature.json holds "
        "an Ed25519 signature of its provenance.json. Check it with python -m airbrief_core.verify <bundle.zip> "
        "--public-key <published key>. The brief on its own (this page, a print, the JSON) carries no signature."
    )

EVIDENCE_NOTICE = (
    "Evidence, not authorisation. AirBrief reports facts, bands and margins for planning; it does "
    "not approve, permit or clear any flight. Obtain the permissions your authority requires."
)
"""Printed on every page of a brief and carried by every analysis export."""

SYNTHETIC_WATERMARK = "SYNTHETIC DEMO DATA — not evidence"
"""Watermark of briefs and exports computed with a synthetic (invented) data pack."""

CANONICAL_JSON = (
    "UTF-8 JSON with keys sorted and no whitespace: "
    "json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)"
)
"""How the hashed JSON values are serialised (so anyone can recompute the SHA-256)."""

MAX_BRIEF_ANALYSES = 12
"""Analyses one brief (and one bundle) may hold."""

SectionKey = Literal[
    "operation",
    "airspace",
    "heights",
    "obstacles",
    "ground_risk",
    "environment",
    "line_of_sight",
    "survey",
    "flight_audit",
    "show",
    "evtol",
]

SECTION_ORDER: tuple[SectionKey, ...] = (
    "operation",
    "airspace",
    "heights",
    "obstacles",
    "ground_risk",
    "environment",
    "line_of_sight",
    "survey",
    "flight_audit",
    "show",
    "evtol",
)

SECTION_TITLES: dict[str, str] = {
    "operation": "Operation",
    "airspace": "Airspace",
    "heights": "Heights and terrain",
    "obstacles": "Obstacles",
    "ground_risk": "Ground risk",
    "environment": "Environment",
    "line_of_sight": "Line of sight",
    "survey": "Survey and spray effort",
    "flight_audit": "Flight audit",
    "show": "Drone show",
    "evtol": "eVTOL siting and corridor",
}


class AircraftInfo(InputModel):
    """The aircraft: a UAS preset (``UAS_PRESETS`` key) and/or custom values.

    Values given here override the preset's; the brief shows where each came from.
    """

    preset: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, max_length=120)
    takeoff_mass_kg: float | None = Field(default=None, gt=0, le=600_000)
    max_dimension_m: float | None = Field(default=None, gt=0, le=100)
    max_speed_ms: float | None = Field(default=None, gt=0, le=350)

    @field_validator("preset")
    @classmethod
    def _known_preset(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        if value not in UAS_PRESETS:
            raise ValueError(f"unknown UAS preset '{value}'; known: {', '.join(UAS_PRESETS)}")
        return value

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class OperationMeta(InputModel):
    """What the operator tells the brief about the operation. Every field is optional.

    ``planned_start_utc`` / ``planned_end_utc`` are UTC (a time without an offset is read as UTC);
    ``utc_offset_min`` (minutes east of UTC) only labels the local times shown in the brief.
    ``pilot_position`` is ``[lon, lat]`` in WGS84 degrees.
    """

    operator: str | None = Field(default=None, max_length=200)
    reference: str | None = Field(default=None, max_length=120)
    aircraft: AircraftInfo | None = None
    pilot_position: tuple[float, float] | None = None
    planned_start_utc: datetime | None = None
    planned_end_utc: datetime | None = None
    utc_offset_min: int = Field(default=0, ge=-720, le=840)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("operator", "reference", "notes")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("pilot_position")
    @classmethod
    def _lonlat(cls, value: tuple[float, float] | None) -> tuple[float, float] | None:
        if value is None:
            return None
        lon, lat = value
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            raise ValueError("pilot_position must be [lon, lat] in WGS84 degrees")
        return value

    @field_validator("planned_start_utc", "planned_end_utc")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value)

    @model_validator(mode="after")
    def _window(self) -> OperationMeta:
        if self.planned_start_utc and self.planned_end_utc and self.planned_end_utc <= self.planned_start_utc:
            raise ValueError("planned_end_utc must be after planned_start_utc")
        return self


class BriefDatasetRef(OutputModel):
    """The dataset (uploaded file) an analysis read, as stored."""

    id: str
    filename: str
    sha256: str | None = None
    size_bytes: int | None = None
    sample: str | None = None


class BriefSource(OutputModel):
    """One completed analysis given to :func:`airbrief_core.brief.build_brief`.

    ``envelope`` is an :class:`~airbrief_core.models.analyses.AnalysisEnvelope` as a dict;
    ``bbox`` the WGS84 bounds of its inputs (used for the "different areas" cross-check).
    """

    analysis_id: str
    envelope: dict[str, Any]
    dataset: BriefDatasetRef | None = None
    bbox: list[float] | None = None


FactTone = Literal["neutral", "ok", "warn", "danger", "info"]


class BriefFact(OutputModel):
    """One labelled value. ``text`` is the metric rendering (number and unit joined by a narrow
    no-break space); ``value`` and ``unit`` (SI) keep the number for machine readers."""

    label: str
    text: str
    value: float | int | str | bool | None = None
    unit: str | None = None
    note: str | None = None
    tone: FactTone = "neutral"
    tone_label: str | None = None


class BriefTable(OutputModel):
    caption: str
    columns: list[str]
    numeric: list[bool] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    total: int = 0
    """Rows before the brief's cut (the bundle CSV holds them all)."""


class BriefPart(OutputModel):
    """What one analysis says about one section."""

    analysis_id: str
    kind: str
    title: str
    status: Literal["assessed", "partial"] = "assessed"
    verdict: Verdict | None = None
    summary: str | None = None
    banners: list[str] = Field(default_factory=list)
    """Prominent caveats, e.g. "Airspace not assessed: no data pack covers this area"."""
    facts: list[BriefFact] = Field(default_factory=list)
    tables: list[BriefTable] = Field(default_factory=list)
    notes: list[Issue] = Field(default_factory=list)
    disclaimer: str | None = None


class BriefSection(OutputModel):
    key: SectionKey
    title: str
    status: Literal["info", "assessed", "partial", "not_assessed"]
    verdict: Verdict | None = None
    reason: str | None = None
    """Why the section is not (fully) assessed."""
    parts: list[BriefPart] = Field(default_factory=list)


class AircraftSummary(OutputModel):
    preset: str | None = None
    name: str | None = None
    takeoff_mass_kg: float | None = None
    max_dimension_m: float | None = None
    max_speed_ms: float | None = None
    source: Literal["preset", "custom", "preset_with_overrides"] | None = None


class OperationSummary(OutputModel):
    """The operation as given, with the aircraft resolved from its preset."""

    operator: str | None = None
    reference: str | None = None
    aircraft: AircraftSummary | None = None
    pilot_position: list[float] | None = None
    planned_start_utc: str | None = None
    planned_end_utc: str | None = None
    utc_offset_min: int = 0
    utc_offset_label: str = "UTC+00:00"
    notes: str | None = None


class PackLayerProvenance(OutputModel):
    key: str
    file: str | None = None
    source: str = ""
    licence: str = ""
    used_by: list[str] = Field(default_factory=list)
    """Analysis kinds of this brief that may read the layer (their required and optional layers)."""
    sha256: str | None = None
    source_url: str | None = None
    """The server that delivered the layer (for OSM layers, the Overpass mirror that answered)."""


class PackProvenance(OutputModel):
    name: str
    installed: bool = True
    """False when the pack an analysis used is no longer installed on this server."""
    version: str | None = None
    created_utc: str | None = None
    synthetic: bool = False
    layers: list[PackLayerProvenance] = Field(default_factory=list)
    attribution: list[str] = Field(default_factory=list)
    content_sha256: str | None = None
    """SHA-256 over the pack's layer hashes (``airbrief_core.data.datapack.content_sha256``);
    None for an older pack built without hashes."""


class BriefAnalysisProvenance(OutputModel):
    analysis_id: str
    kind: str
    title: str
    schema_version: str
    airbrief_version: str
    datapack: str | None = None
    datapack_synthetic: bool = False
    datapack_sha256: str | None = None
    """content_sha256 of the pack when the analysis ran (None: no pack, or one without hashes)."""
    backend: str | None = None
    started_utc: str
    finished_utc: str
    duration_ms: int = 0
    params_sha256: str
    result_sha256: str
    dataset_id: str | None = None
    dataset_filename: str | None = None
    input_sha256: str | None = None
    """SHA-256 of the uploaded file the analysis read; ``None`` when its inputs were all in the
    parameters (drawn geometry, an inline FlightMD report), which ``params_sha256`` covers."""
    input_note: str | None = None


class BriefProvenance(OutputModel):
    airbrief_version: str
    schema_version: str
    brief_schema_version: str = BRIEF_SCHEMA_VERSION
    generated_utc: str
    started_utc: str | None = None
    """Earliest start of the analyses in the brief."""
    finished_utc: str | None = None
    """Latest finish of the analyses in the brief."""
    canonical_json: str = CANONICAL_JSON
    operation_sha256: str
    brief_sha256: str = ""
    """SHA-256 of the canonical brief JSON without ``generated_utc`` and this field: identical
    analyses and operation always give the same value."""
    analyses: list[BriefAnalysisProvenance] = Field(default_factory=list)
    datapacks: list[PackProvenance] = Field(default_factory=list)
    inputs: list[BriefDatasetRef] = Field(default_factory=list)
    signing_key_id: str | None = None
    """Key id of the Ed25519 key that signs the bundle's ``provenance.json`` (first 16 hex
    characters of the SHA-256 of the raw public key); None when the instance does not sign."""
    signature_note: str = UNSIGNED_NOTE
    """What the hashes and the signature prove, in words."""


class Brief(OutputModel):
    """The operation evidence brief (JSON of ``POST /api/briefs/``)."""

    title: str
    notice: str = EVIDENCE_NOTICE
    synthetic: bool = False
    watermark: str | None = None
    operation: OperationSummary
    sections: list[BriefSection]
    warnings: list[Issue] = Field(default_factory=list)
    extent: list[float] | None = None
    """[min_lon, min_lat, max_lon, max_lat] of every analysis' inputs."""
    provenance: BriefProvenance
