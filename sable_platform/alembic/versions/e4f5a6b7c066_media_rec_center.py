"""media recommendation center — reply_outcomes.media_content_id + media_rec_events / media_quality / media_embeddings (migration 066)

Mirrors SQLite migration 066_media_rec_center.sql for Postgres parity
(SablePlatform dual-migration rule). 100 percent ADDITIVE: one ADD COLUMN +
CREATE TABLE + CREATE INDEX, no table rebuild. media_rec_events logs each media
slate offered to an operator for a reply; media_quality is the forward-only Elo
rollup recomputed from that choice log; media_embeddings caches a per-asset
semantic vector. reply_outcomes gains media_content_id so assisted lift can be
sliced by the attached media. There is NO cost column. See
SableRelay/MEDIA_REC_CENTER_PLAN.md.

Revision ID: e4f5a6b7c066
Revises: d3e4f5a6b065
Create Date: 2026-06-06 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e4f5a6b7c066"
down_revision = "d3e4f5a6b065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reply_outcomes",
        sa.Column("media_content_id", sa.Text()),
    )
    op.create_table(
        "media_rec_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("operator_handle", sa.Text()),
        sa.Column("tweet_ref", sa.Text()),
        sa.Column("slate_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("chosen_content_id", sa.Text()),
        sa.Column("applied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.Text(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "media_quality",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("content_id", sa.Text(), nullable=False),
        sa.Column("elo", sa.Float(), nullable=False, server_default="1500"),
        sa.Column("n_offered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("n_chosen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.Text(), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("org_id", "content_id"),
    )
    op.create_table(
        "media_embeddings",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("content_id", sa.Text(), nullable=False),
        sa.Column("embedding_json", sa.Text()),
        sa.Column("embedding_model", sa.Text()),
        sa.Column(
            "updated_at", sa.Text(), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("org_id", "content_id"),
    )
    op.create_index(
        "ix_media_rec_events_unapplied",
        "media_rec_events",
        ["org_id", "applied"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_media_rec_events_unapplied",
        table_name="media_rec_events",
    )
    op.drop_table("media_embeddings")
    op.drop_table("media_quality")
    op.drop_table("media_rec_events")
    op.drop_column("reply_outcomes", "media_content_id")
