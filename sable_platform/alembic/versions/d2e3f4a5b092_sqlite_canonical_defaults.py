"""PostgreSQL peer for the SQLite canonical-default rewrite (migration 092)

DEFECTS_FOUND item 10. PostgreSQL has nothing to do here, and this revision exists to say so
rather than leave a gap in the chain.

The SQL peer, ``092_sqlite_canonical_defaults.sql``, rewrites 48 SQLite column defaults from
``datetime('now')`` to the canonical ``strftime('%Y-%m-%dT%H:%M:%SZ','now')``. That gap was
SQLite-only for one reason: ``ensure_schema`` builds a database by replaying the SQL
migration files, not from ``schema.py``, so early DDL survives into a database created
today.

PostgreSQL never had the gap. Migration 090 issued ``ALTER TABLE ... SET DEFAULT`` against
150 columns on this dialect, and migration 091 restored the canonical default on the 12 it
converted that carry one. A server-read check on both is in
``tests/postgres/test_pg_ts_format.py``.

Revision ID: d2e3f4a5b092
Revises: c1d2e3f4a091
Create Date: 2026-08-29
"""

# revision identifiers, used by Alembic.
revision = "d2e3f4a5b092"
down_revision = "c1d2e3f4a091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No PostgreSQL work. See the module docstring."""


def downgrade() -> None:
    """No PostgreSQL work. See the module docstring."""
