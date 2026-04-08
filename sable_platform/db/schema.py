"""SQLAlchemy Core table definitions for sable.db.

This module is the single source of truth for the platform schema.  Every
table defined here mirrors the cumulative result of migrations 001–030.

Usage::

    from sable_platform.db.schema import metadata
    metadata.create_all(engine)          # create all tables (idempotent)
"""
from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
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
    Index("idx_jobs_org", "org_id"),
    Index("idx_jobs_status", "status"),
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
    Index("idx_steps_job", "job_id"),
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
