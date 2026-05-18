"""align nine _at columns to TEXT for schema.py parity — Scored Mode V2 drift fix

Aligns Postgres column types for the nine `_at` columns that schema.py
declares as Text but were created in earlier Alembic revisions as
TIMESTAMPTZ. Drift was bit twice during the 2026-05-17 live SolStitch
smoke (image_hashing pHash collision audit + delete_monitor json.dumps)
and surgically patched in the application code via coerce_audit_value;
this migration removes the underlying mismatch so future code paths
don't repeat the bite.

SQLite: no-op (TEXT affinity covers TIMESTAMPTZ-declared columns).
Postgres: 9 ALTER COLUMN TYPE statements with explicit ::text USING.

Columns covered:
  - discord_streak_events: posted_at, created_at, updated_at, invalidated_at
  - discord_fitcheck_scores: created_at, updated_at
  - discord_scoring_config: created_at, updated_at
  - discord_guild_config: updated_at

Revision ID: e1f2a3b4c053
Revises: d0e1f2a3b052
Create Date: 2026-05-17 19:55:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'e1f2a3b4c053'
down_revision: str | None = 'd0e1f2a3b052'
branch_labels: str | None = None
depends_on: str | None = None


# (table_name, column_name, has_default, is_nullable)
_TARGETS = [
    ('discord_streak_events', 'posted_at', False, False),
    ('discord_streak_events', 'created_at', True, False),
    ('discord_streak_events', 'updated_at', True, False),
    ('discord_streak_events', 'invalidated_at', False, True),
    ('discord_fitcheck_scores', 'created_at', True, False),
    ('discord_fitcheck_scores', 'updated_at', True, False),
    ('discord_scoring_config', 'created_at', True, False),
    ('discord_scoring_config', 'updated_at', True, False),
    ('discord_guild_config', 'updated_at', True, False),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        # SQLite path is a no-op — TEXT affinity covers TIMESTAMPTZ-declared
        # columns. The SQL migration file's UPDATE schema_version bump is
        # the only side effect there.
        return

    for table, column, has_default, _is_nullable in _TARGETS:
        # Drop the now()-typed default before ALTER TYPE so PG doesn't try
        # to cast the default expression at the same time as the column.
        if has_default:
            op.execute(
                f'ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT'
            )
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {column} '
            f'TYPE TEXT USING {column}::text'
        )
        if has_default:
            # now()::text produces e.g. '2026-05-17 19:48:45.123456+00'.
            # Application code writes its own ISO Z strings via
            # _now_iso_seconds() helpers; the DEFAULT is only consumed on
            # rare bare INSERTs (test fixtures, raw SQL ops).
            op.execute(
                f"ALTER TABLE {table} ALTER COLUMN {column} "
                f"SET DEFAULT (now()::text)"
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return

    # Reverse direction: TEXT → TIMESTAMPTZ. The USING clause parses ISO
    # strings via Postgres timestamptz casting. Defaults restored to now().
    for table, column, has_default, _is_nullable in _TARGETS:
        if has_default:
            op.execute(
                f'ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT'
            )
        op.execute(
            f'ALTER TABLE {table} ALTER COLUMN {column} '
            f'TYPE TIMESTAMPTZ USING {column}::timestamptz'
        )
        if has_default:
            op.execute(
                f'ALTER TABLE {table} ALTER COLUMN {column} '
                f'SET DEFAULT now()'
            )
