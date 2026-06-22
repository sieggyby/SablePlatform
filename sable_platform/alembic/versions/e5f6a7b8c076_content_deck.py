"""Content Deck candidate substrate -- content_candidates + content_deck_decisions +
content_deck_operator_state (migration 076)

Mirrors SQLite migration 076_content_deck.sql for Postgres parity (the dual-migration rule).
HAND-WRITTEN (not --autogenerate) so every CheckConstraint + ForeignKey + ON DELETE is rendered
on Postgres (autogenerate silently drops CHECKs -> SQLite/PG drift). Parent table created before
children. See ~/sable-workspace/CONTENT_DECK_MASTERPLAN.md SS3 + SS7.

Revision ID: e5f6a7b8c076
Revises: c9e0f1a2b075
Create Date: 2026-06-22 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c076"
down_revision = "c9e0f1a2b075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Parent first (content_deck_operator_state has a hard FK -> content_candidates).
    op.create_table(
        "content_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("orgs.org_id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("target_handle", sa.Text()),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("media_content_id", sa.Text()),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("score_reason", sa.Text()),
        sa.Column("tell_score", sa.Float()),
        sa.Column("dedupe_key", sa.Text()),
        sa.Column("expires_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "kind IN ('clip','tweet','thread','quote_card','meme','copypasta')",
            name="ck_content_candidates_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending','kept','scheduled','posted','rejected','expired')",
            name="ck_content_candidates_status",
        ),
    )
    op.create_index(
        "content_candidates_by_org_status", "content_candidates", ["org_id", "status", "score"]
    )
    op.create_index(
        "content_candidates_by_dedupe", "content_candidates", ["org_id", "dedupe_key"]
    )

    op.create_table(
        "content_deck_decisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # candidate_id is a NO-FK learning-join (media_rec_events precedent): the Elo/keep signal
        # survives a candidate soft-expiry/purge. Same-org with candidate enforced in the accessor.
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("orgs.org_id"), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("pair_loser_id", sa.Integer()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "actor_kind IN ('operator','community')", name="ck_content_deck_decisions_actor_kind"
        ),
        sa.CheckConstraint(
            "decision IN ('keep','reject','skip','schedule','post')",
            name="ck_content_deck_decisions_decision",
        ),
        sa.CheckConstraint(
            "surface IN ('web','discord')", name="ck_content_deck_decisions_surface"
        ),
    )
    op.create_index(
        "content_deck_decisions_by_candidate", "content_deck_decisions", ["org_id", "candidate_id"]
    )
    op.create_index(
        "content_deck_decisions_by_actor", "content_deck_decisions", ["org_id", "actor", "created_at"]
    )

    op.create_table(
        "content_deck_operator_state",
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("content_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operator_handle", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("snooze_until", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("candidate_id", "operator_handle"),
        sa.CheckConstraint(
            "state IN ('dismissed','snoozed')", name="ck_content_deck_operator_state_state"
        ),
    )


def downgrade() -> None:
    op.drop_table("content_deck_operator_state")
    op.drop_table("content_deck_decisions")
    op.drop_table("content_candidates")
