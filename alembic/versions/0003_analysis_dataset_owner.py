"""Dataset-owner flag on analyses: ``analyses.by_dataset_owner``.

True when the analysis was created with its dataset's owner token. Where owner tokens are
enforced, a dataset lists only these (plus ids the caller names), so another visitor's run
can't pass for the owner's evidence.

Existing rows are set to true: they predate the rule, and nothing recorded who made them.

Revision ID: 0003_analysis_dataset_owner
Revises: 0002_job_heartbeat
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_analysis_dataset_owner"
down_revision: str | None = "0002_job_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the flag (default false) and set it on every existing row."""
    with op.batch_alter_table("analyses") as batch:
        batch.add_column(sa.Column("by_dataset_owner", sa.Boolean(), nullable=False, server_default=sa.false()))
    # a plain UPDATE, so SQLite doesn't have to rebuild the table to change the default afterwards
    analyses = sa.table("analyses", sa.column("by_dataset_owner", sa.Boolean()))
    op.execute(analyses.update().values(by_dataset_owner=True))


def downgrade() -> None:
    """Drop the flag."""
    with op.batch_alter_table("analyses") as batch:
        batch.drop_column("by_dataset_owner")
