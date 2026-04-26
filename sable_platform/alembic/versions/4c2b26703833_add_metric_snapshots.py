"""add metric_snapshots (migration 031)

Week-over-week metric persistence for client_checkin_loop. One row per
(org_id, snapshot_date). Mirrors the SQLite migration 031_metric_snapshots.sql
for Postgres parity.

Revision ID: 4c2b26703833
Revises: f84560335ce3
Create Date: 2026-04-26 14:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '4c2b26703833'
down_revision: str | None = 'f84560335ce3'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'metric_snapshots',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('snapshot_date', sa.Text(), nullable=False),
        sa.Column('metrics_json', sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('created_at', sa.Text(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.org_id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'snapshot_date'),
    )
    op.create_index(
        'idx_metric_snapshots_org_date',
        'metric_snapshots',
        ['org_id', 'snapshot_date'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('idx_metric_snapshots_org_date', table_name='metric_snapshots')
    op.drop_table('metric_snapshots')
