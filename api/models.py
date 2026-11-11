"""SQLAlchemy 2 ORM models: ``datasets``, ``features`` and ``analyses`` (docs/SPEC.md §4).

JSON columns use the generic ``JSON`` type (TEXT on SQLite) with a ``JSONB`` variant on
PostgreSQL, so the same models work on both. Timestamps are stored in UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to naive datetimes read back from SQLite."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    """Declarative base of the API tables."""


class Dataset(Base):
    """One uploaded (or sample) file and its processing outcome."""

    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    crs: Mapped[str | None] = mapped_column(Text, nullable=True)
    crs_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    feature_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    layer_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bbox: Mapped[list[float] | None] = mapped_column(JSONType, nullable=True)
    layers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    totals: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    issues: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    stored_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sample: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Refreshed by the claiming process while it works (api/jobs.py); stale = the process died.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # SHA-256 of the owner token (api/ownership.py); the token itself is never stored.
    owner_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_datasets_status", "status"),
        Index("ix_datasets_created_at", "created_at"),
    )


class Feature(Base):
    """One feature of a processed dataset, with its measurement."""

    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    feature_index: Mapped[int] = mapped_column("index", Integer, nullable=False)
    layer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fid: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    geometry_type: Mapped[str] = mapped_column(String(64), nullable=False, default="None")
    crs: Mapped[str | None] = mapped_column(Text, nullable=True)
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    geometry_wgs84: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    properties: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    measurement: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    min_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    altitude_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    times: Mapped[list[float] | None] = mapped_column(JSONType, nullable=True)
    time_origin_utc: Mapped[str | None] = mapped_column(String(64), nullable=True)


Index("ix_features_dataset_id", Feature.dataset_id)
Index("ix_features_dataset_index", Feature.dataset_id, Feature.feature_index, unique=True)
Index("ix_features_dataset_geometry_type", Feature.dataset_id, Feature.geometry_type)


class Analysis(Base):
    """One analysis job: its inputs, progress, result and timings."""

    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dataset_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True
    )
    feature_indices: Mapped[list[int] | None] = mapped_column(JSONType, nullable=True)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    datapack: Mapped[str | None] = mapped_column(String(128), nullable=True)
    datapack_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # content_sha256 of the pack the run read, when its files matched their hashes
    datapack_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    backend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Refreshed by the claiming process while it works (api/jobs.py); stale = the process died.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # SHA-256 of the owner token (api/ownership.py); the token itself is never stored.
    owner_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Sent with the dataset's owner token; where tokens are enforced a dataset lists only these.
    by_dataset_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_analyses_status", "status"),
        Index("ix_analyses_dataset_id", "dataset_id"),
        Index("ix_analyses_kind", "kind"),
        Index("ix_analyses_created_at", "created_at"),
    )


__all__ = ["Analysis", "Base", "Dataset", "Feature", "JSONType", "as_utc", "utcnow"]
