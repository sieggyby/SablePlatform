"""discord_guild_config (migration 045)

Per-guild config for sable-roles V2 features:
- relax_mode_on: when 1, sable-roles skips delete+DM + auto-threading in fit-check channel.
- current_burn_mode: global default ('once'|'persist') applied to /burn-me opt-ins (V2 burn-me).

Mirrors SQLite migration 045_discord_guild_config.sql for Postgres parity
(SablePlatform dual-migration rule).

Revision ID: a3b4c5d6e045
Revises: a1b2c3d4e044
Create Date: 2026-05-15 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'a3b4c5d6e045'
down_revision: str | None = 'a1b2c3d4e044'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'discord_guild_config',
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('relax_mode_on', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column(
            'current_burn_mode',
            sa.Text(),
            nullable=False,
            server_default=sa.text("'once'"),
        ),
        sa.Column(
            'updated_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('updated_by', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('guild_id'),
    )


def downgrade() -> None:
    op.drop_table('discord_guild_config')
