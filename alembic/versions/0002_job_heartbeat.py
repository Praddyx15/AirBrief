"""Job heartbeats: ``heartbeat_at`` on datasets and analyses.

A process refreshes the heartbeat of the jobs it is running; only jobs whose heartbeat has gone
stale are failed as interrupted, so starting one worker no longer fails another's live jobs.

Revision ID: 0002_job_heartbeat
Revises: 0001_initial
Create Date: 2026-10-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_job_heartbeat"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable heartbeat column to both job tables."""
    with op.batch_alter_table("datasets") as batch:
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("analyses") as batch:
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Drop the heartbeat columns."""
    with op.batch_alter_table("analyses") as batch:
        batch.drop_column("heartbeat_at")
    with op.batch_alter_table("datasets") as batch:
        batch.drop_column("heartbeat_at")
