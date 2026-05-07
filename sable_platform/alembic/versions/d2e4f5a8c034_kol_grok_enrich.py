"""add Grok-enrichment fields to kol_candidates (migration 034)

Six new columns:
  listed_count        INTEGER (nullable)
  tweets_count        INTEGER (nullable)
  following_count     INTEGER (nullable)
  credibility_signal  TEXT    (nullable; values: high|medium|low|unclear)
  real_name_known     INTEGER NOT NULL DEFAULT 0
  notes               TEXT    (nullable)

Plus a partial index on credibility_signal. Mirrors SQLite migration
034_kol_grok_enrich.sql.

Revision ID: d2e4f5a8c034
Revises: c1d3e7f9a033
Create Date: 2026-05-04 22:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'd2e4f5a8c034'
down_revision: str | None = 'c1d3e7f9a033'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column('kol_candidates', sa.Column('listed_count', sa.Integer(), nullable=True))
    op.add_column('kol_candidates', sa.Column('tweets_count', sa.Integer(), nullable=True))
    op.add_column('kol_candidates', sa.Column('following_count', sa.Integer(), nullable=True))
    op.add_column('kol_candidates', sa.Column('credibility_signal', sa.Text(), nullable=True))
    op.add_column(
        'kol_candidates',
        sa.Column('real_name_known', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )
    op.add_column('kol_candidates', sa.Column('notes', sa.Text(), nullable=True))
    op.create_index(
        'idx_kol_candidates_credibility',
        'kol_candidates',
        ['credibility_signal'],
        unique=False,
        sqlite_where=sa.text('credibility_signal IS NOT NULL'),
        postgresql_where=sa.text('credibility_signal IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('idx_kol_candidates_credibility', table_name='kol_candidates')
    op.drop_column('kol_candidates', 'notes')
    op.drop_column('kol_candidates', 'real_name_known')
    op.drop_column('kol_candidates', 'credibility_signal')
    op.drop_column('kol_candidates', 'following_count')
    op.drop_column('kol_candidates', 'tweets_count')
    op.drop_column('kol_candidates', 'listed_count')
