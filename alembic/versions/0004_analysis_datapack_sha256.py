"""Data-pack content hash on analyses: ``analyses.datapack_sha256``.

The ``content_sha256`` of the data pack an analysis read, recorded when the pack's files matched
their hashes, so the analysis envelope and the brief show which pack content the evidence came
from. Existing rows stay null: their packs were never checked.

Revision ID: 0004_analysis_datapack_sha256
Revises: 0003_analysis_dataset_owner
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_analysis_datapack_sha256"
down_revision: str | None = "0003_analysis_dataset_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable hash column."""
    with op.batch_alter_table("analyses") as batch:
        batch.add_column(sa.Column("datapack_sha256", sa.String(64), nullable=True))


def downgrade() -> None:
    """Drop the hash column."""
    with op.batch_alter_table("analyses") as batch:
        batch.drop_column("datapack_sha256")
