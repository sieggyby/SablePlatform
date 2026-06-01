"""operator reply-suggestion feature (migration 056)

Three tables backing the SableWeb /ops operator reply-suggestion feature:
- operator_reply_quota: persistent per-(operator, UTC-day) generation counter
  (50/day default, raisable). Checked + incremented atomically before spend.
- reply_suggestions: a log of every generation (variants, model, cost, source).
- reply_outcomes: actual-post mapping for assisted-vs-organic lift measurement.
  Strict child of reply_suggestions; UNIQUE(suggestion_id, posted_tweet_id)
  keeps reconciliation re-runs idempotent.

See Sable_Slopper/docs/OPERATOR_REPLY_WEB_FEATURE.md.

Revision ID: b4c5d6e7f056
Revises: a3b4c5d6e055
Create Date: 2026-05-30 19:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'b4c5d6e7f056'
down_revision: str | None = 'a3b4c5d6e055'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'operator_reply_quota',
        sa.Column('operator_handle', sa.Text(), nullable=False),
        sa.Column('day_utc', sa.Text(), nullable=False),
        sa.Column('org_id', sa.Text(), nullable=True),
        sa.Column('count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('updated_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('operator_handle', 'day_utc'),
    )
    op.create_table(
        'reply_suggestions',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('operator_handle', sa.Text(), nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('source_tweet_id', sa.Text(), nullable=False),
        sa.Column('source_author', sa.Text(), nullable=True),
        sa.Column('source_text', sa.Text(), nullable=True),
        sa.Column('variants_json', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('model', sa.Text(), nullable=True),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.Column('generated_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.org_id']),
    )
    op.create_table(
        'reply_outcomes',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('suggestion_id', sa.Text(), nullable=False),
        sa.Column('posted_tweet_id', sa.Text(), nullable=False),
        sa.Column('posted_at', sa.Text(), nullable=True),
        sa.Column('chosen_variant_idx', sa.Integer(), nullable=True),
        sa.Column('was_edited', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('engagement_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('recorded_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['suggestion_id'], ['reply_suggestions.id']),
    )
    op.create_index(
        'ix_reply_suggestions_match',
        'reply_suggestions',
        ['operator_handle', 'source_tweet_id'],
    )
    op.create_index(
        'ix_reply_suggestions_org',
        'reply_suggestions',
        ['org_id', 'generated_at'],
    )
    op.create_index(
        'ux_reply_outcomes_match',
        'reply_outcomes',
        ['suggestion_id', 'posted_tweet_id'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('ux_reply_outcomes_match', table_name='reply_outcomes')
    op.drop_index('ix_reply_suggestions_org', table_name='reply_suggestions')
    op.drop_index('ix_reply_suggestions_match', table_name='reply_suggestions')
    op.drop_table('reply_outcomes')
    op.drop_table('reply_suggestions')
    op.drop_table('operator_reply_quota')
