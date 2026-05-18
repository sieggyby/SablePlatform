"""discord_state_pins — state-pin surface (migration 054)

One row per (guild_id, characteristic). Tracks the currently-pinned
"stitzy state" message in the per-guild ops channel so the bot's
state-pin rotation can detect lost races (optimistic-lock UPDATE)
and clean up orphan pins on restart.

UNIQUE (guild_id, characteristic) enforces one-pin-per-dimension.

Revision ID: f2a3b4c5d054
Revises: e1f2a3b4c053
Create Date: 2026-05-17 21:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'f2a3b4c5d054'
down_revision: str | None = 'e1f2a3b4c053'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # NOTE: timestamp columns are sa.Text() (post-mig-053 alignment).
    # NOTE: id column is sa.BigInteger() to mirror the Alembic-side
    # precedent at migs 050 + 052. schema.py uses Integer; the pre-
    # existing drift is documented + accepted scope-out per the state-pin
    # plan PR4-H2 disposition.
    op.create_table(
        'discord_state_pins',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('characteristic', sa.Text(), nullable=False),
        sa.Column('channel_id', sa.Text(), nullable=False),
        sa.Column('message_id', sa.Text(), nullable=False),
        sa.Column('posted_at', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.Text(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'updated_at',
            sa.Text(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'guild_id', 'characteristic',
            name='uq_discord_state_pins_guild_characteristic',
        ),
    )
    op.create_index(
        'idx_discord_state_pins_guild',
        'discord_state_pins',
        ['guild_id'],
    )


def downgrade() -> None:
    op.drop_index(
        'idx_discord_state_pins_guild',
        table_name='discord_state_pins',
    )
    op.drop_table('discord_state_pins')
