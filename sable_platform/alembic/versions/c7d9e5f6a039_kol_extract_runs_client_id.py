"""kol_extract_runs.client_id column for per-client scoping (migration 039)

Adds client_id column with default '_external'. Backfills existing rows to
'solstitch' since those are all SolStitch runs. Mirrors SQLite migration
039_kol_extract_runs_client_id.sql.

Revision ID: c7d9e5f6a039
Revises: b6c8d4e5f038
Create Date: 2026-05-07 14:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'c7d9e5f6a039'
down_revision: str | None = 'b6c8d4e5f038'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        'kol_extract_runs',
        sa.Column('client_id', sa.Text(), nullable=False, server_default="_external"),
    )
    op.execute(
        "UPDATE kol_extract_runs SET client_id = 'solstitch' WHERE client_id = '_external'"
    )
    op.create_index(
        'idx_kol_extract_runs_client',
        'kol_extract_runs',
        ['client_id', 'extract_type', 'cursor_completed'],
    )


def downgrade() -> None:
    op.drop_index('idx_kol_extract_runs_client', table_name='kol_extract_runs')
    op.drop_column('kol_extract_runs', 'client_id')
