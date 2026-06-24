"""Per-operator meme-production dollar budget -- operator_meme_budget (migration 078)

Mirrors SQLite migration 078_operator_meme_budget.sql for Postgres parity (the dual-migration
rule). HAND-WRITTEN (not --autogenerate). Composite TEXT primary key
(operator_handle, org_id, week_iso); no FKs (mirrors operator_reply_quota -- the org is validated
at the serve layer). See Sable_Slopper/docs/MEME_ENGINE_PLAN.md.

Revision ID: a7b8c9d0e078
Revises: f6a7b8c9d077
Create Date: 2026-06-23 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e078"
down_revision = "f6a7b8c9d077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operator_meme_budget",
        sa.Column("operator_handle", sa.Text(), primary_key=True, nullable=False),
        sa.Column("org_id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("week_iso", sa.Text(), primary_key=True, nullable=False),
        sa.Column("spend_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("operator_meme_budget")
