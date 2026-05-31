"""reply_suggestions.clip_media_kind — prefer-image throttle (migration 060)

Mirrors SQLite migration 060_reply_clip_media_kind.sql for Postgres parity
(SablePlatform dual-migration rule). Adds a nullable TEXT column recording the
media kind (image / video / none) a generated reply attached, backing the
operator reply-assist prefer-image ranking + per-operator anti-spam image
throttle in Slopper /reply. Older rows and clip-less replies stay NULL.

Revision ID: e7f8e9a0b060
Revises: d6e7f8e9a059
Create Date: 2026-05-31 23:10:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7f8e9a0b060"
down_revision = "d6e7f8e9a059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reply_suggestions", sa.Column("clip_media_kind", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("reply_suggestions", "clip_media_kind")
