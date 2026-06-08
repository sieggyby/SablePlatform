"""Tweet Assist tweetbank — tweetbank_entries (migration 074)

Mirrors SQLite migration 074_tweetbank.sql for Postgres parity (SablePlatform
dual-migration rule). One additive table: the curated store of ready-to-post
original tweets (P3 human-fed + P4 AI-suggested), per managed account + a shared
per-org global pool (account_handle NULL). CONTENT, not a cost surface -- NO cost
column. FK -> orgs.

Revision ID: c8d9e0f1a074
Revises: c7d8e9f0a073
Create Date: 2026-06-07 16:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8d9e0f1a074"
down_revision = "c7d8e9f0a073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tweetbank_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("client_org", sa.Text(), sa.ForeignKey("orgs.org_id"), nullable=False),
        sa.Column("account_handle", sa.Text()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("register_band", sa.Text()),
        sa.Column("topic_tags", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("author", sa.Text()),
        sa.Column("source", sa.Text(), nullable=False, server_default="human"),
        sa.Column("status", sa.Text(), nullable=False, server_default="approved"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("used_at", sa.Text()),
        sa.Column("used_by", sa.Text()),
        sa.CheckConstraint("source IN ('human', 'ai')", name="ck_tweetbank_source"),
        sa.CheckConstraint(
            "status IN ('approved', 'pending', 'used', 'rejected')", name="ck_tweetbank_status"
        ),
    )
    op.create_index(
        "ix_tweetbank_entries_org",
        "tweetbank_entries",
        ["client_org", "status", "account_handle"],
    )


def downgrade() -> None:
    op.drop_index("ix_tweetbank_entries_org", table_name="tweetbank_entries")
    op.drop_table("tweetbank_entries")
