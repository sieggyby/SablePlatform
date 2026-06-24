"""Migration and schema tests."""
from __future__ import annotations

import sqlite3

from sable_platform.db.connection import ensure_schema


EXPECTED_TABLES = {
    # Original 15 tables
    "schema_version", "orgs", "entities", "entity_handles", "entity_tags",
    "entity_notes", "merge_candidates", "merge_events", "content_items",
    "diagnostic_runs", "jobs", "job_steps", "artifacts", "cost_events", "sync_runs",
    # Migration 006: 3 new tables
    "workflow_runs", "workflow_steps", "workflow_events",
    # Migration 007: actions + outcomes + diagnostic_deltas
    "actions", "outcomes", "diagnostic_deltas",
    # Migration 008: entity journey
    "entity_tag_history",
    # Migration 009: alerts
    "alerts", "alert_configs",
    # Migration 010: discord pulse
    "discord_pulse_runs",
    # Migration 014: entity interactions
    "entity_interactions",
    # Migration 015: entity decay scores
    "entity_decay_scores",
    # Migration 016: entity centrality
    "entity_centrality_scores",
    # Migration 017: entity watchlist
    "entity_watchlist", "watchlist_snapshots",
    # Migration 018: audit log
    "audit_log",
    # Migration 019: webhooks
    "webhook_subscriptions",
    # Migration 020: prospect scores
    "prospect_scores",
    # Migration 022: playbook tagging
    "playbook_targets", "playbook_outcomes",
    # Migration 028: platform metadata
    "platform_meta",
    # Migration 031: metric snapshots
    "metric_snapshots",
    # Migration 032: SableKOL bank
    "kol_candidates", "project_profiles_external", "kol_handle_resolution_conflicts",
    # Migration 037: SableKOL follow-graph extraction
    "kol_extract_runs", "kol_follow_edges",
    # Migration 038: SableKOL operator relationship-tagging
    "kol_operator_relationships",
    # Migration 040: KOL wizard auth audit log
    "kol_create_audit",
    # Migration 041: KOL enrichment cache
    "kol_enrichment",
    # Migration 043: discord streak events (fitcheck bot)
    "discord_streak_events",
    # Migration 044: API tokens
    "api_tokens",
    # Migration 045: discord guild config (sable-roles V2)
    "discord_guild_config",
    # Migration 046: discord burn-me opt-in state + random-roast dedup log (sable-roles V2)
    "discord_burn_optins", "discord_burn_random_log",
    # Migration 047: /roast V2 peer-economy + personalization layer (sable-roles)
    "discord_burn_blocklist",
    "discord_peer_roast_tokens",
    "discord_peer_roast_flags",
    "discord_message_observations",
    "discord_user_observations",
    "discord_user_vibes",
    # Migration 048: airlock — invite-source-aware member verification (sable-roles)
    "discord_invite_snapshot",
    "discord_team_inviters",
    "discord_member_admit",
    # Migrations 050-051: Scored Mode V2 Pass B (sable-roles)
    # (Mig 049 ALTERs discord_streak_events — no new table.)
    "discord_fitcheck_scores",
    "discord_scoring_config",
    # Migration 052: Scored Mode V2 Pass C — per-emoji milestone crossings.
    "discord_fitcheck_emoji_milestones",
    # Migration 054: state-pin surface — currently-pinned message id per
    # (guild_id, characteristic) for the per-guild ops channel.
    "discord_state_pins",
    # Migration 055: shared media-asset registry.
    "media_assets",
    # Migration 056: operator reply-suggestion feature.
    "operator_reply_quota", "reply_suggestions", "reply_outcomes",
    # Migration 057: SableRelay (relay_* family)
    "relay_clients", "relay_chats", "relay_chat_bindings", "relay_members",
    "relay_member_identities", "relay_member_roles", "relay_member_preferences",
    "relay_tweets", "relay_messages", "relay_submissions",
    "relay_submission_reactions", "relay_publication_jobs", "relay_publications",
    "relay_reply_opportunities", "relay_reply_opportunity_targets",
    "relay_reply_notifications", "relay_processed_updates",
    # Migration 058: SableAutoCM (autocm_* family)
    "autocm_personas", "autocm_clients", "autocm_kb_sources", "autocm_kb_chunks",
    "autocm_kb_constants", "autocm_drafts", "autocm_reviews",
    "autocm_category_state", "autocm_escalations", "autocm_flagged_users",
    "autocm_adversarial_runs", "autocm_digest_interactions",
    "autocm_time_saved_baseline",
    # Migration 059: operator work-tracking
    "mod_slot_sessions", "operator_work_events",
    # Migration 061: coordinated reply campaigns
    "reply_campaigns", "reply_campaign_assignments",
    # Migration 062: reply-opportunity feed (5 new tables; the additive columns
    # on relay_reply_opportunities / relay_tweets / reply_suggestions are
    # asserted by test_migration_062_*).
    "relay_opportunity_operator_state", "relay_opportunity_feedback",
    "relay_sweep_config", "relay_sweep_cursor", "relay_operator_heartbeat",
    # Migration 063: reply-learning is purely ADD COLUMN (no new tables) —
    # tell_score/tell_flags_json on reply_suggestions + embedding_json/
    # embedding_model on relay_tweets, asserted by test_migration_063_*.
    # Migration 064: trending-story autopilot
    "relay_trending_stories",
    # Migration 065: tweet-quality corpus
    "relay_quality_accounts", "relay_quality_tweets", "relay_tweet_snapshots",
    # Migration 066: media recommendation center (3 new tables; the additive
    # reply_outcomes.media_content_id column is asserted by test_migration_066_*).
    "media_rec_events", "media_quality", "media_embeddings",
    # Migration 076: Content Deck candidate substrate
    "content_candidates", "content_deck_decisions", "content_deck_operator_state",
}


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _apply_migrations_through(conn: sqlite3.Connection, target_version: int) -> None:
    """Apply the registered SQL migrations up to (and including) ``target_version``.

    Mirrors ``ensure_schema``'s runner (same split-on-``;`` + per-file
    ``with conn:`` transaction) but stops early so a test can seed a populated
    table on the PRE-062 schema before applying 062 — the seed-then-upgrade
    dual-migration parity check the plan requires (not just an empty-DB run).
    """
    import importlib.resources

    from sable_platform.db.connection import _MIGRATIONS

    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current = row[0] if row else 0
    except sqlite3.OperationalError:
        current = 0

    migrations_pkg = importlib.resources.files("sable_platform.db") / "migrations"
    for filename, version in _MIGRATIONS:
        if version > target_version:
            break
        if version <= current:
            continue  # already applied — only roll forward (like ensure_schema)
        sql = (migrations_pkg / filename).read_text(encoding="utf-8")
        stmts = [s.strip() for s in sql.split(";") if s.strip()]
        with conn:
            for stmt in stmts:
                conn.execute(stmt)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                (version,),
            )


def test_fresh_db_reaches_current_version():
    conn = _make_conn()
    ensure_schema(conn)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == 78


def test_all_tables_exist():
    conn = _make_conn()
    ensure_schema(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for expected in EXPECTED_TABLES:
        assert expected in tables, f"Table '{expected}' not found"


def test_idempotent_schema():
    conn = _make_conn()
    ensure_schema(conn)
    ensure_schema(conn)  # Run again — should not raise
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == 78


def test_workflow_tables_columns():
    conn = _make_conn()
    ensure_schema(conn)

    # workflow_runs must have these columns
    cols = {row[1] for row in conn.execute("PRAGMA table_info(workflow_runs)").fetchall()}
    for expected in ("run_id", "org_id", "workflow_name", "status", "config_json", "started_at", "completed_at", "error"):
        assert expected in cols, f"workflow_runs missing column '{expected}'"

    # workflow_steps must have these columns
    cols = {row[1] for row in conn.execute("PRAGMA table_info(workflow_steps)").fetchall()}
    for expected in ("step_id", "run_id", "step_name", "step_index", "status", "retries", "input_json", "output_json", "error"):
        assert expected in cols, f"workflow_steps missing column '{expected}'"


def test_alert_cooldown_columns():
    """Migration 011: alert_configs.cooldown_hours and alerts.last_delivered_at exist."""
    conn = _make_conn()
    ensure_schema(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(alert_configs)").fetchall()}
    assert "cooldown_hours" in cols, "alert_configs missing 'cooldown_hours'"

    cols = {row[1] for row in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    assert "last_delivered_at" in cols, "alerts missing 'last_delivered_at'"


def test_workflow_step_fingerprint_column():
    """Migration 012: workflow_runs.step_fingerprint exists."""
    conn = _make_conn()
    ensure_schema(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(workflow_runs)").fetchall()}
    assert "step_fingerprint" in cols, "workflow_runs missing 'step_fingerprint'"


def test_cooldown_hours_default_is_4():
    """Migration 011: alert_configs rows inserted without cooldown_hours must default to 4."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('x', 'X', 'active')")
    config_id = "cfg_default_test"
    conn.execute(
        "INSERT INTO alert_configs (config_id, org_id, min_severity, enabled) VALUES (?, 'x', 'warning', 1)",
        (config_id,),
    )
    conn.commit()
    row = conn.execute("SELECT cooldown_hours FROM alert_configs WHERE config_id=?", (config_id,)).fetchone()
    assert row["cooldown_hours"] == 4, f"expected cooldown_hours=4, got {row['cooldown_hours']}"


def test_last_delivered_at_is_null_on_new_alert():
    """Migration 011: freshly created alerts must have last_delivered_at=NULL."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('y', 'Y', 'active')")
    alert_id = "alert_null_test"
    conn.execute(
        "INSERT INTO alerts (alert_id, org_id, alert_type, severity, title, status, dedup_key) "
        "VALUES (?, 'y', 'tracking_stale', 'critical', 'T', 'new', 'dk1')",
        (alert_id,),
    )
    conn.commit()
    row = conn.execute("SELECT last_delivered_at FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
    assert row["last_delivered_at"] is None, "last_delivered_at must be NULL on new alerts"


def test_alert_delivery_error_column():
    """Migration 013: alerts.last_delivery_error exists and is NULL by default."""
    conn = _make_conn()
    ensure_schema(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    assert "last_delivery_error" in cols, "alerts missing 'last_delivery_error'"

    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('de', 'DE', 'active')")
    alert_id = "de_alert_test"
    conn.execute(
        "INSERT INTO alerts (alert_id, org_id, alert_type, severity, title, status, dedup_key) "
        "VALUES (?, 'de', 'tracking_stale', 'critical', 'T', 'new', 'de_dk')",
        (alert_id,),
    )
    conn.commit()
    row = conn.execute("SELECT last_delivery_error FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
    assert row["last_delivery_error"] is None, "last_delivery_error must be NULL by default"


def test_step_fingerprint_is_null_on_new_run():
    """Migration 012: workflow_runs inserted without step_fingerprint must have NULL."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('z', 'Z', 'active')")
    run_id = "run_null_test"
    conn.execute(
        "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status, config_json) "
        "VALUES (?, 'z', 'test_wf', 'completed', '{}')",
        (run_id,),
    )
    conn.commit()
    row = conn.execute("SELECT step_fingerprint FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    assert row["step_fingerprint"] is None, "step_fingerprint must be NULL when not set (legacy run)"


def test_run_summary_json_column():
    """Migration 021: diagnostic_runs.run_summary_json exists."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(diagnostic_runs)").fetchall()}
    assert "run_summary_json" in cols, "diagnostic_runs missing 'run_summary_json'"


def test_playbook_tables_exist():
    """Migration 022: playbook_targets and playbook_outcomes tables exist."""
    conn = _make_conn()
    ensure_schema(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "playbook_targets" in tables, "playbook_targets table not found"
    assert "playbook_outcomes" in tables, "playbook_outcomes table not found"


def test_playbook_targets_columns():
    """Migration 022: playbook_targets has expected columns."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(playbook_targets)").fetchall()}
    for expected in ("id", "org_id", "artifact_id", "targets_json", "created_at"):
        assert expected in cols, f"playbook_targets missing column '{expected}'"


def test_playbook_outcomes_columns():
    """Migration 022: playbook_outcomes has expected columns."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(playbook_outcomes)").fetchall()}
    for expected in ("id", "org_id", "targets_artifact_id", "outcomes_json", "created_at"):
        assert expected in cols, f"playbook_outcomes missing column '{expected}'"


def test_prospect_rejected_at_column():
    """Migration 026: prospect_scores.rejected_at exists and is NULL by default."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(prospect_scores)").fetchall()}
    assert "rejected_at" in cols, "prospect_scores missing 'rejected_at'"


def test_prospect_score_fields_columns():
    """Migration 029: prospect_scores has four new Lead Identifier contract fields."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(prospect_scores)").fetchall()}
    for expected in ("recommended_action", "score_band_low", "score_band_high", "timing_urgency"):
        assert expected in cols, f"prospect_scores missing column '{expected}'"


def test_platform_meta_table_exists():
    """Migration 028: platform_meta key-value table exists with correct columns."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(platform_meta)").fetchall()}
    for expected in ("key", "value", "updated_at"):
        assert expected in cols, f"platform_meta missing column '{expected}'"


def test_workflow_active_lock_index_exists():
    """Migration 027: workflow_runs has a unique partial index for active runs."""
    conn = _make_conn()
    ensure_schema(conn)
    rows = conn.execute("PRAGMA index_list(workflow_runs)").fetchall()
    names = {row[1] for row in rows}
    assert "idx_workflow_runs_active_lock" in names


def test_kol_candidates_columns():
    """Migration 032 + 033 + 034: kol_candidates has expected columns."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(kol_candidates)").fetchall()}
    for expected in (
        # Migration 032
        "candidate_id", "twitter_id", "handle_normalized", "is_unresolved",
        "handle_history_json", "display_name", "bio_snapshot", "followers_snapshot",
        "discovery_sources_json", "first_seen_at", "last_seen_at",
        "archetype_tags_json", "sector_tags_json", "sable_relationship_json",
        "enrichment_tier", "last_enriched_at", "status", "manual_notes",
        # Migration 033
        "kol_strength_score", "verified", "account_created_at",
        # Migration 034
        "listed_count", "tweets_count", "following_count",
        "credibility_signal", "real_name_known", "notes",
        # Migration 035
        "location",
    ):
        assert expected in cols, f"kol_candidates missing column '{expected}'"


def test_grok_enrich_columns_default_correctly():
    """Migration 034: nullable cols default to NULL; real_name_known defaults to 0."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute("INSERT INTO kol_candidates (handle_normalized) VALUES ('x')")
    conn.commit()
    row = conn.execute(
        "SELECT listed_count, tweets_count, following_count, credibility_signal, "
        "       real_name_known, notes FROM kol_candidates WHERE handle_normalized='x'"
    ).fetchone()
    assert row["listed_count"] is None
    assert row["tweets_count"] is None
    assert row["following_count"] is None
    assert row["credibility_signal"] is None
    assert row["real_name_known"] == 0
    assert row["notes"] is None


def test_kol_strength_score_default_null():
    """Migration 033: kol_strength_score defaults to NULL on new rows."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute("INSERT INTO kol_candidates (handle_normalized) VALUES ('alice')")
    conn.commit()
    row = conn.execute(
        "SELECT kol_strength_score, verified, account_created_at "
        "FROM kol_candidates WHERE handle_normalized='alice'"
    ).fetchone()
    assert row["kol_strength_score"] is None
    assert row["verified"] == 0  # NOT NULL DEFAULT 0
    assert row["account_created_at"] is None


def test_kol_candidates_partial_unique_index_allows_unresolved_dupes():
    """Migration 032: partial unique index permits multiple is_unresolved=1 rows
    with the same handle_normalized — that is the whole point of the design."""
    conn = _make_conn()
    ensure_schema(conn)
    # Two unresolved rows sharing the same handle should both succeed.
    conn.execute(
        "INSERT INTO kol_candidates (handle_normalized, is_unresolved, status) "
        "VALUES ('alice', 1, 'active')"
    )
    conn.execute(
        "INSERT INTO kol_candidates (handle_normalized, is_unresolved, status) "
        "VALUES ('alice', 1, 'active')"
    )
    conn.commit()
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM kol_candidates WHERE handle_normalized='alice'"
    ).fetchone()
    assert rows["n"] == 2


def test_kol_candidates_partial_unique_index_rejects_live_dupes():
    """Migration 032: partial unique index forbids two LIVE (is_unresolved=0) rows
    with the same handle_normalized."""
    import pytest
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO kol_candidates (handle_normalized, is_unresolved, status) "
        "VALUES ('bob', 0, 'active')"
    )
    conn.commit()
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO kol_candidates (handle_normalized, is_unresolved, status) "
            "VALUES ('bob', 0, 'active')"
        )
        conn.commit()


def test_kol_candidates_live_and_unresolved_can_coexist():
    """Migration 032: one live row + one unresolved row for the same handle is allowed.
    This is the recycled-handle resolution path."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO kol_candidates (handle_normalized, is_unresolved, status) "
        "VALUES ('carol', 0, 'active')"
    )
    conn.execute(
        "INSERT INTO kol_candidates (handle_normalized, is_unresolved, status) "
        "VALUES ('carol', 1, 'active')"
    )
    conn.commit()
    rows = conn.execute(
        "SELECT is_unresolved FROM kol_candidates WHERE handle_normalized='carol' "
        "ORDER BY is_unresolved"
    ).fetchall()
    assert [r["is_unresolved"] for r in rows] == [0, 1]


def test_kol_candidates_defaults():
    """Migration 032: defaults populate the JSON columns and enums correctly."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute("INSERT INTO kol_candidates (handle_normalized) VALUES ('dave')")
    conn.commit()
    row = conn.execute(
        "SELECT is_unresolved, handle_history_json, discovery_sources_json, "
        "archetype_tags_json, sector_tags_json, sable_relationship_json, "
        "enrichment_tier, status FROM kol_candidates WHERE handle_normalized='dave'"
    ).fetchone()
    assert row["is_unresolved"] == 0
    assert row["handle_history_json"] == "[]"
    assert row["discovery_sources_json"] == "[]"
    assert row["archetype_tags_json"] == "[]"
    assert row["sector_tags_json"] == "[]"
    assert row["sable_relationship_json"] == '{"communities":[],"operators":[]}'
    assert row["enrichment_tier"] == "none"
    assert row["status"] == "active"


def test_project_profiles_external_columns():
    """Migration 032: project_profiles_external has expected columns."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(project_profiles_external)").fetchall()}
    for expected in (
        "handle_normalized", "twitter_id", "sector_tags_json", "themes_json",
        "profile_blob", "enrichment_source", "last_enriched_at",
        "created_at", "last_used_at",
    ):
        assert expected in cols, f"project_profiles_external missing column '{expected}'"


def test_kol_handle_resolution_conflicts_columns():
    """Migration 032: kol_handle_resolution_conflicts has expected columns."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(kol_handle_resolution_conflicts)"
    ).fetchall()}
    for expected in (
        "conflict_id", "incoming_candidate_id", "existing_candidate_id",
        "resolved_twitter_id", "detected_at", "resolution_state",
        "resolved_at", "notes",
    ):
        assert expected in cols, f"kol_handle_resolution_conflicts missing column '{expected}'"


def test_kol_handle_resolution_conflicts_fk():
    """Migration 032: conflict rows reference real candidate ids."""
    import pytest
    conn = _make_conn()
    ensure_schema(conn)
    cur = conn.execute(
        "INSERT INTO kol_candidates (handle_normalized, is_unresolved) VALUES ('eve', 0)"
    )
    eve_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO kol_candidates (handle_normalized, is_unresolved) VALUES ('eve', 1)"
    )
    eve_dup_id = cur.lastrowid
    conn.execute(
        "INSERT INTO kol_handle_resolution_conflicts "
        "(incoming_candidate_id, existing_candidate_id, resolution_state) "
        "VALUES (?, ?, 'open')",
        (eve_dup_id, eve_id),
    )
    conn.commit()
    # FK to nonexistent candidate must fail
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO kol_handle_resolution_conflicts "
            "(incoming_candidate_id, existing_candidate_id, resolution_state) "
            "VALUES (999999, 999998, 'open')"
        )
        conn.commit()


def test_foreign_keys_enabled():
    conn = _make_conn()
    ensure_schema(conn)
    # FK enforcement: inserting workflow_run for nonexistent org should fail
    import pytest
    conn.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO workflow_runs (run_id, org_id, workflow_name) VALUES ('r1', 'noexist', 'test')"
        )
        conn.commit()


def test_kol_extract_runs_columns():
    """Migration 037: kol_extract_runs has expected columns."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(kol_extract_runs)").fetchall()}
    for expected in (
        "run_id", "target_handle_normalized", "target_user_id", "provider",
        "extract_type", "started_at", "completed_at", "cursor_completed",
        "last_cursor", "pages_fetched", "rows_inserted", "expected_count",
        "partial_failure_reason", "cost_usd_logged",
    ):
        assert expected in cols, f"kol_extract_runs missing column '{expected}'"


def test_kol_extract_runs_defaults():
    """Migration 037: defaults populate flag/counter columns correctly."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO kol_extract_runs (run_id, target_handle_normalized, provider, extract_type) "
        "VALUES ('r1', 'doji_com', 'socialdata', 'followers')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT cursor_completed, pages_fetched, rows_inserted, cost_usd_logged "
        "FROM kol_extract_runs WHERE run_id='r1'"
    ).fetchone()
    assert row["cursor_completed"] == 0
    assert row["pages_fetched"] == 0
    assert row["rows_inserted"] == 0
    assert row["cost_usd_logged"] == 0


def test_kol_follow_edges_columns():
    """Migration 037: kol_follow_edges has expected columns."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(kol_follow_edges)").fetchall()}
    for expected in (
        "run_id", "follower_id", "follower_handle", "followed_id",
        "followed_handle", "fetched_at",
    ):
        assert expected in cols, f"kol_follow_edges missing column '{expected}'"


def test_kol_follow_edges_fk_to_extract_runs():
    """Migration 037: edges FK references real run_ids."""
    import pytest
    conn = _make_conn()
    ensure_schema(conn)
    # FK to nonexistent run must fail
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO kol_follow_edges (run_id, follower_id, followed_id, followed_handle) "
            "VALUES ('does_not_exist', 'f1', 'fd1', 'h1')"
        )
        conn.commit()


def test_kol_follow_edges_pk_dedupes_same_edge():
    """Migration 037: composite PK (run_id, follower_id, followed_id) prevents duplicate edges."""
    import pytest
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO kol_extract_runs (run_id, target_handle_normalized, provider, extract_type) "
        "VALUES ('r2', 'doji_com', 'socialdata', 'followers')"
    )
    conn.execute(
        "INSERT INTO kol_follow_edges (run_id, follower_id, followed_id, followed_handle) "
        "VALUES ('r2', 'f1', 'fd1', 'targethandle')"
    )
    conn.commit()
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO kol_follow_edges (run_id, follower_id, followed_id, followed_handle) "
            "VALUES ('r2', 'f1', 'fd1', 'targethandle')"
        )
        conn.commit()


def test_kol_follow_edges_same_pair_different_run_allowed():
    """Migration 037: the same (follower, followed) pair under different run_ids is allowed
    (so re-extraction over time can be tracked / diffed)."""
    conn = _make_conn()
    ensure_schema(conn)
    for run_id in ("r3a", "r3b"):
        conn.execute(
            "INSERT INTO kol_extract_runs (run_id, target_handle_normalized, provider, extract_type) "
            "VALUES (?, 'doji_com', 'socialdata', 'followers')",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO kol_follow_edges (run_id, follower_id, followed_id, followed_handle) "
            "VALUES (?, 'f1', 'fd1', 'targethandle')",
            (run_id,),
        )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) AS n FROM kol_follow_edges").fetchone()["n"]
    assert n == 2


def test_kol_extract_runs_indexes_exist():
    """Migration 037: target + completed indexes are created."""
    conn = _make_conn()
    ensure_schema(conn)
    rows = conn.execute("PRAGMA index_list(kol_extract_runs)").fetchall()
    names = {row[1] for row in rows}
    assert "idx_kol_extract_runs_target" in names
    assert "idx_kol_extract_runs_completed" in names


def test_kol_follow_edges_indexes_exist():
    """Migration 037: followed/followed_handle/follower indexes are created."""
    conn = _make_conn()
    ensure_schema(conn)
    rows = conn.execute("PRAGMA index_list(kol_follow_edges)").fetchall()
    names = {row[1] for row in rows}
    assert "idx_kol_follow_edges_followed" in names
    assert "idx_kol_follow_edges_followed_handle" in names
    assert "idx_kol_follow_edges_follower" in names


def test_kol_operator_relationships_columns():
    """Migration 038: kol_operator_relationships has expected columns."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute(
        "PRAGMA table_info(kol_operator_relationships)"
    ).fetchall()}
    for expected in (
        "id", "handle_normalized", "client_id", "operator_id",
        "status", "note", "is_private", "created_at",
    ):
        assert expected in cols, f"kol_operator_relationships missing column '{expected}'"


def test_kol_operator_relationships_status_check():
    """Migration 038: status must be one of the 7 allowed values."""
    import pytest
    conn = _make_conn()
    ensure_schema(conn)
    # Valid statuses succeed
    for status in ("dm_sent", "replied", "replied_engaged", "meeting",
                   "relationship", "pass", "blocked"):
        conn.execute(
            "INSERT INTO kol_operator_relationships "
            "(handle_normalized, client_id, operator_id, status) "
            "VALUES (?, 'solstitch', 'op_alice', ?)",
            (f"h_{status}", status),
        )
    conn.commit()
    # Invalid status fails
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO kol_operator_relationships "
            "(handle_normalized, client_id, operator_id, status) "
            "VALUES ('bad', 'solstitch', 'op_alice', 'invented_status')"
        )
        conn.commit()


def test_kol_operator_relationships_is_private_default_zero():
    """Migration 038: is_private defaults to 0 (shared)."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO kol_operator_relationships "
        "(handle_normalized, client_id, operator_id, status) "
        "VALUES ('alice', 'solstitch', 'op_alice', 'dm_sent')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT is_private FROM kol_operator_relationships WHERE handle_normalized='alice'"
    ).fetchone()
    assert row["is_private"] == 0


def test_kol_operator_relationships_is_private_check():
    """Migration 038: is_private must be 0 or 1."""
    import pytest
    conn = _make_conn()
    ensure_schema(conn)
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO kol_operator_relationships "
            "(handle_normalized, client_id, operator_id, status, is_private) "
            "VALUES ('bob', 'solstitch', 'op_alice', 'dm_sent', 5)"
        )
        conn.commit()


def test_kol_operator_relationships_append_only_history():
    """Migration 038: multiple status changes for the same (handle, client)
    are stored as separate rows; current state is the most-recent."""
    conn = _make_conn()
    ensure_schema(conn)
    handle = "carol"
    for status in ("dm_sent", "replied", "replied_engaged", "meeting"):
        conn.execute(
            "INSERT INTO kol_operator_relationships "
            "(handle_normalized, client_id, operator_id, status) "
            "VALUES (?, 'solstitch', 'op_alice', ?)",
            (handle, status),
        )
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM kol_operator_relationships "
        "WHERE handle_normalized=? AND client_id='solstitch'",
        (handle,),
    ).fetchone()["n"]
    assert n == 4
    # Current state = MAX(created_at) — but since SQLite datetime('now') is
    # second-resolution, all four rows may share the same timestamp here.
    # Use id (autoincrement) as the tiebreaker, which is the production query.
    row = conn.execute(
        "SELECT status FROM kol_operator_relationships "
        "WHERE handle_normalized=? AND client_id='solstitch' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (handle,),
    ).fetchone()
    assert row["status"] == "meeting"


def test_kol_operator_relationships_indexes_exist():
    """Migration 038: handle_client / operator / created indexes are created."""
    conn = _make_conn()
    ensure_schema(conn)
    rows = conn.execute("PRAGMA index_list(kol_operator_relationships)").fetchall()
    names = {row[1] for row in rows}
    assert "idx_kor_handle_client" in names
    assert "idx_kor_operator" in names
    assert "idx_kor_created" in names


def test_kol_extract_runs_has_client_id_column():
    """Migration 039: client_id column added for per-client graph scoping."""
    conn = _make_conn()
    ensure_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(kol_extract_runs)").fetchall()}
    assert "client_id" in cols, "kol_extract_runs missing client_id column"


def test_kol_extract_runs_client_id_default():
    """Migration 039: rows inserted without client_id default to '_external'."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO kol_extract_runs (run_id, target_handle_normalized, provider, extract_type) "
        "VALUES ('r_default', 'newaccount', 'socialdata', 'followers')"
    )
    conn.commit()
    row = conn.execute(
        "SELECT client_id FROM kol_extract_runs WHERE run_id='r_default'"
    ).fetchone()
    assert row["client_id"] == "_external"


def test_kol_extract_runs_client_id_filter():
    """Migration 039: per-client filter returns scoped runs."""
    conn = _make_conn()
    ensure_schema(conn)
    for run_id, client in [("r_a", "solstitch"), ("r_b", "tig"), ("r_c", "solstitch")]:
        conn.execute(
            "INSERT INTO kol_extract_runs "
            "(run_id, target_handle_normalized, provider, extract_type, client_id) "
            "VALUES (?, 'h', 'socialdata', 'followers', ?)",
            (run_id, client),
        )
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM kol_extract_runs WHERE client_id='solstitch'"
    ).fetchone()["n"]
    assert n == 2


def test_kol_extract_runs_client_index_exists():
    """Migration 039: composite index for client-scoped queries."""
    conn = _make_conn()
    ensure_schema(conn)
    rows = conn.execute("PRAGMA index_list(kol_extract_runs)").fetchall()
    names = {row[1] for row in rows}
    assert "idx_kol_extract_runs_client" in names


# ---------------------------------------------------------------------------
# Migration 062 — reply-opportunity feed (additive extend + 5 new tables)
# ---------------------------------------------------------------------------

def test_migration_062_additive_columns_exist():
    """Migration 062: the 6+3+2 additive columns are present on a fresh DB."""
    conn = _make_conn()
    ensure_schema(conn)

    opp_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(relay_reply_opportunities)"
    ).fetchall()}
    for expected in ("score", "score_reason", "suggested_angle", "status",
                     "expires_at", "sweep_source"):
        assert expected in opp_cols, f"relay_reply_opportunities missing '{expected}'"

    tweet_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(relay_tweets)"
    ).fetchall()}
    for expected in ("engagement_json", "lang", "author_followers"):
        assert expected in tweet_cols, f"relay_tweets missing '{expected}'"

    sugg_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(reply_suggestions)"
    ).fetchall()}
    for expected in ("opportunity_id", "source_conversation_id"):
        assert expected in sugg_cols, f"reply_suggestions missing '{expected}'"


def test_migration_062_indexes_exist():
    """Migration 062: the 3 new non-unique indexes are created."""
    conn = _make_conn()
    ensure_schema(conn)
    opp_idx = {r[1] for r in conn.execute(
        "PRAGMA index_list(relay_reply_opportunities)"
    ).fetchall()}
    assert "ix_relay_opportunities_feed" in opp_idx
    assert "ix_relay_opportunities_expiry" in opp_idx
    fb_idx = {r[1] for r in conn.execute(
        "PRAGMA index_list(relay_opportunity_feedback)"
    ).fetchall()}
    assert "ix_relay_opportunity_feedback_opp" in fb_idx


def test_migration_062_no_unique_org_tweet_index():
    """Migration 062 (plan §3.1): dedup is application-level — there must be NO
    UNIQUE index on (org_id, tweet_id) (it could not build on a populated 057
    table that already holds duplicate flags)."""
    conn = _make_conn()
    ensure_schema(conn)
    for row in conn.execute("PRAGMA index_list(relay_reply_opportunities)").fetchall():
        # row = (seq, name, unique, origin, partial)
        if row[2]:  # unique
            cols = [
                c[2]
                for c in conn.execute(f"PRAGMA index_info({row[1]})").fetchall()
            ]
            assert set(cols) != {"org_id", "tweet_id"}, (
                f"unexpected UNIQUE(org_id,tweet_id) index {row[1]!r}"
            )


def test_migration_062_new_tables_columns():
    """Migration 062: the 5 new tables have the expected columns."""
    conn = _make_conn()
    ensure_schema(conn)
    expectations = {
        "relay_opportunity_operator_state": {
            "opportunity_id", "operator_handle", "state", "snooze_until", "created_at",
        },
        "relay_opportunity_feedback": {
            "id", "opportunity_id", "suggestion_id", "rater_handle",
            "rater_role", "thumb", "created_at",
        },
        "relay_sweep_config": {
            "org_id", "mention_handles", "topic_queries", "from_set",
            "operator_handles", "enabled", "expiry_hours", "last_sweep_at",
            "sweep_requested_at", "updated_at",
        },
        "relay_sweep_cursor": {
            "org_id", "source", "query_hash", "since_id", "updated_at",
        },
        "relay_operator_heartbeat": {"org_id", "operator_handle", "last_seen"},
    }
    for table, expected in expectations.items():
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        assert expected <= cols, f"{table} missing {expected - cols}"


def test_migration_062_sweep_config_defaults():
    """Migration 062: relay_sweep_config defaults populate JSON/flag columns."""
    conn = _make_conn()
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('o62', 'O', 'active')")
    conn.execute("INSERT INTO relay_clients (org_id) VALUES ('o62')")
    conn.execute("INSERT INTO relay_sweep_config (org_id) VALUES ('o62')")
    conn.commit()
    row = conn.execute(
        "SELECT mention_handles, topic_queries, from_set, operator_handles, "
        "       enabled, expiry_hours FROM relay_sweep_config WHERE org_id='o62'"
    ).fetchone()
    assert row["mention_handles"] == "[]"
    assert row["topic_queries"] == "[]"
    assert row["from_set"] == "[]"
    assert row["operator_handles"] == "[]"
    assert row["enabled"] == 0
    assert row["expiry_hours"] == 36


def test_migration_062_dual_migration_seed_then_upgrade():
    """HARD requirement (plan §3 / §8): a POPULATED relay_reply_opportunities on
    the PRE-062 schema must survive the 062 upgrade — the additive columns appear,
    existing data is intact, and status BACKFILLS to 'active'.

    This is the seed-then-upgrade dual-migration parity check (not just an
    empty-DB run): it proves the migration is genuinely additive (no rebuild) and
    the NOT NULL status DEFAULT applies to already-present rows.
    """
    conn = _make_conn()
    # 1. Bring the schema up to 061 (the verified single head BEFORE 062).
    _apply_migrations_through(conn, 61)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 61
    # The 062 columns must NOT exist yet.
    pre_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(relay_reply_opportunities)"
    ).fetchall()}
    assert "status" not in pre_cols
    assert "score" not in pre_cols

    # 2. Seed a REAL populated opportunity row (a legacy TG /flag-reply row) with
    #    a valid flagger_id + tweet_id so the FK chain holds.
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('tig', 'TIG', 'active')")
    conn.execute("INSERT INTO relay_clients (org_id) VALUES ('tig')")
    conn.execute("INSERT INTO relay_members (display_name) VALUES ('flagger')")
    flagger_id = conn.execute(
        "SELECT id FROM relay_members WHERE display_name='flagger'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO relay_tweets (x_id, x_author_handle, text) "
        "VALUES ('999', 'someone', 'legacy flagged tweet')"
    )
    tweet_id = conn.execute(
        "SELECT id FROM relay_tweets WHERE x_id='999'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO relay_reply_opportunities "
        "(org_id, tweet_id, flagger_id, origin, note) "
        "VALUES ('tig', ?, ?, 'explicit_command', 'pre-062 legacy flag')",
        (tweet_id, flagger_id),
    )
    conn.commit()
    opp_id = conn.execute(
        "SELECT id FROM relay_reply_opportunities WHERE note='pre-062 legacy flag'"
    ).fetchone()["id"]

    # 3. Apply 062 on the POPULATED table.
    _apply_migrations_through(conn, 62)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 62

    # 4a. Additive columns now exist.
    post_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(relay_reply_opportunities)"
    ).fetchall()}
    for expected in ("score", "score_reason", "suggested_angle", "status",
                     "expires_at", "sweep_source"):
        assert expected in post_cols, f"missing additive column '{expected}'"

    # 4b. The pre-existing row survived intact (data not lost — no rebuild).
    row = conn.execute(
        "SELECT org_id, tweet_id, flagger_id, origin, note, status, score "
        "FROM relay_reply_opportunities WHERE id=?",
        (opp_id,),
    ).fetchone()
    assert row is not None, "the seeded row was lost across the 062 upgrade"
    assert row["org_id"] == "tig"
    assert row["tweet_id"] == tweet_id
    assert row["flagger_id"] == flagger_id
    assert row["origin"] == "explicit_command"
    assert row["note"] == "pre-062 legacy flag"
    # 4c. status BACKFILLED to 'active' on the populated row; score still NULL.
    assert row["status"] == "active", "status must backfill to 'active' on existing rows"
    assert row["score"] is None

    # 4d. The 5 new tables now exist.
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for t in ("relay_opportunity_operator_state", "relay_opportunity_feedback",
              "relay_sweep_config", "relay_sweep_cursor", "relay_operator_heartbeat"):
        assert t in tables, f"new table '{t}' not created by 062"


# ---------------------------------------------------------------------------
# Migration 063 — reply-learning (additive tell-score + embedding cache columns)
# ---------------------------------------------------------------------------

def test_migration_063_additive_columns_exist():
    """Migration 063: the 2+2 additive columns are present on a fresh DB."""
    conn = _make_conn()
    ensure_schema(conn)

    sugg_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(reply_suggestions)"
    ).fetchall()}
    for expected in ("tell_score", "tell_flags_json"):
        assert expected in sugg_cols, f"reply_suggestions missing '{expected}'"

    tweet_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(relay_tweets)"
    ).fetchall()}
    for expected in ("embedding_json", "embedding_model"):
        assert expected in tweet_cols, f"relay_tweets missing '{expected}'"


def test_migration_063_adds_no_new_tables():
    """Migration 063 is purely ADD COLUMN — it must not introduce any table."""
    conn = _make_conn()
    _apply_migrations_through(conn, 62)
    before = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    _apply_migrations_through(conn, 63)
    after = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert after == before, f"063 unexpectedly created tables: {after - before}"


def test_migration_063_dual_migration_seed_then_upgrade():
    """HARD requirement (plan §3 / §8 / §10.4): POPULATED reply_suggestions +
    relay_tweets on the PRE-063 schema must survive the 063 upgrade — the 4 new
    columns appear (NULL on seeded rows), the seeded data is intact, and the old
    columns are untouched.

    The seed-then-upgrade dual-migration parity check (not just an empty-DB run):
    it proves 063 is genuinely additive (no rebuild on either table).
    """
    conn = _make_conn()
    # 1. Bring the schema up to 062 (the verified single head BEFORE 063).
    _apply_migrations_through(conn, 62)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 62
    # The 063 columns must NOT exist yet.
    pre_sugg = {r[1] for r in conn.execute(
        "PRAGMA table_info(reply_suggestions)"
    ).fetchall()}
    assert "tell_score" not in pre_sugg
    assert "tell_flags_json" not in pre_sugg
    pre_tweet = {r[1] for r in conn.execute(
        "PRAGMA table_info(relay_tweets)"
    ).fetchall()}
    assert "embedding_json" not in pre_tweet
    assert "embedding_model" not in pre_tweet

    # 2. Seed REAL populated rows on the pre-063 schema.
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('tig', 'TIG', 'active')")
    conn.execute(
        "INSERT INTO reply_suggestions "
        "(id, operator_handle, org_id, source_tweet_id, source_author, "
        " source_text, variants_json, model, cost_usd, opportunity_id, "
        " source_conversation_id) "
        "VALUES ('sug-pre63', '@arf', 'tig', '777', 'someone', 'pre-063 text', "
        "        '[{\"text\":\"hi\"}]', 'claude', 0.01, 9, 'conv-9')"
    )
    conn.execute(
        "INSERT INTO relay_tweets "
        "(x_id, x_author_handle, text, engagement_json, lang, author_followers) "
        "VALUES ('tw-pre63', 'alice', 'pre-063 tweet', '{\"likes\":5}', 'en', 42)"
    )
    conn.commit()

    # 3. Apply 063 on the POPULATED tables.
    _apply_migrations_through(conn, 63)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 63

    # 4a. The 4 additive columns now exist.
    post_sugg = {r[1] for r in conn.execute(
        "PRAGMA table_info(reply_suggestions)"
    ).fetchall()}
    assert {"tell_score", "tell_flags_json"} <= post_sugg
    post_tweet = {r[1] for r in conn.execute(
        "PRAGMA table_info(relay_tweets)"
    ).fetchall()}
    assert {"embedding_json", "embedding_model"} <= post_tweet

    # 4b. The seeded reply_suggestions row survived intact; new cols NULL.
    srow = conn.execute(
        "SELECT operator_handle, org_id, source_tweet_id, source_text, cost_usd, "
        "       opportunity_id, source_conversation_id, tell_score, tell_flags_json "
        "FROM reply_suggestions WHERE id = 'sug-pre63'"
    ).fetchone()
    assert srow is not None, "seeded suggestion lost across the 063 upgrade"
    assert srow["operator_handle"] == "@arf"
    assert srow["org_id"] == "tig"
    assert srow["source_tweet_id"] == "777"
    assert srow["source_text"] == "pre-063 text"
    assert srow["opportunity_id"] == 9
    assert srow["source_conversation_id"] == "conv-9"
    assert srow["tell_score"] is None
    assert srow["tell_flags_json"] is None

    # 4c. The seeded relay_tweets row survived intact; new cols NULL.
    trow = conn.execute(
        "SELECT x_id, x_author_handle, text, engagement_json, lang, "
        "       author_followers, embedding_json, embedding_model "
        "FROM relay_tweets WHERE x_id = 'tw-pre63'"
    ).fetchone()
    assert trow is not None, "seeded tweet lost across the 063 upgrade"
    assert trow["x_author_handle"] == "alice"
    assert trow["text"] == "pre-063 tweet"
    assert trow["engagement_json"] == '{"likes":5}'
    assert trow["lang"] == "en"
    assert trow["author_followers"] == 42
    assert trow["embedding_json"] is None
    assert trow["embedding_model"] is None


def test_migration_066_additive_column_exists():
    """Migration 066: reply_outcomes gains media_content_id on a fresh DB."""
    conn = _make_conn()
    ensure_schema(conn)

    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(reply_outcomes)"
    ).fetchall()}
    assert "media_content_id" in cols, "reply_outcomes missing 'media_content_id'"


def test_migration_066_new_tables_columns():
    """Migration 066: the 3 new tables exist with the contracted columns/PKs."""
    conn = _make_conn()
    ensure_schema(conn)

    ev_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(media_rec_events)"
    ).fetchall()}
    assert {
        "id", "org_id", "operator_handle", "tweet_ref", "slate_json",
        "chosen_content_id", "applied", "created_at",
    } <= ev_cols

    # media_quality composite (org_id, content_id) PK
    q_info = conn.execute("PRAGMA table_info(media_quality)").fetchall()
    q_cols = {r[1] for r in q_info}
    assert {"org_id", "content_id", "elo", "n_offered", "n_chosen", "updated_at"} <= q_cols
    q_pk = {r[1] for r in q_info if r[5]}  # r[5] = pk position (non-zero => part of PK)
    assert q_pk == {"org_id", "content_id"}, f"media_quality PK should be composite, got {q_pk}"

    # media_embeddings composite (org_id, content_id) PK
    e_info = conn.execute("PRAGMA table_info(media_embeddings)").fetchall()
    e_cols = {r[1] for r in e_info}
    assert {"org_id", "content_id", "embedding_json", "embedding_model", "updated_at"} <= e_cols
    e_pk = {r[1] for r in e_info if r[5]}
    assert e_pk == {"org_id", "content_id"}, f"media_embeddings PK should be composite, got {e_pk}"


def test_migration_066_unapplied_index_exists():
    """Migration 066: the partial-scan index for the Elo sweep is present."""
    conn = _make_conn()
    ensure_schema(conn)
    idx = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "ix_media_rec_events_unapplied" in idx


def test_migration_066_dual_migration_seed_then_upgrade():
    """HARD requirement: a POPULATED reply_outcomes row on the PRE-066 schema must
    survive the 066 upgrade — media_content_id appears (NULL on the seeded row),
    the seeded data is intact, and the old columns are untouched (additive, no
    table rebuild). reply_outcomes FK -> reply_suggestions, so a parent suggestion
    is seeded first.
    """
    conn = _make_conn()
    # 1. Bring the schema up to 065 (the verified single head BEFORE 066).
    _apply_migrations_through(conn, 65)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 65
    pre_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(reply_outcomes)"
    ).fetchall()}
    assert "media_content_id" not in pre_cols

    # 2. Seed REAL populated rows on the pre-066 schema (suggestion -> outcome).
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('tig', 'TIG', 'active')")
    conn.execute(
        "INSERT INTO reply_suggestions "
        "(id, operator_handle, org_id, source_tweet_id, variants_json) "
        "VALUES ('sug-pre66', '@arf', 'tig', '777', '[{\"text\":\"hi\"}]')"
    )
    conn.execute(
        "INSERT INTO reply_outcomes "
        "(id, suggestion_id, posted_tweet_id, posted_at, chosen_variant_idx, "
        " was_edited, engagement_json) "
        "VALUES ('out-pre66', 'sug-pre66', '888', '2026-06-06T00:00:00Z', 0, 0, "
        "        '{\"total\":5}')"
    )
    conn.commit()

    # 3. Apply 066 on the POPULATED table.
    _apply_migrations_through(conn, 66)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 66

    # 4a. The additive column now exists.
    post_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(reply_outcomes)"
    ).fetchall()}
    assert "media_content_id" in post_cols

    # 4b. The seeded outcome row survived intact; the new column is NULL.
    orow = conn.execute(
        "SELECT suggestion_id, posted_tweet_id, posted_at, chosen_variant_idx, "
        "       was_edited, engagement_json, media_content_id "
        "FROM reply_outcomes WHERE id = 'out-pre66'"
    ).fetchone()
    assert orow is not None, "seeded outcome lost across the 066 upgrade"
    assert orow["suggestion_id"] == "sug-pre66"
    assert orow["posted_tweet_id"] == "888"
    assert orow["posted_at"] == "2026-06-06T00:00:00Z"
    assert orow["engagement_json"] == '{"total":5}'
    assert orow["media_content_id"] is None


# ---------------------------------------------------------------------------
# Migration 068 — make relay_opportunity_feedback.opportunity_id NULLABLE
# (per-variant gen-quality thumbs on freeform drafts: a suggestion_id but no
#  feed-sourced opportunity). Leaf-table rebuild.
# ---------------------------------------------------------------------------

def test_migration_068_opportunity_id_nullable_on_fresh_db():
    """Migration 068: on a fresh DB, relay_opportunity_feedback.opportunity_id is
    NULLABLE (the whole point — freeform-draft variant thumbs have no opportunity).
    """
    conn = _make_conn()
    ensure_schema(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 78

    # PRAGMA table_info: row = (cid, name, type, notnull, dflt_value, pk)
    cols = {
        r[1]: r for r in conn.execute(
            "PRAGMA table_info(relay_opportunity_feedback)"
        ).fetchall()
    }
    assert cols["opportunity_id"][3] == 0, (
        "opportunity_id must be NULLABLE (notnull flag must be 0) after migration 068"
    )
    # The index survived the leaf rebuild.
    fb_idx = {r[1] for r in conn.execute(
        "PRAGMA index_list(relay_opportunity_feedback)"
    ).fetchall()}
    assert "ix_relay_opportunity_feedback_opp" in fb_idx


def test_migration_068_null_opportunity_insert_succeeds():
    """THE POINT (mig 068): a freeform-draft variant thumb — opportunity_id NULL but
    a suggestion_id SET — must INSERT successfully on the post-068 schema. The
    suggestion_id FK to reply_suggestions is still enforced (foreign_keys=ON).
    """
    conn = _make_conn()
    ensure_schema(conn)

    # Parent suggestion (freeform draft — no opportunity row exists for it).
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('tig', 'TIG', 'active')")
    conn.execute(
        "INSERT INTO reply_suggestions "
        "(id, operator_handle, org_id, source_tweet_id, variants_json) "
        "VALUES ('sug-freeform-68', '@arf', 'tig', '999', '[{\"text\":\"gm\"}]')"
    )
    conn.commit()

    # opportunity_id NULL + suggestion_id SET — this is the freeform-draft thumb.
    conn.execute(
        "INSERT INTO relay_opportunity_feedback "
        "(opportunity_id, suggestion_id, rater_handle, rater_role, thumb) "
        "VALUES (NULL, 'sug-freeform-68', '@arf', 'operator', 1)"
    )
    conn.commit()

    row = conn.execute(
        "SELECT opportunity_id, suggestion_id, rater_handle, rater_role, thumb "
        "FROM relay_opportunity_feedback WHERE suggestion_id = 'sug-freeform-68'"
    ).fetchone()
    assert row is not None, "freeform-draft thumb (NULL opportunity_id) failed to insert"
    assert row["opportunity_id"] is None
    assert row["suggestion_id"] == "sug-freeform-68"
    assert row["thumb"] == 1

    # The suggestion_id FK is still live: a thumb pointing at a non-existent
    # suggestion (and NULL opportunity_id) must be rejected.
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO relay_opportunity_feedback "
            "(opportunity_id, suggestion_id, rater_handle, rater_role, thumb) "
            "VALUES (NULL, 'sug-does-not-exist', '@arf', 'operator', -1)"
        )


def test_migration_068_dual_migration_seed_then_upgrade():
    """HARD requirement: a POPULATED relay_opportunity_feedback row on the PRE-068
    schema (opportunity_id NOT NULL there) must survive the leaf-table rebuild that
    068 performs — data intact, opportunity_id flips to NULLABLE, and a NULL insert
    that the pre-068 NOT-NULL constraint REJECTED now succeeds.
    """
    conn = _make_conn()
    # 1. Bring the schema up to 067 (the verified single head BEFORE 068).
    _apply_migrations_through(conn, 67)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 67

    # Pre-068, opportunity_id is NOT NULL.
    pre = {
        r[1]: r for r in conn.execute(
            "PRAGMA table_info(relay_opportunity_feedback)"
        ).fetchall()
    }
    assert pre["opportunity_id"][3] == 1, "pre-068 opportunity_id must be NOT NULL"

    # 2. Seed a REAL populated feed-sourced thumb (the full FK chain to an
    #    opportunity row + a suggestion row).
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('tig', 'TIG', 'active')")
    conn.execute("INSERT INTO relay_clients (org_id) VALUES ('tig')")
    conn.execute("INSERT INTO relay_members (display_name) VALUES ('flagger')")
    flagger_id = conn.execute(
        "SELECT id FROM relay_members WHERE display_name='flagger'"
    ).fetchone()["id"]
    # relay_reply_opportunities.tweet_id FK -> relay_tweets.id (integer PK), NOT NULL.
    conn.execute(
        "INSERT INTO relay_tweets (x_id, x_author_handle, text) "
        "VALUES ('111', 'someone', 'feed-sourced tweet')"
    )
    tweet_id = conn.execute(
        "SELECT id FROM relay_tweets WHERE x_id='111'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO relay_reply_opportunities (org_id, tweet_id, flagger_id, origin) "
        "VALUES ('tig', ?, ?, 'explicit_command')",
        (tweet_id, flagger_id),
    )
    opp_id = conn.execute(
        "SELECT id FROM relay_reply_opportunities WHERE tweet_id=?",
        (tweet_id,),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO reply_suggestions "
        "(id, operator_handle, org_id, source_tweet_id, variants_json) "
        "VALUES ('sug-pre68', '@arf', 'tig', '111', '[{\"text\":\"hi\"}]')"
    )
    conn.execute(
        "INSERT INTO relay_opportunity_feedback "
        "(opportunity_id, suggestion_id, rater_handle, rater_role, thumb) "
        "VALUES (?, 'sug-pre68', '@arf', 'operator', 1)",
        (opp_id,),
    )
    conn.commit()

    # Pre-068, a NULL opportunity_id insert is REJECTED by the NOT NULL constraint.
    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO relay_opportunity_feedback "
            "(opportunity_id, suggestion_id, rater_handle, rater_role, thumb) "
            "VALUES (NULL, 'sug-pre68', '@arf', 'operator', -1)"
        )
    conn.rollback()  # discard the failed-insert txn before applying 068

    # 3. Apply 068 (the leaf-table rebuild) on the POPULATED table.
    _apply_migrations_through(conn, 68)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 68

    # 4a. opportunity_id is now NULLABLE.
    post = {
        r[1]: r for r in conn.execute(
            "PRAGMA table_info(relay_opportunity_feedback)"
        ).fetchall()
    }
    assert post["opportunity_id"][3] == 0, "068 must flip opportunity_id to NULLABLE"

    # 4b. The seeded feed-sourced thumb survived the rebuild intact.
    seeded = conn.execute(
        "SELECT opportunity_id, suggestion_id, rater_handle, rater_role, thumb "
        "FROM relay_opportunity_feedback WHERE suggestion_id = 'sug-pre68'"
    ).fetchone()
    assert seeded is not None, "the seeded feedback row was lost across the 068 rebuild"
    assert seeded["opportunity_id"] == opp_id
    assert seeded["rater_role"] == "operator"
    assert seeded["thumb"] == 1

    # 4c. The previously-rejected NULL-opportunity insert now SUCCEEDS.
    conn.execute(
        "INSERT INTO relay_opportunity_feedback "
        "(opportunity_id, suggestion_id, rater_handle, rater_role, thumb) "
        "VALUES (NULL, 'sug-pre68', '@arf', 'operator', -1)"
    )
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM relay_opportunity_feedback "
        "WHERE opportunity_id IS NULL"
    ).fetchone()["n"] == 1


# ---------------------------------------------------------------------------
# Migration 069 — reply_outcomes.detected_via (auto-detect vs operator Mark-posted
# provenance). Additive ADD COLUMN, nullable; legacy rows stay NULL.
# ---------------------------------------------------------------------------

def test_migration_069_detected_via_on_fresh_db():
    """Migration 069: reply_outcomes gains a NULLABLE detected_via column; a posted
    reply can be stamped 'auto' (the scheduled detection job) or left NULL (legacy)."""
    conn = _make_conn()
    ensure_schema(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 78

    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(reply_outcomes)").fetchall()}
    assert "detected_via" in cols, "migration 069 must add reply_outcomes.detected_via"
    assert cols["detected_via"][3] == 0, "detected_via must be NULLABLE (notnull flag 0)"

    # Parent suggestion (FK target) + an auto-detected outcome + a legacy outcome.
    conn.execute("INSERT INTO orgs (org_id, display_name, status) VALUES ('tig', 'TIG', 'active')")
    conn.execute(
        "INSERT INTO reply_suggestions "
        "(id, operator_handle, org_id, source_tweet_id, variants_json) "
        "VALUES ('sug-69', '@CahitArf11', 'tig', '777', '[{\"text\":\"gm\"}]')"
    )
    conn.execute(
        "INSERT INTO reply_outcomes (id, suggestion_id, posted_tweet_id, detected_via) "
        "VALUES ('out-69a', 'sug-69', '888', 'auto')"
    )
    conn.execute(
        "INSERT INTO reply_outcomes (id, suggestion_id, posted_tweet_id) "
        "VALUES ('out-69b', 'sug-69', '889')"
    )
    conn.commit()

    rows = {
        r[0]: r[1] for r in conn.execute(
            "SELECT posted_tweet_id, detected_via FROM reply_outcomes WHERE suggestion_id='sug-69'"
        ).fetchall()
    }
    assert rows["888"] == "auto"
    assert rows["889"] is None  # legacy / un-stamped


def test_migration_069_partial_apply_reaches_69():
    """Applying through 069 lands at version 69 with the column present."""
    conn = _make_conn()
    _apply_migrations_through(conn, 69)
    assert conn.execute("SELECT version FROM schema_version").fetchone()["version"] == 69
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reply_outcomes)").fetchall()}
    assert "detected_via" in cols
