"""SableRelay schema — relay_* table family (migration 057)

Mirrors SQLite migration 057_relay.sql for Postgres parity (SablePlatform
dual-migration rule). The full relay_* schema from SableRelay/PLAN.md section
5.1, with two C1.1-decided corrections:

  1. relay_publication_jobs.state CHECK follows the section-3.1 decided set
     ('pending','claimed','retry','done','dead') — NOT the stale section-5.1
     DDL ('pending','claimed','done','failed','dead'). 'failed' dropped (was
     ambiguous), 'retry' added (the state the publisher loop writes).
  2. Two tables added beyond section-5.1 for the AutoCM FK reconciliation:
     relay_chats (chat-id surface for autocm_drafts.source_chat_id) and
     relay_messages (one row per inbound message — the digest/analytics corpus;
     autocm_drafts.source_message_id FKs to relay_messages.id).

All _at columns are TEXT with server_default=func.now() (post-053 timestamp
contract; the SQLite migration carries the strftime ISO-8601-Z default).

Revision ID: b5c6d7e8f057
Revises: b4c5d6e7f056
Create Date: 2026-05-30 20:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'b5c6d7e8f057'
down_revision: str | None = 'b4c5d6e7f056'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'relay_clients',
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('x_handle_override', sa.Text(), nullable=True),
        sa.Column(
            'polling_interval_seconds',
            sa.Integer(),
            nullable=False,
            server_default='300',
        ),
        sa.Column('last_polled_at', sa.Text(), nullable=True),
        sa.Column('last_seen_x_id', sa.Text(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('config', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('org_id'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.org_id']),
    )

    op.create_table(
        'relay_chats',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('platform', sa.Text(), nullable=False),
        sa.Column('chat_id', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "platform IN ('telegram','discord')",
            name='ck_relay_chats_platform',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['relay_clients.org_id']),
    )
    op.create_index('relay_chats_unique', 'relay_chats', ['platform', 'chat_id'], unique=True)
    op.create_index('relay_chats_by_org', 'relay_chats', ['org_id'])

    op.create_table(
        'relay_chat_bindings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('platform', sa.Text(), nullable=False),
        sa.Column('chat_id', sa.Text(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='active'),
        sa.Column('superseded_by_chat_id', sa.Text(), nullable=True),
        sa.Column('last_seen_at', sa.Text(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "platform IN ('telegram','discord')",
            name='ck_relay_chat_bindings_platform',
        ),
        sa.CheckConstraint(
            "role IN ('operator','shared','community','broadcast')",
            name='ck_relay_chat_bindings_role',
        ),
        sa.CheckConstraint(
            "status IN ('active','migrated','kicked','disabled')",
            name='ck_relay_chat_bindings_status',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['relay_clients.org_id']),
    )
    op.create_index(
        'relay_chat_bindings_unique_role',
        'relay_chat_bindings',
        ['org_id', 'platform', 'role'],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index(
        'relay_chat_bindings_unique_chat',
        'relay_chat_bindings',
        ['platform', 'chat_id'],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    op.create_table(
        'relay_members',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('display_name', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'relay_member_identities',
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.Column('platform', sa.Text(), nullable=False),
        sa.Column('external_user_id', sa.Text(), nullable=False),
        sa.Column('handle', sa.Text(), nullable=True),
        sa.Column('linked_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "platform IN ('telegram','x','discord')",
            name='ck_relay_member_identities_platform',
        ),
        sa.PrimaryKeyConstraint('platform', 'external_user_id'),
        sa.ForeignKeyConstraint(['member_id'], ['relay_members.id']),
    )
    op.create_index(
        'relay_member_identities_by_member',
        'relay_member_identities',
        ['member_id'],
    )

    op.create_table(
        'relay_member_roles',
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('granted_by', sa.Integer(), nullable=True),
        sa.Column('granted_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "role IN ('sable_operator','client_team','admin')",
            name='ck_relay_member_roles_role',
        ),
        sa.PrimaryKeyConstraint('member_id', 'org_id', 'role'),
        sa.ForeignKeyConstraint(['member_id'], ['relay_members.id']),
        sa.ForeignKeyConstraint(['org_id'], ['relay_clients.org_id']),
        sa.ForeignKeyConstraint(['granted_by'], ['relay_members.id']),
    )
    op.create_index(
        'relay_member_roles_by_org_role',
        'relay_member_roles',
        ['org_id', 'role'],
    )

    op.create_table(
        'relay_member_preferences',
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('replies_optin', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('mute_until', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('member_id', 'org_id'),
        sa.ForeignKeyConstraint(['member_id'], ['relay_members.id']),
        sa.ForeignKeyConstraint(['org_id'], ['relay_clients.org_id']),
    )
    op.create_index(
        'relay_member_preferences_optin',
        'relay_member_preferences',
        ['org_id', 'replies_optin', 'mute_until'],
    )

    op.create_table(
        'relay_tweets',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('x_id', sa.Text(), nullable=False),
        sa.Column('x_author_id', sa.Text(), nullable=True),
        sa.Column('x_author_handle', sa.Text(), nullable=False),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('media_urls', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('is_reply', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('in_reply_to_x_id', sa.Text(), nullable=True),
        sa.Column('conversation_x_id', sa.Text(), nullable=True),
        sa.Column('fetched_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column('raw', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('x_id'),
    )
    op.create_index('relay_tweets_author', 'relay_tweets', ['x_author_id'])

    op.create_table(
        'relay_messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('chat_id', sa.Integer(), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=True),
        sa.Column('platform', sa.Text(), nullable=False),
        sa.Column('external_message_id', sa.Text(), nullable=False),
        sa.Column('external_user_id', sa.Text(), nullable=True),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('reply_to_external_message_id', sa.Text(), nullable=True),
        sa.Column('received_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "platform IN ('telegram','discord')",
            name='ck_relay_messages_platform',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['relay_clients.org_id']),
        sa.ForeignKeyConstraint(['chat_id'], ['relay_chats.id']),
        sa.ForeignKeyConstraint(['member_id'], ['relay_members.id']),
    )
    op.create_index(
        'relay_messages_unique',
        'relay_messages',
        ['platform', 'chat_id', 'external_message_id'],
        unique=True,
    )
    op.create_index('relay_messages_org_received', 'relay_messages', ['org_id', 'received_at'])
    op.create_index('relay_messages_member', 'relay_messages', ['member_id', 'received_at'])
    op.create_index('relay_messages_gc', 'relay_messages', ['received_at'])

    op.create_table(
        'relay_submissions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('tweet_id', sa.Integer(), nullable=False),
        sa.Column('submitter_id', sa.Integer(), nullable=False),
        sa.Column('source_chat_id', sa.Text(), nullable=False),
        sa.Column('source_message_id', sa.Text(), nullable=False),
        sa.Column('control_message_id', sa.Text(), nullable=True),
        sa.Column('source_role', sa.Text(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.Text(), nullable=False),
        sa.Column('resolved_at', sa.Text(), nullable=True),
        sa.CheckConstraint(
            "source_role IN ('operator','shared')",
            name='ck_relay_submissions_source_role',
        ),
        sa.CheckConstraint(
            "status IN ('pending','ready_to_publish','published','expired','rejected')",
            name='ck_relay_submissions_status',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['relay_clients.org_id']),
        sa.ForeignKeyConstraint(['tweet_id'], ['relay_tweets.id']),
        sa.ForeignKeyConstraint(['submitter_id'], ['relay_members.id']),
    )
    op.create_index(
        'relay_submissions_org_status',
        'relay_submissions',
        ['org_id', 'status', 'created_at'],
    )
    op.create_index('relay_submissions_expires', 'relay_submissions', ['status', 'expires_at'])
    op.create_index(
        'relay_submissions_one_pending_per_tweet',
        'relay_submissions',
        ['org_id', 'tweet_id'],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','ready_to_publish')"),
        sqlite_where=sa.text("status IN ('pending','ready_to_publish')"),
    )
    op.create_index(
        'relay_submissions_control_lookup',
        'relay_submissions',
        ['source_chat_id', 'control_message_id'],
    )

    op.create_table(
        'relay_submission_reactions',
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.Column('emoji', sa.Text(), nullable=False),
        sa.Column('reacted_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('submission_id', 'member_id', 'emoji'),
        sa.ForeignKeyConstraint(['submission_id'], ['relay_submissions.id']),
        sa.ForeignKeyConstraint(['member_id'], ['relay_members.id']),
    )
    op.create_index(
        'relay_submission_reactions_by_emoji',
        'relay_submission_reactions',
        ['submission_id', 'emoji'],
    )

    op.create_table(
        'relay_publication_jobs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=True),
        sa.Column('tweet_id', sa.Integer(), nullable=False),
        sa.Column('destination_platform', sa.Text(), nullable=False),
        sa.Column('destination_chat_id', sa.Text(), nullable=False),
        sa.Column('state', sa.Text(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('claimed_by', sa.Text(), nullable=True),
        sa.Column('claimed_at', sa.Text(), nullable=True),
        sa.Column('next_attempt_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "destination_platform IN ('discord','telegram')",
            name='ck_relay_publication_jobs_destination_platform',
        ),
        sa.CheckConstraint(
            "state IN ('pending','claimed','retry','done','dead')",
            name='ck_relay_publication_jobs_state',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['relay_clients.org_id']),
        sa.ForeignKeyConstraint(['submission_id'], ['relay_submissions.id']),
        sa.ForeignKeyConstraint(['tweet_id'], ['relay_tweets.id']),
    )
    op.create_index(
        'relay_publication_jobs_due',
        'relay_publication_jobs',
        ['state', 'next_attempt_at'],
    )
    op.create_index(
        'relay_publication_jobs_dedupe',
        'relay_publication_jobs',
        ['org_id', 'tweet_id', 'destination_platform', 'destination_chat_id'],
        unique=True,
        postgresql_where=sa.text("state IN ('pending','claimed','done')"),
        sqlite_where=sa.text("state IN ('pending','claimed','done')"),
    )

    op.create_table(
        'relay_publications',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=True),
        sa.Column('tweet_id', sa.Integer(), nullable=False),
        sa.Column('destination_platform', sa.Text(), nullable=False),
        sa.Column('destination_chat_id', sa.Text(), nullable=False),
        sa.Column('destination_message_id', sa.Text(), nullable=False),
        sa.Column('published_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['relay_clients.org_id']),
        sa.ForeignKeyConstraint(['submission_id'], ['relay_submissions.id']),
        sa.ForeignKeyConstraint(['tweet_id'], ['relay_tweets.id']),
    )
    op.create_index(
        'relay_publications_unique',
        'relay_publications',
        ['org_id', 'tweet_id', 'destination_platform', 'destination_chat_id'],
        unique=True,
    )
    op.create_index('relay_publications_by_tweet', 'relay_publications', ['tweet_id'])
    op.create_index(
        'relay_publications_by_message',
        'relay_publications',
        ['destination_platform', 'destination_chat_id', 'destination_message_id'],
    )

    op.create_table(
        'relay_reply_opportunities',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('tweet_id', sa.Integer(), nullable=False),
        sa.Column('flagger_id', sa.Integer(), nullable=False),
        sa.Column('origin', sa.Text(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "origin IN ('explicit_command','reaction','auto_mention')",
            name='ck_relay_reply_opportunities_origin',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['relay_clients.org_id']),
        sa.ForeignKeyConstraint(['tweet_id'], ['relay_tweets.id']),
        sa.ForeignKeyConstraint(['flagger_id'], ['relay_members.id']),
    )
    op.create_index(
        'relay_reply_opportunities_by_org',
        'relay_reply_opportunities',
        ['org_id', 'created_at'],
    )

    op.create_table(
        'relay_reply_opportunity_targets',
        sa.Column('opportunity_id', sa.Integer(), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('opportunity_id', 'member_id'),
        sa.ForeignKeyConstraint(['opportunity_id'], ['relay_reply_opportunities.id']),
        sa.ForeignKeyConstraint(['member_id'], ['relay_members.id']),
    )

    op.create_table(
        'relay_reply_notifications',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('opportunity_id', sa.Integer(), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.Column('notified_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column('dismissed_at', sa.Text(), nullable=True),
        sa.Column('replied_at', sa.Text(), nullable=True),
        sa.Column('replied_tweet_id', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['opportunity_id'], ['relay_reply_opportunities.id']),
        sa.ForeignKeyConstraint(['member_id'], ['relay_members.id']),
    )
    op.create_index(
        'relay_reply_notifications_unique',
        'relay_reply_notifications',
        ['opportunity_id', 'member_id'],
        unique=True,
    )
    op.create_index(
        'relay_reply_notifications_inbox',
        'relay_reply_notifications',
        ['member_id', 'dismissed_at'],
    )

    op.create_table(
        'relay_processed_updates',
        sa.Column('platform', sa.Text(), nullable=False),
        sa.Column('update_id', sa.Text(), nullable=False),
        sa.Column('processed_at', sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "platform IN ('telegram','discord')",
            name='ck_relay_processed_updates_platform',
        ),
        sa.PrimaryKeyConstraint('platform', 'update_id'),
    )
    op.create_index(
        'relay_processed_updates_gc',
        'relay_processed_updates',
        ['processed_at'],
    )


def downgrade() -> None:
    op.drop_index('relay_processed_updates_gc', table_name='relay_processed_updates')
    op.drop_table('relay_processed_updates')
    op.drop_index('relay_reply_notifications_inbox', table_name='relay_reply_notifications')
    op.drop_index('relay_reply_notifications_unique', table_name='relay_reply_notifications')
    op.drop_table('relay_reply_notifications')
    op.drop_table('relay_reply_opportunity_targets')
    op.drop_index('relay_reply_opportunities_by_org', table_name='relay_reply_opportunities')
    op.drop_table('relay_reply_opportunities')
    op.drop_index('relay_publications_by_message', table_name='relay_publications')
    op.drop_index('relay_publications_by_tweet', table_name='relay_publications')
    op.drop_index('relay_publications_unique', table_name='relay_publications')
    op.drop_table('relay_publications')
    op.drop_index('relay_publication_jobs_dedupe', table_name='relay_publication_jobs')
    op.drop_index('relay_publication_jobs_due', table_name='relay_publication_jobs')
    op.drop_table('relay_publication_jobs')
    op.drop_index('relay_submission_reactions_by_emoji', table_name='relay_submission_reactions')
    op.drop_table('relay_submission_reactions')
    op.drop_index('relay_submissions_control_lookup', table_name='relay_submissions')
    op.drop_index('relay_submissions_one_pending_per_tweet', table_name='relay_submissions')
    op.drop_index('relay_submissions_expires', table_name='relay_submissions')
    op.drop_index('relay_submissions_org_status', table_name='relay_submissions')
    op.drop_table('relay_submissions')
    op.drop_index('relay_messages_gc', table_name='relay_messages')
    op.drop_index('relay_messages_member', table_name='relay_messages')
    op.drop_index('relay_messages_org_received', table_name='relay_messages')
    op.drop_index('relay_messages_unique', table_name='relay_messages')
    op.drop_table('relay_messages')
    op.drop_index('relay_tweets_author', table_name='relay_tweets')
    op.drop_table('relay_tweets')
    op.drop_index('relay_member_preferences_optin', table_name='relay_member_preferences')
    op.drop_table('relay_member_preferences')
    op.drop_index('relay_member_roles_by_org_role', table_name='relay_member_roles')
    op.drop_table('relay_member_roles')
    op.drop_index('relay_member_identities_by_member', table_name='relay_member_identities')
    op.drop_table('relay_member_identities')
    op.drop_table('relay_members')
    op.drop_index('relay_chat_bindings_unique_chat', table_name='relay_chat_bindings')
    op.drop_index('relay_chat_bindings_unique_role', table_name='relay_chat_bindings')
    op.drop_table('relay_chat_bindings')
    op.drop_index('relay_chats_by_org', table_name='relay_chats')
    op.drop_index('relay_chats_unique', table_name='relay_chats')
    op.drop_table('relay_chats')
    op.drop_table('relay_clients')
