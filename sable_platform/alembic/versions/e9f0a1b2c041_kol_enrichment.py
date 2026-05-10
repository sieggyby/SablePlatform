"""kol_enrichment cache (migration 041)

Per-(candidate, operator) Grok enrichment payload cache for the KO-3
redesign. KO-3 v1 was a "draft cold-intro opener" — the operator
feedback (2026-05-10) was that the drafts were unusable and the actual
need is INTEL the operator uses to write their own outreach. This table
caches the structured intel so re-rendering doesn't re-bill xAI.

Mirrors SQLite migration 041_kol_enrichment.sql for Postgres parity.

Revision ID: e9f0a1b2c041
Revises: d8e0f1a2b040
Create Date: 2026-05-10 16:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'e9f0a1b2c041'
down_revision: str | None = 'd8e0f1a2b040'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'kol_enrichment',
        sa.Column('enrichment_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('candidate_id', sa.Integer(), nullable=False),
        sa.Column('operator_email', sa.Text(), nullable=False),
        sa.Column('operator_persona', sa.Text(), nullable=False),
        sa.Column(
            'fetched_at',
            sa.Text(),
            server_default=sa.text('CURRENT_TIMESTAMP'),
            nullable=False,
        ),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('grok_model', sa.Text(), nullable=True),
        sa.Column('cost_usd', sa.Float(), server_default='0', nullable=True),
        sa.ForeignKeyConstraint(
            ['candidate_id'], ['kol_candidates.candidate_id']
        ),
        sa.PrimaryKeyConstraint('enrichment_id'),
    )
    op.create_index(
        'idx_kol_enrichment_lookup',
        'kol_enrichment',
        ['candidate_id', 'operator_email', sa.text('fetched_at DESC')],
    )
    op.create_index(
        'idx_kol_enrichment_operator', 'kol_enrichment', ['operator_email']
    )


def downgrade() -> None:
    op.drop_index('idx_kol_enrichment_operator', table_name='kol_enrichment')
    op.drop_index('idx_kol_enrichment_lookup', table_name='kol_enrichment')
    op.drop_table('kol_enrichment')
