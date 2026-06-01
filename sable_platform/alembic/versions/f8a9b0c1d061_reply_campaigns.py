"""reply_campaigns + reply_campaign_assignments — coordinated reply campaigns (migration 061)

Mirrors SQLite migration 061_reply_campaigns.sql for Postgres parity
(SablePlatform dual-migration rule). Two tables backing the "flash mob"
coordinated-reply feature: a campaign (target tweet + objective + status) and
per-operator assignments (angle taken + posted tweet). Ties into the existing
reply_suggestions / reply_outcomes (migration 056).

Revision ID: f8a9b0c1d061
Revises: e7f8e9a0b060
Create Date: 2026-06-01 02:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f8a9b0c1d061"
down_revision = "e7f8e9a0b060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reply_campaigns",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("orgs.org_id"), nullable=False),
        sa.Column("target_tweet_id", sa.Text(), nullable=False),
        sa.Column("target_url", sa.Text()),
        sa.Column("target_author", sa.Text()),
        sa.Column("objective", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("created_by", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("won_at", sa.Text()),
        sa.Column("closed_at", sa.Text()),
    )
    op.create_index("ix_reply_campaigns_org", "reply_campaigns", ["org_id", "status", "created_at"])
    op.create_table(
        "reply_campaign_assignments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("campaign_id", sa.Text(), sa.ForeignKey("reply_campaigns.id"), nullable=False),
        sa.Column("operator_handle", sa.Text(), nullable=False),
        sa.Column("suggestion_id", sa.Text()),
        sa.Column("posted_tweet_id", sa.Text()),
        sa.Column("angle", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="assigned"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("posted_at", sa.Text()),
    )
    op.create_index(
        "ix_reply_campaign_assignments_campaign", "reply_campaign_assignments", ["campaign_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_reply_campaign_assignments_campaign", table_name="reply_campaign_assignments")
    op.drop_table("reply_campaign_assignments")
    op.drop_index("ix_reply_campaigns_org", table_name="reply_campaigns")
    op.drop_table("reply_campaigns")
