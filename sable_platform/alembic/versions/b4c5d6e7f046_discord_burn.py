"""discord_burn tables (migration 046)

Burn-me opt-in state + random-roast dedup log for sable-roles V2 burn-me feature.
- discord_burn_optins: per (guild_id, user_id) opt-in with mode (once|persist) and audit fields.
- discord_burn_random_log: append-only log of random-bypass roasts, indexed for 7d per-target dedup.

Mirrors SQLite migration 046_discord_burn.sql for Postgres parity
(SablePlatform dual-migration rule).

Revision ID: b4c5d6e7f046
Revises: a3b4c5d6e045
Create Date: 2026-05-15 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'b4c5d6e7f046'
down_revision: str | None = 'a3b4c5d6e045'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'discord_burn_optins',
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('mode', sa.Text(), nullable=False),
        sa.Column('opted_in_by', sa.Text(), nullable=False),
        sa.Column(
            'opted_in_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint('guild_id', 'user_id'),
    )
    op.create_table(
        'discord_burn_random_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column(
            'roasted_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_discord_burn_random_log_recent',
        'discord_burn_random_log',
        ['guild_id', 'user_id', sa.text('roasted_at DESC')],
    )


def downgrade() -> None:
    op.drop_index('idx_discord_burn_random_log_recent', table_name='discord_burn_random_log')
    op.drop_table('discord_burn_random_log')
    op.drop_table('discord_burn_optins')
