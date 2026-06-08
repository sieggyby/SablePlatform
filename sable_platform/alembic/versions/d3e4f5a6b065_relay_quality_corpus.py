"""tweet-quality corpus — relay_quality_accounts / relay_quality_tweets / relay_tweet_snapshots (migration 065)

Mirrors SQLite migration 065_relay_quality_corpus.sql for Postgres parity
(SablePlatform dual-migration rule). 100 percent ADDITIVE: CREATE TABLE +
CREATE INDEX only, no table rebuild. The tweet-quality corpus store holds a
curated/stratified bank of CT accounts, the tweets sampled from them, and a
longitudinal engagement-decay log per tweet (snapshots repeated at target
ages). band/kol_strength/archetype_json are interpretive; snapshot metrics are
measured; there is NO cost column. See SableRelay/QUALITY_CORPUS_PLAN.md.

Revision ID: d3e4f5a6b065
Revises: c2d3e4f5a064
Create Date: 2026-06-05 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d3e4f5a6b065"
down_revision = "c2d3e4f5a064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relay_quality_accounts",
        sa.Column("handle", sa.Text(), primary_key=True),
        sa.Column("band", sa.Text()),
        sa.Column("kol_strength", sa.Float()),
        sa.Column("archetype_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source", sa.Text()),
        sa.Column("followers_snapshot", sa.Integer()),
        sa.Column("active", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "added_at", sa.Text(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "relay_quality_tweets",
        sa.Column("tweet_x_id", sa.Text(), primary_key=True),
        sa.Column("author_handle", sa.Text()),
        sa.Column("posted_at", sa.Text()),
        sa.Column("text", sa.Text()),
        sa.Column("band", sa.Text()),
        sa.Column(
            "first_seen_at", sa.Text(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "relay_tweet_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tweet_x_id", sa.Text(), nullable=False),
        sa.Column("target_age_hours", sa.Integer(), nullable=False),
        sa.Column(
            "taken_at", sa.Text(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("age_hours", sa.Float()),
        sa.Column("likes", sa.Integer()),
        sa.Column("retweets", sa.Integer()),
        sa.Column("replies", sa.Integer()),
        sa.Column("quotes", sa.Integer()),
        sa.Column("bookmarks", sa.Integer()),
        sa.Column("views", sa.Integer()),
        sa.Column("author_followers", sa.Integer()),
        sa.Column("status", sa.Text(), nullable=False, server_default="ok"),
    )
    op.create_index(
        "ix_relay_tweet_snapshots_tweet",
        "relay_tweet_snapshots",
        ["tweet_x_id"],
    )
    op.create_index(
        "ix_relay_quality_tweets_posted",
        "relay_quality_tweets",
        ["posted_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_relay_quality_tweets_posted",
        table_name="relay_quality_tweets",
    )
    op.drop_index(
        "ix_relay_tweet_snapshots_tweet",
        table_name="relay_tweet_snapshots",
    )
    op.drop_table("relay_tweet_snapshots")
    op.drop_table("relay_quality_tweets")
    op.drop_table("relay_quality_accounts")
