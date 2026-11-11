"""Initial schema: datasets, features, analyses.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Create the three tables and their indexes."""
    op.create_table(
        "datasets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(16), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("crs", sa.Text(), nullable=True),
        sa.Column("crs_source", sa.String(16), nullable=True),
        sa.Column("feature_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("layer_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bbox", _json(), nullable=True),
        sa.Column("layers", _json(), nullable=True),
        sa.Column("totals", _json(), nullable=True),
        sa.Column("issues", _json(), nullable=True),
        sa.Column("stored_path", sa.Text(), nullable=True),
        sa.Column("sample", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_ms", sa.Integer(), nullable=True),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("owner_token_hash", sa.String(64), nullable=True),
    )
    op.create_index("ix_datasets_status", "datasets", ["status"])
    op.create_index("ix_datasets_created_at", "datasets", ["created_at"])

    op.create_table(
        "features",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "dataset_id",
            sa.String(36),
            sa.ForeignKey("datasets.id", ondelete="CASCADE", name="fk_features_dataset_id"),
            nullable=False,
        ),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False, server_default=""),
        sa.Column("fid", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("geometry_type", sa.String(64), nullable=False, server_default="None"),
        sa.Column("crs", sa.Text(), nullable=True),
        sa.Column("geometry", _json(), nullable=True),
        sa.Column("geometry_wgs84", _json(), nullable=True),
        sa.Column("properties", _json(), nullable=True),
        sa.Column("measurement", _json(), nullable=True),
        sa.Column("min_lon", sa.Float(), nullable=True),
        sa.Column("min_lat", sa.Float(), nullable=True),
        sa.Column("max_lon", sa.Float(), nullable=True),
        sa.Column("max_lat", sa.Float(), nullable=True),
        sa.Column("altitude_mode", sa.String(32), nullable=True),
        sa.Column("times", _json(), nullable=True),
        sa.Column("time_origin_utc", sa.String(64), nullable=True),
    )
    op.create_index("ix_features_dataset_id", "features", ["dataset_id"])
    op.create_index("ix_features_dataset_index", "features", ["dataset_id", "index"], unique=True)
    op.create_index("ix_features_dataset_geometry_type", "features", ["dataset_id", "geometry_type"])

    op.create_table(
        "analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("stage", sa.String(64), nullable=True),
        sa.Column(
            "dataset_id",
            sa.String(36),
            sa.ForeignKey("datasets.id", ondelete="CASCADE", name="fk_analyses_dataset_id"),
            nullable=True,
        ),
        sa.Column("feature_indices", _json(), nullable=True),
        sa.Column("params", _json(), nullable=True),
        sa.Column("result", _json(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("datapack", sa.String(128), nullable=True),
        sa.Column("datapack_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("backend", sa.String(16), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("owner_token_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_analyses_status", "analyses", ["status"])
    op.create_index("ix_analyses_dataset_id", "analyses", ["dataset_id"])
    op.create_index("ix_analyses_kind", "analyses", ["kind"])
    op.create_index("ix_analyses_created_at", "analyses", ["created_at"])


def downgrade() -> None:
    """Drop everything created by :func:`upgrade`."""
    op.drop_index("ix_analyses_created_at", table_name="analyses")
    op.drop_index("ix_analyses_kind", table_name="analyses")
    op.drop_index("ix_analyses_dataset_id", table_name="analyses")
    op.drop_index("ix_analyses_status", table_name="analyses")
    op.drop_table("analyses")
    op.drop_index("ix_features_dataset_geometry_type", table_name="features")
    op.drop_index("ix_features_dataset_index", table_name="features")
    op.drop_index("ix_features_dataset_id", table_name="features")
    op.drop_table("features")
    op.drop_index("ix_datasets_created_at", table_name="datasets")
    op.drop_index("ix_datasets_status", table_name="datasets")
    op.drop_table("datasets")
