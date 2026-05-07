"""add kol_strength_score, verified, account_created_at to kol_candidates (migration 033)

Adds three columns to ``kol_candidates``:
  kol_strength_score   REAL    project-independent KOL strength (0-1).
  verified             INTEGER NOT NULL DEFAULT 0 (Twitter verified flag).
  account_created_at   TEXT    Twitter account creation timestamp.

Plus a partial index on kol_strength_score for sorted queries. Mirrors
the SQLite migration 033_kol_strength_score.sql.

Revision ID: c1d3e7f9a033
Revises: 8a2f4b7c9d12
Create Date: 2026-05-04 21:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c1d3e7f9a033'
down_revision: str | None = '8a2f4b7c9d12'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        'kol_candidates',
        sa.Column('kol_strength_score', sa.Float(), nullable=True),
    )
    op.add_column(
        'kol_candidates',
        sa.Column(
            'verified',
            sa.Integer(),
            server_default=sa.text('0'),
            nullable=False,
        ),
    )
    op.add_column(
        'kol_candidates',
        sa.Column('account_created_at', sa.Text(), nullable=True),
    )
    op.create_index(
        'idx_kol_candidates_strength',
        'kol_candidates',
        ['kol_strength_score'],
        unique=False,
        sqlite_where=sa.text('kol_strength_score IS NOT NULL'),
        postgresql_where=sa.text('kol_strength_score IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('idx_kol_candidates_strength', table_name='kol_candidates')
    op.drop_column('kol_candidates', 'account_created_at')
    op.drop_column('kol_candidates', 'verified')
    op.drop_column('kol_candidates', 'kol_strength_score')
