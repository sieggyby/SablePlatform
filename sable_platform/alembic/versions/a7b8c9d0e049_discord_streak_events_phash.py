"""discord_streak_events.image_phash — Scored Mode V2 Pass A (migration 049)

Adds the perceptual-hash column + supporting index. Captured at post time
regardless of scoring state — collision detection (repost / theft) is the
Pass A surface that works even when scoring is Off.

Revision ID: a7b8c9d0e049
Revises: d6e7f8a9e048
Create Date: 2026-05-16 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'a7b8c9d0e049'
down_revision: str | None = 'd6e7f8a9e048'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        'discord_streak_events',
        sa.Column('image_phash', sa.Text(), nullable=True),
    )
    op.create_index(
        'idx_discord_streak_events_org_phash',
        'discord_streak_events',
        ['org_id', 'image_phash'],
    )


def downgrade() -> None:
    op.drop_index('idx_discord_streak_events_org_phash', table_name='discord_streak_events')
    op.drop_column('discord_streak_events', 'image_phash')
