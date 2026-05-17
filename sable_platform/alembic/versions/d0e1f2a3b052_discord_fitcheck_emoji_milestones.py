"""discord_fitcheck_emoji_milestones — Scored Mode V2 Pass C (migration 052)

Per-(post_id, emoji_key, milestone) crossing state for reveal pipeline.
Durable across VPS restarts so the bot doesn't re-audit milestones every
recompute. UNIQUE constraint blocks the double-audit race.

Revision ID: d0e1f2a3b052
Revises: c9d0e1f2a051
Create Date: 2026-05-16 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'd0e1f2a3b052'
down_revision: str | None = 'c9d0e1f2a051'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # NOTE: `created_at` is sa.Text() (not TIMESTAMP) to match the schema.py
    # convention shared by every other discord_* table — ISO-Z strings
    # throughout the stack, no TZ-typed columns. Mirrors mig 050/051.
    op.create_table(
        'discord_fitcheck_emoji_milestones',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('post_id', sa.Text(), nullable=False),
        sa.Column('emoji_key', sa.Text(), nullable=False),
        sa.Column('milestone', sa.Integer(), nullable=False),
        sa.Column('crossed_at', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.Text(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'guild_id', 'post_id', 'emoji_key', 'milestone',
            name='uq_discord_fitcheck_emoji_milestones_crossing',
        ),
    )
    op.create_index(
        'idx_discord_fitcheck_emoji_milestones_post',
        'discord_fitcheck_emoji_milestones',
        ['guild_id', 'post_id'],
    )


def downgrade() -> None:
    op.drop_index(
        'idx_discord_fitcheck_emoji_milestones_post',
        table_name='discord_fitcheck_emoji_milestones',
    )
    op.drop_table('discord_fitcheck_emoji_milestones')
