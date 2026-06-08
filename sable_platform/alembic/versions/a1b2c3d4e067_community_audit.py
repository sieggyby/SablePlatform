"""community audit — community_audit_* family (migration 067)

Mirrors SQLite migration 067_community_audit.sql for Postgres parity
(SablePlatform dual-migration rule). 100 percent ADDITIVE: 11 new tables backing
the self-invite Discord community-audit bot (sable-audit). The community_audit_
prefix avoids the existing audit_log table / db/audit.py (a DIFFERENT, compliance
surface). The contributor leaderboard score is DERIVED from
community_audit_reaction_ledger (ADD upserts, REMOVE deletes), never a monotonic
counter. See sable-audit/PLAN.md.

Revision ID: a1b2c3d4e067
Revises: e4f5a6b7c066
Create Date: 2026-06-06 13:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e067"
down_revision = "e4f5a6b7c066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "community_audit_guilds",
        sa.Column("guild_id", sa.Text(), primary_key=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("orgs.org_id")),
        sa.Column("invited_by", sa.Text()),
        sa.Column("plan_tier", sa.Text(), nullable=False, server_default="free"),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("consent_at", sa.Text()),
        sa.Column("joined_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_audit_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "community_audit_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "guild_id",
            sa.Text(),
            sa.ForeignKey("community_audit_guilds.guild_id"),
            nullable=False,
        ),
        sa.Column("tier", sa.Text(), nullable=False, server_default="free"),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("messages_analyzed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("channels_active", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("channels_dead", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("span_start", sa.Text()),
        sa.Column("overall_grade", sa.Text()),
        sa.Column("category_grades_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "kind IN ('metadata','deep')", name="ck_community_audit_runs_kind"
        ),
        sa.CheckConstraint(
            "status IN ('running','ok','aborted','partial')",
            name="ck_community_audit_runs_status",
        ),
    )
    op.create_table(
        "community_audit_findings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("community_audit_runs.id"),
            nullable=False,
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False, server_default="info"),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("plain_detail", sa.Text()),
        sa.Column("message_ref", sa.Text()),
        sa.Column("confidence", sa.Float()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "community_audit_security_checks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("community_audit_runs.id"),
            nullable=False,
        ),
        sa.Column("check_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pass','warn','fail')",
            name="ck_community_audit_security_checks_status",
        ),
    )
    op.create_table(
        "community_audit_settings_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("community_audit_runs.id"),
            nullable=False,
        ),
        sa.Column("boost_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("boost_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("custom_emoji_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("soundboard_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vanity_url", sa.Text()),
        sa.Column("has_banner", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_icon", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verification_level", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("raw_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "community_audit_reaction_ledger",
        sa.Column(
            "guild_id",
            sa.Text(),
            sa.ForeignKey("community_audit_guilds.guild_id"),
            nullable=False,
        ),
        sa.Column("post_id", sa.Text(), nullable=False),
        sa.Column("reactor_id", sa.Text(), nullable=False),
        sa.Column("emoji", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("guild_id", "post_id", "reactor_id", "emoji"),
    )
    op.create_table(
        "community_audit_member_scores",
        sa.Column(
            "guild_id",
            sa.Text(),
            sa.ForeignKey("community_audit_guilds.guild_id"),
            nullable=False,
        ),
        sa.Column("member_id", sa.Text(), nullable=False),
        sa.Column("contribution_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("components_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_active_at", sa.Text()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("guild_id", "member_id"),
    )
    op.create_table(
        "community_audit_member_activity",
        sa.Column(
            "guild_id",
            sa.Text(),
            sa.ForeignKey("community_audit_guilds.guild_id"),
            nullable=False,
        ),
        sa.Column("member_id", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("guild_id", "member_id", "period"),
    )
    op.create_table(
        "community_audit_rate_limits",
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("window_start", sa.Text(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ai_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "scope IN ('guild','inviter','global')",
            name="ck_community_audit_rate_limits_scope",
        ),
        sa.PrimaryKeyConstraint("scope", "key", "window_start"),
    )
    op.create_table(
        "community_audit_benchmark",
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("metric_key", sa.Text(), nullable=False),
        sa.Column("distribution_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("sample_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("category", "metric_key"),
    )
    op.create_table(
        "community_audit_identity_links",
        sa.Column(
            "guild_id",
            sa.Text(),
            sa.ForeignKey("community_audit_guilds.guild_id"),
            nullable=False,
        ),
        sa.Column("discord_member_id", sa.Text(), nullable=False),
        sa.Column("twitter_handle", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("source", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("guild_id", "discord_member_id"),
    )
    op.create_index(
        "community_audit_runs_by_guild",
        "community_audit_runs",
        ["guild_id", "started_at"],
    )
    op.create_index(
        "community_audit_findings_by_run",
        "community_audit_findings",
        ["run_id", "category"],
    )
    op.create_index(
        "community_audit_security_checks_by_run",
        "community_audit_security_checks",
        ["run_id"],
    )
    op.create_index(
        "community_audit_settings_snapshot_by_run",
        "community_audit_settings_snapshot",
        ["run_id"],
        unique=True,
    )
    op.create_index(
        "community_audit_reaction_ledger_by_author",
        "community_audit_reaction_ledger",
        ["guild_id", "author_id"],
    )
    op.create_index(
        "community_audit_member_scores_rank",
        "community_audit_member_scores",
        ["guild_id", "contribution_score"],
    )


def downgrade() -> None:
    op.drop_index(
        "community_audit_member_scores_rank",
        table_name="community_audit_member_scores",
    )
    op.drop_index(
        "community_audit_reaction_ledger_by_author",
        table_name="community_audit_reaction_ledger",
    )
    op.drop_index(
        "community_audit_settings_snapshot_by_run",
        table_name="community_audit_settings_snapshot",
    )
    op.drop_index(
        "community_audit_security_checks_by_run",
        table_name="community_audit_security_checks",
    )
    op.drop_index(
        "community_audit_findings_by_run", table_name="community_audit_findings"
    )
    op.drop_index("community_audit_runs_by_guild", table_name="community_audit_runs")
    op.drop_table("community_audit_identity_links")
    op.drop_table("community_audit_benchmark")
    op.drop_table("community_audit_rate_limits")
    op.drop_table("community_audit_member_activity")
    op.drop_table("community_audit_member_scores")
    op.drop_table("community_audit_reaction_ledger")
    op.drop_table("community_audit_settings_snapshot")
    op.drop_table("community_audit_security_checks")
    op.drop_table("community_audit_findings")
    op.drop_table("community_audit_runs")
    op.drop_table("community_audit_guilds")
