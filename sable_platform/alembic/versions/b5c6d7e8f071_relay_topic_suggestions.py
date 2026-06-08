"""topic-suggestion engine cache — relay_topic_suggestions (migration 071)

Mirrors SQLite migration 071_relay_topic_suggestions.sql for Postgres parity
(SablePlatform dual-migration rule). One additive table: the per-org cache of
synthesized compose topic suggestions (Tweet Assist P2). topics_json is the LLM
synthesis output (INTERPRETIVE); there is NO cost column (cost lives only in
cost_events, tag relay_compose.topics). One current row per org (app-level
delete-then-insert in relay/db), so no UNIQUE constraint.

Revision ID: b5c6d7e8f071
Revises: a4b5c6d7e070
Create Date: 2026-06-06 18:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b5c6d7e8f071"
down_revision = "a4b5c6d7e070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relay_topic_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("relay_clients.org_id"), nullable=False),
        sa.Column("topics_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("model", sa.Text()),
        sa.Column("refreshed_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_relay_topic_suggestions_org",
        "relay_topic_suggestions",
        ["org_id", "refreshed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_relay_topic_suggestions_org", table_name="relay_topic_suggestions")
    op.drop_table("relay_topic_suggestions")
