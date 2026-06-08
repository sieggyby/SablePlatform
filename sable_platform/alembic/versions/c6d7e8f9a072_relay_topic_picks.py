"""topic-suggestion feedback loop — relay_topic_picks (migration 072)

Mirrors SQLite migration 072_relay_topic_picks.sql for Postgres parity
(SablePlatform dual-migration rule). One additive table: an append-only log of
suggested-topic chip picks (SableWeb writes on click), read by the weekly topic
synthesis as a steering signal. A pick is a USAGE SIGNAL; there is NO cost column.
Append-only — no UNIQUE constraint (a repeat pick is itself signal).

Revision ID: c6d7e8f9a072
Revises: b5c6d7e8f071
Create Date: 2026-06-07 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c6d7e8f9a072"
down_revision = "b5c6d7e8f071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relay_topic_picks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("relay_clients.org_id"), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("register_band", sa.Text()),
        sa.Column("operator_handle", sa.Text()),
        sa.Column("picked_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_relay_topic_picks_org",
        "relay_topic_picks",
        ["org_id", "picked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_relay_topic_picks_org", table_name="relay_topic_picks")
    op.drop_table("relay_topic_picks")
