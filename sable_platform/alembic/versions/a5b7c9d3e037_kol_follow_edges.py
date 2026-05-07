"""kol follow-graph extraction tables (migration 037)

Adds kol_extract_runs (parent run record) and kol_follow_edges (edge table).
The cursor_completed flag on kol_extract_runs distinguishes complete graphs
from partial extractions so downstream clustering and kingmaker queries can
gate on clean runs. Mirrors SQLite migration 037_kol_follow_edges.sql.

Revision ID: a5b7c9d3e037
Revises: f4a6b8c2e036
Create Date: 2026-05-06 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'a5b7c9d3e037'
down_revision: str | None = 'f4a6b8c2e036'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'kol_extract_runs',
        sa.Column('run_id', sa.Text(), primary_key=True),
        sa.Column('target_handle_normalized', sa.Text(), nullable=False),
        sa.Column('target_user_id', sa.Text()),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('extract_type', sa.Text(), nullable=False),
        sa.Column('started_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.Text()),
        sa.Column('cursor_completed', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('last_cursor', sa.Text()),
        sa.Column('pages_fetched', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('rows_inserted', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('expected_count', sa.Integer()),
        sa.Column('partial_failure_reason', sa.Text()),
        sa.Column('cost_usd_logged', sa.Float(), nullable=False, server_default=sa.text('0')),
    )
    op.create_index(
        'idx_kol_extract_runs_target',
        'kol_extract_runs',
        ['target_handle_normalized', 'extract_type'],
    )
    op.create_index(
        'idx_kol_extract_runs_completed',
        'kol_extract_runs',
        ['cursor_completed'],
    )

    op.create_table(
        'kol_follow_edges',
        sa.Column('run_id', sa.Text(), sa.ForeignKey('kol_extract_runs.run_id'), nullable=False),
        sa.Column('follower_id', sa.Text(), nullable=False),
        sa.Column('follower_handle', sa.Text()),
        sa.Column('followed_id', sa.Text(), nullable=False),
        sa.Column('followed_handle', sa.Text(), nullable=False),
        sa.Column('fetched_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('run_id', 'follower_id', 'followed_id'),
    )
    op.create_index(
        'idx_kol_follow_edges_followed',
        'kol_follow_edges',
        ['followed_id'],
    )
    op.create_index(
        'idx_kol_follow_edges_followed_handle',
        'kol_follow_edges',
        ['followed_handle'],
    )
    op.create_index(
        'idx_kol_follow_edges_follower',
        'kol_follow_edges',
        ['follower_id'],
    )


def downgrade() -> None:
    op.drop_index('idx_kol_follow_edges_follower', table_name='kol_follow_edges')
    op.drop_index('idx_kol_follow_edges_followed_handle', table_name='kol_follow_edges')
    op.drop_index('idx_kol_follow_edges_followed', table_name='kol_follow_edges')
    op.drop_table('kol_follow_edges')
    op.drop_index('idx_kol_extract_runs_completed', table_name='kol_extract_runs')
    op.drop_index('idx_kol_extract_runs_target', table_name='kol_extract_runs')
    op.drop_table('kol_extract_runs')
