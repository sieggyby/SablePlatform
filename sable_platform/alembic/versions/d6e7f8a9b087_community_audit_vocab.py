"""community_audit_vocab_corpus — cross-community vocabulary background corpus (mig 087)

Mirrors SQLite migration 087_community_audit_vocab.sql for Postgres parity (the
dual-migration rule).

Deciding whether a phrase is community-COINED or ordinary language currently needs an
LLM, because a single community's top phrases are dominated by generic English ("to
get", "how do"). The durable answer is contrast: a phrase appearing across MANY audited
communities is generic, one unique to a single community is endemic. Measured at n=2
that method killed only 1 of 18 candidates, so it needs ~10-20 corpora to work.

Every deep audit already computes a top-50 phrase list and discards it. Persisting it
builds the corpus as a byproduct of running the product. 100% additive.

PRIVACY (R4): phrases and counts only, never message content.

Revision ID: d6e7f8a9b087
Revises: c5d6e7f8a086
Create Date: 2026-07-28 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d6e7f8a9b087"
down_revision = "c5d6e7f8a086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "community_audit_vocab_corpus",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "guild_id",
            sa.Text(),
            sa.ForeignKey("community_audit_guilds.guild_id"),
            nullable=False,
        ),
        sa.Column("run_id", sa.Integer()),
        sa.Column("phrase", sa.Text(), nullable=False),
        sa.Column("unique_users", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spread_velocity", sa.Float()),
        sa.Column("first_seen_week", sa.Text()),
        # NULL = never judged. Lets a contrast-based method be scored against the LLM's
        # verdicts on the same rows before the LLM is retired.
        sa.Column("judged_coined", sa.Integer()),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    # The contrast query is "how many DISTINCT guilds used this phrase", so phrase leads.
    op.create_index(
        "idx_vocab_corpus_phrase", "community_audit_vocab_corpus", ["phrase"]
    )
    op.create_index(
        "idx_vocab_corpus_guild", "community_audit_vocab_corpus", ["guild_id"]
    )
    # One row per (guild, phrase, run) so a re-audit cannot double-count a community
    # toward that phrase's breadth — which would make its own vocabulary look generic.
    op.create_index(
        "idx_vocab_corpus_unique",
        "community_audit_vocab_corpus",
        ["guild_id", "phrase", "run_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_vocab_corpus_unique", table_name="community_audit_vocab_corpus")
    op.drop_index("idx_vocab_corpus_guild", table_name="community_audit_vocab_corpus")
    op.drop_index("idx_vocab_corpus_phrase", table_name="community_audit_vocab_corpus")
    op.drop_table("community_audit_vocab_corpus")
