"""KOL wizard infrastructure (migration 040)

Three additions for the any-project KOL wizard build (see
~/Projects/SableKOL/docs/any_project_wizard_plan.md, v3 Codex round 2):

  1. kol_create_audit         New audit log for /api/ops/kol-network/* hits.
                               email is NULLABLE so anonymous failures can log.
  2. jobs.worker_id           Generic worker tag used by claim_next_job().
  3. job_steps.next_retry_at  Deferred-retry timestamp for 429-backoff steps.

Mirrors SQLite migration 040_kol_wizard_infra.sql for Postgres parity.

Revision ID: d8e0f1a2b040
Revises: c7d9e5f6a039
Create Date: 2026-05-08 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'd8e0f1a2b040'
down_revision: str | None = 'c7d9e5f6a039'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'kol_create_audit',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            'at_utc',
            sa.Text(),
            server_default=sa.text('CURRENT_TIMESTAMP'),
            nullable=False,
        ),
        sa.Column('email', sa.Text(), nullable=True),
        sa.Column('endpoint', sa.Text(), nullable=False),
        sa.Column('method', sa.Text(), nullable=False),
        sa.Column('outcome', sa.Text(), nullable=False),
        sa.Column('job_id', sa.Text(), nullable=True),
        sa.Column('ip', sa.Text(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.job_id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_kol_create_audit_email', 'kol_create_audit', ['email']
    )
    op.create_index(
        'idx_kol_create_audit_at', 'kol_create_audit', ['at_utc']
    )
    op.create_index(
        'idx_kol_create_audit_outcome', 'kol_create_audit', ['outcome']
    )

    op.add_column('jobs', sa.Column('worker_id', sa.Text(), nullable=True))
    op.create_index('idx_jobs_worker', 'jobs', ['worker_id'])

    op.add_column('job_steps', sa.Column('next_retry_at', sa.Text(), nullable=True))
    op.create_index(
        'idx_job_steps_next_retry', 'job_steps', ['next_retry_at']
    )


def downgrade() -> None:
    op.drop_index('idx_job_steps_next_retry', table_name='job_steps')
    op.drop_column('job_steps', 'next_retry_at')

    op.drop_index('idx_jobs_worker', table_name='jobs')
    op.drop_column('jobs', 'worker_id')

    op.drop_index('idx_kol_create_audit_outcome', table_name='kol_create_audit')
    op.drop_index('idx_kol_create_audit_at', table_name='kol_create_audit')
    op.drop_index('idx_kol_create_audit_email', table_name='kol_create_audit')
    op.drop_table('kol_create_audit')
