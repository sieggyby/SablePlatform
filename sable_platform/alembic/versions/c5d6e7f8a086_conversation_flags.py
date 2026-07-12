"""community_conversation_flags — Conversation Watcher output (mig 086)

Mirrors SQLite migration 086_conversation_flags.sql for Postgres parity (the
dual-migration rule). The durable record of scored community-chat moments the
watcher flags for an operator to pitch into, plus the dedupe/cooldown substrate
and the feedback ledger calibration reads. 100% additive. No cost column — the
scorer is zero-LLM.

Revision ID: c5d6e7f8a086
Revises: b4c5d6e7f085
Create Date: 2026-07-12 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5d6e7f8a086"
down_revision = "b4c5d6e7f085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "community_conversation_flags",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("orgs.org_id"), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("space_id", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("anchor_message_id", sa.Text(), nullable=False),
        sa.Column("window_start", sa.Text(), nullable=False),
        sa.Column("window_end", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="opportunity"),
        sa.Column("signals_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "platform IN ('discord', 'telegram')", name="ck_ccf_platform"
        ),
        sa.CheckConstraint(
            "kind IN ('opportunity', 'brand_risk')", name="ck_ccf_kind"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'delivered', 'handled', 'noise', 'expired')",
            name="ck_ccf_status",
        ),
    )
    op.create_index(
        "ix_ccf_feed", "community_conversation_flags", ["org_id", "status", "created_at"]
    )
    op.create_index(
        "ix_ccf_cooldown",
        "community_conversation_flags",
        ["platform", "channel_id", "created_at"],
    )
    op.create_index(
        "ix_ccf_expiry", "community_conversation_flags", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_ccf_expiry", table_name="community_conversation_flags")
    op.drop_index("ix_ccf_cooldown", table_name="community_conversation_flags")
    op.drop_index("ix_ccf_feed", table_name="community_conversation_flags")
    op.drop_table("community_conversation_flags")
