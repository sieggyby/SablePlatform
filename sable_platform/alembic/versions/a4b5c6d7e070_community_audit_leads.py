"""community-audit lead capture — community_audit_leads (migration 070)

Mirrors SQLite migration 070_community_audit_leads.sql for Postgres parity
(SablePlatform dual-migration rule). One additive table: a non-privileged marketing
list for the sable-audit funnel (PLAN §1.2). No FK (a lead may precede any guild).

Revision ID: a4b5c6d7e070
Revises: a3b4c5d6e069
Create Date: 2026-06-06 16:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a4b5c6d7e070"
down_revision = "a3b4c5d6e069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "community_audit_leads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("guild_id", sa.Text()),
        sa.Column("source", sa.Text(), nullable=False, server_default="audit_page"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("community_audit_leads_by_email", "community_audit_leads", ["email"])


def downgrade() -> None:
    op.drop_index("community_audit_leads_by_email", table_name="community_audit_leads")
    op.drop_table("community_audit_leads")
