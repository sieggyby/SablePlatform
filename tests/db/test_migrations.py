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
}


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def test_fresh_db_reaches_current_version():
    conn = _make_conn()
    ensure_schema(conn)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row["version"] == 48


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
    assert row["version"] == 48


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
