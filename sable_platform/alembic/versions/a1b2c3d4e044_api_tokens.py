"""api_tokens (migration 044)

Owner-issued bearer credentials for the alert-triage HTTP API MVP.
Stores SHA-256 hashes only; raw secrets are never persisted.

Mirrors SQLite migration 044_api_tokens.sql for Postgres parity
(SablePlatform dual-migration rule).

Revision ID: a1b2c3d4e044
Revises: b2da0d6b1be1
Create Date: 2026-05-12 02:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'a1b2c3d4e044'
down_revision: str | None = 'b2da0d6b1be1'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'api_tokens',
        sa.Column('token_id', sa.Text(), nullable=False),
        sa.Column('token_hash', sa.Text(), nullable=False),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('operator_id', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Text(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('last_used_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('revoked_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('enabled', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('scopes_json', sa.Text(), nullable=False,
                  server_default=sa.text("'[\"read_only\"]'")),
        sa.Column('org_scopes_json', sa.Text(), nullable=False,
                  server_default=sa.text("'[]'")),
        sa.PrimaryKeyConstraint('token_id'),
    )
    op.create_index(
        'idx_api_tokens_enabled', 'api_tokens', ['enabled', 'expires_at'],
    )
    op.create_index(
        'idx_api_tokens_operator', 'api_tokens', ['operator_id'],
    )


def downgrade() -> None:
    op.drop_index('idx_api_tokens_operator', table_name='api_tokens')
    op.drop_index('idx_api_tokens_enabled', table_name='api_tokens')
    op.drop_table('api_tokens')
