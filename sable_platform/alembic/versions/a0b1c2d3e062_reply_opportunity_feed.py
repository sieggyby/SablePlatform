"""reply-opportunity feed — extend relay_reply_opportunities + 5 new tables (migration 062)

Mirrors SQLite migration 062_reply_opportunity_feed.sql for Postgres parity
(SablePlatform dual-migration rule). 100 percent ADDITIVE: ADD COLUMN +
CREATE TABLE + CREATE INDEX only, no table rebuild, no CHECK change, no
NOT-NULL relax on existing columns. Unifies the auto-sourced reply-opportunity
feed onto the EXISTING relay_reply_opportunities table (migration 057) by
extending it. NEW NOT-NULL columns carry a server_default so the ADD COLUMN
applies on populated tables. See SableRelay/REPLY_OPPORTUNITY_FEED_PLAN.md §3.

Revision ID: a0b1c2d3e062
Revises: f8a9b0c1d061
Create Date: 2026-06-02 03:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a0b1c2d3e062"
down_revision = "f8a9b0c1d061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend relay_reply_opportunities (057) for the feed. status NOT NULL needs
    # a server_default so the ADD COLUMN backfills existing rows to 'active'.
    op.add_column("relay_reply_opportunities", sa.Column("score", sa.Float()))
    op.add_column("relay_reply_opportunities", sa.Column("score_reason", sa.Text()))
    op.add_column("relay_reply_opportunities", sa.Column("suggested_angle", sa.Text()))
    op.add_column(
        "relay_reply_opportunities",
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
    )
    op.add_column("relay_reply_opportunities", sa.Column("expires_at", sa.Text()))
    op.add_column("relay_reply_opportunities", sa.Column("sweep_source", sa.Text()))
    op.create_index(
        "ix_relay_opportunities_feed",
        "relay_reply_opportunities",
        ["org_id", "status", "score"],
    )
    op.create_index(
        "ix_relay_opportunities_expiry",
        "relay_reply_opportunities",
        ["expires_at"],
    )

    # relay_tweets read-through cache signals.
    op.add_column("relay_tweets", sa.Column("engagement_json", sa.Text()))
    op.add_column("relay_tweets", sa.Column("lang", sa.Text()))
    op.add_column("relay_tweets", sa.Column("author_followers", sa.Integer()))

    # reply_suggestions (056) learning join + LOCAL depress-already-replied.
    op.add_column("reply_suggestions", sa.Column("opportunity_id", sa.Integer()))
    op.add_column("reply_suggestions", sa.Column("source_conversation_id", sa.Text()))

    # Per-operator web-feed state (handle-keyed).
    op.create_table(
        "relay_opportunity_operator_state",
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("relay_reply_opportunities.id"),
            nullable=False,
        ),
        sa.Column("operator_handle", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("snooze_until", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("opportunity_id", "operator_handle"),
    )

    # The two thumbs (learning labels).
    op.create_table(
        "relay_opportunity_feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "opportunity_id",
            sa.Integer(),
            sa.ForeignKey("relay_reply_opportunities.id"),
            nullable=False,
        ),
        sa.Column("suggestion_id", sa.Text(), sa.ForeignKey("reply_suggestions.id")),
        sa.Column("rater_handle", sa.Text(), nullable=False),
        sa.Column("rater_role", sa.Text(), nullable=False),
        sa.Column("thumb", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_relay_opportunity_feedback_opp",
        "relay_opportunity_feedback",
        ["opportunity_id"],
    )

    # Per-client curated query set (cap lives in relay_clients.config.polling).
    op.create_table(
        "relay_sweep_config",
        sa.Column(
            "org_id",
            sa.Text(),
            sa.ForeignKey("relay_clients.org_id"),
            primary_key=True,
        ),
        sa.Column("mention_handles", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("topic_queries", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("from_set", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("operator_handles", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expiry_hours", sa.Integer(), nullable=False, server_default="36"),
        sa.Column("last_sweep_at", sa.Text()),
        sa.Column("sweep_requested_at", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )

    # Per-source since_id cursor.
    op.create_table(
        "relay_sweep_cursor",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.Text(), nullable=False),
        sa.Column("since_id", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("org_id", "source", "query_hash"),
    )

    # Logged-in gating heartbeat.
    op.create_table(
        "relay_operator_heartbeat",
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("operator_handle", sa.Text(), nullable=False),
        sa.Column("last_seen", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("org_id", "operator_handle"),
    )


def downgrade() -> None:
    # Reverse in FK-safe order: drop the child tables (FK -> relay_reply_opportunities
    # / reply_suggestions / relay_clients) first, then the additive columns.
    op.drop_table("relay_operator_heartbeat")
    op.drop_table("relay_sweep_cursor")
    op.drop_table("relay_sweep_config")
    op.drop_index(
        "ix_relay_opportunity_feedback_opp",
        table_name="relay_opportunity_feedback",
    )
    op.drop_table("relay_opportunity_feedback")
    op.drop_table("relay_opportunity_operator_state")

    op.drop_column("reply_suggestions", "source_conversation_id")
    op.drop_column("reply_suggestions", "opportunity_id")

    op.drop_column("relay_tweets", "author_followers")
    op.drop_column("relay_tweets", "lang")
    op.drop_column("relay_tweets", "engagement_json")

    op.drop_index(
        "ix_relay_opportunities_expiry",
        table_name="relay_reply_opportunities",
    )
    op.drop_index(
        "ix_relay_opportunities_feed",
        table_name="relay_reply_opportunities",
    )
    op.drop_column("relay_reply_opportunities", "sweep_source")
    op.drop_column("relay_reply_opportunities", "expires_at")
    op.drop_column("relay_reply_opportunities", "status")
    op.drop_column("relay_reply_opportunities", "suggested_angle")
    op.drop_column("relay_reply_opportunities", "score_reason")
    op.drop_column("relay_reply_opportunities", "score")
