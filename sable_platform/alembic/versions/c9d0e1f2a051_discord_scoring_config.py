"""discord_scoring_config — Scored Mode V2 Pass B (migration 051)

Per-guild scoring state machine + per-guild tunables. Default state='off' —
no scoring, no API calls — until a mod explicitly /scoring set silent.

Revision ID: c9d0e1f2a051
Revises: b8c9d0e1f050
Create Date: 2026-05-16 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'c9d0e1f2a051'
down_revision: str | None = 'b8c9d0e1f050'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'discord_scoring_config',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False, server_default=sa.text("'off'")),
        sa.Column('state_changed_by', sa.Text(), nullable=True),
        sa.Column('state_changed_at', sa.Text(), nullable=True),
        sa.Column('reaction_threshold', sa.Integer(), nullable=False, server_default=sa.text('10')),
        sa.Column('thread_message_threshold', sa.Integer(), nullable=False, server_default=sa.text('100')),
        sa.Column('reveal_window_days', sa.Integer(), nullable=False, server_default=sa.text('7')),
        sa.Column('reveal_min_age_minutes', sa.Integer(), nullable=False, server_default=sa.text('10')),
        sa.Column('curve_window_days', sa.Integer(), nullable=False, server_default=sa.text('30')),
        sa.Column('cold_start_min_pool', sa.Integer(), nullable=False, server_default=sa.text('20')),
        sa.Column('model_id', sa.Text(), nullable=False, server_default=sa.text("'claude-sonnet-4-6'")),
        sa.Column('prompt_version', sa.Text(), nullable=False, server_default=sa.text("'rubric_v1'")),
        # `created_at` / `updated_at` are sa.Text() (ISO-Z strings) to match
        # schema.py — every discord_* table in the platform uses Text, not
        # TIMESTAMP, for timestamp columns. Originally TIMESTAMP here; aligned
        # in Pass C QA round 2 (M-NEW-1) to remove Postgres schema drift.
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
        sa.UniqueConstraint('guild_id', name='uq_discord_scoring_config_guild'),
    )


def downgrade() -> None:
    op.drop_table('discord_scoring_config')
