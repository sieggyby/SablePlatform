"""SableAutoCM schema — autocm_* table family (migration 058)

Mirrors SQLite migration 058_autocm.sql for Postgres parity (SablePlatform
dual-migration rule). The autocm_* schema from SableAutoCM/DESIGN.md section 4
(the 11 base tables) + KB_DESIGN.md (kb storage + retrieval) + DIGEST.md
section 4 (founder digest interactions) + sable-pulse/MEGAPLAN.md C3.0
single-logical-change additions:

  1. autocm_digest_interactions (DIGEST section 4) — founder digest button
     responses captured for weekly review.
  2. autocm_clients.incident_active (MEGAPLAN C3.8b) — per-client incident-mode
     flag.
  3. autocm_category_state freeze columns (MEGAPLAN C3.8a / C3.5a) — the
     per-client/per-category SAFETY section 6 48h pure-HITL freeze.
  4. autocm_time_saved_baseline (DIGEST section 2a/3) — per-client time-saved
     calibration consumed by the C3.7 digest formula.

DECISION D-2 (locked): embedding storage. autocm_kb_chunks.chunk_embedding is
TEXT (JSON-encoded float vector) — pure SQLite, no extension; app-side cosine
top-K. The companion FTS5 virtual table autocm_kb_chunks_fts (the C3.2a hybrid
keyword leg) is a SQLite-only mechanism (stdlib FTS5, no enable_load_extension)
— created only on the SQLite dialect here. pgvector is an OPTIONAL accelerator
on the shared SP Postgres (CREATE EXTENSION vector), gated behind an explicit
ops step; the embedding column is the one intentional documented dialect
divergence (Postgres may use a vector type while SQLite keeps TEXT + app-side
cosine). Postgres keeps chunk_embedding as Text in this revision so the base
schema applies without the optional extension.

All _at columns are TEXT with server_default=func.now() (post-053 timestamp
contract; the SQLite migration carries the strftime ISO-8601-Z default).
autocm_clients.org_id FK -> orgs.org_id (TEXT). autocm_drafts source FKs ->
relay_messages.id / relay_chats.id (the 057 relay surface).

Revision ID: c5d6e7f8e058
Revises: b5c6d7e8f057
Create Date: 2026-05-31 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'c5d6e7f8e058'
down_revision: str | None = 'b5c6d7e8f057'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'autocm_personas',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('calm_prompt', sa.Text(), nullable=True),
        sa.Column('reactive_prompt', sa.Text(), nullable=True),
        sa.Column('calibration_set', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('config', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('autocm_personas_name_unique', 'autocm_personas', ['name'], unique=True)

    op.create_table(
        'autocm_clients',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('persona_id', sa.Integer(), nullable=True),
        sa.Column('display_name', sa.Text(), nullable=True),
        sa.Column('autonomy_state', sa.Text(), nullable=False, server_default='hitl'),
        sa.Column('incident_active', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('surface_config', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('kb_config', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('enabled', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "autonomy_state IN ('hitl','partial','auto','paused')",
            name='ck_autocm_clients_autonomy_state',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.org_id']),
        sa.ForeignKeyConstraint(['persona_id'], ['autocm_personas.id']),
    )
    op.create_index('autocm_clients_org_unique', 'autocm_clients', ['org_id'], unique=True)
    op.create_index('autocm_clients_persona', 'autocm_clients', ['persona_id'])

    op.create_table(
        'autocm_kb_sources',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('source_type', sa.Text(), nullable=False),
        sa.Column('source_url', sa.Text(), nullable=True),
        sa.Column('refresh_cadence', sa.Text(), nullable=True),
        sa.Column('authority_default', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('fetch_config', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('status', sa.Text(), nullable=False, server_default='active'),
        sa.Column('last_refreshed_at', sa.Text(), nullable=True),
        sa.Column('last_changed_at', sa.Text(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('active','stale','disabled')",
            name='ck_autocm_kb_sources_status',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['client_id'], ['autocm_clients.id']),
    )
    op.create_index('autocm_kb_sources_by_client', 'autocm_kb_sources', ['client_id', 'source_type'])
    op.create_index('autocm_kb_sources_refresh', 'autocm_kb_sources', ['status', 'last_refreshed_at'])

    op.create_table(
        'autocm_kb_chunks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('chunk_text', sa.Text(), nullable=False),
        # DECISION D-2: TEXT (JSON-encoded float vector). pgvector is an OPTIONAL
        # accelerator behind CREATE EXTENSION vector — not applied here.
        sa.Column('chunk_embedding', sa.Text(), nullable=True),
        sa.Column('chunk_metadata', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('chunk_authority', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('content_hash', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=False, server_default='active'),
        sa.Column('indexed_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('active','stale','wrong')",
            name='ck_autocm_kb_chunks_status',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['source_id'], ['autocm_kb_sources.id']),
        sa.ForeignKeyConstraint(['client_id'], ['autocm_clients.id']),
    )
    op.create_index('autocm_kb_chunks_by_source', 'autocm_kb_chunks', ['source_id'])
    op.create_index('autocm_kb_chunks_by_client_status', 'autocm_kb_chunks', ['client_id', 'status'])

    # FTS5 companion (C3.2a hybrid keyword leg) is a SQLite-only mechanism.
    # Created only on SQLite; Postgres uses tsvector/pgvector as the optional
    # accelerator (documented dialect divergence per D-2).
    if op.get_bind().dialect.name == 'sqlite':
        op.execute(
            "CREATE VIRTUAL TABLE autocm_kb_chunks_fts USING fts5("
            "chunk_text, content='autocm_kb_chunks', content_rowid='id')"
        )

    op.create_table(
        'autocm_kb_constants',
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.Text(), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('updated_by', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('client_id', 'key'),
        sa.ForeignKeyConstraint(['client_id'], ['autocm_clients.id']),
    )

    op.create_table(
        'autocm_drafts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('source_message_id', sa.Integer(), nullable=True),
        sa.Column('source_chat_id', sa.Integer(), nullable=True),
        sa.Column('category', sa.Text(), nullable=True),
        sa.Column('tier', sa.Integer(), nullable=True),
        sa.Column('register', sa.Text(), nullable=True),
        sa.Column('draft_text', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('cited_chunk_ids', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column('resolved_at', sa.Text(), nullable=True),
        sa.CheckConstraint(
            "register IN ('calm','reactive')",
            name='ck_autocm_drafts_register',
        ),
        sa.CheckConstraint(
            "status IN ('pending','auto_sent','hitl_pending','approved','rejected',"
            "'published','escalated','suppressed')",
            name='ck_autocm_drafts_status',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['client_id'], ['autocm_clients.id']),
        sa.ForeignKeyConstraint(['source_message_id'], ['relay_messages.id']),
        sa.ForeignKeyConstraint(['source_chat_id'], ['relay_chats.id']),
    )
    op.create_index('autocm_drafts_by_client_status', 'autocm_drafts', ['client_id', 'status', 'created_at'])
    op.create_index('autocm_drafts_by_category', 'autocm_drafts', ['client_id', 'category', 'created_at'])
    op.create_index('autocm_drafts_by_message', 'autocm_drafts', ['source_message_id'])

    op.create_table(
        'autocm_reviews',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('draft_id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('reviewer', sa.Text(), nullable=True),
        sa.Column('decision', sa.Text(), nullable=False),
        sa.Column('edited_text', sa.Text(), nullable=True),
        sa.Column('edit_diff_size', sa.Float(), nullable=False, server_default='0'),
        sa.Column('is_clean_approval', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('reviewed_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "decision IN ('approve','edit','reject','punt_to_founder')",
            name='ck_autocm_reviews_decision',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['draft_id'], ['autocm_drafts.id']),
        sa.ForeignKeyConstraint(['client_id'], ['autocm_clients.id']),
    )
    op.create_index('autocm_reviews_by_draft', 'autocm_reviews', ['draft_id'])
    op.create_index('autocm_reviews_by_client', 'autocm_reviews', ['client_id', 'reviewed_at'])

    op.create_table(
        'autocm_category_state',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('category', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False, server_default='hitl'),
        sa.Column('confidence_threshold', sa.Float(), nullable=False, server_default='0.8'),
        sa.Column('sample_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('clean_approval_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('freeze_until', sa.Text(), nullable=True),
        sa.Column('freeze_reason', sa.Text(), nullable=True),
        sa.Column('frozen_by', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "state IN ('hitl','auto')",
            name='ck_autocm_category_state_state',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['client_id'], ['autocm_clients.id']),
    )
    op.create_index(
        'autocm_category_state_unique',
        'autocm_category_state',
        ['client_id', 'category'],
        unique=True,
    )
    op.create_index('autocm_category_state_frozen', 'autocm_category_state', ['freeze_until'])

    op.create_table(
        'autocm_escalations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('draft_id', sa.Integer(), nullable=True),
        sa.Column('source_message_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('founder_status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('oncall_status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column('resolved_at', sa.Text(), nullable=True),
        sa.CheckConstraint(
            "founder_status IN ('pending','notified','acknowledged','resolved')",
            name='ck_autocm_escalations_founder_status',
        ),
        sa.CheckConstraint(
            "oncall_status IN ('pending','notified','acknowledged','resolved')",
            name='ck_autocm_escalations_oncall_status',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['client_id'], ['autocm_clients.id']),
        sa.ForeignKeyConstraint(['draft_id'], ['autocm_drafts.id']),
        sa.ForeignKeyConstraint(['source_message_id'], ['relay_messages.id']),
    )
    op.create_index('autocm_escalations_by_client', 'autocm_escalations', ['client_id', 'created_at'])
    op.create_index('autocm_escalations_open', 'autocm_escalations', ['founder_status', 'oncall_status'])

    op.create_table(
        'autocm_flagged_users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=True),
        sa.Column('external_user_id', sa.Text(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=False, server_default='silenced'),
        sa.Column('flagged_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column('cleared_at', sa.Text(), nullable=True),
        sa.Column('cleared_by', sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('silenced','cleared')",
            name='ck_autocm_flagged_users_status',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['client_id'], ['autocm_clients.id']),
        sa.ForeignKeyConstraint(['member_id'], ['relay_members.id']),
    )
    op.create_index('autocm_flagged_users_by_client', 'autocm_flagged_users', ['client_id', 'status'])
    op.create_index('autocm_flagged_users_by_member', 'autocm_flagged_users', ['member_id'])

    op.create_table(
        'autocm_adversarial_runs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('suite', sa.Text(), nullable=True),
        sa.Column('total_cases', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('passed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('result', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('ran_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending','passed','failed','error')",
            name='ck_autocm_adversarial_runs_status',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['client_id'], ['autocm_clients.id']),
    )
    op.create_index('autocm_adversarial_runs_by_client', 'autocm_adversarial_runs', ['client_id', 'ran_at'])

    op.create_table(
        'autocm_digest_interactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('digest_period', sa.Text(), nullable=True),
        sa.Column('section', sa.Text(), nullable=True),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('target_ref', sa.Text(), nullable=True),
        sa.Column('payload', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('actor', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "action IN ('approve_for_kb','recognize','demote','compose','ignore','ask')",
            name='ck_autocm_digest_interactions_action',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['client_id'], ['autocm_clients.id']),
    )
    op.create_index(
        'autocm_digest_interactions_by_client',
        'autocm_digest_interactions',
        ['client_id', 'digest_period'],
    )
    op.create_index(
        'autocm_digest_interactions_by_action',
        'autocm_digest_interactions',
        ['client_id', 'action'],
    )

    op.create_table(
        'autocm_time_saved_baseline',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('minutes_per_auto', sa.Float(), nullable=False, server_default='0'),
        sa.Column('minutes_per_hitl', sa.Float(), nullable=False, server_default='0'),
        sa.Column('engagement_start_at', sa.Text(), nullable=True),
        sa.Column('calibrated_by', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['client_id'], ['autocm_clients.id']),
    )
    op.create_index(
        'autocm_time_saved_baseline_client_unique',
        'autocm_time_saved_baseline',
        ['client_id'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('autocm_time_saved_baseline_client_unique', table_name='autocm_time_saved_baseline')
    op.drop_table('autocm_time_saved_baseline')
    op.drop_index('autocm_digest_interactions_by_action', table_name='autocm_digest_interactions')
    op.drop_index('autocm_digest_interactions_by_client', table_name='autocm_digest_interactions')
    op.drop_table('autocm_digest_interactions')
    op.drop_index('autocm_adversarial_runs_by_client', table_name='autocm_adversarial_runs')
    op.drop_table('autocm_adversarial_runs')
    op.drop_index('autocm_flagged_users_by_member', table_name='autocm_flagged_users')
    op.drop_index('autocm_flagged_users_by_client', table_name='autocm_flagged_users')
    op.drop_table('autocm_flagged_users')
    op.drop_index('autocm_escalations_open', table_name='autocm_escalations')
    op.drop_index('autocm_escalations_by_client', table_name='autocm_escalations')
    op.drop_table('autocm_escalations')
    op.drop_index('autocm_category_state_frozen', table_name='autocm_category_state')
    op.drop_index('autocm_category_state_unique', table_name='autocm_category_state')
    op.drop_table('autocm_category_state')
    op.drop_index('autocm_reviews_by_client', table_name='autocm_reviews')
    op.drop_index('autocm_reviews_by_draft', table_name='autocm_reviews')
    op.drop_table('autocm_reviews')
    op.drop_index('autocm_drafts_by_message', table_name='autocm_drafts')
    op.drop_index('autocm_drafts_by_category', table_name='autocm_drafts')
    op.drop_index('autocm_drafts_by_client_status', table_name='autocm_drafts')
    op.drop_table('autocm_drafts')
    op.drop_table('autocm_kb_constants')
    if op.get_bind().dialect.name == 'sqlite':
        op.execute("DROP TABLE IF EXISTS autocm_kb_chunks_fts")
    op.drop_index('autocm_kb_chunks_by_client_status', table_name='autocm_kb_chunks')
    op.drop_index('autocm_kb_chunks_by_source', table_name='autocm_kb_chunks')
    op.drop_table('autocm_kb_chunks')
    op.drop_index('autocm_kb_sources_refresh', table_name='autocm_kb_sources')
    op.drop_index('autocm_kb_sources_by_client', table_name='autocm_kb_sources')
    op.drop_table('autocm_kb_sources')
    op.drop_index('autocm_clients_persona', table_name='autocm_clients')
    op.drop_index('autocm_clients_org_unique', table_name='autocm_clients')
    op.drop_table('autocm_clients')
    op.drop_index('autocm_personas_name_unique', table_name='autocm_personas')
    op.drop_table('autocm_personas')
