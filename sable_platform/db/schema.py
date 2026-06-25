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

# Per-(operator, org, ISO-week) dollar budget for on-demand meme production (mig 078).
# Reserve-then-reconcile accumulator; cap default $5/operator/client/week (orgs.config_json
# override). Output bank (content_candidates) is org-shared; the budget here is per-operator.
operator_meme_budget = Table(
    "operator_meme_budget",
    metadata,
    Column("operator_handle", Text, primary_key=True),
    Column("org_id", Text, primary_key=True),
    Column("week_iso", Text, primary_key=True),
    Column("spend_usd", Float, nullable=False, server_default="0"),
    Column("runs", Integer, nullable=False, server_default="0"),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    # Accumulator invariants -- spend/runs are non-negative. DB backstop for the app-layer
    # negative-spend guard in meme_budget.reconcile_meme_spend (mirrors 078_*.sql + the Alembic rev).
    CheckConstraint("spend_usd >= 0", name="ck_operator_meme_budget_spend_nonneg"),
    CheckConstraint("runs >= 0", name="ck_operator_meme_budget_runs_nonneg"),
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
    # mig 062 — reply-opportunity feed learning join + the cheap LOCAL
    # depress-already-replied lookup. opportunity_id is INTEGER (matches the
    # relay_reply_opportunities PK). No SQLite FK (ADD COLUMN can't add one;
    # the .sql matches).
    Column("opportunity_id", Integer),
    Column("source_conversation_id", Text),
    # mig 063 — §10 anti-AI-tell humanizer signals persisted for the quality
    # dashboard / guardrail-refinement proposals (§10.4 / §6). tell_score is the
    # 0..1 weighted flag density; tell_flags_json is the {type,span,why} blob.
    # Both nullable (NULL for pre-063 / unlinted rows).
    Column("tell_score", Float),
    Column("tell_flags_json", Text),
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
    # Migration 066: the media asset (if any) that rode along with this posted
    # reply, so assisted-vs-organic lift can be sliced by attached media.
    Column("media_content_id", Text),
    # Migration 069: provenance — 'auto' (the scheduled persona-timeline detection
    # job) vs 'operator' (manual Mark-posted). NULL on rows written before mig 069.
    Column("detected_via", Text),
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
# Migration 061 — coordinated reply campaigns (the "flash mob")
# ------------------------------------------------------------------
reply_campaigns = Table(
    "reply_campaigns",
    metadata,
    Column("id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("target_tweet_id", Text, nullable=False),
    Column("target_url", Text),
    Column("target_author", Text),
    Column("objective", Text),
    Column("status", Text, nullable=False, server_default="active"),
    Column("created_by", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("won_at", Text),
    Column("closed_at", Text),
    Index("ix_reply_campaigns_org", "org_id", "status", "created_at"),
)

reply_campaign_assignments = Table(
    "reply_campaign_assignments",
    metadata,
    Column("id", Text, primary_key=True),
    Column("campaign_id", Text, ForeignKey("reply_campaigns.id"), nullable=False),
    Column("operator_handle", Text, nullable=False),
    Column("suggestion_id", Text),
    Column("posted_tweet_id", Text),
    Column("angle", Text),
    Column("status", Text, nullable=False, server_default="assigned"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("posted_at", Text),
    Index("ix_reply_campaign_assignments_campaign", "campaign_id"),
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
    # Migration 062 — read-through cache signals for the heuristic pre-rank.
    Column("engagement_json", Text),
    Column("lang", Text),
    Column("author_followers", Integer),
    # Migration 063 — P3 embedding-ranker cache (§8 P3): the candidate's cached
    # embedding vector + the provider/model that produced it (a model swap
    # invalidates correctly). Both nullable.
    Column("embedding_json", Text),
    Column("embedding_model", Text),
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
    # Migration 062 — reply-opportunity feed (purely additive, see 062.sql).
    Column("score", Float),
    Column("score_reason", Text),
    Column("suggested_angle", Text),
    Column("status", Text, nullable=False, server_default="active"),
    Column("expires_at", Text),
    Column("sweep_source", Text),
    CheckConstraint(
        "origin IN ('explicit_command','reaction','auto_mention')",
        name="ck_relay_reply_opportunities_origin",
    ),
    Index("relay_reply_opportunities_by_org", "org_id", "created_at"),
    # Migration 062 — non-unique feed/expiry indexes (NO UNIQUE(org_id,tweet_id),
    # dedup is application-level per plan §3.1).
    Index("ix_relay_opportunities_feed", "org_id", "status", "score"),
    Index("ix_relay_opportunities_expiry", "expires_at"),
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

# ------------------------------------------------------------------
# Migration 062 — reply-opportunity feed (new tables; mirrors 062.sql)
# ------------------------------------------------------------------
# Per-operator web-feed state (handle-keyed — distinct from the TG member-keyed
# relay_reply_notifications). dismiss/snooze personalizes the shared feed view.
relay_opportunity_operator_state = Table(
    "relay_opportunity_operator_state",
    metadata,
    Column(
        "opportunity_id",
        Integer,
        ForeignKey("relay_reply_opportunities.id"),
        nullable=False,
    ),
    Column("operator_handle", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("snooze_until", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("opportunity_id", "operator_handle"),
)

# The two thumbs (learning labels). suggestion_id NULL = thumb on the OPPORTUNITY
# (relevance / ranker). suggestion_id set = thumb on a SUGGESTION (gen quality).
relay_opportunity_feedback = Table(
    "relay_opportunity_feedback",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "opportunity_id",
        Integer,
        ForeignKey("relay_reply_opportunities.id"),
    ),  # mig 068: NULLABLE — freeform-draft variant thumbs have a suggestion_id but no opportunity
    Column("suggestion_id", Text, ForeignKey("reply_suggestions.id")),
    Column("rater_handle", Text, nullable=False),
    Column("rater_role", Text, nullable=False),
    Column("thumb", Integer, nullable=False),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("ix_relay_opportunity_feedback_opp", "opportunity_id"),
)

# Per-client curated query set (managed via the TG bot command). The daily cost
# cap is NOT here — it lives in relay_clients.config.polling.daily_cost_cap_usd
# (the existing get_daily_cost_cap resolver).
relay_sweep_config = Table(
    "relay_sweep_config",
    metadata,
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), primary_key=True),
    Column("mention_handles", Text, nullable=False, server_default="[]"),
    Column("topic_queries", Text, nullable=False, server_default="[]"),
    Column("from_set", Text, nullable=False, server_default="[]"),
    Column("operator_handles", Text, nullable=False, server_default="[]"),
    Column("enabled", Integer, nullable=False, server_default="0"),
    Column("expiry_hours", Integer, nullable=False, server_default="36"),
    Column("last_sweep_at", Text),
    Column("sweep_requested_at", Text),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
)

# Per-source since_id cursor (do NOT overload relay_clients.last_seen_x_id).
relay_sweep_cursor = Table(
    "relay_sweep_cursor",
    metadata,
    Column("org_id", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("query_hash", Text, nullable=False),
    Column("since_id", Text),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("org_id", "source", "query_hash"),
)

# Logged-in gating: SableWeb stamps this on each /ops/reply-assist load. The
# sweep only runs for orgs with a recent heartbeat.
relay_operator_heartbeat = Table(
    "relay_operator_heartbeat",
    metadata,
    Column("org_id", Text, nullable=False),
    Column("operator_handle", Text, nullable=False),
    Column("last_seen", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("org_id", "operator_handle"),
)

# ------------------------------------------------------------------
# Migration 064 — trending-story autopilot (new table; mirrors 064.sql)
# ------------------------------------------------------------------
# Stage A persists bursting-AND-relevant stories here, Stage B auto-monitors them
# via decaying relay_sweep_config.topic_queries, and Stage C reads this for the
# sable.tools "Trending Stories" digest. relevance/momentum/summary are
# INTERPRETIVE (rendered behind a caveat banner), never measured fact. Dedup is
# application-level (no UNIQUE constraint). No cost column, ever.
relay_trending_stories = Table(
    "relay_trending_stories",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("label", Text, nullable=False),
    Column("summary", Text),
    Column("relevance", Float),
    Column("momentum", Float),
    Column("member_tweet_ids_json", Text, nullable=False, server_default="[]"),
    Column("monitor_terms_json", Text, nullable=False, server_default="[]"),
    Column("status", Text, nullable=False, server_default="emerging"),
    Column("first_seen_at", Text, nullable=False, server_default=func.now()),
    Column("last_seen_at", Text, nullable=False, server_default=func.now()),
    Column("expires_at", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("ix_relay_trending_stories_feed", "org_id", "status"),
)

# Tweet Assist Compose -- topic-suggestion engine cache (mig 071). The weekly
# refresh job synthesizes ranked "suggested topics + angles" per org from
# deterministic signals via ONE batched Claude pass and caches the result here, so
# the compose UI reads topics at near-zero per-view cost. topics_json is INTERPRETIVE
# (LLM synthesis, rendered behind a caveat). NO cost column, ever (cost lives only in
# cost_events, tag relay_compose.topics). One current row per org -- the refresh does
# an app-level delete-then-insert (relay/db), so no UNIQUE constraint.
relay_topic_suggestions = Table(
    "relay_topic_suggestions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("topics_json", Text, nullable=False, server_default="[]"),
    Column("model", Text),
    Column("refreshed_at", Text, nullable=False, server_default=func.now()),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("ix_relay_topic_suggestions_org", "org_id", "refreshed_at"),
)

# Tweet Assist Compose -- topic-suggestion FEEDBACK LOOP (mig 072). An append-only log
# of suggested-topic chip picks (SableWeb writes on click). The weekly synthesis reads
# recent picks as a steering signal (favor themes operators act on). A pick is a USAGE
# SIGNAL, not measured fact. NO cost column, ever. No UNIQUE constraint (append-only;
# a repeat pick is itself signal).
relay_topic_picks = Table(
    "relay_topic_picks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("relay_clients.org_id"), nullable=False),
    Column("topic", Text, nullable=False),
    Column("register_band", Text),
    Column("operator_handle", Text),
    Column("picked_at", Text, nullable=False, server_default=func.now()),
    Index("ix_relay_topic_picks_org", "org_id", "picked_at"),
)

# Tweet Assist Compose -- the TWEETBANK (mig 074, P3 human-fed + P4 AI-suggested). A
# curated store of ready-to-post original tweets, per managed account + a shared per-org
# global pool (account_handle NULL). Humans submit -> 'approved'; the P4 AI suggester
# writes source='ai' status='pending'. CONTENT, not a cost surface -- NO cost column.
# The 'used' status is an advisory soft-claim (mark-used on Compose). FK -> orgs.
tweetbank_entries = Table(
    "tweetbank_entries",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("client_org", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("account_handle", Text),
    Column("text", Text, nullable=False),
    Column("register_band", Text),
    Column("topic_tags", Text, nullable=False, server_default="[]"),
    Column("author", Text),
    Column("source", Text, nullable=False, server_default="human"),
    Column("status", Text, nullable=False, server_default="approved"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("used_at", Text),
    Column("used_by", Text),
    CheckConstraint("source IN ('human', 'ai')", name="ck_tweetbank_source"),
    CheckConstraint("status IN ('approved', 'pending', 'used', 'rejected')", name="ck_tweetbank_status"),
    Index("ix_tweetbank_entries_org", "client_org", "status", "account_handle"),
)

# ------------------------------------------------------------------
# Migration 065 — tweet-quality corpus (new tables; mirrors 065.sql)
# ------------------------------------------------------------------
# A curated/stratified bank of CT accounts (relay_quality_accounts, keyed by
# handle) + the tweets sampled from them (relay_quality_tweets, keyed by X id) +
# a longitudinal engagement-decay log per tweet (relay_tweet_snapshots, repeated
# at target ages). band/kol_strength/archetype_json are INTERPRETIVE (carried
# from kol_candidates); snapshot metrics ARE measured. No cost column, ever.
relay_quality_accounts = Table(
    "relay_quality_accounts",
    metadata,
    Column("handle", Text, primary_key=True),
    Column("band", Text),
    Column("kol_strength", Float),
    Column("archetype_json", Text, nullable=False, server_default="[]"),
    Column("source", Text),
    Column("followers_snapshot", Integer),
    Column("active", Integer, nullable=False, server_default="1"),
    Column("added_at", Text, nullable=False, server_default=func.now()),
)

relay_quality_tweets = Table(
    "relay_quality_tweets",
    metadata,
    Column("tweet_x_id", Text, primary_key=True),
    Column("author_handle", Text),
    Column("posted_at", Text),
    Column("text", Text),
    Column("band", Text),
    Column("first_seen_at", Text, nullable=False, server_default=func.now()),
    Index("ix_relay_quality_tweets_posted", "posted_at"),
)

relay_tweet_snapshots = Table(
    "relay_tweet_snapshots",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tweet_x_id", Text, nullable=False),
    Column("target_age_hours", Integer, nullable=False),
    Column("taken_at", Text, nullable=False, server_default=func.now()),
    Column("age_hours", Float),
    Column("likes", Integer),
    Column("retweets", Integer),
    Column("replies", Integer),
    Column("quotes", Integer),
    Column("bookmarks", Integer),
    Column("views", Integer),
    Column("author_followers", Integer),
    Column("status", Text, nullable=False, server_default="ok"),
    Index("ix_relay_tweet_snapshots_tweet", "tweet_x_id"),
)

# ------------------------------------------------------------------
# Migration 066 — media recommendation center (new tables; mirrors 066.sql)
# ------------------------------------------------------------------
# media_rec_events logs each media slate offered to an operator for a reply (the
# source of truth). media_quality is the forward-only Elo rollup recomputed from
# that choice log (elo/n_offered/n_chosen DERIVED, not measured-external).
# media_embeddings caches a per-asset semantic vector (embedding_json + producing
# model). All three are keyed/scoped by org_id. No cost column, ever. (The
# reply_outcomes.media_content_id ADD COLUMN lives on the reply_outcomes Table
# above.)
media_rec_events = Table(
    "media_rec_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, nullable=False),
    Column("operator_handle", Text),
    Column("tweet_ref", Text),
    Column("slate_json", Text, nullable=False, server_default="[]"),
    Column("chosen_content_id", Text),
    Column("applied", Integer, nullable=False, server_default="0"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("ix_media_rec_events_unapplied", "org_id", "applied"),
)

media_quality = Table(
    "media_quality",
    metadata,
    Column("org_id", Text, nullable=False),
    Column("content_id", Text, nullable=False),
    Column("elo", Float, nullable=False, server_default="1500"),
    Column("n_offered", Integer, nullable=False, server_default="0"),
    Column("n_chosen", Integer, nullable=False, server_default="0"),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("org_id", "content_id"),
)

# Content-preference Elo (mig 080), parallel to media_quality. Dual-grain: subject_kind='candidate'
# (live within-deck tie-break, ephemeral) | 'feature' (durable engine signal at kind/template/format
# grain). Folded forward-only from content_deck_decisions duels (pair_loser_id) by content_quality.py.
content_quality = Table(
    "content_quality",
    metadata,
    Column("org_id", Text, nullable=False),
    Column("subject_kind", Text, nullable=False),
    Column("subject_key", Text, nullable=False),
    Column("elo", Float, nullable=False, server_default="1500"),
    Column("n_offered", Integer, nullable=False, server_default="0"),
    Column("n_chosen", Integer, nullable=False, server_default="0"),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("org_id", "subject_kind", "subject_key"),
    CheckConstraint(
        "subject_kind IN ('candidate', 'feature')", name="ck_content_quality_subject_kind"
    ),
)

media_embeddings = Table(
    "media_embeddings",
    metadata,
    Column("org_id", Text, nullable=False),
    Column("content_id", Text, nullable=False),
    Column("embedding_json", Text),
    Column("embedding_model", Text),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("org_id", "content_id"),
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

# ------------------------------------------------------------------
# Migration 067 — community audit (community_audit_* family; mirrors 067.sql)
# Parents before children: guilds (FK -> orgs) -> runs -> findings/checks/snapshot.
# ------------------------------------------------------------------
community_audit_guilds = Table(
    "community_audit_guilds",
    metadata,
    Column("guild_id", Text, primary_key=True),
    Column("org_id", Text, ForeignKey("orgs.org_id")),
    Column("invited_by", Text),
    Column("plan_tier", Text, nullable=False, server_default="free"),
    Column("status", Text, nullable=False, server_default="active"),
    Column("consent_at", Text),
    Column("joined_at", Text, nullable=False, server_default=func.now()),
    Column("last_audit_at", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
)

community_audit_runs = Table(
    "community_audit_runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("guild_id", Text, ForeignKey("community_audit_guilds.guild_id"), nullable=False),
    Column("tier", Text, nullable=False, server_default="free"),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="running"),
    Column("messages_analyzed", Integer, nullable=False, server_default="0"),
    Column("channels_active", Integer, nullable=False, server_default="0"),
    Column("channels_dead", Integer, nullable=False, server_default="0"),
    Column("span_start", Text),
    Column("overall_grade", Text),
    Column("category_grades_json", Text, nullable=False, server_default="{}"),
    Column("started_at", Text, nullable=False, server_default=func.now()),
    Column("finished_at", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint("kind IN ('metadata','deep')", name="ck_community_audit_runs_kind"),
    CheckConstraint(
        "status IN ('running','ok','aborted','partial')",
        name="ck_community_audit_runs_status",
    ),
    Index("community_audit_runs_by_guild", "guild_id", "started_at"),
)

community_audit_findings = Table(
    "community_audit_findings",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, ForeignKey("community_audit_runs.id"), nullable=False),
    Column("category", Text, nullable=False),
    Column("severity", Text, nullable=False, server_default="info"),
    Column("type", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("plain_detail", Text),
    Column("message_ref", Text),
    Column("confidence", Float),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("community_audit_findings_by_run", "run_id", "category"),
)

community_audit_security_checks = Table(
    "community_audit_security_checks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, ForeignKey("community_audit_runs.id"), nullable=False),
    Column("check_key", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("detail", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "status IN ('pass','warn','fail')",
        name="ck_community_audit_security_checks_status",
    ),
    Index("community_audit_security_checks_by_run", "run_id"),
)

community_audit_settings_snapshot = Table(
    "community_audit_settings_snapshot",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Integer, ForeignKey("community_audit_runs.id"), nullable=False),
    Column("boost_level", Integer, nullable=False, server_default="0"),
    Column("boost_count", Integer, nullable=False, server_default="0"),
    Column("custom_emoji_count", Integer, nullable=False, server_default="0"),
    Column("soundboard_count", Integer, nullable=False, server_default="0"),
    Column("vanity_url", Text),
    Column("has_banner", Integer, nullable=False, server_default="0"),
    Column("has_icon", Integer, nullable=False, server_default="0"),
    Column("verification_level", Text),
    Column("description", Text),
    Column("raw_json", Text, nullable=False, server_default="{}"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index(
        "community_audit_settings_snapshot_by_run",
        "run_id",
        unique=True,
    ),
)

community_audit_reaction_ledger = Table(
    "community_audit_reaction_ledger",
    metadata,
    Column("guild_id", Text, ForeignKey("community_audit_guilds.guild_id"), nullable=False),
    Column("post_id", Text, nullable=False),
    Column("reactor_id", Text, nullable=False),
    Column("emoji", Text, nullable=False),
    Column("author_id", Text, nullable=False),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("guild_id", "post_id", "reactor_id", "emoji"),
    Index("community_audit_reaction_ledger_by_author", "guild_id", "author_id"),
)

community_audit_member_scores = Table(
    "community_audit_member_scores",
    metadata,
    Column("guild_id", Text, ForeignKey("community_audit_guilds.guild_id"), nullable=False),
    Column("member_id", Text, nullable=False),
    Column("contribution_score", Float, nullable=False, server_default="0"),
    Column("components_json", Text, nullable=False, server_default="{}"),
    Column("last_active_at", Text),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("guild_id", "member_id"),
    Index("community_audit_member_scores_rank", "guild_id", "contribution_score"),
)

community_audit_member_activity = Table(
    "community_audit_member_activity",
    metadata,
    Column("guild_id", Text, ForeignKey("community_audit_guilds.guild_id"), nullable=False),
    Column("member_id", Text, nullable=False),
    Column("period", Text, nullable=False),
    Column("message_count", Integer, nullable=False, server_default="0"),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("guild_id", "member_id", "period"),
)

community_audit_rate_limits = Table(
    "community_audit_rate_limits",
    metadata,
    Column("scope", Text, nullable=False),
    Column("key", Text, nullable=False),
    Column("window_start", Text, nullable=False),
    Column("count", Integer, nullable=False, server_default="0"),
    Column("ai_usd", Float, nullable=False, server_default="0"),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "scope IN ('guild','inviter','global')",
        name="ck_community_audit_rate_limits_scope",
    ),
    PrimaryKeyConstraint("scope", "key", "window_start"),
)

community_audit_benchmark = Table(
    "community_audit_benchmark",
    metadata,
    Column("category", Text, nullable=False),
    Column("metric_key", Text, nullable=False),
    Column("distribution_json", Text, nullable=False, server_default="{}"),
    Column("sample_size", Integer, nullable=False, server_default="0"),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("category", "metric_key"),
)

community_audit_identity_links = Table(
    "community_audit_identity_links",
    metadata,
    Column("guild_id", Text, ForeignKey("community_audit_guilds.guild_id"), nullable=False),
    Column("discord_member_id", Text, nullable=False),
    Column("twitter_handle", Text, nullable=False),
    Column("confidence", Float),
    Column("source", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("guild_id", "discord_member_id"),
)

# Migration 070 — community-audit lead capture (non-privileged marketing list).
community_audit_leads = Table(
    "community_audit_leads",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", Text, nullable=False),
    Column("guild_id", Text),
    Column("source", Text, nullable=False, server_default="audit_page"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("community_audit_leads_by_email", "email"),
)

# --- Migration 073: client & operator onboarding (intake SSOT + entitlements) -------
# OPS-ONLY tables (client PII + commercial state) -- never cross the SableWeb /client
# wall. See docs/CLIENT_ONBOARDING_PLAN.md.
client_intake = Table(
    "client_intake",
    metadata,
    Column("org_id", Text, ForeignKey("orgs.org_id"), primary_key=True),
    Column("manifest_status", Text, nullable=False, server_default="draft"),
    Column("primary_contact_name", Text),
    Column("primary_contact_email", Text),
    Column("primary_contact_telegram", Text),
    Column("website_url", Text),
    Column("notes", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "manifest_status IN ('draft','ready','applied')",
        name="ck_client_intake_status",
    ),
)

client_accounts = Table(
    "client_accounts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("platform", Text, nullable=False),
    Column("handle", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("controlled", Integer, nullable=False, server_default="0"),
    Column("display_name", Text),
    Column("bio", Text),
    Column("notes", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    UniqueConstraint("org_id", "platform", "handle", name="uq_client_accounts_handle"),
    Index("client_accounts_by_org", "org_id"),
)

client_docs = Table(
    "client_docs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("label", Text, nullable=False),
    Column("location", Text, nullable=False),
    Column("notes", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Index("client_docs_by_org", "org_id"),
)

org_entitlements = Table(
    "org_entitlements",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("service_key", Text, nullable=False),
    Column("tier", Text),
    Column("status", Text, nullable=False, server_default="active"),
    Column("started_at", Text),
    Column("ended_at", Text),
    Column("config_json", Text, nullable=False, server_default="{}"),
    Column("notes", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "status IN ('trial','active','paused','ended')",
        name="ck_org_entitlements_status",
    ),
    UniqueConstraint("org_id", "service_key", name="uq_org_entitlements_service"),
    Index("org_entitlements_by_org", "org_id"),
)

# --- Migration 075: DB-backed SableWeb allowlist (ONBOARDING_PHASE2_PLAN.md P1) ----
# AUTH table -- OPS-ONLY, never on /client. `email` is the lowercased PK.
allowlist_entries = Table(
    "allowlist_entries",
    metadata,
    Column("email", Text, primary_key=True),
    Column("role", Text, nullable=False),
    Column("operator_id", Text),
    Column("org", Text),
    Column("assigned_orgs", Text),
    Column("enabled", Integer, nullable=False, server_default="1"),
    Column("notes", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "role IN ('admin','operator','client','client_ops')",
        name="ck_allowlist_entries_role",
    ),
    CheckConstraint("email = lower(email)", name="ck_allowlist_entries_email_lower"),
)

# ------------------------------------------------------------------
# Migration 076 — Content Deck candidate substrate (mirrors 076_content_deck.sql)
# ------------------------------------------------------------------
# The durable home for the ambient generate->swipe->schedule loop. org_id is the scope wall
# (FK -> orgs, NOT relay_clients -- producers work for any org). content_deck_decisions is a
# NO-FK learning-join on candidate_id so the Elo/keep signal survives a candidate soft-expiry/
# purge; content_deck_operator_state cascades on a candidate hard-delete. No cost column, ever.
content_candidates = Table(
    "content_candidates",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="pending"),
    Column("target_handle", Text),
    Column("payload_json", Text, nullable=False),
    Column("media_content_id", Text),
    Column("source", Text, nullable=False),
    Column("score", Float),
    Column("score_reason", Text),
    Column("tell_score", Float),
    Column("dedupe_key", Text),
    Column("expires_at", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "kind IN ('clip','tweet','thread','quote_card','meme','copypasta')",
        name="ck_content_candidates_kind",
    ),
    CheckConstraint(
        "status IN ('pending','kept','scheduled','posted','rejected','expired')",
        name="ck_content_candidates_status",
    ),
    Index("content_candidates_by_org_status", "org_id", "status", "score"),
    Index("content_candidates_by_dedupe", "org_id", "dedupe_key"),
)

content_deck_decisions = Table(
    "content_deck_decisions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("candidate_id", Integer, nullable=False),  # no-FK learning-join (survives candidate purge)
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("actor", Text, nullable=False),
    Column("actor_kind", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("surface", Text, nullable=False),
    Column("pair_loser_id", Integer),
    # mig 080: forward-only fold flag for the content-quality Elo (parallel to media_rec_events.applied).
    Column("applied", Integer, nullable=False, server_default="0"),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "actor_kind IN ('operator','community')", name="ck_content_deck_decisions_actor_kind"
    ),
    CheckConstraint(
        "decision IN ('keep','reject','skip','schedule','post')",
        name="ck_content_deck_decisions_decision",
    ),
    CheckConstraint("surface IN ('web','discord')", name="ck_content_deck_decisions_surface"),
    Index("content_deck_decisions_by_candidate", "org_id", "candidate_id"),
    Index("content_deck_decisions_by_actor", "org_id", "actor", "created_at"),
    Index("ix_content_deck_decisions_unapplied", "org_id", "applied"),  # mig 080
)

content_deck_operator_state = Table(
    "content_deck_operator_state",
    metadata,
    Column(
        "candidate_id",
        Integer,
        ForeignKey("content_candidates.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("operator_handle", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("snooze_until", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    PrimaryKeyConstraint("candidate_id", "operator_handle"),
    CheckConstraint(
        "state IN ('dismissed','snoozed')", name="ck_content_deck_operator_state_state"
    ),
)

# Migration 077: Content Deck Phase 4 release substrate. A kept candidate is scheduled into a
# publish job; a claim-due worker flips it to 'due' at publish_at for OPERATOR hand-off (no
# auto-send v1). release_state holds the worker lifecycle (the candidate status CHECK is not
# overloaded). FK -> content_candidates ON DELETE CASCADE + orgs. No cost column.
content_publish_jobs = Table(
    "content_publish_jobs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "candidate_id",
        Integer,
        ForeignKey("content_candidates.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("org_id", Text, ForeignKey("orgs.org_id"), nullable=False),
    Column("target_handle", Text, nullable=False),
    Column("release_state", Text, nullable=False, server_default="scheduled"),
    Column("publish_at", Text, nullable=False),
    Column("next_attempt_at", Text),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("claimed_at", Text),
    Column("handed_off_at", Text),
    Column("posted_ref", Text),
    Column("created_at", Text, nullable=False, server_default=func.now()),
    Column("updated_at", Text, nullable=False, server_default=func.now()),
    CheckConstraint(
        "release_state IN ('scheduled','due','claimed','handed_off','posted','canceled')",
        name="ck_content_publish_jobs_release_state",
    ),
    # publish_at STRICT-UTC FORMAT backstop (Codex Tier-2). The claim-due worker compares publish_at
    # LEXICALLY, so a non-canonical value (offset/naive/space/compact/fractional) would release early
    # or never release. The Slopper route + schedule_candidate() validate it, but the DB CHECK stops a
    # direct writer/backfill from storing a malformed instant. SHAPE only (digit-classes) -- calendar
    # validity stays in the accessor's strptime (a GLOB/regex cannot range-check month/day). Dialect-
    # split so create_all() stays valid on BOTH backends: SQLite GLOB, Postgres POSIX-regex (~).
    CheckConstraint(
        "publish_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'",
        name="ck_content_publish_jobs_publish_at_utc",
    ).ddl_if(dialect="sqlite"),
    CheckConstraint(
        r"publish_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'",
        name="ck_content_publish_jobs_publish_at_utc",
    ).ddl_if(dialect="postgresql"),
    Index("content_publish_jobs_by_org_state", "org_id", "release_state", "publish_at"),
    Index("content_publish_jobs_due", "release_state", "publish_at"),
    Index("content_publish_jobs_by_candidate", "candidate_id"),
)

# Migration 079: single-use store for deck/produce authorization assertions (Codex Tier-1 replay
# defense). Slopper consumes the SableWeb-signed assertion SIGNATURE (HMAC hex) exactly once
# (PRIMARY KEY(sig)) BEFORE any budget reserve / state change, so a captured-but-valid assertion
# cannot be replayed within its TTL (even with tampered unsigned request fields). No FKs, no cost
# column. exp (unix seconds) is stored only so expired rows can be GC'd. See sable/serve/deck_authz.py.
deck_consumed_assertions = Table(
    "deck_consumed_assertions",
    metadata,
    Column("sig", Text, primary_key=True, nullable=False),
    Column("action", Text, nullable=False),
    Column("org_id", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("exp", Integer, nullable=False),
    Column("consumed_at", Text, nullable=False, server_default=func.now()),
    Index("deck_consumed_assertions_by_exp", "exp"),
)
