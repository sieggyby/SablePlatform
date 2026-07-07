"""Add 'community_tweet' to the content_candidates kind CHECK (mig 083)

Mirrors SQLite migration 083_community_tweet_kind.sql for Postgres parity (the
dual-migration rule). Ingested REAL community tweets served in the sable-roles
/duel "which popped" game (COMMUNITY_DUEL_PLAN.md Phase A) — duel-only rows,
target_handle always NULL, excluded from every operator surface, never folded
into the content Elo.

Postgres is the easy side of this migration: 076 NAMED the constraint
(ck_content_candidates_kind), so it's a deterministic DROP/ADD — no table
rebuild. The ALTER takes ACCESS EXCLUSIVE against a busy table (SableWeb's
pool has no statement_timeout), so we SET LOCAL lock_timeout and let the
deploy runbook retry rather than queue indefinitely behind a reader.

SQLite is handled ENTIRELY by 083_community_tweet_kind.sql via _MIGRATIONS
(an FK-aware three-table rebuild — content_candidates is not a leaf table).
This revision deliberately no-ops on any non-Postgres dialect.

DOWNGRADE NOTE: re-adding the six-kind CHECK validates existing rows — any
community_tweet rows must be DELETEd BEFORE downgrading (runbook rollback
step 1), or the downgrade fails.

Revision ID: f2a3b4c5d083
Revises: e1f2a3b4c082
Create Date: 2026-07-07 00:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "f2a3b4c5d083"
down_revision = "e1f2a3b4c082"
branch_labels = None
depends_on = None

_KINDS_OLD = "kind IN ('clip', 'tweet', 'thread', 'quote_card', 'meme', 'copypasta')"
_KINDS_NEW = (
    "kind IN ('clip', 'tweet', 'thread', 'quote_card', 'meme', 'copypasta', 'community_tweet')"
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite path owned by 083_community_tweet_kind.sql (_MIGRATIONS)
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.drop_constraint("ck_content_candidates_kind", "content_candidates", type_="check")
    op.create_check_constraint("ck_content_candidates_kind", "content_candidates", _KINDS_NEW)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.drop_constraint("ck_content_candidates_kind", "content_candidates", type_="check")
    op.create_check_constraint("ck_content_candidates_kind", "content_candidates", _KINDS_OLD)
