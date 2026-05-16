"""roast V2 peer economy + personalization layer (migration 047)

Six new tables plus one ALTER on discord_guild_config for the
/roast V2 peer-economy + personalization layer (sable-roles).
Mirrors SQLite migration 047_roast_personalization.sql for Postgres
parity (SablePlatform dual-migration rule).

Tables (created in FK-safe order — observations BEFORE vibes):

* discord_burn_blocklist          — sticky /stop-pls opt-out list
* discord_peer_roast_tokens       — peer-economy token ledger
* discord_peer_roast_flags        — peer-roast 🚩 flag log
* discord_message_observations    — raw per-message observation log
* discord_user_observations       — rollup of recent user activity
* discord_user_vibes              — LLM-summarized per-user vibe block
  (FK -> discord_user_observations.id, so observations must precede vibes)

ALTER on discord_guild_config adds personalize_mode_on (Integer, default 0).

Revision ID: c5d6e7f8e047
Revises: b4c5d6e7f046
Create Date: 2026-05-16 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'c5d6e7f8e047'
down_revision: str | None = 'b4c5d6e7f046'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 3.1 sticky stop-pls blocklist
    op.create_table(
        'discord_burn_blocklist',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column(
            'blocked_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('guild_id', 'user_id', name='uq_discord_burn_blocklist_guild_user'),
    )
    op.create_index(
        'idx_discord_burn_blocklist_user',
        'discord_burn_blocklist',
        ['user_id', 'guild_id'],
    )

    # 3.2 peer-roast token ledger
    op.create_table(
        'discord_peer_roast_tokens',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('actor_user_id', sa.Text(), nullable=False),
        sa.Column(
            'granted_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('year_month', sa.Text(), nullable=False),
        sa.Column('consumed_at', sa.TIMESTAMP(timezone=True)),
        sa.Column('consumed_on_post_id', sa.Text()),
        sa.Column('consumed_target_user_id', sa.Text()),
        sa.CheckConstraint(
            "source IN ('monthly', 'streak_restoration')",
            name='ck_discord_peer_roast_tokens_source',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'guild_id', 'actor_user_id', 'year_month', 'source',
            name='uq_discord_peer_roast_tokens_grant',
        ),
    )
    op.create_index(
        'idx_discord_peer_roast_tokens_actor_month',
        'discord_peer_roast_tokens',
        ['actor_user_id', 'guild_id', 'year_month'],
    )
    op.create_index(
        'idx_discord_peer_roast_tokens_target_month',
        'discord_peer_roast_tokens',
        ['consumed_target_user_id', 'guild_id', 'year_month'],
        postgresql_where=sa.text('consumed_at IS NOT NULL'),
        sqlite_where=sa.text('consumed_at IS NOT NULL'),
    )

    # 3.3 peer-roast flag log
    op.create_table(
        'discord_peer_roast_flags',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('target_user_id', sa.Text(), nullable=False),
        sa.Column('actor_user_id', sa.Text(), nullable=False),
        sa.Column('post_id', sa.Text(), nullable=False),
        sa.Column('bot_reply_id', sa.Text(), nullable=False),
        sa.Column('reactor_user_id', sa.Text(), nullable=False),
        sa.Column(
            'flagged_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_discord_peer_roast_flags_target',
        'discord_peer_roast_flags',
        ['target_user_id', 'guild_id', 'flagged_at'],
    )
    op.create_index(
        'idx_discord_peer_roast_flags_bot_reply',
        'discord_peer_roast_flags',
        ['bot_reply_id'],
    )

    # 3.7 raw per-message observation log (source for the daily rollup).
    # Created BEFORE discord_user_observations + discord_user_vibes so the
    # rollup target tables come after their data source — matches the
    # TABLE_LOAD_ORDER chain in migrate_pg.
    op.create_table(
        'discord_message_observations',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('channel_id', sa.Text(), nullable=False),
        sa.Column('message_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('content_truncated', sa.Text()),
        sa.Column('reactions_given_json', sa.Text()),
        sa.Column('posted_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            'captured_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'guild_id', 'message_id',
            name='uq_discord_message_observations_guild_message',
        ),
    )
    op.create_index(
        'idx_discord_message_observations_user_time',
        'discord_message_observations',
        ['user_id', 'guild_id', 'posted_at'],
    )
    op.create_index(
        'idx_discord_message_observations_gc',
        'discord_message_observations',
        ['captured_at'],
    )

    # 3.4 user observation rollups (populated by daily cron)
    op.create_table(
        'discord_user_observations',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('window_start', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('window_end', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('message_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('sample_messages_json', sa.Text()),
        sa.Column('reaction_emojis_given_json', sa.Text()),
        sa.Column('channels_active_in_json', sa.Text()),
        sa.Column(
            'computed_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_discord_user_observations_user',
        'discord_user_observations',
        ['user_id', 'guild_id', 'computed_at'],
    )

    # 3.5 user vibes (LLM-summarized weekly; FK -> discord_user_observations.id)
    op.create_table(
        'discord_user_vibes',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('guild_id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('vibe_block_text', sa.Text(), nullable=False),
        sa.Column('identity', sa.Text()),
        sa.Column('activity_rhythm', sa.Text()),
        sa.Column('reaction_signature', sa.Text()),
        sa.Column('palette_signals', sa.Text()),
        sa.Column('tone', sa.Text()),
        sa.Column(
            'inferred_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('source_observation_id', sa.BigInteger()),
        sa.ForeignKeyConstraint(
            ['source_observation_id'],
            ['discord_user_observations.id'],
            name='fk_discord_user_vibes_source_observation',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_discord_user_vibes_user_recent',
        'discord_user_vibes',
        ['user_id', 'guild_id', 'inferred_at'],
    )

    # 3.6 personalize toggle on discord_guild_config. Default 0 (OFF) — guilds
    # that never opt in stay observation-only. Integer matches relax_mode_on
    # already on the same table.
    op.add_column(
        'discord_guild_config',
        sa.Column(
            'personalize_mode_on',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('0'),
        ),
    )


def downgrade() -> None:
    op.drop_column('discord_guild_config', 'personalize_mode_on')

    op.drop_index('idx_discord_user_vibes_user_recent', table_name='discord_user_vibes')
    op.drop_table('discord_user_vibes')

    op.drop_index('idx_discord_user_observations_user', table_name='discord_user_observations')
    op.drop_table('discord_user_observations')

    op.drop_index('idx_discord_message_observations_gc', table_name='discord_message_observations')
    op.drop_index('idx_discord_message_observations_user_time', table_name='discord_message_observations')
    op.drop_table('discord_message_observations')

    op.drop_index('idx_discord_peer_roast_flags_bot_reply', table_name='discord_peer_roast_flags')
    op.drop_index('idx_discord_peer_roast_flags_target', table_name='discord_peer_roast_flags')
    op.drop_table('discord_peer_roast_flags')

    op.drop_index('idx_discord_peer_roast_tokens_target_month', table_name='discord_peer_roast_tokens')
    op.drop_index('idx_discord_peer_roast_tokens_actor_month', table_name='discord_peer_roast_tokens')
    op.drop_table('discord_peer_roast_tokens')

    op.drop_index('idx_discord_burn_blocklist_user', table_name='discord_burn_blocklist')
    op.drop_table('discord_burn_blocklist')
