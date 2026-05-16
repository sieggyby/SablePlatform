"""airlock — invite-source-aware member verification (migration 048)

Three new tables for the airlock feature (sable-roles). Mirrors SQLite
migration 048_airlock.sql for Postgres parity per SablePlatform's
dual-migration rule.

Tables:

* discord_invite_snapshot  — bot cache of guild.invites() state for diff
* discord_team_inviters    — allowlist of Sable-team user-IDs whose invites bypass
* discord_member_admit     — per-join ledger with airlock state machine

Revision ID: d6e7f8a9e048
Revises: c5d6e7f8e047
Create Date: 2026-05-16 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'd6e7f8a9e048'
down_revision: str | None = 'c5d6e7f8e047'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 2.1 discord_invite_snapshot
    op.create_table(
        'discord_invite_snapshot',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('code', sa.Text(), nullable=False),
        sa.Column('inviter_user_id', sa.Text(), nullable=True),
        sa.Column('uses', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('max_uses', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('expires_at', sa.Text(), nullable=True),
        sa.Column(
            'captured_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('guild_id', 'code', name='uq_discord_invite_snapshot_guild_code'),
    )
    op.create_index(
        'idx_discord_invite_snapshot_guild',
        'discord_invite_snapshot',
        ['guild_id'],
    )

    # 2.2 discord_team_inviters
    op.create_table(
        'discord_team_inviters',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column(
            'added_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('added_by', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('guild_id', 'user_id', name='uq_discord_team_inviters_guild_user'),
    )
    op.create_index(
        'idx_discord_team_inviters_guild',
        'discord_team_inviters',
        ['guild_id'],
    )

    # 2.3 discord_member_admit
    op.create_table(
        'discord_member_admit',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column(
            'joined_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('attributed_invite_code', sa.Text(), nullable=True),
        sa.Column('attributed_inviter_user_id', sa.Text(), nullable=True),
        sa.Column('is_team_invite', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('airlock_status', sa.Text(), nullable=False),
        sa.Column('decision_by', sa.Text(), nullable=True),
        sa.Column('decision_at', sa.Text(), nullable=True),
        sa.Column('decision_reason', sa.Text(), nullable=True),
        sa.CheckConstraint(
            "airlock_status IN ('held', 'auto_admitted', 'admitted', 'banned',"
            " 'kicked', 'left_during_airlock')",
            name='ck_discord_member_admit_status',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('guild_id', 'user_id', name='uq_discord_member_admit_guild_user'),
    )
    op.create_index(
        'idx_discord_member_admit_status',
        'discord_member_admit',
        ['guild_id', 'airlock_status'],
    )


def downgrade() -> None:
    op.drop_index('idx_discord_member_admit_status', table_name='discord_member_admit')
    op.drop_table('discord_member_admit')
    op.drop_index('idx_discord_team_inviters_guild', table_name='discord_team_inviters')
    op.drop_table('discord_team_inviters')
    op.drop_index('idx_discord_invite_snapshot_guild', table_name='discord_invite_snapshot')
    op.drop_table('discord_invite_snapshot')
