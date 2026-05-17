"""discord_fitcheck_scores — Scored Mode V2 Pass B (migration 050)

One row per scored fit (success/failed). percentile frozen at scoring time;
reveal_* columns ship now even though Pass C ships the reveal pipeline in a
follow-up PR — schema parity tests pin the table shape so migration 050 is
the source of truth across both PRs.

Revision ID: b8c9d0e1f050
Revises: a7b8c9d0e049
Create Date: 2026-05-16 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'b8c9d0e1f050'
down_revision: str | None = 'a7b8c9d0e049'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'discord_fitcheck_scores',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('post_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('posted_at', sa.Text(), nullable=False),
        sa.Column('scored_at', sa.Text(), nullable=False),
        sa.Column('model_id', sa.Text(), nullable=False),
        sa.Column('prompt_version', sa.Text(), nullable=False),
        sa.Column('score_status', sa.Text(), nullable=False),
        sa.Column('score_error', sa.Text(), nullable=True),
        sa.Column('axis_cohesion', sa.Integer(), nullable=True),
        sa.Column('axis_execution', sa.Integer(), nullable=True),
        sa.Column('axis_concept', sa.Integer(), nullable=True),
        sa.Column('axis_catch', sa.Integer(), nullable=True),
        sa.Column('raw_total', sa.Integer(), nullable=True),
        sa.Column('catch_detected', sa.Text(), nullable=True),
        sa.Column('catch_naming_class', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('axis_rationales_json', sa.Text(), nullable=True),
        sa.Column('curve_basis', sa.Text(), nullable=True),
        sa.Column('pool_size_at_score_time', sa.Integer(), nullable=True),
        sa.Column('percentile', sa.Float(), nullable=True),
        sa.Column('reveal_eligible', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('reveal_fired_at', sa.Text(), nullable=True),
        sa.Column('reveal_post_id', sa.Text(), nullable=True),
        sa.Column('reveal_trigger', sa.Text(), nullable=True),
        sa.Column('invalidated_at', sa.Text(), nullable=True),
        sa.Column('invalidated_reason', sa.Text(), nullable=True),
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
        sa.UniqueConstraint('guild_id', 'post_id', name='uq_discord_fitcheck_scores_guild_post'),
    )
    op.create_index(
        'idx_discord_fitcheck_scores_user_pct',
        'discord_fitcheck_scores',
        ['org_id', 'user_id', sa.text('percentile DESC')],
    )
    op.create_index(
        'idx_discord_fitcheck_scores_org_posted',
        'discord_fitcheck_scores',
        ['org_id', 'posted_at'],
    )
    op.create_index(
        'idx_discord_fitcheck_scores_status',
        'discord_fitcheck_scores',
        ['org_id', 'score_status'],
    )
    op.create_index(
        'idx_discord_fitcheck_scores_reveal_fired',
        'discord_fitcheck_scores',
        ['org_id', 'reveal_fired_at'],
    )


def downgrade() -> None:
    op.drop_index('idx_discord_fitcheck_scores_reveal_fired', table_name='discord_fitcheck_scores')
    op.drop_index('idx_discord_fitcheck_scores_status', table_name='discord_fitcheck_scores')
    op.drop_index('idx_discord_fitcheck_scores_org_posted', table_name='discord_fitcheck_scores')
    op.drop_index('idx_discord_fitcheck_scores_user_pct', table_name='discord_fitcheck_scores')
    op.drop_table('discord_fitcheck_scores')
