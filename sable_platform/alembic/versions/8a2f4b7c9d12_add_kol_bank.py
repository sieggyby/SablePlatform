"""add SableKOL bank tables (migration 032)

Three tables for the SableKOL Phase 0 bank-backed KOL matcher:
  kol_candidates                    — bank rows with surrogate PK and a partial
                                      unique index (is_unresolved=0) for live handles.
  project_profiles_external         — path-(ii) lite-profile cache; last_enriched_at
                                      drives a 7-day TTL on paid_basic rows.
  kol_handle_resolution_conflicts   — audit/triage log for handle resolution collisions.

Mirrors SQLite migration 032_kol_bank.sql for Postgres parity.
See ~/Projects/SableKOL/PLAN.md for design rationale.

Revision ID: 8a2f4b7c9d12
Revises: 4c2b26703833
Create Date: 2026-05-04 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8a2f4b7c9d12'
down_revision: str | None = '4c2b26703833'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'kol_candidates',
        sa.Column('candidate_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('twitter_id', sa.Text(), nullable=True),
        sa.Column('handle_normalized', sa.Text(), nullable=False),
        sa.Column('is_unresolved', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('handle_history_json', sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column('display_name', sa.Text(), nullable=True),
        sa.Column('bio_snapshot', sa.Text(), nullable=True),
        sa.Column('followers_snapshot', sa.Integer(), nullable=True),
        sa.Column('discovery_sources_json', sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column('first_seen_at', sa.Text(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('last_seen_at', sa.Text(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('archetype_tags_json', sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column('sector_tags_json', sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            'sable_relationship_json',
            sa.Text(),
            server_default=sa.text("'{\"communities\":[],\"operators\":[]}'"),
            nullable=False,
        ),
        sa.Column('enrichment_tier', sa.Text(), server_default=sa.text("'none'"), nullable=False),
        sa.Column('last_enriched_at', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column('manual_notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('candidate_id'),
    )
    op.create_index(
        'idx_kol_candidates_handle_live',
        'kol_candidates',
        ['handle_normalized'],
        unique=True,
        sqlite_where=sa.text('is_unresolved = 0'),
        postgresql_where=sa.text('is_unresolved = 0'),
    )
    op.create_index(
        'idx_kol_candidates_twitter_id',
        'kol_candidates',
        ['twitter_id'],
        unique=False,
        sqlite_where=sa.text('twitter_id IS NOT NULL'),
        postgresql_where=sa.text('twitter_id IS NOT NULL'),
    )
    op.create_index(
        'idx_kol_candidates_status',
        'kol_candidates',
        ['status'],
        unique=False,
    )

    op.create_table(
        'project_profiles_external',
        sa.Column('handle_normalized', sa.Text(), nullable=False),
        sa.Column('twitter_id', sa.Text(), nullable=True),
        sa.Column('sector_tags_json', sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column('themes_json', sa.Text(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column('profile_blob', sa.Text(), nullable=True),
        sa.Column('enrichment_source', sa.Text(), server_default=sa.text("'manual_only'"), nullable=False),
        sa.Column('last_enriched_at', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('last_used_at', sa.Text(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('handle_normalized'),
    )

    op.create_table(
        'kol_handle_resolution_conflicts',
        sa.Column('conflict_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('incoming_candidate_id', sa.Integer(), nullable=False),
        sa.Column('existing_candidate_id', sa.Integer(), nullable=False),
        sa.Column('resolved_twitter_id', sa.Text(), nullable=True),
        sa.Column('detected_at', sa.Text(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('resolution_state', sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column('resolved_at', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['incoming_candidate_id'], ['kol_candidates.candidate_id']),
        sa.ForeignKeyConstraint(['existing_candidate_id'], ['kol_candidates.candidate_id']),
        sa.PrimaryKeyConstraint('conflict_id'),
    )
    op.create_index(
        'idx_kol_conflicts_state',
        'kol_handle_resolution_conflicts',
        ['resolution_state'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('idx_kol_conflicts_state', table_name='kol_handle_resolution_conflicts')
    op.drop_table('kol_handle_resolution_conflicts')
    op.drop_table('project_profiles_external')
    op.drop_index('idx_kol_candidates_status', table_name='kol_candidates')
    op.drop_index('idx_kol_candidates_twitter_id', table_name='kol_candidates')
    op.drop_index('idx_kol_candidates_handle_live', table_name='kol_candidates')
    op.drop_table('kol_candidates')
