"""Shared SocialData cache — relay_search_windows + posted_at/source columns (mig 082)

Mirrors SQLite migration 082_shared_tweet_cache.sql for Postgres parity (the
dual-migration rule). HAND-WRITTEN (not --autogenerate), including the UNIQUE
constraint. Layer B closed-window search cache (a past, closed search window is
final — the first system caches the result-set, the second reuses it), plus
relay_tweets.posted_at (the Layer-A 14-day engagement-plateau gate + the
bidirectional-flow routing gates) and provenance `source` columns — on
relay_tweet_snapshots, source='cult_final' + target_age_hours=-1 marks the K1-pure
final-engagement track that fixed-age consumers can never pool. 100% additive.

Revision ID: e1f2a3b4c082
Revises: d0e1f2a3b081
Create Date: 2026-07-03 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c082"
down_revision = "d0e1f2a3b081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relay_search_windows",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("query_norm", sa.Text(), nullable=False),
        sa.Column("window_start", sa.Text(), nullable=False),
        sa.Column("window_end", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("result_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("result_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source", sa.Text(), nullable=True),
        sa.UniqueConstraint("query_norm", "window_start", "window_end",
                            name="uq_relay_search_windows_window"),
    )
    op.create_index(
        "ix_relay_search_windows_query", "relay_search_windows", ["query_norm", "window_start"]
    )
    op.add_column("relay_tweets", sa.Column("posted_at", sa.Text(), nullable=True))
    op.add_column("relay_tweets", sa.Column("source", sa.Text(), nullable=True))
    op.add_column("relay_tweet_snapshots", sa.Column("source", sa.Text(), nullable=True))
    op.create_index("ix_relay_tweets_posted_at", "relay_tweets", ["posted_at"])


def downgrade() -> None:
    op.drop_index("ix_relay_tweets_posted_at", table_name="relay_tweets")
    op.drop_column("relay_tweet_snapshots", "source")
    op.drop_column("relay_tweets", "source")
    op.drop_column("relay_tweets", "posted_at")
    op.drop_index("ix_relay_search_windows_query", table_name="relay_search_windows")
    op.drop_table("relay_search_windows")
