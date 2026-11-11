"""One result per feature: the ``batch`` parameter and the result of a per-feature run.

The runner is :mod:`airbrief_core.batch`; docs/features/batch.md describes the method.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from .common import GeoJSON, InputModel, Issue, OutputModel

BATCH_KINDS: tuple[str, ...] = ("airspace", "ground_risk", "survey", "environment", "line_of_sight")
"""Analysis kinds that can give one result per feature."""

MAX_BATCH_FEATURES = 1000
"""Features in one per-feature run (line of sight takes fewer, see ``BATCH_FEATURE_LIMITS``)."""

BATCH_FEATURE_LIMITS: dict[str, int] = {kind: MAX_BATCH_FEATURES for kind in BATCH_KINDS} | {"line_of_sight": 500}
"""A line-of-sight item can take 0.8 s (a 400 × 400 viewshed when the observer is far away)."""

BatchTone = Literal["ok", "info", "warn", "danger", "neutral"]


class BatchParams(InputModel):
    """Run the analysis once per feature instead of once on their union.

    ``features`` is a GeoJSON FeatureCollection (at most 1,000 features); the API fills it from
    the dataset selection when the request leaves it out, and only then sets ``from_dataset``:
    each feature's ``id`` is its dataset feature number and becomes its item's ``index``.
    Otherwise (or when those ids are not all different integers) an item's ``index`` is the
    feature's position in ``features``. ``key_property``
    names the property that identifies each feature in the results and exports (e.g.
    ``asset_id``); without it, the feature's name, then its number, is used.
    """

    mode: Literal["per_feature"] = "per_feature"
    key_property: str | None = Field(default=None, min_length=1, max_length=120)
    features: GeoJSON
    from_dataset: bool = False

    @field_validator("features")
    @classmethod
    def _collection(cls, value: GeoJSON) -> GeoJSON:
        if value.get("type") != "FeatureCollection" or not isinstance(value.get("features"), list):
            raise ValueError("batch.features must be a GeoJSON FeatureCollection")
        features = value["features"]
        if not features:
            raise ValueError("batch.features has no features")
        if len(features) > MAX_BATCH_FEATURES:
            raise ValueError(f"one result per feature takes at most {MAX_BATCH_FEATURES:,} features, not {len(features):,}")
        if not all(isinstance(f, dict) for f in features):
            raise ValueError("every item of batch.features must be a GeoJSON Feature")
        return value


class BatchItem(OutputModel):
    """The result for one feature. ``index`` is unique per item: the dataset feature number, or
    the position in ``batch.features`` (see :class:`BatchParams`). ``values`` holds the kind's
    figures (:data:`airbrief_core.batch.BATCH_FIELDS`)."""

    index: int
    key: str
    name: str | None = None
    status: Literal["ok", "error", "skipped"] = "ok"
    verdict: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)  # kind-specific, eg the ceiling or the iGRC
    issues: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class BatchCount(OutputModel):
    verdict: str
    label: str
    tone: BatchTone = "neutral"
    count: int


class BatchResult(OutputModel):
    """A per-feature run: one :class:`BatchItem` per feature, the count of each verdict and a
    ``features`` layer with every feature tagged with its verdict."""

    batch: Literal["per_feature"] = "per_feature"
    kind: str
    key_property: str | None = None
    feature_count: int
    ok: int
    errors: int
    skipped: int = 0
    counts: list[BatchCount] = Field(default_factory=list)
    items: list[BatchItem] = Field(default_factory=list)
    summary: str
    method: str
    disclaimer: str
    issues: list[Issue] = Field(default_factory=list)
    layers: dict[str, GeoJSON] = Field(default_factory=dict)
    points_only: int = 0
    """Features drawn as a point in ``layers`` because the layer reached its vertex cap."""
