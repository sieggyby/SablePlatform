"""DB-backed SableWeb allowlist -- allowlist_entries (migration 075)

Mirrors SQLite migration 075_allowlist_entries.sql for Postgres parity (the dual-migration
rule). One additive AUTH table (OPS-ONLY, never on /client) letting operators manage portal
access from the CLI without a redeploy. No FK. See docs/ONBOARDING_PHASE2_PLAN.md P1.

Revision ID: c9e0f1a2b075
Revises: c8d9e0f1a074
Create Date: 2026-06-07 14:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9e0f1a2b075"
down_revision = "c8d9e0f1a074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "allowlist_entries",
        sa.Column("email", sa.Text(), primary_key=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("operator_id", sa.Text()),
        sa.Column("org", sa.Text()),
        sa.Column("assigned_orgs", sa.Text()),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('admin','operator','client','client_ops')",
            name="ck_allowlist_entries_role",
        ),
        sa.CheckConstraint("email = lower(email)", name="ck_allowlist_entries_email_lower"),
    )


def downgrade() -> None:
    op.drop_table("allowlist_entries")
