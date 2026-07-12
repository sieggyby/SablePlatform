"""quality corpus media + reply metadata (mig 085)

Mirrors SQLite migration 085_quality_media_reply.sql for Postgres parity (the
dual-migration rule). K1 instrumentation: the fixed-age quality corpus gains
media_kinds / is_reply / in_reply_to_x_id, parsed from the SocialData raw
object at ingest (Slopper quality tap) and backfilled from relay_tweets.raw
for pre-085 rows. NULL = not yet parsed; media_kinds '' = parsed, no media.
100% additive.

Revision ID: b4c5d6e7f085
Revises: a3b4c5d6e084
Create Date: 2026-07-12 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4c5d6e7f085"
down_revision = "a3b4c5d6e084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("relay_quality_tweets", sa.Column("media_kinds", sa.Text(), nullable=True))
    op.add_column("relay_quality_tweets", sa.Column("is_reply", sa.Integer(), nullable=True))
    op.add_column("relay_quality_tweets", sa.Column("in_reply_to_x_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("relay_quality_tweets", "in_reply_to_x_id")
    op.drop_column("relay_quality_tweets", "is_reply")
    op.drop_column("relay_quality_tweets", "media_kinds")
