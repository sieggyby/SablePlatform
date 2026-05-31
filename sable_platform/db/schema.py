"""SQLAlchemy Core table definitions for sable.db.

This module is the single source of truth for the platform schema.  Every
table defined here mirrors the cumulative result of migrations 001–031.

Usage::

    from sable_platform.db.schema import metadata
    metadata.create_all(engine)          # create all tables (idempotent)
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)

metadata = MetaData()

# ------------------------------------------------------------------
# Metadata
# ------------------------------------------------------------------

schema_version = Table(
    "schema_version",
    metadata,
    Column("version", Integer, nullable=False),
)

platform_meta = Table(
    "platform_meta",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
)

# ------------------------------------------------------------------
# Core entities
# ------------------------------------------------------------------

orgs = Table(
    "orgs",
    metadata,
    Column("org_id", Text, primary_key=True),
    Column("display_name", Text, nullable=False),
    Column("discord_server_id", Text),
    Column("twitter_handle", Text),
    Column("config_json", Text, nullable=False, server_default=text("'{}'")),
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
)

entities = Table(
    "entities",
    metadata,
    Column("entity_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("display_name", Text),
    Column("status", Text, nullable=False, server_default=text("'candidate'")),
    Column("source", Text, nullable=False, server_default=text("'auto'")),
    Column("config_json", Text, nullable=False, server_default=text("'{}'")),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    Index("idx_entities_org", "org_id"),
)

entity_handles = Table(
    "entity_handles",
    metadata,
    Column("handle_id", Integer, primary_key=True, autoincrement=True),
    Column("entity_id", Text, ForeignKey("entities.entity_id"), nullable=False),
    Column("platform", Text, nullable=False),
    Column("handle", Text, nullable=False),
    Column("is_primary", Integer, nullable=False, server_default="0"),
    Column("added_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint("platform", "handle"),
    Index("idx_handles_entity", "entity_id"),
    Index("idx_handles_platform_handle", "platform", "handle"),
)

entity_tags = Table(
    "entity_tags",
    metadata,
    Column("tag_id", Integer, primary_key=True, autoincrement=True),
    Column("entity_id", Text, ForeignKey("entities.entity_id"), nullable=False),
    Column("tag", Text, nullable=False),
    Column("source", Text),
    Column("confidence", Float, nullable=False, server_default="1.0"),
    Column("is_current", Integer, nullable=False, server_default="1"),
    Column("expires_at", Text),
    Column("added_at", Text, nullable=False, server_default=func.now()),
    Column("deactivated_at", Text),
    Index("idx_tags_entity", "entity_id"),
    Index("idx_tags_tag", "tag"),
    # Migration 024
    Index("idx_entity_tags_tag_current", "tag", "is_current"),
    # Migration 030
    Index("idx_entity_tags_current", "entity_id", "is_current", "tag"),
)

entity_notes = Table(
    "entity_notes",
    metadata,
    Column("note_id", Integer, primary_key=True, autoincrement=True),
    Column("entity_id", Text, ForeignKey("entities.entity_id"), nullable=False),
    Column("body", Text, nullable=False),
    Column("source", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("idx_notes_entity", "entity_id"),
)

merge_candidates = Table(
    "merge_candidates",
    metadata,
    Column("candidate_id", Integer, primary_key=True, autoincrement=True),
    Column("entity_a_id", Text, ForeignKey("entities.entity_id"), nullable=False),
    Column("entity_b_id", Text, ForeignKey("entities.entity_id"), nullable=False),
    Column("confidence", Float, nullable=False, server_default="0.0"),
    Column("reason", Text),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint("entity_a_id", "entity_b_id"),
    Index("idx_merge_candidates_status", "status"),
)

merge_events = Table(
    "merge_events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("source_entity_id", Text, nullable=False),
    Column("target_entity_id", Text, nullable=False),
    Column("candidate_id", Integer, ForeignKey("merge_candidates.candidate_id")),
    Column("merged_by", Text),
    Column("snapshot_json", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
)

# ------------------------------------------------------------------
# Content & diagnostics
# ------------------------------------------------------------------

content_items = Table(
    "content_items",
    metadata,
    Column("item_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("entity_id", Text, ForeignKey("entities.entity_id")),
    Column("content_type", Text),
    Column("platform", Text),
    Column("external_id", Text),
    Column("body", Text),
    Column("metadata_json", Text, nullable=False, server_default=text("'{}'")),
    Column("posted_at", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("idx_content_org", "org_id"),
    Index("idx_content_entity", "entity_id"),
)

diagnostic_runs = Table(
    "diagnostic_runs",
    metadata,
    Column("run_id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("run_type", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'running'")),
    Column("started_at", Text, nullable=False, server_default=func.now()),
    Column("completed_at", Text),
    Column("result_json", Text),
    Column("error", Text),
    # Migration 003
    Column("cult_run_id", Text),
    Column("project_slug", Text),
    Column("run_date", Text),
    Column("research_mode", Text),
    Column("checkpoint_path", Text),
    Column("overall_grade", Text),
    Column("fit_score", Integer),
    Column("recommended_action", Text),
    Column("sable_verdict", Text),
    Column("total_cost_usd", Float),
    # Migration 021
    Column("run_summary_json", Text),
    Index("idx_diagnostic_org", "org_id"),
    Index("idx_diagnostic_slug", "project_slug"),
)

# Unique index on cult_run_id (migration 003) — only non-NULL values.
Index("idx_diagnostic_cult_run_id", diagnostic_runs.c.cult_run_id, unique=True)

diagnostic_deltas = Table(
    "diagnostic_deltas",
    metadata,
    Column("delta_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("run_id_before", Integer, nullable=False),
    Column("run_id_after", Integer, nullable=False),
    Column("metric_name", Text, nullable=False),
    Column("value_before", Float),
    Column("value_after", Float),
    Column("delta", Float),
    Column("pct_change", Float),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("idx_deltas_org", "org_id", "metric_name"),
    Index("idx_deltas_after", "run_id_after"),
)

# ------------------------------------------------------------------
# Jobs & artifacts
# ------------------------------------------------------------------

jobs = Table(
    "jobs",
    metadata,
    Column("job_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("job_type", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("config_json", Text, nullable=False, server_default=text("'{}'")),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    # Migration 004
    Column("completed_at", Text),
    Column("result_json", Text),
    Column("error_message", Text),
    # Migration 040
    Column("worker_id", Text),
    Index("idx_jobs_org", "org_id"),
    Index("idx_jobs_status", "status"),
    Index("idx_jobs_worker", "worker_id"),
)

job_steps = Table(
    "job_steps",
    metadata,
    Column("step_id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", Text, ForeignKey("jobs.job_id"), nullable=False),
    Column("step_name", Text, nullable=False),
    Column("step_order", Integer, nullable=False, server_default="0"),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("retries", Integer, nullable=False, server_default="0"),
    Column("input_json", Text, nullable=False, server_default=text("'{}'")),
    Column("output_json", Text),
    Column("error", Text),
    Column("started_at", Text),
    Column("completed_at", Text),
    # Migration 040
    Column("next_retry_at", Text),
    Index("idx_steps_job", "job_id"),
    Index("idx_job_steps_next_retry", "next_retry_at"),
)

artifacts = Table(
    "artifacts",
    metadata,
    Column("artifact_id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("job_id", Text, ForeignKey("jobs.job_id")),
    Column("artifact_type", Text, nullable=False),
    Column("path", Text),
    Column("metadata_json", Text, nullable=False, server_default=text("'{}'")),
    Column("stale", Integer, nullable=False, server_default="0"),
    # Migration 005
    Column("degraded", Integer, nullable=False, server_default="0"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("idx_artifacts_org", "org_id"),
    Index("idx_artifacts_type", "artifact_type"),
)

cost_events = Table(
    "cost_events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("job_id", Text, ForeignKey("jobs.job_id")),
    Column("call_type", Text, nullable=False),
    Column("model", Text),
    Column("input_tokens", Integer, nullable=False, server_default="0"),
    Column("output_tokens", Integer, nullable=False, server_default="0"),
    Column("cost_usd", Float, nullable=False, server_default="0.0"),
    Column("call_status", Text, nullable=False, server_default=text("'success'")),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("idx_cost_org", "org_id"),
    Index("idx_cost_created", "created_at"),
    # Migration 030
    Index("idx_cost_events_org_date", "org_id", "created_at"),
)

sync_runs = Table(
    "sync_runs",
    metadata,
    Column("sync_id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("sync_type", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'running'")),
    Column("started_at", Text, nullable=False, server_default=func.now()),
    Column("completed_at", Text),
    Column("records_synced", Integer, nullable=False, server_default="0"),
    Column("error", Text),
    # Migration 002
    Column("cult_run_id", Text),
    Column("entities_created", Integer, nullable=False, server_default="0"),
    Column("entities_updated", Integer, nullable=False, server_default="0"),
    Column("handles_added", Integer, nullable=False, server_default="0"),
    Column("tags_added", Integer, nullable=False, server_default="0"),
    Column("tags_replaced", Integer, nullable=False, server_default="0"),
    Column("merge_candidates_created", Integer, nullable=False, server_default="0"),
    Index("idx_sync_org", "org_id"),
    Index("idx_sync_cult_run_id", "cult_run_id"),
)

# ------------------------------------------------------------------
# Workflow orchestration
# ------------------------------------------------------------------

workflow_runs = Table(
    "workflow_runs",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("workflow_name", Text, nullable=False),
    Column("workflow_version", Text, nullable=False, server_default=text("'1.0'")),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("config_json", Text),
    Column("started_at", Text),
    Column("completed_at", Text),
    Column("error", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    # Migration 012
    Column("step_fingerprint", Text),
    # Migration 024
    Column("operator_id", Text),
    Index("idx_workflow_runs_org", "org_id"),
    Index("idx_workflow_runs_name", "workflow_name", "status"),
)

# Migration 027: partial unique index — at most one active run per (org, workflow)
Index(
    "idx_workflow_runs_active_lock",
    workflow_runs.c.org_id,
    workflow_runs.c.workflow_name,
    unique=True,
    sqlite_where=workflow_runs.c.status.in_(["pending", "running"]),
    postgresql_where=workflow_runs.c.status.in_(["pending", "running"]),
)

workflow_steps = Table(
    "workflow_steps",
    metadata,
    Column("step_id", Text, primary_key=True),
    Column("run_id", Text, ForeignKey("workflow_runs.run_id"), nullable=False),
    Column("step_name", Text, nullable=False),
    Column("step_index", Integer, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("retries", Integer, nullable=False, server_default="0"),
    Column("input_json", Text),
    Column("output_json", Text),
    Column("error", Text),
    Column("started_at", Text),
    Column("completed_at", Text),
    Index("idx_workflow_steps_run", "run_id"),
)

workflow_events = Table(
    "workflow_events",
    metadata,
    Column("event_id", Text, primary_key=True),
    Column("run_id", Text, ForeignKey("workflow_runs.run_id"), nullable=False),
    Column("step_id", Text),
    Column("event_type", Text, nullable=False),
    Column("payload_json", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("idx_workflow_events_run", "run_id"),
)

# ------------------------------------------------------------------
# Actions & outcomes
# ------------------------------------------------------------------

actions = Table(
    "actions",
    metadata,
    Column("action_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("entity_id", Text, ForeignKey("entities.entity_id")),
    Column("content_item_id", Text, ForeignKey("content_items.item_id")),
    Column("source", Text, nullable=False, server_default=text("'manual'")),
    Column("source_ref", Text),
    Column("action_type", Text, nullable=False, server_default=text("'general'")),
    Column("title", Text, nullable=False),
    Column("description", Text),
    Column("operator", Text),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("claimed_at", Text),
    Column("completed_at", Text),
    Column("skipped_at", Text),
    Column("outcome_notes", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("idx_actions_org", "org_id", "status"),
)

# Partial indexes from migration 007
Index(
    "idx_actions_entity",
    actions.c.entity_id,
    sqlite_where=actions.c.entity_id.isnot(None),
    postgresql_where=actions.c.entity_id.isnot(None),
)
Index(
    "idx_actions_pending",
    actions.c.org_id,
    actions.c.created_at,
    sqlite_where=actions.c.status == "pending",
    postgresql_where=actions.c.status == "pending",
)

outcomes = Table(
    "outcomes",
    metadata,
    Column("outcome_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("entity_id", Text, ForeignKey("entities.entity_id")),
    Column("action_id", Text, ForeignKey("actions.action_id")),
    Column("outcome_type", Text, nullable=False),
    Column("description", Text),
    Column("metric_name", Text),
    Column("metric_before", Float),
    Column("metric_after", Float),
    Column("metric_delta", Float),
    Column("data_json", Text),
    Column("recorded_by", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("idx_outcomes_org", "org_id"),
)

# Partial indexes from migration 007
Index(
    "idx_outcomes_action",
    outcomes.c.action_id,
    sqlite_where=outcomes.c.action_id.isnot(None),
    postgresql_where=outcomes.c.action_id.isnot(None),
)
Index(
    "idx_outcomes_entity",
    outcomes.c.entity_id,
    sqlite_where=outcomes.c.entity_id.isnot(None),
    postgresql_where=outcomes.c.entity_id.isnot(None),
)

# ------------------------------------------------------------------
# Entity journey
# ------------------------------------------------------------------

entity_tag_history = Table(
    "entity_tag_history",
    metadata,
    Column("history_id", Text, primary_key=True),
    Column("entity_id", Text, ForeignKey("entities.entity_id"), nullable=False),
    Column("org_id", Text, nullable=False),
    Column("change_type", Text, nullable=False),
    Column("tag", Text, nullable=False),
    Column("confidence", Float),
    Column("source", Text),
    Column("source_ref", Text),
    Column("expires_at", Text),
    Column("effective_at", Text, nullable=False, server_default=func.now()),
    Index("idx_tag_history_entity", "entity_id", "effective_at"),
    Index("idx_tag_history_org", "org_id", "tag", "effective_at"),
)

# ------------------------------------------------------------------
# Alerting
# ------------------------------------------------------------------

alert_configs = Table(
    "alert_configs",
    metadata,
    Column("config_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("min_severity", Text, nullable=False, server_default=text("'warning'")),
    Column("telegram_chat_id", Text),
    Column("discord_webhook_url", Text),
    Column("enabled", Integer, nullable=False, server_default="1"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    # Migration 011
    Column("cooldown_hours", Integer, nullable=False, server_default="4"),
    UniqueConstraint("org_id"),
    Index("idx_alert_configs", "org_id"),
)

alerts = Table(
    "alerts",
    metadata,
    Column("alert_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id")),
    Column("alert_type", Text, nullable=False),
    Column("severity", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("body", Text),
    Column("entity_id", Text, ForeignKey("entities.entity_id")),
    Column("action_id", Text, ForeignKey("actions.action_id")),
    Column("run_id", Text, ForeignKey("workflow_runs.run_id")),
    Column("data_json", Text),
    Column("status", Text, nullable=False, server_default=text("'new'")),
    Column("dedup_key", Text),
    Column("acknowledged_at", Text),
    Column("acknowledged_by", Text),
    Column("resolved_at", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    # Migration 011
    Column("last_delivered_at", Text),
    # Migration 013
    Column("last_delivery_error", Text),
    Index("idx_alerts_org", "org_id", "status", "severity"),
)

# Partial unique index on dedup_key (migration 009)
Index(
    "idx_alerts_dedup",
    alerts.c.dedup_key,
    sqlite_where=(alerts.c.dedup_key.isnot(None)) & (alerts.c.status == "new"),
    postgresql_where=(alerts.c.dedup_key.isnot(None)) & (alerts.c.status == "new"),
)

# ------------------------------------------------------------------
# Discord pulse
# ------------------------------------------------------------------

discord_pulse_runs = Table(
    "discord_pulse_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, nullable=False),
    Column("project_slug", Text, nullable=False),
    Column("run_date", Text, nullable=False),
    Column("wow_retention_rate", Float),
    Column("echo_rate", Float),
    Column("avg_silence_gap_hours", Float),
    Column("weekly_active_posters", Integer),
    Column("retention_delta", Float),
    Column("echo_rate_delta", Float),
    # Migration 010 uses strftime('%Y-%m-%dT%H:%M:%SZ', 'now') — not datetime('now')
    Column("created_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint("org_id", "project_slug", "run_date"),
    Index("idx_discord_pulse_runs_org_date", "org_id", "run_date"),
)

# Migration 043: discord_streak_events for fit-check streak bot (PLAN.md SS10).
# One row per image post in a configured #fitcheck channel; reaction_score
# is recomputed on add/remove via optimistic lock on updated_at.
discord_streak_events = Table(
    "discord_streak_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, nullable=False),
    Column("guild_id", Text, nullable=False),
    Column("channel_id", Text, nullable=False),
    Column("post_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("posted_at", Text, nullable=False),
    Column("counted_for_day", Text, nullable=False),
    Column("attachment_count", Integer, nullable=False, server_default=text("0")),
    Column("image_attachment_count", Integer, nullable=False, server_default=text("0")),
    Column("reaction_score", Integer, nullable=False, server_default=text("0")),
    Column("counts_for_streak", Integer, nullable=False, server_default=text("1")),
    Column("invalidated_at", Text),
    Column("invalidated_reason", Text),
    Column("ingest_source", Text, nullable=False, server_default=text("'gateway'")),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    # Migration 049: image_phash for Scored Mode V2 Pass A. Captured at post
    # time even when scoring state='off' — collision detection works always.
    Column("image_phash", Text),
    UniqueConstraint("guild_id", "post_id", name="uq_discord_streak_events_guild_post"),
    Index("idx_discord_streak_events_org_day", "org_id", "counted_for_day"),
    Index("idx_discord_streak_events_user_day", "org_id", "user_id", "counted_for_day"),
    Index("idx_discord_streak_events_channel_posted", "org_id", "channel_id", "posted_at"),
    Index(
        "idx_discord_streak_events_user_reactions",
        "org_id",
        "user_id",
        text("reaction_score DESC"),
    ),
    Index("idx_discord_streak_events_org_phash", "org_id", "image_phash"),
)

# Migration 045: discord_guild_config for sable-roles V2 (relax-mode + global burn-me mode).
# One row per configured guild. Created lazily by the first mod command that mutates it.
discord_guild_config = Table(
    "discord_guild_config",
    metadata,
    Column("guild_id", Text, primary_key=True),
    Column("relax_mode_on", Integer, nullable=False, server_default=text("0")),
    Column("current_burn_mode", Text, nullable=False, server_default=text("'once'")),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    Column("updated_by", Text, nullable=False),
    # Migration 047 — personalize-mode toggle for /roast personalization layer.
    Column("personalize_mode_on", Integer, nullable=False, server_default=text("0")),
)

# Migration 046: burn-me opt-in state + random-roast dedup log for sable-roles V2 burn-me.
discord_burn_optins = Table(
    "discord_burn_optins",
    metadata,
    Column("guild_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("mode", Text, nullable=False),
    Column("opted_in_by", Text, nullable=False),
    Column("opted_in_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("guild_id", "user_id"),
)

discord_burn_random_log = Table(
    "discord_burn_random_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("roasted_at", Text, nullable=False, server_default=func.now()),
    Index(
        "idx_discord_burn_random_log_recent",
        "guild_id",
        "user_id",
        text("roasted_at DESC"),
    ),
)

# Migration 047: sticky /stop-pls blocklist for sable-roles V2 burn-me + /roast.
# UNIQUE(guild_id, user_id) prevents duplicate opt-outs; the id is an audit
# autoincrement so we can list opt-outs in insertion order if needed.
discord_burn_blocklist = Table(
    "discord_burn_blocklist",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("blocked_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint("guild_id", "user_id", name="uq_discord_burn_blocklist_guild_user"),
    Index("idx_discord_burn_blocklist_user", "user_id", "guild_id"),
)

# Migration 047: peer-roast token ledger (monthly + streak-restoration).
# UNIQUE(guild_id, actor_user_id, year_month, source) blocks the concurrent
# double-grant race; grants MUST use ON CONFLICT DO NOTHING. The partial
# target index accelerates the per-target monthly volume cap on the peer-
# roast hot path.
discord_peer_roast_tokens = Table(
    "discord_peer_roast_tokens",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, nullable=False),
    Column("actor_user_id", Text, nullable=False),
    Column("granted_at", Text, nullable=False, server_default=func.now()),
    Column("source", Text, nullable=False),
    Column("year_month", Text, nullable=False),
    Column("consumed_at", Text),
    Column("consumed_on_post_id", Text),
    Column("consumed_target_user_id", Text),
    CheckConstraint(
        "source IN ('monthly', 'streak_restoration')",
        name="ck_discord_peer_roast_tokens_source",
    ),
    UniqueConstraint(
        "guild_id", "actor_user_id", "year_month", "source",
        name="uq_discord_peer_roast_tokens_grant",
    ),
    Index(
        "idx_discord_peer_roast_tokens_actor_month",
        "actor_user_id", "guild_id", "year_month",
    ),
)

Index(
    "idx_discord_peer_roast_tokens_target_month",
    discord_peer_roast_tokens.c.consumed_target_user_id,
    discord_peer_roast_tokens.c.guild_id,
    discord_peer_roast_tokens.c.year_month,
    sqlite_where=discord_peer_roast_tokens.c.consumed_at.isnot(None),
    postgresql_where=discord_peer_roast_tokens.c.consumed_at.isnot(None),
)

# Migration 047: peer-roast flag log. reactor_user_id distinguishes target-
# self-flags from third-party flags; bot_reply_id resolves attribution when
# mod + peer roasts share the same target fit post_id.
discord_peer_roast_flags = Table(
    "discord_peer_roast_flags",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, nullable=False),
    Column("target_user_id", Text, nullable=False),
    Column("actor_user_id", Text, nullable=False),
    Column("post_id", Text, nullable=False),
    Column("bot_reply_id", Text, nullable=False),
    Column("reactor_user_id", Text, nullable=False),
    Column("flagged_at", Text, nullable=False, server_default=func.now()),
    Index(
        "idx_discord_peer_roast_flags_target",
        "target_user_id", "guild_id", "flagged_at",
    ),
    Index("idx_discord_peer_roast_flags_bot_reply", "bot_reply_id"),
)

# Migration 047: raw per-message observation log (source for daily rollup).
# Created BEFORE discord_user_observations + discord_user_vibes so the
# TABLE_LOAD_ORDER chain on the Postgres restore side stays FK-safe.
discord_message_observations = Table(
    "discord_message_observations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, nullable=False),
    Column("channel_id", Text, nullable=False),
    Column("message_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("content_truncated", Text),
    Column("reactions_given_json", Text),
    Column("posted_at", Text, nullable=False),
    Column("captured_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint(
        "guild_id", "message_id",
        name="uq_discord_message_observations_guild_message",
    ),
    Index(
        "idx_discord_message_observations_user_time",
        "user_id", "guild_id", "posted_at",
    ),
    Index("idx_discord_message_observations_gc", "captured_at"),
)

# Migration 047: user observation rollups (populated by daily cron from
# discord_message_observations).
discord_user_observations = Table(
    "discord_user_observations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("window_start", Text, nullable=False),
    Column("window_end", Text, nullable=False),
    Column("message_count", Integer, nullable=False, server_default=text("0")),
    Column("sample_messages_json", Text),
    Column("reaction_emojis_given_json", Text),
    Column("channels_active_in_json", Text),
    Column("computed_at", Text, nullable=False, server_default=func.now()),
    Index(
        "idx_discord_user_observations_user",
        "user_id", "guild_id", "computed_at",
    ),
)

# Migration 047: LLM-summarized per-user vibe block. FK ->
# discord_user_observations.id pins the FK-safe TABLE_LOAD_ORDER
# (observations BEFORE vibes) on Postgres restore.
discord_user_vibes = Table(
    "discord_user_vibes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("vibe_block_text", Text, nullable=False),
    Column("identity", Text),
    Column("activity_rhythm", Text),
    Column("reaction_signature", Text),
    Column("palette_signals", Text),
    Column("tone", Text),
    Column("inferred_at", Text, nullable=False, server_default=func.now()),
    Column(
        "source_observation_id",
        Integer,
        ForeignKey("discord_user_observations.id"),
    ),
    Index(
        "idx_discord_user_vibes_user_recent",
        "user_id", "guild_id", "inferred_at",
    ),
)

# Migration 048: airlock — bot-local snapshot of guild.invites(). Diffed on
# each on_member_join to attribute the join to whichever invite's uses
# incremented. UNIQUE(guild_id, code) so re-snapshot UPSERTs cleanly.
discord_invite_snapshot = Table(
    "discord_invite_snapshot",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, nullable=False),
    Column("code", Text, nullable=False),
    Column("inviter_user_id", Text),
    Column("uses", Integer, nullable=False, server_default=text("0")),
    Column("max_uses", Integer, nullable=False, server_default=text("0")),
    Column("expires_at", Text),
    Column("captured_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint("guild_id", "code", name="uq_discord_invite_snapshot_guild_code"),
    Index("idx_discord_invite_snapshot_guild", "guild_id"),
)

# Migration 048: Sable-team allowlist whose invites bypass airlock.
# Bootstrapped from SABLE_ROLES_TEAM_INVITERS_JSON env on bot boot
# (UPSERT, idempotent). Runtime mgmt via /add-team-inviter slash command.
discord_team_inviters = Table(
    "discord_team_inviters",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("added_at", Text, nullable=False, server_default=func.now()),
    Column("added_by", Text, nullable=False),
    UniqueConstraint("guild_id", "user_id", name="uq_discord_team_inviters_guild_user"),
    Index("idx_discord_team_inviters_guild", "guild_id"),
)

# Migration 048: per-join admit ledger w/ airlock state machine. UNIQUE
# (guild_id, user_id) so rejoin overwrites via ON CONFLICT DO UPDATE
# (callers refresh the prior row's status to reflect the fresh join).
discord_member_admit = Table(
    "discord_member_admit",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("joined_at", Text, nullable=False, server_default=func.now()),
    Column("attributed_invite_code", Text),
    Column("attributed_inviter_user_id", Text),
    Column("is_team_invite", Integer, nullable=False, server_default=text("0")),
    Column("airlock_status", Text, nullable=False),
    Column("decision_by", Text),
    Column("decision_at", Text),
    Column("decision_reason", Text),
    CheckConstraint(
        "airlock_status IN ('held', 'auto_admitted', 'admitted', 'banned',"
        " 'kicked', 'left_during_airlock')",
        name="ck_discord_member_admit_status",
    ),
    UniqueConstraint("guild_id", "user_id", name="uq_discord_member_admit_guild_user"),
    Index("idx_discord_member_admit_status", "guild_id", "airlock_status"),
)


# Migration 050: discord_fitcheck_scores for Scored Mode V2 Pass B. One row
# per scored fit (success or failed). percentile frozen at score time. reveal_*
# columns ship now even though Pass C is a separate PR — schema parity tests
# pin the table shape so migration 050 stays load-bearing across PRs.
discord_fitcheck_scores = Table(
    "discord_fitcheck_scores",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, nullable=False),
    Column("guild_id", Text, nullable=False),
    Column("post_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("posted_at", Text, nullable=False),
    Column("scored_at", Text, nullable=False),
    Column("model_id", Text, nullable=False),
    Column("prompt_version", Text, nullable=False),
    Column("score_status", Text, nullable=False),
    Column("score_error", Text),
    Column("axis_cohesion", Integer),
    Column("axis_execution", Integer),
    Column("axis_concept", Integer),
    Column("axis_catch", Integer),
    Column("raw_total", Integer),
    Column("catch_detected", Text),
    Column("catch_naming_class", Text),
    Column("description", Text),
    Column("confidence", Float),
    Column("axis_rationales_json", Text),
    Column("curve_basis", Text),
    Column("pool_size_at_score_time", Integer),
    Column("percentile", Float),
    Column("reveal_eligible", Integer, nullable=False, server_default=text("0")),
    Column("reveal_fired_at", Text),
    Column("reveal_post_id", Text),
    Column("reveal_trigger", Text),
    Column("invalidated_at", Text),
    Column("invalidated_reason", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint("guild_id", "post_id", name="uq_discord_fitcheck_scores_guild_post"),
    Index(
        "idx_discord_fitcheck_scores_user_pct",
        "org_id",
        "user_id",
        text("percentile DESC"),
    ),
    Index("idx_discord_fitcheck_scores_org_posted", "org_id", "posted_at"),
    Index("idx_discord_fitcheck_scores_status", "org_id", "score_status"),
    Index("idx_discord_fitcheck_scores_reveal_fired", "org_id", "reveal_fired_at"),
)


# Migration 052: discord_fitcheck_emoji_milestones for Scored Mode V2 Pass C.
# Durable per-(post_id, emoji_key, milestone) crossing state. The reveal
# pipeline records 5/8/10-reactor milestones here so post-restart recomputes
# don't re-audit the same milestone. UNIQUE constraint blocks double-audit
# from near-simultaneous reaction events.
discord_fitcheck_emoji_milestones = Table(
    "discord_fitcheck_emoji_milestones",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, nullable=False),
    Column("guild_id", Text, nullable=False),
    Column("post_id", Text, nullable=False),
    Column("emoji_key", Text, nullable=False),
    Column("milestone", Integer, nullable=False),
    Column("crossed_at", Text, nullable=False),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint(
        "guild_id", "post_id", "emoji_key", "milestone",
        name="uq_discord_fitcheck_emoji_milestones_crossing",
    ),
    Index(
        "idx_discord_fitcheck_emoji_milestones_post",
        "guild_id", "post_id",
    ),
)


# Migration 051: discord_scoring_config for Scored Mode V2 Pass B. One row per
# guild. Default state='off' — safety floor. First mod /scoring set is what
# flips a guild into scoring. UNIQUE(guild_id) lets upsert use ON CONFLICT.
discord_scoring_config = Table(
    "discord_scoring_config",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, nullable=False),
    Column("guild_id", Text, nullable=False),
    Column("state", Text, nullable=False, server_default=text("'off'")),
    Column("state_changed_by", Text),
    Column("state_changed_at", Text),
    Column("reaction_threshold", Integer, nullable=False, server_default=text("10")),
    Column("thread_message_threshold", Integer, nullable=False, server_default=text("100")),
    Column("reveal_window_days", Integer, nullable=False, server_default=text("7")),
    Column("reveal_min_age_minutes", Integer, nullable=False, server_default=text("10")),
    Column("curve_window_days", Integer, nullable=False, server_default=text("30")),
    Column("cold_start_min_pool", Integer, nullable=False, server_default=text("20")),
    Column("model_id", Text, nullable=False, server_default=text("'claude-sonnet-4-6'")),
    Column("prompt_version", Text, nullable=False, server_default=text("'rubric_v1'")),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint("guild_id", name="uq_discord_scoring_config_guild"),
)


# Migration 054: discord_state_pins for the state-pin surface. One row
# per (guild_id, characteristic) tracking the currently-pinned "stitzy
# state" message id in the per-guild ops channel. UNIQUE constraint
# enforces one-pin-per-dimension. The optimistic-lock UPDATE in
# upsert_state_pin uses WHERE updated_at = :expected to detect lost
# races between concurrent state rotations.
discord_state_pins = Table(
    "discord_state_pins",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, nullable=False),
    Column("characteristic", Text, nullable=False),
    Column("channel_id", Text, nullable=False),
    Column("message_id", Text, nullable=False),
    Column("posted_at", Text, nullable=False),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint(
        "guild_id", "characteristic",
        name="uq_discord_state_pins_guild_characteristic",
    ),
    Index("idx_discord_state_pins_guild", "guild_id"),
)


# NOTE: schema.py must stay in sync with _MIGRATIONS in connection.py.
# The parity tests in tests/db/test_schema.py verify this mechanically.

# ------------------------------------------------------------------
# Community graph
# ------------------------------------------------------------------

entity_interactions = Table(
    "entity_interactions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("source_handle", Text, nullable=False),
    Column("target_handle", Text, nullable=False),
    Column("interaction_type", Text, nullable=False),
    Column("count", Integer, nullable=False, server_default="1"),
    Column("first_seen", Text),
    Column("last_seen", Text),
    Column("run_date", Text),
    Index("idx_entity_interactions_org", "org_id"),
    Index("idx_entity_interactions_source", "org_id", "source_handle"),
    Index("idx_entity_interactions_type", "org_id", "interaction_type"),
)

entity_decay_scores = Table(
    "entity_decay_scores",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("decay_score", Float, nullable=False),
    Column("risk_tier", Text, nullable=False),
    Column("scored_at", Text, nullable=False, server_default=func.now()),
    Column("run_date", Text),
    Column("factors_json", Text),
    UniqueConstraint("org_id", "entity_id"),
    Index("idx_decay_scores_org", "org_id"),
    Index("idx_decay_scores_tier", "org_id", "risk_tier"),
)

entity_centrality_scores = Table(
    "entity_centrality_scores",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("degree_centrality", Float, nullable=False, server_default="0.0"),
    Column("betweenness_centrality", Float, nullable=False, server_default="0.0"),
    Column("eigenvector_centrality", Float, nullable=False, server_default="0.0"),
    Column("scored_at", Text, nullable=False, server_default=func.now()),
    Column("run_date", Text, nullable=False),
    # Migration 023
    Column("in_centrality", Float, nullable=False, server_default="0.0"),
    Column("out_centrality", Float, nullable=False, server_default="0.0"),
    UniqueConstraint("org_id", "entity_id"),
    Index("idx_centrality_org", "org_id"),
)

# ------------------------------------------------------------------
# Watchlist
# ------------------------------------------------------------------

entity_watchlist = Table(
    "entity_watchlist",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("added_by", Text, nullable=False),
    Column("note", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint("org_id", "entity_id"),
    Index("idx_watchlist_org", "org_id"),
)

watchlist_snapshots = Table(
    "watchlist_snapshots",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("decay_score", Float),
    Column("tags_json", Text),
    Column("interaction_count", Integer),
    Column("snapshot_at", Text, nullable=False, server_default=func.now()),
    Index("idx_watchlist_snap", "org_id", "entity_id", "snapshot_at"),
)

# ------------------------------------------------------------------
# Audit log
# ------------------------------------------------------------------

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", Text, nullable=False, server_default=func.now()),
    Column("actor", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("org_id", Text),
    Column("entity_id", Text),
    Column("detail_json", Text),
    Column("source", Text, nullable=False, server_default=text("'cli'")),
    Index("idx_audit_org", "org_id", "timestamp"),
    Index("idx_audit_actor", "actor", "timestamp"),
)

# ------------------------------------------------------------------
# Webhooks
# ------------------------------------------------------------------

webhook_subscriptions = Table(
    "webhook_subscriptions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("url", Text, nullable=False),
    Column("event_types", Text, nullable=False),
    Column("secret", Text, nullable=False),
    Column("enabled", Integer, nullable=False, server_default="1"),
    Column("consecutive_failures", Integer, nullable=False, server_default="0"),
    Column("last_failure_at", Text),
    Column("last_failure_error", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint("org_id", "url"),
)

# ------------------------------------------------------------------
# Prospect pipeline
# ------------------------------------------------------------------

prospect_scores = Table(
    "prospect_scores",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, nullable=False),
    Column("run_date", Text, nullable=False),
    Column("composite_score", Float, nullable=False),
    Column("tier", Text, nullable=False),
    Column("stage", Text),
    Column("dimensions_json", Text, nullable=False, server_default=text("'{}'")),
    Column("rationale_json", Text),
    Column("enrichment_json", Text),
    Column("next_action", Text),
    Column("scored_at", Text, nullable=False, server_default=func.now()),
    # Migration 025
    Column("graduated_at", Text),
    # Migration 026
    Column("rejected_at", Text),
    # Migration 029
    Column("recommended_action", Text),
    Column("score_band_low", Float),
    Column("score_band_high", Float),
    Column("timing_urgency", Text),
    UniqueConstraint("org_id", "run_date"),
    Index("idx_prospect_scores_org", "org_id"),
    Index("idx_prospect_scores_date", "run_date"),
)

# ------------------------------------------------------------------
# Playbook
# ------------------------------------------------------------------

playbook_targets = Table(
    "playbook_targets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("artifact_id", Text),
    Column("targets_json", Text, nullable=False),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("idx_playbook_targets_org", "org_id"),
)

playbook_outcomes = Table(
    "playbook_outcomes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("targets_artifact_id", Text),
    Column("outcomes_json", Text, nullable=False),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("idx_playbook_outcomes_org", "org_id"),
)

# ------------------------------------------------------------------
# Metric snapshots (Migration 031) — week-over-week persistence for
# client_checkin_loop. One row per (org_id, snapshot_date).
# ------------------------------------------------------------------

metric_snapshots = Table(
    "metric_snapshots",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("snapshot_date", Text, nullable=False),
    Column("metrics_json", Text, nullable=False, server_default=text("'{}'")),
    Column("source", Text, nullable=False),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint("org_id", "snapshot_date"),
    Index("idx_metric_snapshots_org_date", "org_id", "snapshot_date"),
)

# ------------------------------------------------------------------
# SableKOL bank (Migration 032) — three tables for the Phase 0
# bank-backed KOL matcher. See ~/Projects/SableKOL/PLAN.md.
# ------------------------------------------------------------------

kol_candidates = Table(
    "kol_candidates",
    metadata,
    Column("candidate_id", Integer, primary_key=True, autoincrement=True),
    Column("twitter_id", Text),
    Column("handle_normalized", Text, nullable=False),
    Column("is_unresolved", Integer, nullable=False, server_default=text("0")),
    Column("handle_history_json", Text, nullable=False, server_default=text("'[]'")),
    Column("display_name", Text),
    Column("bio_snapshot", Text),
    Column("followers_snapshot", Integer),
    Column("discovery_sources_json", Text, nullable=False, server_default=text("'[]'")),
    Column("first_seen_at", Text, nullable=False, server_default=func.now()),
    Column("last_seen_at", Text, nullable=False, server_default=func.now()),
    Column("archetype_tags_json", Text, nullable=False, server_default=text("'[]'")),
    Column("sector_tags_json", Text, nullable=False, server_default=text("'[]'")),
    Column(
        "sable_relationship_json",
        Text,
        nullable=False,
        server_default=text("'{\"communities\":[],\"operators\":[]}'"),
    ),
    Column("enrichment_tier", Text, nullable=False, server_default=text("'none'")),
    Column("last_enriched_at", Text),
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column("manual_notes", Text),
    # Migration 033: KOL strength score + paid-enrichment fields.
    Column("kol_strength_score", Float),
    Column("verified", Integer, nullable=False, server_default=text("0")),
    Column("account_created_at", Text),
    # Migration 034: Grok-enrichment fields.
    Column("listed_count", Integer),
    Column("tweets_count", Integer),
    Column("following_count", Integer),
    Column("credibility_signal", Text),
    Column("real_name_known", Integer, nullable=False, server_default=text("0")),
    Column("notes", Text),
    # Migration 035: location.
    Column("location", Text),
    # Migration 036: cross-platform presence (Instagram/TikTok/Threads/etc as JSON).
    Column("platform_presence_json", Text, nullable=False, server_default="{}"),
    # At most one LIVE (is_unresolved=0) row per normalized handle. Unresolved
    # duplicates are permitted; tracked via kol_handle_resolution_conflicts.
    Index(
        "idx_kol_candidates_handle_live",
        "handle_normalized",
        unique=True,
        sqlite_where=text("is_unresolved = 0"),
        postgresql_where=text("is_unresolved = 0"),
    ),
    Index(
        "idx_kol_candidates_twitter_id",
        "twitter_id",
        sqlite_where=text("twitter_id IS NOT NULL"),
        postgresql_where=text("twitter_id IS NOT NULL"),
    ),
    Index("idx_kol_candidates_status", "status"),
    Index(
        "idx_kol_candidates_strength",
        "kol_strength_score",
        sqlite_where=text("kol_strength_score IS NOT NULL"),
        postgresql_where=text("kol_strength_score IS NOT NULL"),
    ),
    Index(
        "idx_kol_candidates_credibility",
        "credibility_signal",
        sqlite_where=text("credibility_signal IS NOT NULL"),
        postgresql_where=text("credibility_signal IS NOT NULL"),
    ),
)

project_profiles_external = Table(
    "project_profiles_external",
    metadata,
    Column("handle_normalized", Text, primary_key=True),
    Column("twitter_id", Text),
    Column("sector_tags_json", Text, nullable=False, server_default=text("'[]'")),
    Column("themes_json", Text, nullable=False, server_default=text("'[]'")),
    Column("profile_blob", Text),
    Column("enrichment_source", Text, nullable=False, server_default=text("'manual_only'")),
    Column("last_enriched_at", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("last_used_at", Text, nullable=False, server_default=func.now()),
)

kol_handle_resolution_conflicts = Table(
    "kol_handle_resolution_conflicts",
    metadata,
    Column("conflict_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "incoming_candidate_id",
        Integer,
        ForeignKey("kol_candidates.candidate_id"),
        nullable=False,
    ),
    Column(
        "existing_candidate_id",
        Integer,
        ForeignKey("kol_candidates.candidate_id"),
        nullable=False,
    ),
    Column("resolved_twitter_id", Text),
    Column("detected_at", Text, nullable=False, server_default=func.now()),
    Column("resolution_state", Text, nullable=False, server_default=text("'open'")),
    Column("resolved_at", Text),
    Column("notes", Text),
    Index("idx_kol_conflicts_state", "resolution_state"),
)

# Migration 037: follow-graph extraction tables. Parent run record + edge
# table so partial extractions are distinguishable from complete ones (the
# cursor_completed flag gates downstream kingmaker / cluster queries).
kol_extract_runs = Table(
    "kol_extract_runs",
    metadata,
    Column("run_id", Text, primary_key=True),
    Column("target_handle_normalized", Text, nullable=False),
    Column("target_user_id", Text),
    Column("provider", Text, nullable=False),
    Column("extract_type", Text, nullable=False),
    Column("started_at", Text, nullable=False, server_default=func.now()),
    Column("completed_at", Text),
    Column("cursor_completed", Integer, nullable=False, server_default=text("0")),
    Column("last_cursor", Text),
    Column("pages_fetched", Integer, nullable=False, server_default=text("0")),
    Column("rows_inserted", Integer, nullable=False, server_default=text("0")),
    Column("expected_count", Integer),
    Column("partial_failure_reason", Text),
    Column("cost_usd_logged", Float, nullable=False, server_default=text("0")),
    # Migration 039: per-client scoping. Default '_external' for sentinel/legacy;
    # SolStitch runs backfilled to 'solstitch'.
    Column("client_id", Text, nullable=False, server_default=text("'_external'")),
    Index("idx_kol_extract_runs_target", "target_handle_normalized", "extract_type"),
    Index("idx_kol_extract_runs_completed", "cursor_completed"),
    Index("idx_kol_extract_runs_client", "client_id", "extract_type", "cursor_completed"),
)

kol_follow_edges = Table(
    "kol_follow_edges",
    metadata,
    Column(
        "run_id",
        Text,
        ForeignKey("kol_extract_runs.run_id"),
        nullable=False,
    ),
    Column("follower_id", Text, nullable=False),
    Column("follower_handle", Text),
    Column("followed_id", Text, nullable=False),
    Column("followed_handle", Text, nullable=False),
    Column("fetched_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("run_id", "follower_id", "followed_id"),
    Index("idx_kol_follow_edges_followed", "followed_id"),
    Index("idx_kol_follow_edges_followed_handle", "followed_handle"),
    Index("idx_kol_follow_edges_follower", "follower_id"),
)

# Migration 038: append-only operator relationship-tagging table. One row
# per status change. Current state for a (handle, client) is the row with
# MAX(created_at). See ~/Projects/SableKOL/docs/sableweb_kol_build_plan.md.
kol_operator_relationships = Table(
    "kol_operator_relationships",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("handle_normalized", Text, nullable=False),
    Column("client_id", Text, nullable=False),
    Column("operator_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("note", Text),
    Column("is_private", Integer, nullable=False, server_default=text("0")),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("idx_kor_handle_client", "handle_normalized", "client_id"),
    Index("idx_kor_operator", "operator_id", "client_id"),
    Index("idx_kor_created", "created_at"),
)


# ------------------------------------------------------------------
# KOL wizard auth audit (migration 040)
# ------------------------------------------------------------------

# Migration 040: append-only audit log for /api/ops/kol-network/* requests.
# `email` is NULLABLE so anonymous (no session) failures with
# outcome='auth_failed' can still log. Retention: 90 days via cron-purge.
# See ~/Projects/SableKOL/docs/any_project_wizard_plan.md.
kol_create_audit = Table(
    "kol_create_audit",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("at_utc", Text, nullable=False, server_default=func.now()),
    Column("email", Text),
    Column("endpoint", Text, nullable=False),
    Column("method", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("job_id", Text, ForeignKey("jobs.job_id")),
    Column("ip", Text),
    Column("user_agent", Text),
    # Migration 042: per-row review state. The /ops/kol-network picker
    # blocks "+ New project" while a user has any pending row; the admin
    # approval page mutates these fields when an admin acts on a row.
    # Default 'approved' (not 'pending') so the migration backfills
    # historical rows as already-cleared. The wizard write path stamps
    # 'pending' explicitly on new submissions; only those count toward
    # the per-user gate.
    Column("review_status", Text, nullable=False, server_default=text("'approved'")),
    Column("reviewed_by", Text),
    Column("reviewed_at", Text),
    Index("idx_kol_create_audit_email", "email"),
    Index("idx_kol_create_audit_at", "at_utc"),
    Index("idx_kol_create_audit_outcome", "outcome"),
    Index("idx_kol_create_audit_review", "email", "review_status", "endpoint"),
)


# ------------------------------------------------------------------
# API tokens (migration 044) — owner-issued bearer credentials for the
# alert-triage HTTP API MVP. See TODO_API.md.
# ------------------------------------------------------------------

api_tokens = Table(
    "api_tokens",
    metadata,
    Column("token_id", Text, primary_key=True),
    Column("token_hash", Text, nullable=False),
    Column("label", Text, nullable=False),
    Column("operator_id", Text, nullable=False),
    Column("created_by", Text, nullable=False),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("expires_at", Text),
    Column("last_used_at", Text),
    Column("revoked_at", Text),
    Column("enabled", Integer, nullable=False, server_default="1"),
    Column("scopes_json", Text, nullable=False, server_default=text("'[\"read_only\"]'")),
    Column("org_scopes_json", Text, nullable=False, server_default=text("'[]'")),
    Index("idx_api_tokens_enabled", "enabled", "expires_at"),
    Index("idx_api_tokens_operator", "operator_id"),
)


# Per-(candidate, operator) Grok enrichment cache. KO-3 redesign post-2026-05-10.
# Each row is one fetch; lookups read latest by (candidate_id, operator_email)
# ordered by fetched_at DESC. payload_json carries the structured + prose
# blocks (Enrichment schema in sable_kol/preflight_schemas.py).
kol_enrichment = Table(
    "kol_enrichment",
    metadata,
    Column("enrichment_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "candidate_id",
        Integer,
        ForeignKey("kol_candidates.candidate_id"),
        nullable=False,
    ),
    Column("operator_email", Text, nullable=False),
    Column("operator_persona", Text, nullable=False),
    Column("fetched_at", Text, nullable=False, server_default=func.now()),
    Column("payload_json", Text, nullable=False),
    Column("grok_model", Text),
    Column("cost_usd", Float, server_default="0"),
    # SQLite indexes don't support DESC inside CREATE INDEX (well, they
    # accept the syntax, but the SA Index helper doesn't pass through
    # the DESC modifier across both dialects without a literal_column).
    # Postgres handles the DESC explicitly via the Alembic migration; for
    # the SA-test parity check we just need the column set to match.
    Index(
        "idx_kol_enrichment_lookup",
        "candidate_id",
        "operator_email",
        "fetched_at",
    ),
    Index("idx_kol_enrichment_operator", "operator_email"),
)

# ------------------------------------------------------------------
# Migration 055 — shared media-asset registry
# ------------------------------------------------------------------

media_assets = Table(
    "media_assets",
    metadata,
    Column("asset_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("source_project", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("r2_ref", Text, nullable=False),
    Column("mime", Text),
    Column("bytes", Integer),
    Column("sha256", Text),
    Column("entity_id", Text, ForeignKey("entities.entity_id")),
    Column("content_item_id", Text, ForeignKey("content_items.item_id")),
    Column("source_ref", Text),
    Column("caption", Text),
    Column("metadata_json", Text, nullable=False, server_default="{}"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("ix_media_assets_org_kind", "org_id", "kind"),
    Index("ix_media_assets_sha", "org_id", "sha256"),
)

# Named unique index (NOT a UniqueConstraint — must match the SQL migration's
# named CREATE UNIQUE INDEX for test_schema.py legacy-index parity).
Index("ux_media_assets_org_ref", media_assets.c.org_id, media_assets.c.r2_ref, unique=True)


# ------------------------------------------------------------------
# Migration 056 — operator reply-suggestion feature
# ------------------------------------------------------------------

operator_reply_quota = Table(
    "operator_reply_quota",
    metadata,
    Column("operator_handle", Text, primary_key=True),
    Column("day_utc", Text, primary_key=True),
    Column("org_id", Text),
    Column("count", Integer, nullable=False, server_default="0"),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
)

reply_suggestions = Table(
    "reply_suggestions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("operator_handle", Text, nullable=False),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("source_tweet_id", Text, nullable=False),
    Column("source_author", Text),
    Column("source_text", Text),
    Column("variants_json", Text, nullable=False, server_default="[]"),
    Column("model", Text),
    Column("cost_usd", Float),
    Column("generated_at", Text, nullable=False, server_default=func.now()),
    # mig 060 — media kind (image/video/none) the reply attached; backs the
    # prefer-image ranking + per-operator anti-spam image throttle.
    Column("clip_media_kind", Text),
    Index("ix_reply_suggestions_match", "operator_handle", "source_tweet_id"),
    Index("ix_reply_suggestions_org", "org_id", "generated_at"),
)

reply_outcomes = Table(
    "reply_outcomes",
    metadata,
    Column("id", Text, primary_key=True),
    Column("suggestion_id", Text, ForeignKey("reply_suggestions.id"), nullable=False),
    Column("posted_tweet_id", Text, nullable=False),
    Column("posted_at", Text),
    Column("chosen_variant_idx", Integer),
    Column("was_edited", Integer, nullable=False, server_default="0"),
    Column("engagement_json", Text, nullable=False, server_default="{}"),
    Column("recorded_at", Text, nullable=False, server_default=func.now()),
)

# Named unique index (NOT a UniqueConstraint — must match the SQL migration's
# named CREATE UNIQUE INDEX for test_schema.py legacy-index parity).
Index(
    "ux_reply_outcomes_match",
    reply_outcomes.c.suggestion_id,
    reply_outcomes.c.posted_tweet_id,
    unique=True,
)


# ------------------------------------------------------------------
# Migration 057 — SableRelay (relay_* table family)
# ------------------------------------------------------------------
# Mirrors 057_relay.sql. The .sql file carries the strftime ISO-8601-Z _at
# default + the CHECK constraints; test_schema.py parity compares only table
# names, column names, type affinity, nullability, and named indexes — so this
# uses the house server_default=func.now() (defaults are not compared) and the
# named partial indexes are reproduced exactly. relay_publication_jobs.state
# CHECK = the corrected section-3.1 set ('pending','claimed','retry','done','dead').

relay_clients = Table(
    "relay_clients",
    metadata,
    Column("org_id", Text, ForeignKey("orgs.org_id"), primary_key=True),
    Column("enabled", Integer, nullable=False, server_default="0"),
    Column("x_handle_override", Text),
    Column("polling_interval_seconds", Integer, nullable=False, server_default="300"),
    Column("last_polled_at", Text),
    Column("last_seen_x_id", Text),
    Column("last_error", Text),
    Column("config", Text, nullable=False, server_default="{}"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
)

relay_chats = Table(
    "relay_chats",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("platform", Text, nullable=False),
    Column("chat_id", Text, nullable=False),
    Column("title", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "platform IN ('telegram','discord')",
        name="ck_relay_chats_platform",
    ),
    Index("relay_chats_by_org", "org_id"),
)

# Named unique index (NOT a UniqueConstraint — must match the SQL migration's
# named CREATE UNIQUE INDEX for test_schema.py legacy-index parity).
Index("relay_chats_unique", relay_chats.c.platform, relay_chats.c.chat_id, unique=True)

relay_chat_bindings = Table(
    "relay_chat_bindings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("platform", Text, nullable=False),
    Column("chat_id", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="active"),
    Column("superseded_by_chat_id", Text),
    Column("last_seen_at", Text),
    Column("last_error", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "platform IN ('telegram','discord')",
        name="ck_relay_chat_bindings_platform",
    ),
    CheckConstraint(
        "role IN ('operator','shared','community','broadcast')",
        name="ck_relay_chat_bindings_role",
    ),
    CheckConstraint(
        "status IN ('active','migrated','kicked','disabled')",
        name="ck_relay_chat_bindings_status",
    ),
)

# Partial unique indexes (WHERE status='active') — named to match the migration.
Index(
    "relay_chat_bindings_unique_role",
    relay_chat_bindings.c.org_id,
    relay_chat_bindings.c.platform,
    relay_chat_bindings.c.role,
    unique=True,
    sqlite_where=relay_chat_bindings.c.status == "active",
    postgresql_where=relay_chat_bindings.c.status == "active",
)
Index(
    "relay_chat_bindings_unique_chat",
    relay_chat_bindings.c.platform,
    relay_chat_bindings.c.chat_id,
    unique=True,
    sqlite_where=relay_chat_bindings.c.status == "active",
    postgresql_where=relay_chat_bindings.c.status == "active",
)

relay_members = Table(
    "relay_members",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("display_name", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
)

relay_member_identities = Table(
    "relay_member_identities",
    metadata,
    Column("member_id", Integer, ForeignKey("relay_members.id"), nullable=False),
    Column("platform", Text, nullable=False),
    Column("external_user_id", Text, nullable=False),
    Column("handle", Text),
    Column("linked_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "platform IN ('telegram','x','discord')",
        name="ck_relay_member_identities_platform",
    ),
    PrimaryKeyConstraint("platform", "external_user_id"),
    Index("relay_member_identities_by_member", "member_id"),
)

relay_member_roles = Table(
    "relay_member_roles",
    metadata,
    Column("member_id", Integer, ForeignKey("relay_members.id"), nullable=False),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("role", Text, nullable=False),
    Column("granted_by", Integer, ForeignKey("relay_members.id")),
    Column("granted_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "role IN ('sable_operator','client_team','admin')",
        name="ck_relay_member_roles_role",
    ),
    PrimaryKeyConstraint("member_id", "org_id", "role"),
    Index("relay_member_roles_by_org_role", "org_id", "role"),
)

relay_member_preferences = Table(
    "relay_member_preferences",
    metadata,
    Column("member_id", Integer, ForeignKey("relay_members.id"), nullable=False),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("replies_optin", Integer, nullable=False, server_default="0"),
    Column("mute_until", Text),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("member_id", "org_id"),
    Index("relay_member_preferences_optin", "org_id", "replies_optin", "mute_until"),
)

relay_tweets = Table(
    "relay_tweets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("x_id", Text, nullable=False, unique=True),
    Column("x_author_id", Text),
    Column("x_author_handle", Text, nullable=False),
    Column("text", Text),
    Column("media_urls", Text, nullable=False, server_default="[]"),
    Column("is_reply", Integer, nullable=False, server_default="0"),
    Column("in_reply_to_x_id", Text),
    Column("conversation_x_id", Text),
    Column("fetched_at", Text, nullable=False, server_default=func.now()),
    Column("raw", Text),
    Index("relay_tweets_author", "x_author_id"),
)

relay_messages = Table(
    "relay_messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("chat_id", Integer, ForeignKey("relay_chats.id"), nullable=False),
    Column("member_id", Integer, ForeignKey("relay_members.id")),
    Column("platform", Text, nullable=False),
    Column("external_message_id", Text, nullable=False),
    Column("external_user_id", Text),
    Column("text", Text),
    Column("reply_to_external_message_id", Text),
    Column("received_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "platform IN ('telegram','discord')",
        name="ck_relay_messages_platform",
    ),
    Index("relay_messages_org_received", "org_id", "received_at"),
    Index("relay_messages_member", "member_id", "received_at"),
    Index("relay_messages_gc", "received_at"),
)

# Named unique index (NOT a UniqueConstraint — must match the SQL migration's
# named CREATE UNIQUE INDEX for test_schema.py legacy-index parity).
Index(
    "relay_messages_unique",
    relay_messages.c.platform,
    relay_messages.c.chat_id,
    relay_messages.c.external_message_id,
    unique=True,
)

relay_submissions = Table(
    "relay_submissions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("tweet_id", Integer, ForeignKey("relay_tweets.id"), nullable=False),
    Column("submitter_id", Integer, ForeignKey("relay_members.id"), nullable=False),
    Column("source_chat_id", Text, nullable=False),
    Column("source_message_id", Text, nullable=False),
    Column("control_message_id", Text),
    Column("source_role", Text, nullable=False),
    Column("note", Text),
    Column("status", Text, nullable=False),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("expires_at", Text, nullable=False),
    Column("resolved_at", Text),
    CheckConstraint(
        "source_role IN ('operator','shared')",
        name="ck_relay_submissions_source_role",
    ),
    CheckConstraint(
        "status IN ('pending','ready_to_publish','published','expired','rejected')",
        name="ck_relay_submissions_status",
    ),
    Index("relay_submissions_org_status", "org_id", "status", "created_at"),
    Index("relay_submissions_expires", "status", "expires_at"),
    Index("relay_submissions_control_lookup", "source_chat_id", "control_message_id"),
)

# Partial unique index (WHERE status IN ('pending','ready_to_publish')).
Index(
    "relay_submissions_one_pending_per_tweet",
    relay_submissions.c.org_id,
    relay_submissions.c.tweet_id,
    unique=True,
    sqlite_where=relay_submissions.c.status.in_(["pending", "ready_to_publish"]),
    postgresql_where=relay_submissions.c.status.in_(["pending", "ready_to_publish"]),
)

relay_submission_reactions = Table(
    "relay_submission_reactions",
    metadata,
    Column("submission_id", Integer, ForeignKey("relay_submissions.id"), nullable=False),
    Column("member_id", Integer, ForeignKey("relay_members.id"), nullable=False),
    Column("emoji", Text, nullable=False),
    Column("reacted_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("submission_id", "member_id", "emoji"),
    Index("relay_submission_reactions_by_emoji", "submission_id", "emoji"),
)

relay_publication_jobs = Table(
    "relay_publication_jobs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("submission_id", Integer, ForeignKey("relay_submissions.id")),
    Column("tweet_id", Integer, ForeignKey("relay_tweets.id"), nullable=False),
    Column("destination_platform", Text, nullable=False),
    Column("destination_chat_id", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("claimed_by", Text),
    Column("claimed_at", Text),
    Column("next_attempt_at", Text, nullable=False, server_default=func.now()),
    Column("last_error", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "destination_platform IN ('discord','telegram')",
        name="ck_relay_publication_jobs_destination_platform",
    ),
    CheckConstraint(
        "state IN ('pending','claimed','retry','done','dead')",
        name="ck_relay_publication_jobs_state",
    ),
    Index("relay_publication_jobs_due", "state", "next_attempt_at"),
)

# Partial unique dedupe index (WHERE state IN ('pending','claimed','done')).
Index(
    "relay_publication_jobs_dedupe",
    relay_publication_jobs.c.org_id,
    relay_publication_jobs.c.tweet_id,
    relay_publication_jobs.c.destination_platform,
    relay_publication_jobs.c.destination_chat_id,
    unique=True,
    sqlite_where=relay_publication_jobs.c.state.in_(["pending", "claimed", "done"]),
    postgresql_where=relay_publication_jobs.c.state.in_(["pending", "claimed", "done"]),
)

relay_publications = Table(
    "relay_publications",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("submission_id", Integer, ForeignKey("relay_submissions.id")),
    Column("tweet_id", Integer, ForeignKey("relay_tweets.id"), nullable=False),
    Column("destination_platform", Text, nullable=False),
    Column("destination_chat_id", Text, nullable=False),
    Column("destination_message_id", Text, nullable=False),
    Column("published_at", Text, nullable=False, server_default=func.now()),
    Index("relay_publications_by_tweet", "tweet_id"),
    Index(
        "relay_publications_by_message",
        "destination_platform", "destination_chat_id", "destination_message_id",
    ),
)

# Named unique index (NOT a UniqueConstraint — must match the SQL migration's
# named CREATE UNIQUE INDEX for test_schema.py legacy-index parity).
Index(
    "relay_publications_unique",
    relay_publications.c.org_id,
    relay_publications.c.tweet_id,
    relay_publications.c.destination_platform,
    relay_publications.c.destination_chat_id,
    unique=True,
)

relay_reply_opportunities = Table(
    "relay_reply_opportunities",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("tweet_id", Integer, ForeignKey("relay_tweets.id"), nullable=False),
    Column("flagger_id", Integer, ForeignKey("relay_members.id"), nullable=False),
    Column("origin", Text, nullable=False),
    Column("note", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "origin IN ('explicit_command','reaction','auto_mention')",
        name="ck_relay_reply_opportunities_origin",
    ),
    Index("relay_reply_opportunities_by_org", "org_id", "created_at"),
)

relay_reply_opportunity_targets = Table(
    "relay_reply_opportunity_targets",
    metadata,
    Column(
        "opportunity_id",
        Integer,
        ForeignKey("relay_reply_opportunities.id"),
        nullable=False,
    ),
    Column("member_id", Integer, ForeignKey("relay_members.id"), nullable=False),
    PrimaryKeyConstraint("opportunity_id", "member_id"),
)

relay_reply_notifications = Table(
    "relay_reply_notifications",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "opportunity_id",
        Integer,
        ForeignKey("relay_reply_opportunities.id"),
        nullable=False,
    ),
    Column("member_id", Integer, ForeignKey("relay_members.id"), nullable=False),
    Column("notified_at", Text, nullable=False, server_default=func.now()),
    Column("dismissed_at", Text),
    Column("replied_at", Text),
    Column("replied_tweet_id", Text),
    Index("relay_reply_notifications_inbox", "member_id", "dismissed_at"),
)

# Named unique index (NOT a UniqueConstraint — must match the SQL migration's
# named CREATE UNIQUE INDEX for test_schema.py legacy-index parity).
Index(
    "relay_reply_notifications_unique",
    relay_reply_notifications.c.opportunity_id,
    relay_reply_notifications.c.member_id,
    unique=True,
)

relay_processed_updates = Table(
    "relay_processed_updates",
    metadata,
    Column("platform", Text, nullable=False),
    Column("update_id", Text, nullable=False),
    Column("processed_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "platform IN ('telegram','discord')",
        name="ck_relay_processed_updates_platform",
    ),
    PrimaryKeyConstraint("platform", "update_id"),
    Index("relay_processed_updates_gc", "processed_at"),
)

# ------------------------------------------------------------------
# Migration 058 — SableAutoCM (autocm_* table family)
# ------------------------------------------------------------------
# Mirrors 058_autocm.sql. The .sql carries the strftime ISO-8601-Z _at default +
# the CHECK constraints; test_schema.py parity compares only table names, column
# names, type affinity, nullability, and named indexes — so this uses the house
# server_default=func.now() (defaults not compared) and the named indexes are
# reproduced exactly. DECISION D-2: autocm_kb_chunks.chunk_embedding is Text
# (JSON-encoded float vector; app-side cosine). The companion FTS5 virtual table
# autocm_kb_chunks_fts (+ its shadow tables) is a SQLite-only mechanism not
# representable in SA Core metadata — it is the documented schema-parity
# divergence tolerated by test_schema.py (mirrors the D-2 pgvector divergence
# note). autocm_clients.org_id FK -> orgs.org_id; autocm_drafts source FKs ->
# relay_messages.id / relay_chats.id (the 057 surface).

autocm_personas = Table(
    "autocm_personas",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("description", Text),
    Column("calm_prompt", Text),
    Column("reactive_prompt", Text),
    Column("calibration_set", Text, nullable=False, server_default="{}"),
    Column("config", Text, nullable=False, server_default="{}"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
)
Index("autocm_personas_name_unique", autocm_personas.c.name, unique=True)

autocm_clients = Table(
    "autocm_clients",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("persona_id", Integer, ForeignKey("autocm_personas.id")),
    Column("display_name", Text),
    Column("autonomy_state", Text, nullable=False, server_default="hitl"),
    Column("incident_active", Integer, nullable=False, server_default="0"),
    Column("surface_config", Text, nullable=False, server_default="{}"),
    Column("kb_config", Text, nullable=False, server_default="{}"),
    Column("enabled", Integer, nullable=False, server_default="0"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "autonomy_state IN ('hitl','partial','auto','paused')",
        name="ck_autocm_clients_autonomy_state",
    ),
    Index("autocm_clients_persona", "persona_id"),
)
Index("autocm_clients_org_unique", autocm_clients.c.org_id, unique=True)

autocm_kb_sources = Table(
    "autocm_kb_sources",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", Integer, ForeignKey("autocm_clients.id"), nullable=False),
    Column("source_type", Text, nullable=False),
    Column("source_url", Text),
    Column("refresh_cadence", Text),
    Column("authority_default", Float, nullable=False, server_default="0.5"),
    Column("fetch_config", Text, nullable=False, server_default="{}"),
    Column("status", Text, nullable=False, server_default="active"),
    Column("last_refreshed_at", Text),
    Column("last_changed_at", Text),
    Column("last_error", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "status IN ('active','stale','disabled')",
        name="ck_autocm_kb_sources_status",
    ),
    Index("autocm_kb_sources_by_client", "client_id", "source_type"),
    Index("autocm_kb_sources_refresh", "status", "last_refreshed_at"),
)

# DECISION D-2: chunk_embedding is Text (JSON-encoded float vector; app-side
# cosine). The FTS5 companion (autocm_kb_chunks_fts) is created by the .sql
# migration only — it is the documented SQLite-only divergence (no SA metadata
# representation; test_schema.py tolerates it).
autocm_kb_chunks = Table(
    "autocm_kb_chunks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", Integer, ForeignKey("autocm_kb_sources.id"), nullable=False),
    Column("client_id", Integer, ForeignKey("autocm_clients.id"), nullable=False),
    Column("chunk_text", Text, nullable=False),
    Column("chunk_embedding", Text),
    Column("chunk_metadata", Text, nullable=False, server_default="{}"),
    Column("chunk_authority", Float, nullable=False, server_default="0.5"),
    Column("content_hash", Text),
    Column("status", Text, nullable=False, server_default="active"),
    Column("indexed_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "status IN ('active','stale','wrong')",
        name="ck_autocm_kb_chunks_status",
    ),
    Index("autocm_kb_chunks_by_source", "source_id"),
    Index("autocm_kb_chunks_by_client_status", "client_id", "status"),
)

autocm_kb_constants = Table(
    "autocm_kb_constants",
    metadata,
    Column("client_id", Integer, ForeignKey("autocm_clients.id"), nullable=False),
    Column("key", Text, nullable=False),
    Column("value", Text, nullable=False),
    Column("description", Text),
    Column("updated_by", Text),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("client_id", "key"),
)

autocm_drafts = Table(
    "autocm_drafts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", Integer, ForeignKey("autocm_clients.id"), nullable=False),
    Column("source_message_id", Integer, ForeignKey("relay_messages.id")),
    Column("source_chat_id", Integer, ForeignKey("relay_chats.id")),
    Column("category", Text),
    Column("tier", Integer),
    Column("register", Text),
    Column("draft_text", Text),
    Column("confidence", Float),
    Column("cited_chunk_ids", Text, nullable=False, server_default="[]"),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("resolved_at", Text),
    CheckConstraint(
        "register IN ('calm','reactive')",
        name="ck_autocm_drafts_register",
    ),
    CheckConstraint(
        "status IN ('pending','auto_sent','hitl_pending','approved','rejected',"
        "'published','escalated','suppressed')",
        name="ck_autocm_drafts_status",
    ),
    Index("autocm_drafts_by_client_status", "client_id", "status", "created_at"),
    Index("autocm_drafts_by_category", "client_id", "category", "created_at"),
    Index("autocm_drafts_by_message", "source_message_id"),
)

autocm_reviews = Table(
    "autocm_reviews",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("draft_id", Integer, ForeignKey("autocm_drafts.id"), nullable=False),
    Column("client_id", Integer, ForeignKey("autocm_clients.id"), nullable=False),
    Column("reviewer", Text),
    Column("decision", Text, nullable=False),
    Column("edited_text", Text),
    Column("edit_diff_size", Float, nullable=False, server_default="0"),
    Column("is_clean_approval", Integer, nullable=False, server_default="0"),
    Column("note", Text),
    Column("reviewed_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "decision IN ('approve','edit','reject','punt_to_founder')",
        name="ck_autocm_reviews_decision",
    ),
    Index("autocm_reviews_by_draft", "draft_id"),
    Index("autocm_reviews_by_client", "client_id", "reviewed_at"),
)

autocm_category_state = Table(
    "autocm_category_state",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", Integer, ForeignKey("autocm_clients.id"), nullable=False),
    Column("category", Text, nullable=False),
    Column("state", Text, nullable=False, server_default="hitl"),
    Column("confidence_threshold", Float, nullable=False, server_default="0.8"),
    Column("sample_count", Integer, nullable=False, server_default="0"),
    Column("clean_approval_count", Integer, nullable=False, server_default="0"),
    Column("freeze_until", Text),
    Column("freeze_reason", Text),
    Column("frozen_by", Text),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "state IN ('hitl','auto')",
        name="ck_autocm_category_state_state",
    ),
    Index("autocm_category_state_frozen", "freeze_until"),
)
Index(
    "autocm_category_state_unique",
    autocm_category_state.c.client_id,
    autocm_category_state.c.category,
    unique=True,
)

autocm_escalations = Table(
    "autocm_escalations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", Integer, ForeignKey("autocm_clients.id"), nullable=False),
    Column("draft_id", Integer, ForeignKey("autocm_drafts.id")),
    Column("source_message_id", Integer, ForeignKey("relay_messages.id")),
    Column("reason", Text),
    Column("founder_status", Text, nullable=False, server_default="pending"),
    Column("oncall_status", Text, nullable=False, server_default="pending"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("resolved_at", Text),
    CheckConstraint(
        "founder_status IN ('pending','notified','acknowledged','resolved')",
        name="ck_autocm_escalations_founder_status",
    ),
    CheckConstraint(
        "oncall_status IN ('pending','notified','acknowledged','resolved')",
        name="ck_autocm_escalations_oncall_status",
    ),
    Index("autocm_escalations_by_client", "client_id", "created_at"),
    Index("autocm_escalations_open", "founder_status", "oncall_status"),
)

autocm_flagged_users = Table(
    "autocm_flagged_users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", Integer, ForeignKey("autocm_clients.id"), nullable=False),
    Column("member_id", Integer, ForeignKey("relay_members.id")),
    Column("external_user_id", Text),
    Column("reason", Text),
    Column("status", Text, nullable=False, server_default="silenced"),
    Column("flagged_at", Text, nullable=False, server_default=func.now()),
    Column("cleared_at", Text),
    Column("cleared_by", Text),
    CheckConstraint(
        "status IN ('silenced','cleared')",
        name="ck_autocm_flagged_users_status",
    ),
    Index("autocm_flagged_users_by_client", "client_id", "status"),
    Index("autocm_flagged_users_by_member", "member_id"),
)

autocm_adversarial_runs = Table(
    "autocm_adversarial_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", Integer, ForeignKey("autocm_clients.id"), nullable=False),
    Column("suite", Text),
    Column("total_cases", Integer, nullable=False, server_default="0"),
    Column("passed", Integer, nullable=False, server_default="0"),
    Column("failed", Integer, nullable=False, server_default="0"),
    Column("result", Text, nullable=False, server_default="{}"),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("ran_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "status IN ('pending','passed','failed','error')",
        name="ck_autocm_adversarial_runs_status",
    ),
    Index("autocm_adversarial_runs_by_client", "client_id", "ran_at"),
)

autocm_digest_interactions = Table(
    "autocm_digest_interactions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", Integer, ForeignKey("autocm_clients.id"), nullable=False),
    Column("digest_period", Text),
    Column("section", Text),
    Column("action", Text, nullable=False),
    Column("target_ref", Text),
    Column("payload", Text, nullable=False, server_default="{}"),
    Column("actor", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "action IN ('approve_for_kb','recognize','demote','compose','ignore','ask')",
        name="ck_autocm_digest_interactions_action",
    ),
    Index("autocm_digest_interactions_by_client", "client_id", "digest_period"),
    Index("autocm_digest_interactions_by_action", "client_id", "action"),
)

autocm_time_saved_baseline = Table(
    "autocm_time_saved_baseline",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("client_id", Integer, ForeignKey("autocm_clients.id"), nullable=False),
    Column("minutes_per_auto", Float, nullable=False, server_default="0"),
    Column("minutes_per_hitl", Float, nullable=False, server_default="0"),
    Column("engagement_start_at", Text),
    Column("calibrated_by", Text),
    Column("notes", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
)
Index(
    "autocm_time_saved_baseline_client_unique",
    autocm_time_saved_baseline.c.client_id,
    unique=True,
)


# ------------------------------------------------------------------
# Operator work-tracking (migration 059 — SW-TASKING Phase 1)
# ------------------------------------------------------------------

mod_slot_sessions = Table(
    "mod_slot_sessions",
    metadata,
    Column("session_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("operator_handle", Text, nullable=False),
    Column("started_at", Text, nullable=False, server_default=func.now()),
    Column("ended_at", Text),
    Column("chats_watched_json", Text, nullable=False, server_default="[]"),
    Column("note", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("ix_mod_slot_sessions_org", "org_id", "started_at"),
    Index("ix_mod_slot_sessions_operator", "operator_handle", "ended_at"),
)

operator_work_events = Table(
    "operator_work_events",
    metadata,
    Column("event_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("operator_handle", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("occurred_at", Text, nullable=False, server_default=func.now()),
    Column("ref_json", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("ix_operator_work_events_org", "org_id", "occurred_at"),
)
