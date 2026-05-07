"""add platform_presence_json column to kol_candidates (migration 036)

Cross-platform presence tracking: Instagram, TikTok, Threads, YouTube etc.
Stored as a JSON blob keyed by platform name. Lets the matcher score across
platforms (e.g. IG matters more than X for fashion). Mirrors the SQLite
migration 036_kol_platform_presence.sql for Postgres parity.

Revision ID: f4a6b8c2e036
Revises: e3f5a7b9d035
Create Date: 2026-05-05 15:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'f4a6b8c2e036'
down_revision: str | None = 'e3f5a7b9d035'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        'kol_candidates',
        sa.Column('platform_presence_json', sa.Text(), nullable=False, server_default='{}'),
    )


def downgrade() -> None:
    op.drop_column('kol_candidates', 'platform_presence_json')
