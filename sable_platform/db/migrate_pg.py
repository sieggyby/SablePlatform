"""SQLite -> Postgres data migration for sable.db.

Reads all rows from a SQLite sable.db, creates the Postgres schema via
Alembic, copies data in FK-safe order, resets sequences, and validates
row counts.

Usage (via CLI):
    sable-platform migrate to-postgres --target-url postgresql://...
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, text

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE = 1000

# All 40 tables in FK-safe insertion order (parents before children).
# Derived from sable_platform/db/schema.py ForeignKey declarations.
TABLE_LOAD_ORDER: list[str] = [
    # Tier 0 — no FKs
    "schema_version",
    "platform_meta",
    "orgs",
    # Tier 1 — FK -> orgs, then cascading entity/job/workflow deps
    "entities",
    "jobs",
    "workflow_runs",
    "content_items",         # FK -> orgs, entities
    "actions",               # FK -> orgs, entities, content_items
    "outcomes",              # FK -> orgs, entities, actions
    "alerts",                # FK -> orgs, entities, actions, workflow_runs
    "diagnostic_runs",
    "diagnostic_deltas",
    "sync_runs",
    "cost_events",           # FK -> orgs, jobs
    "artifacts",             # FK -> orgs, jobs
    "media_assets",          # FK -> orgs, entities, content_items (mig 055)
    "operator_reply_quota",  # no FKs (mig 056) - composite TEXT PK (operator_handle, day_utc)
    "reply_suggestions",     # FK -> orgs (mig 056)
    "reply_outcomes",        # FK -> reply_suggestions (mig 056) - must follow it
    "reply_campaigns",       # FK -> orgs (mig 061) - TEXT PK, no sequence
    "reply_campaign_assignments",  # FK -> reply_campaigns (mig 061) - must follow it
    "mod_slot_sessions",     # FK -> orgs (mig 059) - TEXT PK, no sequence
    "operator_work_events",  # FK -> orgs (mig 059) - TEXT PK, no sequence
    "operator_meme_budget",  # no FKs (mig 078) - composite TEXT PK (operator_handle, org_id, week_iso)
    "alert_configs",
    "entity_interactions",
    "entity_decay_scores",
    "entity_centrality_scores",
    "entity_watchlist",
    "watchlist_snapshots",
    "audit_log",
    "webhook_subscriptions",
    "prospect_scores",
    "playbook_targets",
    "playbook_outcomes",
    "metric_snapshots",
    "discord_pulse_runs",
    # SableKOL bank (mig 032) — kol_candidates has no FKs; project_profiles_external
    # has no FKs; kol_handle_resolution_conflicts FK -> kol_candidates.
    "kol_candidates",
    "project_profiles_external",
    # SableKOL follow-graph (mig 037) — kol_extract_runs has no FKs;
    # kol_follow_edges FK -> kol_extract_runs.
    "kol_extract_runs",
    # Tier 2 — FK -> entities
    "entity_handles",
    "entity_tags",
    "entity_notes",
    "merge_candidates",      # FK -> entities (both a and b)
    "entity_tag_history",
    # Tier 3 — FK -> merge_candidates
    "merge_events",
    # Tier 4 — FK -> jobs, workflow_runs, kol_candidates
    "job_steps",
    "workflow_steps",
    "workflow_events",
    "kol_handle_resolution_conflicts",  # FK -> kol_candidates
    "kol_follow_edges",  # FK -> kol_extract_runs
    "kol_operator_relationships",  # no FKs (client_id + handle_normalized are loose)
    "kol_create_audit",  # FK -> jobs (mig 040)
    "kol_enrichment",  # FK -> kol_candidates (mig 041)
    "discord_streak_events",  # no FKs (mig 043)
    "api_tokens",  # no FKs (mig 044)
    "discord_guild_config",  # no FKs (mig 045) - PK is TEXT guild_id, no sequence
    "discord_burn_optins",  # no FKs (mig 046) - composite TEXT PK (guild_id, user_id)
    "discord_burn_random_log",  # no FKs (mig 046) - Integer autoincrement PK, see SEQUENCE_TABLES
    # Migration 047: /roast V2 + personalization. discord_user_vibes FKs ->
    # discord_user_observations(id), so observations MUST precede vibes.
    # discord_message_observations is the raw source for the rollup; no SQL FK
    # but ordered first so a future audit knows the dependency direction.
    "discord_burn_blocklist",  # no FKs (mig 047) - Integer autoincrement PK
    "discord_peer_roast_tokens",  # no FKs (mig 047) - Integer autoincrement PK
    "discord_peer_roast_flags",  # no FKs (mig 047) - Integer autoincrement PK
    "discord_message_observations",  # no FKs (mig 047) - Integer autoincrement PK
    "discord_user_observations",  # no FKs (mig 047) - Integer autoincrement PK
    "discord_user_vibes",  # FK -> discord_user_observations.id (mig 047)
    # Migration 048: airlock — invite snapshot + team-inviter allowlist + per-join admit ledger.
    # No FKs across the 3 tables; ordering arbitrary but kept stable for restore-reproducibility.
    "discord_invite_snapshot",  # no FKs (mig 048) - Integer autoincrement PK
    "discord_team_inviters",  # no FKs (mig 048) - Integer autoincrement PK
    "discord_member_admit",  # no FKs (mig 048) - Integer autoincrement PK
    # Migration 050-051: Scored Mode V2 Pass B. discord_fitcheck_scores +
    # discord_scoring_config. No FKs across either; ordering arbitrary.
    "discord_fitcheck_scores",  # no FKs (mig 050) - Integer autoincrement PK
    "discord_scoring_config",  # no FKs (mig 051) - Integer autoincrement PK
    # Migration 052: Scored Mode V2 Pass C. Per-emoji milestone crossings.
    # No FKs (intentional — score row may be invalidated/deleted; milestone
    # rows are independent audit-adjacent state).
    "discord_fitcheck_emoji_milestones",  # no FKs (mig 052) - Integer autoincrement PK
    # Migration 054: state-pin surface. One row per (guild, characteristic)
    # tracking the currently-pinned "stitzy state" message id in the
    # per-guild ops channel. No FKs.
    "discord_state_pins",  # no FKs (mig 054) - Integer autoincrement PK
    # Migration 057: SableRelay (relay_* family). FK-safe order (children after
    # parents). relay_clients.org_id FK -> orgs; everything else roots off
    # relay_clients / relay_members / relay_tweets / relay_chats /
    # relay_submissions / relay_reply_opportunities.
    "relay_clients",                     # FK -> orgs (TEXT PK org_id, no sequence)
    "relay_chats",                       # FK -> relay_clients (Integer autoincrement PK)
    "relay_chat_bindings",               # FK -> relay_clients (Integer autoincrement PK)
    "relay_members",                     # no FKs (Integer autoincrement PK)
    "relay_member_identities",           # FK -> relay_members (composite TEXT PK)
    "relay_member_roles",                # FK -> relay_members, relay_clients (composite PK)
    "relay_member_preferences",          # FK -> relay_members, relay_clients (composite PK)
    "relay_tweets",                      # no FKs (Integer autoincrement PK)
    "relay_messages",                    # FK -> relay_clients, relay_chats, relay_members (Integer autoincrement PK)
    "relay_submissions",                 # FK -> relay_clients, relay_tweets, relay_members (Integer autoincrement PK)
    "relay_submission_reactions",        # FK -> relay_submissions, relay_members (composite PK)
    "relay_publication_jobs",            # FK -> relay_clients, relay_submissions, relay_tweets (Integer autoincrement PK)
    "relay_publications",                # FK -> relay_clients, relay_submissions, relay_tweets (Integer autoincrement PK)
    "relay_reply_opportunities",         # FK -> relay_clients, relay_tweets, relay_members (Integer autoincrement PK)
    "relay_reply_opportunity_targets",   # FK -> relay_reply_opportunities, relay_members (composite PK)
    "relay_reply_notifications",         # FK -> relay_reply_opportunities, relay_members (Integer autoincrement PK)
    "relay_processed_updates",           # no FKs (composite TEXT PK, no sequence)
    # Migration 062: reply-opportunity feed. FK-safe order — all parents
    # (relay_reply_opportunities, reply_suggestions, relay_clients) precede these.
    "relay_opportunity_operator_state",  # FK -> relay_reply_opportunities (composite PK, no sequence)
    "relay_opportunity_feedback",        # FK -> relay_reply_opportunities, reply_suggestions (Integer autoincrement PK)
    "relay_sweep_config",                # FK -> relay_clients (TEXT PK org_id, no sequence)
    "relay_sweep_cursor",                # no FKs (composite PK, no sequence)
    "relay_operator_heartbeat",          # no FKs (composite PK, no sequence)
    # Migration 064: trending-story autopilot. FK -> relay_clients (the parent
    # precedes this block). Integer autoincrement PK -> also in SEQUENCE_TABLES.
    "relay_trending_stories",            # FK -> relay_clients (Integer autoincrement PK)
    # Migration 065: tweet-quality corpus. 3 new tables with NO FKs to each
    # other (or anything) -- any order among them is fine. Only
    # relay_tweet_snapshots has an Integer autoincrement PK (-> SEQUENCE_TABLES)
    # the other two are TEXT-PK (handle / tweet_x_id).
    "relay_quality_accounts",            # no FKs (TEXT PK handle, no sequence)
    "relay_quality_tweets",              # no FKs (TEXT PK tweet_x_id, no sequence)
    "relay_search_windows",              # no FKs (mig 082 closed-window cache) - Integer autoincrement PK, see SEQUENCE_TABLES
    "relay_search_windows",
    "relay_tweet_snapshots",             # no FKs (Integer autoincrement PK)
    # Migration 066: media recommendation center. 3 new tables with NO FKs to
    # each other (or anything) -- any order among them is fine. Only
    # media_rec_events has an Integer autoincrement PK (-> SEQUENCE_TABLES); the
    # other two are composite TEXT-PK (org_id, content_id). The reply_outcomes
    # ADD COLUMN media_content_id rides along automatically (the copy enumerates
    # columns dynamically via SELECT * + list(rows[0].keys())).
    "media_rec_events",                  # no FKs (Integer autoincrement PK)
    "media_quality",                     # no FKs (composite TEXT PK, no sequence)
    "media_embeddings",                  # no FKs (composite TEXT PK, no sequence)
    # Migration 063: reply-learning. Pure ADD COLUMN on reply_suggestions
    # (tell_score/tell_flags_json) + relay_tweets (embedding_json/embedding_model)
    # -- NO new tables, so no TABLE_LOAD_ORDER/SEQUENCE_TABLES entries. The copy
    # enumerates columns dynamically (SELECT * + list(rows[0].keys())), so the new
    # columns ride along automatically.
    # Migration 058: SableAutoCM (autocm_* family). FK-safe order (children after
    # parents). autocm_clients.org_id FK -> orgs; autocm_drafts source FKs ->
    # relay_messages / relay_chats (the 057 surface); everything else roots off
    # autocm_clients / autocm_personas / autocm_kb_sources / autocm_drafts.
    "autocm_personas",                   # no FKs (Integer autoincrement PK)
    "autocm_clients",                    # FK -> orgs, autocm_personas (Integer autoincrement PK)
    "autocm_kb_sources",                 # FK -> autocm_clients (Integer autoincrement PK)
    "autocm_kb_chunks",                  # FK -> autocm_kb_sources, autocm_clients (Integer autoincrement PK)
    "autocm_kb_constants",               # FK -> autocm_clients (composite TEXT PK, no sequence)
    "autocm_drafts",                     # FK -> autocm_clients, relay_messages, relay_chats (Integer autoincrement PK)
    "autocm_reviews",                    # FK -> autocm_drafts, autocm_clients (Integer autoincrement PK)
    "autocm_category_state",             # FK -> autocm_clients (Integer autoincrement PK)
    "autocm_escalations",                # FK -> autocm_clients, autocm_drafts, relay_messages (Integer autoincrement PK)
    "autocm_flagged_users",              # FK -> autocm_clients, relay_members (Integer autoincrement PK)
    "autocm_adversarial_runs",           # FK -> autocm_clients (Integer autoincrement PK)
    "autocm_digest_interactions",        # FK -> autocm_clients (Integer autoincrement PK)
    "autocm_time_saved_baseline",        # FK -> autocm_clients (Integer autoincrement PK)
    # Migration 067: community audit (community_audit_* family). FK-safe order --
    # community_audit_guilds (FK -> orgs, nullable) is the parent; runs FK -> guilds;
    # findings/security_checks/settings_snapshot FK -> runs; ledger/member_scores/
    # member_activity/identity_links FK -> guilds; rate_limits/benchmark have no FK.
    # Integer autoincrement PKs (-> SEQUENCE_TABLES): runs, findings, security_checks,
    # settings_snapshot. The rest are TEXT/composite PK (no sequence).
    "community_audit_guilds",             # FK -> orgs (TEXT PK guild_id, no sequence)
    "community_audit_runs",               # FK -> community_audit_guilds (Integer autoincrement PK)
    "community_audit_findings",           # FK -> community_audit_runs (Integer autoincrement PK)
    "community_audit_security_checks",    # FK -> community_audit_runs (Integer autoincrement PK)
    "community_audit_settings_snapshot",  # FK -> community_audit_runs (Integer autoincrement PK)
    "community_audit_reaction_ledger",    # FK -> community_audit_guilds (composite TEXT PK, no sequence)
    "community_audit_member_scores",      # FK -> community_audit_guilds (composite TEXT PK, no sequence)
    "community_audit_member_activity",    # FK -> community_audit_guilds (composite TEXT PK, no sequence)
    "community_audit_rate_limits",        # no FKs (composite TEXT PK, no sequence)
    "community_audit_benchmark",          # no FKs (composite TEXT PK, no sequence)
    "community_audit_identity_links",     # FK -> community_audit_guilds (composite TEXT PK, no sequence)
    # Migration 070: community-audit lead capture (no FK, Integer autoincrement PK -> SEQUENCE_TABLES).
    "community_audit_leads",
    # Migration 071: Tweet Assist compose topic-suggestion cache. FK -> relay_clients
    # (the parent precedes this block). Integer autoincrement PK -> SEQUENCE_TABLES.
    "relay_topic_suggestions",           # FK -> relay_clients (Integer autoincrement PK)
    # Migration 072: Tweet Assist compose topic-pick feedback log. FK -> relay_clients.
    # Integer autoincrement PK -> SEQUENCE_TABLES.
    "relay_topic_picks",                 # FK -> relay_clients (Integer autoincrement PK)
    # Migration 073: client & operator onboarding (intake SSOT + entitlements). All FK -> orgs
    # (which precedes this block). client_intake is a TEXT PK (NOT-NULL FK -> no _TEXT_PK_COLUMNS);
    # the other 3 are Integer autoincrement PK -> SEQUENCE_TABLES.
    "client_intake",                     # FK -> orgs (TEXT PK)
    "client_accounts",                   # FK -> orgs (Integer autoincrement PK)
    "client_docs",                       # FK -> orgs (Integer autoincrement PK)
    "org_entitlements",                  # FK -> orgs (Integer autoincrement PK)
    # Migration 074: Tweet Assist tweetbank. FK -> orgs. Integer autoincrement PK.
    "tweetbank_entries",                 # FK -> orgs (Integer autoincrement PK)
    # Migration 075: DB-backed allowlist. No FK (auth table). TEXT PK (NOT-NULL -> no _TEXT_PK_COLUMNS).
    "allowlist_entries",                 # no FK (TEXT PK)
    # Migration 076: Content Deck candidate substrate. The PARENT (content_candidates) must
    # precede BOTH children: content_deck_operator_state has a hard FK -> content_candidates;
    # content_deck_decisions has NO FK to candidates (no-FK learning-join so Elo survives purge)
    # but stays after the parent by convention. All org_id FKs -> orgs (precedes this block).
    "content_candidates",                # FK -> orgs (Integer autoincrement PK)
    "content_deck_decisions",            # FK -> orgs (Integer autoincrement PK; no FK to candidates)
    "content_deck_operator_state",       # FK -> content_candidates (composite PK, no sequence)
    # Migration 077: Content Deck Phase 4 release substrate. FK -> content_candidates (ON DELETE
    # CASCADE) + orgs, both precede this. Integer autoincrement PK -> also in SEQUENCE_TABLES.
    "content_publish_jobs",              # FK -> content_candidates + orgs (Integer autoincrement PK)
    # Migration 079: single-use deck/produce assertion store (Codex Tier-1 replay defense). No FKs,
    # TEXT primary key (sig) -> NOT in SEQUENCE_TABLES.
    "deck_consumed_assertions",          # no FKs (TEXT PK = sig)
    # Migration 080: content-preference Elo rollup (parallel to media_quality). No FKs, composite TEXT
    # PK (org_id, subject_kind, subject_key) -> NOT in SEQUENCE_TABLES. (content_deck_decisions.applied
    # rides its existing create above.)
    "content_quality",                   # no FKs (composite TEXT PK, no sequence)
]

# Tables with Integer autoincrement PKs that need Postgres sequence resets.
# Maps table_name -> pk_column_name.
SEQUENCE_TABLES: dict[str, str] = {
    "entity_handles": "handle_id",
    "entity_tags": "tag_id",
    "entity_notes": "note_id",
    "merge_candidates": "candidate_id",
    "merge_events": "event_id",
    "diagnostic_runs": "run_id",
    "job_steps": "step_id",
    "artifacts": "artifact_id",
    "cost_events": "event_id",
    "sync_runs": "sync_id",
    "discord_pulse_runs": "id",
    "entity_interactions": "id",
    "entity_decay_scores": "id",
    "entity_centrality_scores": "id",
    "entity_watchlist": "id",
    "watchlist_snapshots": "id",
    "audit_log": "id",
    "webhook_subscriptions": "id",
    "prospect_scores": "id",
    "playbook_targets": "id",
    "playbook_outcomes": "id",
    "metric_snapshots": "id",
    "kol_candidates": "candidate_id",
    "kol_handle_resolution_conflicts": "conflict_id",
    "kol_operator_relationships": "id",
    "kol_create_audit": "id",
    "kol_enrichment": "enrichment_id",
    "discord_streak_events": "id",
    "discord_burn_random_log": "id",
    # Migration 047: all 6 new tables have Integer autoincrement id PKs.
    "discord_burn_blocklist": "id",
    "discord_peer_roast_tokens": "id",
    "discord_peer_roast_flags": "id",
    "discord_message_observations": "id",
    "discord_user_observations": "id",
    "discord_user_vibes": "id",
    # Migration 048: airlock tables (Integer autoincrement id PKs)
    "discord_invite_snapshot": "id",
    "discord_team_inviters": "id",
    "discord_member_admit": "id",
    # Migration 050-051: Scored Mode V2 Pass B
    "discord_fitcheck_scores": "id",
    "discord_scoring_config": "id",
    # Migration 052: Scored Mode V2 Pass C
    "discord_fitcheck_emoji_milestones": "id",
    # Migration 054: state-pin surface
    "discord_state_pins": "id",
    # Migration 057: SableRelay tables with Integer autoincrement PKs.
    # TEXT-PK tables (relay_clients) and composite-PK tables
    # (relay_member_identities/roles/preferences, relay_submission_reactions,
    # relay_reply_opportunity_targets, relay_processed_updates) are excluded.
    "relay_chats": "id",
    "relay_chat_bindings": "id",
    "relay_members": "id",
    "relay_tweets": "id",
    "relay_messages": "id",
    "relay_submissions": "id",
    "relay_publication_jobs": "id",
    "relay_publications": "id",
    "relay_reply_opportunities": "id",
    "relay_reply_notifications": "id",
    # Migration 062: reply-opportunity feed. Only relay_opportunity_feedback has
    # an Integer autoincrement PK; operator_state/sweep_config/sweep_cursor/
    # operator_heartbeat are composite-/TEXT-PK and have no sequence.
    "relay_opportunity_feedback": "id",
    # Migration 064: trending-story autopilot (Integer autoincrement PK).
    "relay_trending_stories": "id",
    # Migration 065: tweet-quality corpus. Only relay_tweet_snapshots has an
    # Integer autoincrement PK -- relay_quality_accounts (TEXT PK handle) and
    # relay_quality_tweets (TEXT PK tweet_x_id) have no sequence.
    "relay_tweet_snapshots": "id",
    # Migration 066: media recommendation center. Only media_rec_events has an
    # Integer autoincrement PK -- media_quality and media_embeddings are
    # composite TEXT-PK (org_id, content_id) and have no sequence.
    "media_rec_events": "id",
    # Migration 058: SableAutoCM tables with Integer autoincrement PKs.
    # autocm_kb_constants is EXCLUDED (composite TEXT PK (client_id, key), no
    # sequence). The FTS5 companion (autocm_kb_chunks_fts) is SQLite-only and not
    # migrated to Postgres.
    "autocm_personas": "id",
    "autocm_clients": "id",
    "autocm_kb_sources": "id",
    "autocm_kb_chunks": "id",
    "autocm_drafts": "id",
    "autocm_reviews": "id",
    "autocm_category_state": "id",
    "autocm_escalations": "id",
    "autocm_flagged_users": "id",
    "autocm_adversarial_runs": "id",
    "autocm_digest_interactions": "id",
    "autocm_time_saved_baseline": "id",
    # Migration 067: community audit. Integer autoincrement PKs only --
    # community_audit_guilds (TEXT PK) and the ledger/member/rate/benchmark/identity
    # tables (composite TEXT PK) are excluded.
    "community_audit_runs": "id",
    "community_audit_findings": "id",
    "community_audit_security_checks": "id",
    "community_audit_settings_snapshot": "id",
    # Migration 070: community-audit leads.
    "community_audit_leads": "id",
    # Migration 071: Tweet Assist compose topic-suggestion cache.
    "relay_topic_suggestions": "id",
    # Migration 072: Tweet Assist compose topic-pick feedback log.
    "relay_topic_picks": "id",
    # Migration 073: client onboarding (client_intake is a TEXT PK -> no sequence).
    "client_accounts": "id",
    "client_docs": "id",
    "org_entitlements": "id",
    # Migration 074: Tweet Assist tweetbank.
    "tweetbank_entries": "id",
    # Migration 076: Content Deck. content_candidates + content_deck_decisions have Integer
    # autoincrement id PKs; content_deck_operator_state is composite-PK (candidate_id,
    # operator_handle) and has NO sequence.
    "content_candidates": "id",
    "content_deck_decisions": "id",
    # Migration 077: Content Deck Phase 4 release substrate (Integer autoincrement id PK).
    "content_publish_jobs": "id",
}

# Tables with Text primary keys that SQLite allowed to be NULL.
# Used to generate UUIDs for NULL PKs during migration.
_TEXT_PK_COLUMNS: dict[str, str] = {
    "orgs": "org_id",
    "entities": "entity_id",
    "content_items": "item_id",
    "diagnostic_deltas": "delta_id",
    "jobs": "job_id",
    "workflow_runs": "run_id",
    "workflow_steps": "step_id",
    "workflow_events": "event_id",
    "actions": "action_id",
    "outcomes": "outcome_id",
    "entity_tag_history": "history_id",
    "alert_configs": "config_id",
    "alerts": "alert_id",
    "platform_meta": "key",
    "project_profiles_external": "handle_normalized",
    "api_tokens": "token_id",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TableResult:
    table_name: str
    source_rows: int
    target_rows: int
    status: str  # "ok" | "skipped" | "error"
    error: str | None = None


@dataclass
class MigrationReport:
    status: str  # "success" | "failed"
    tables: list[TableResult] = field(default_factory=list)
    total_source_rows: int = 0
    total_target_rows: int = 0
    error: str | None = None


class MigrationError(Exception):
    """Raised when migration fails — transaction is rolled back."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_migration(
    source_engine: Engine,
    target_engine: Engine,
    *,
    force: bool = False,
) -> MigrationReport:
    """Orchestrate full SQLite -> Postgres data migration.

    Steps:
      1. Validate engines (source=SQLite, target=Postgres or SQLite for tests)
      2. Run Alembic upgrade head on target (Postgres only)
      3. Check target is empty (or ``--force`` to truncate)
      4. Copy all tables in FK-safe order
      5. Reset Postgres sequences for autoincrement tables
      6. Validate row counts match

    Returns a :class:`MigrationReport`.  Raises :class:`MigrationError` on
    failure (transaction is rolled back, target is unchanged).
    """
    is_pg = target_engine.dialect.name == "postgresql"

    # 1. Validate source
    if source_engine.dialect.name != "sqlite":
        raise MigrationError(
            f"Source must be SQLite, got {source_engine.dialect.name!r}"
        )

    # 2. Alembic schema creation (Postgres only)
    if is_pg:
        target_url = str(target_engine.url)
        log.info("Running Alembic upgrade head on target...")
        _run_alembic_upgrade(target_url)

    # 3. Check target emptiness
    needs_truncate = False
    if not _check_target_empty(target_engine, TABLE_LOAD_ORDER):
        if not force:
            raise MigrationError(
                "Target database is not empty. Use --force to truncate before migration."
            )
        needs_truncate = True

    # 4. Copy all tables (single transaction — truncate + copy are atomic)
    report = MigrationReport(status="success")
    with target_engine.begin() as conn:
        try:
            if needs_truncate:
                log.warning("--force: truncating target tables...")
                _truncate_target(conn, TABLE_LOAD_ORDER, is_pg=is_pg)
            # Disable FK triggers for the duration of the copy (Postgres only)
            if is_pg:
                for tbl in TABLE_LOAD_ORDER:
                    conn.execute(text(f'ALTER TABLE "{tbl}" DISABLE TRIGGER ALL'))

            for table_name in TABLE_LOAD_ORDER:
                rows = _read_all_rows(source_engine, table_name)
                if not rows:
                    report.tables.append(TableResult(
                        table_name=table_name, source_rows=0,
                        target_rows=0, status="skipped",
                    ))
                    continue

                # Fix NULL Text PKs — SQLite allows them, Postgres doesn't.
                pk_col = _TEXT_PK_COLUMNS.get(table_name)
                if pk_col:
                    fixed = 0
                    for row in rows:
                        if row.get(pk_col) is None:
                            row[pk_col] = uuid.uuid4().hex
                            fixed += 1
                    if fixed:
                        log.warning(
                            "Fixed %d NULL %s values in %s",
                            fixed, pk_col, table_name,
                        )

                columns = list(rows[0].keys())
                inserted = 0
                for i in range(0, len(rows), BATCH_SIZE):
                    batch = rows[i : i + BATCH_SIZE]
                    inserted += _insert_batch(conn, table_name, columns, batch)

                report.tables.append(TableResult(
                    table_name=table_name, source_rows=len(rows),
                    target_rows=inserted, status="ok",
                ))
                log.info("Copied %s: %d rows", table_name, inserted)

            # 5. Reset sequences (Postgres only)
            if is_pg:
                _reset_sequences(conn, SEQUENCE_TABLES)

            # Re-enable FK triggers (Postgres only)
            if is_pg:
                for tbl in TABLE_LOAD_ORDER:
                    conn.execute(text(f'ALTER TABLE "{tbl}" ENABLE TRIGGER ALL'))

        except Exception as exc:
            report.status = "failed"
            report.error = str(exc)
            raise MigrationError(f"Migration failed during copy: {exc}") from exc

    # 6. Validate counts
    validation = _validate_counts(source_engine, target_engine, TABLE_LOAD_ORDER)
    mismatches = [r for r in validation if r.source_rows != r.target_rows]
    if mismatches:
        details = ", ".join(
            f"{r.table_name} (src={r.source_rows}, tgt={r.target_rows})"
            for r in mismatches
        )
        report.status = "failed"
        report.error = f"Row count mismatch: {details}"
        raise MigrationError(report.error)

    report.total_source_rows = sum(r.source_rows for r in report.tables)
    report.total_target_rows = sum(r.target_rows for r in report.tables)
    return report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_all_rows(engine: Engine, table_name: str) -> list[dict[str, Any]]:
    """Read all rows from a table as a list of dicts."""
    with engine.connect() as conn:
        result = conn.execute(text(f'SELECT * FROM "{table_name}"'))
        return [dict(row._mapping) for row in result]


def _insert_batch(
    conn: Any,
    table_name: str,
    columns: list[str],
    rows: list[dict[str, Any]],
) -> int:
    """Insert a batch of rows using SA text() with :named params.

    Returns the number of rows inserted.
    """
    if not rows:
        return 0

    col_list = ", ".join(f'"{c}"' for c in columns)
    param_list = ", ".join(f":{c}" for c in columns)
    sql = f'INSERT INTO "{table_name}" ({col_list}) VALUES ({param_list})'

    conn.execute(text(sql), rows)
    return len(rows)


def _reset_sequences(conn: Any, tables: dict[str, str]) -> None:
    """Reset Postgres sequences to match the current max PK value.

    For each table with an autoincrement Integer PK:
    - If table has rows: setval(seq, max_val, true) -> next value = max_val + 1
    - If table is empty: setval(seq, 1, false) -> next value = 1

    Uses pg_get_serial_sequence with fallback to the conventional
    ``{table}_{col}_seq`` name (Alembic-created columns may not have
    the ownership link that pg_get_serial_sequence requires).
    """
    for table_name, pk_col in tables.items():
        # Resolve sequence name: try pg_get_serial_sequence first, fall back
        # to conventional naming if it returns NULL.
        seq_row = conn.execute(text(
            "SELECT pg_get_serial_sequence(:table_name, :column_name) AS seq"
        ), {"table_name": table_name, "column_name": pk_col}).fetchone()
        seq_name = seq_row[0] if seq_row and seq_row[0] else None

        if not seq_name:
            # Conventional Postgres sequence name for SERIAL columns
            seq_name = f"{table_name}_{pk_col}_seq"
            log.debug(
                "pg_get_serial_sequence returned NULL for %s.%s, "
                "using conventional name: %s",
                table_name, pk_col, seq_name,
            )

        conn.execute(text(f"""
            SELECT setval(
                :seq_name,
                COALESCE((SELECT MAX("{pk_col}") FROM "{table_name}"), 1),
                (SELECT MAX("{pk_col}") IS NOT NULL FROM "{table_name}")
            )
        """), {"seq_name": seq_name})
        log.debug("Reset sequence %s for %s.%s", seq_name, table_name, pk_col)


def _validate_counts(
    source_engine: Engine,
    target_engine: Engine,
    tables: list[str],
) -> list[TableResult]:
    """Compare row counts per table between source and target."""
    results: list[TableResult] = []
    for table_name in tables:
        with source_engine.connect() as src_conn:
            src_count = src_conn.execute(
                text(f'SELECT COUNT(*) FROM "{table_name}"')
            ).scalar() or 0
        with target_engine.connect() as tgt_conn:
            tgt_count = tgt_conn.execute(
                text(f'SELECT COUNT(*) FROM "{table_name}"')
            ).scalar() or 0
        status = "ok" if src_count == tgt_count else "error"
        results.append(TableResult(
            table_name=table_name,
            source_rows=src_count,
            target_rows=tgt_count,
            status=status,
            error=f"count mismatch: {src_count} vs {tgt_count}" if status == "error" else None,
        ))
    return results


def _check_target_empty(engine: Engine, tables: list[str]) -> bool:
    """Return True if all tables in the target have zero rows."""
    with engine.connect() as conn:
        for table_name in tables:
            try:
                count = conn.execute(
                    text(f'SELECT COUNT(*) FROM "{table_name}"')
                ).scalar() or 0
            except Exception as exc:
                # Table may not exist yet (pre-Alembic) — treat as empty
                log.debug("Skipping emptiness check for %s: %s", table_name, exc)
                continue
            if count > 0:
                return False
    return True


def _truncate_target(conn: Any, tables: list[str], *, is_pg: bool) -> None:
    """Clear all tables in reverse FK order."""
    for table_name in reversed(tables):
        if is_pg:
            conn.execute(text(f'TRUNCATE TABLE "{table_name}" CASCADE'))
        else:
            conn.execute(text(f'DELETE FROM "{table_name}"'))


def _run_alembic_upgrade(database_url: str) -> None:
    """Programmatically run ``alembic upgrade head``."""
    import importlib.resources

    from alembic import command
    from alembic.config import Config

    # Resolve script_location from package-owned assets so migrations work
    # from installed wheels and container images, not just source checkouts.
    alembic_root = importlib.resources.files("sable_platform.alembic")

    with importlib.resources.as_file(alembic_root) as script_dir:
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(script_dir))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

        # Suppress Alembic's default logging to avoid leaking credentials
        # in the connection URL.  Errors still propagate as exceptions.
        logging.getLogger("alembic").setLevel(logging.WARNING)

        command.upgrade(alembic_cfg, "head")
