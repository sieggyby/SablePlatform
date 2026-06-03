"""reply-learning — tell-score persistence + embedding cache (migration 063)

Mirrors SQLite migration 063_reply_learning.sql for Postgres parity
(SablePlatform dual-migration rule). 100 percent ADDITIVE: ADD COLUMN only,
no table rebuild, no CHECK change, no NOT-NULL relax. Persists the §10
anti-AI-tell tell-score + tell-flags on reply_suggestions (the §10.4 "its own
dashboard/migration" deferral) and caches the §8 P3 embedding-ranker vector on
relay_tweets. All four columns are nullable with no server_default. See
SableRelay/REPLY_OPPORTUNITY_FEED_PLAN.md §6 / §8 P3 / §10.4.

Revision ID: b1c2d3e4f063
Revises: a0b1c2d3e062
Create Date: 2026-06-03 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f063"
down_revision = "a0b1c2d3e062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # reply_suggestions (056/062): persist the §10 humanizer signals.
    op.add_column("reply_suggestions", sa.Column("tell_score", sa.Float()))
    op.add_column("reply_suggestions", sa.Column("tell_flags_json", sa.Text()))

    # relay_tweets (057/062): cache the P3 ranker embedding (vector + model id).
    op.add_column("relay_tweets", sa.Column("embedding_json", sa.Text()))
    op.add_column("relay_tweets", sa.Column("embedding_model", sa.Text()))


def downgrade() -> None:
    op.drop_column("relay_tweets", "embedding_model")
    op.drop_column("relay_tweets", "embedding_json")
    op.drop_column("reply_suggestions", "tell_flags_json")
    op.drop_column("reply_suggestions", "tell_score")
