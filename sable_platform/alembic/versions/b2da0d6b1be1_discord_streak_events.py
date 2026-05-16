"""discord_streak_events (migration 043)

Discord streak events for the fit-check streak bot (PLAN.md SS10). One row
per image post in a configured #fitcheck channel. Powers /streak, reaction
score recompute (optimistic-locked via updated_at), and audit attribution.

Mirrors SQLite migration 043_discord_streak_events.sql for Postgres parity
(SablePlatform dual-migration rule).

Revision ID: b2da0d6b1be1
Revises: f1b2c3d4e042
Create Date: 2026-05-12 01:12:19.696936
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'b2da0d6b1be1'
down_revision: str | None = 'f1b2c3d4e042'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'discord_streak_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('channel_id', sa.Text(), nullable=False),
        sa.Column('post_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('posted_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('counted_for_day', sa.Text(), nullable=False),
        sa.Column('attachment_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('image_attachment_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('reaction_score', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('counts_for_streak', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('invalidated_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('invalidated_reason', sa.Text()),
        sa.Column('ingest_source', sa.Text(), nullable=False, server_default=sa.text("'gateway'")),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('guild_id', 'post_id', name='uq_discord_streak_events_guild_post'),
    )
    op.create_index(
        'idx_discord_streak_events_org_day',
        'discord_streak_events',
        ['org_id', 'counted_for_day'],
    )
    op.create_index(
        'idx_discord_streak_events_user_day',
        'discord_streak_events',
        ['org_id', 'user_id', 'counted_for_day'],
    )
    op.create_index(
        'idx_discord_streak_events_channel_posted',
        'discord_streak_events',
        ['org_id', 'channel_id', 'posted_at'],
    )
    op.create_index(
        'idx_discord_streak_events_user_reactions',
        'discord_streak_events',
        ['org_id', 'user_id', sa.text('reaction_score DESC')],
    )


def downgrade() -> None:
    op.drop_index('idx_discord_streak_events_user_reactions', table_name='discord_streak_events')
    op.drop_index('idx_discord_streak_events_channel_posted', table_name='discord_streak_events')
    op.drop_index('idx_discord_streak_events_user_day', table_name='discord_streak_events')
    op.drop_index('idx_discord_streak_events_org_day', table_name='discord_streak_events')
    op.drop_table('discord_streak_events')
