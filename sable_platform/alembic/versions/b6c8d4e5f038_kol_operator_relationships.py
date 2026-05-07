"""kol operator relationship-tagging table (migration 038)

Append-only history of operator status changes against (handle, client)
pairs. Status enum + visibility model documented in the SQL migration.
Mirrors SQLite migration 038_kol_operator_relationships.sql.

Revision ID: b6c8d4e5f038
Revises: a5b7c9d3e037
Create Date: 2026-05-07 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'b6c8d4e5f038'
down_revision: str | None = 'a5b7c9d3e037'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'kol_operator_relationships',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('handle_normalized', sa.Text(), nullable=False),
        sa.Column('client_id', sa.Text(), nullable=False),
        sa.Column('operator_id', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('note', sa.Text()),
        sa.Column('is_private', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        'idx_kor_handle_client',
        'kol_operator_relationships',
        ['handle_normalized', 'client_id'],
    )
    op.create_index(
        'idx_kor_operator',
        'kol_operator_relationships',
        ['operator_id', 'client_id'],
    )
    op.create_index(
        'idx_kor_created',
        'kol_operator_relationships',
        ['created_at'],
    )


def downgrade() -> None:
    op.drop_index('idx_kor_created', table_name='kol_operator_relationships')
    op.drop_index('idx_kor_operator', table_name='kol_operator_relationships')
    op.drop_index('idx_kor_handle_client', table_name='kol_operator_relationships')
    op.drop_table('kol_operator_relationships')
