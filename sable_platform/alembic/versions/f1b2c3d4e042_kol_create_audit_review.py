"""kol_create_audit review fields (migration 042)

Per-row review state on the kol_create_audit table. Powers the per-user
"one map at a time" gate on /ops/kol-network and the admin approval page
at /ops/kol-network/admin/approvals.

Adds:
  - review_status TEXT NOT NULL DEFAULT 'approved'
                  ('pending'|'approved'|'rejected'). Historical rows are
                  backfilled to 'approved' so the per-user gate doesn't
                  retroactively trip on every operator. The wizard write
                  path stamps 'pending' explicitly for new submissions.
  - reviewed_by   TEXT NULL                        admin email at decision
  - reviewed_at   TEXT NULL                        ISO timestamp at decision
  - idx_kol_create_audit_review (email, review_status, endpoint)
    composite index for the picker's per-user pending count.

Mirrors SQLite migration 042_kol_create_audit_review.sql for Postgres
parity (SablePlatform dual-migration rule).

Revision ID: f1b2c3d4e042
Revises: e9f0a1b2c041
Create Date: 2026-05-11 18:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'f1b2c3d4e042'
down_revision: str | None = 'e9f0a1b2c041'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        'kol_create_audit',
        sa.Column(
            'review_status',
            sa.Text(),
            nullable=False,
            server_default=sa.text("'approved'"),
        ),
    )
    op.add_column(
        'kol_create_audit',
        sa.Column('reviewed_by', sa.Text(), nullable=True),
    )
    op.add_column(
        'kol_create_audit',
        sa.Column('reviewed_at', sa.Text(), nullable=True),
    )
    op.create_index(
        'idx_kol_create_audit_review',
        'kol_create_audit',
        ['email', 'review_status', 'endpoint'],
    )


def downgrade() -> None:
    op.drop_index(
        'idx_kol_create_audit_review', table_name='kol_create_audit'
    )
    op.drop_column('kol_create_audit', 'reviewed_at')
    op.drop_column('kol_create_audit', 'reviewed_by')
    op.drop_column('kol_create_audit', 'review_status')
