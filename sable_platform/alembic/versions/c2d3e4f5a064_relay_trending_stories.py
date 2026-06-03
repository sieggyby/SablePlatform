"""trending-story autopilot — relay_trending_stories table (migration 064)

Mirrors SQLite migration 064_relay_trending_stories.sql for Postgres parity
(SablePlatform dual-migration rule). 100 percent ADDITIVE: CREATE TABLE +
CREATE INDEX only, no table rebuild. The Trending-Story Autopilot (Stage A
detect / Stage B auto-monitor / Stage C digest) persists bursting-AND-relevant
stories here. relevance/momentum/summary are interpretive; there is NO cost
column. See SableRelay/TRENDING_STORY_AUTOPILOT_PLAN.md.

Revision ID: c2d3e4f5a064
Revises: b1c2d3e4f063
Create Date: 2026-06-03 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c2d3e4f5a064"
down_revision = "b1c2d3e4f063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relay_trending_stories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "org_id",
            sa.Text(),
            sa.ForeignKey("relay_clients.org_id"),
            nullable=False,
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("relevance", sa.Float()),
        sa.Column("momentum", sa.Float()),
        sa.Column(
            "member_tweet_ids_json", sa.Text(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "monitor_terms_json", sa.Text(), nullable=False, server_default="[]"
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="emerging"),
        sa.Column(
            "first_seen_at", sa.Text(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen_at", sa.Text(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.Text()),
        sa.Column(
            "created_at", sa.Text(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_relay_trending_stories_feed",
        "relay_trending_stories",
        ["org_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_relay_trending_stories_feed",
        table_name="relay_trending_stories",
    )
    op.drop_table("relay_trending_stories")
