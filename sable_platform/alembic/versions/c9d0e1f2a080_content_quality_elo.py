"""Content-preference Elo rollup for the Content Deck A/B duel -- content_quality + content_deck_decisions.applied (mig 080)

Mirrors SQLite migration 080_content_quality_elo.sql for Postgres parity (the dual-migration rule).
HAND-WRITTEN (not --autogenerate). Parallel to mig 066 media_rec_events -> media_quality: the deck's
pairwise duel writes a content_deck_decisions row (winner=candidate_id, loser=pair_loser_id) with no
status flip; content_quality.py folds the unapplied rows forward-only into a dual-grain Elo
(subject_kind='candidate' live tie-break | 'feature' durable engine signal). 100% additive. No FKs
(org validated at the serve layer; composite TEXT PK). NO cost column.

Revision ID: c9d0e1f2a080
Revises: b8c9d0e1f079
Create Date: 2026-06-25 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a080"
down_revision = "b8c9d0e1f079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # (1) forward-only fold flag on the existing swipe/duel log (parallel to media_rec_events.applied)
    op.add_column(
        "content_deck_decisions",
        sa.Column("applied", sa.Integer(), nullable=False, server_default="0"),
    )
    # (2) the content-quality Elo rollup (parallel to media_quality), dual-grain
    op.create_table(
        "content_quality",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("subject_kind", sa.Text(), nullable=False),  # 'candidate' | 'feature'
        sa.Column("subject_key", sa.Text(), nullable=False),
        sa.Column("elo", sa.Float(), nullable=False, server_default="1500"),
        sa.Column("n_offered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_chosen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("org_id", "subject_kind", "subject_key"),
        sa.CheckConstraint(
            "subject_kind IN ('candidate', 'feature')", name="ck_content_quality_subject_kind"
        ),
    )
    # (3) unapplied-fold index (parallel to ix_media_rec_events_unapplied)
    op.create_index(
        "ix_content_deck_decisions_unapplied", "content_deck_decisions", ["org_id", "applied"]
    )


def downgrade() -> None:
    op.drop_index("ix_content_deck_decisions_unapplied", table_name="content_deck_decisions")
    op.drop_table("content_quality")
    op.drop_column("content_deck_decisions", "applied")
