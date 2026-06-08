"""make relay_opportunity_feedback.opportunity_id nullable (migration 068)

Mirrors SQLite migration 068_make_opportunity_feedback_optional.sql for Postgres
parity (SablePlatform dual-migration rule). Per-variant gen-quality thumbs now work
on freeform drafts (a suggestion_id but no feed-sourced opportunity), not just
feed-sourced opportunities. relay_opportunity_feedback is a LEAF table (nothing
FK-references it). On Postgres this is a plain ALTER COLUMN DROP NOT NULL — no
table rebuild (SQLite needs the rebuild only because it cannot ALTER-DROP NOT NULL).

Revision ID: a2b3c4d5e068
Revises: a1b2c3d4e067
Create Date: 2026-06-06 14:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a2b3c4d5e068"
down_revision = "a1b2c3d4e067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "relay_opportunity_feedback",
        "opportunity_id",
        existing_type=sa.Integer(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "relay_opportunity_feedback",
        "opportunity_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
