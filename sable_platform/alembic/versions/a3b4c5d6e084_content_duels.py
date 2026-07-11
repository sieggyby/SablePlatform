"""content_duels — durable open-duel registry (mig 084)

Mirrors SQLite migration 084_content_duels.sql for Postgres parity (the
dual-migration rule). A restart-durable record of open community duels so a
24h duel survives a bot restart: the persistent view rebinds by message_id,
a background sweep closes past-deadline rows (incl. a startup pass), and one
OPEN row per channel is the restart-safe per-channel lock. Card snapshots
decouple the close reveal from content_candidates lifecycle. 100% additive.

Revision ID: a3b4c5d6e084
Revises: f2a3b4c5d083
Create Date: 2026-07-11 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3b4c5d6e084"
down_revision = "f2a3b4c5d083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_duels",
        sa.Column("message_id", sa.Text(), primary_key=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("orgs.org_id"), nullable=False),
        sa.Column("guild_id", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("card_a_json", sa.Text(), nullable=False),
        sa.Column("card_b_json", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.Text(), nullable=False),
        sa.Column("deadline", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("closed_at", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('open', 'closed')", name="ck_content_duels_status"),
    )
    op.create_index("content_duels_due", "content_duels", ["status", "deadline"])
    op.create_index("content_duels_by_channel", "content_duels", ["channel_id", "status"])


def downgrade() -> None:
    op.drop_index("content_duels_by_channel", table_name="content_duels")
    op.drop_index("content_duels_due", table_name="content_duels")
    op.drop_table("content_duels")
