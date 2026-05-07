"""add location column to kol_candidates (migration 035)

Free-form text from X profile location field. Mirrors the SQLite migration
035_kol_location.sql for Postgres parity.

Revision ID: e3f5a7b9d035
Revises: d2e4f5a8c034
Create Date: 2026-05-04 22:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'e3f5a7b9d035'
down_revision: str | None = 'd2e4f5a8c034'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column('kol_candidates', sa.Column('location', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('kol_candidates', 'location')
