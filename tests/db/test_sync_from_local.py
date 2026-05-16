"""Tests for sable_platform.db.sync_from_local.

Covers the SP-SYNC-LAPTOP-TO-PROD audit findings:
  - schema-version mismatch aborts cleanly
  - missing org on target aborts cleanly
  - source/target are independent connections (no SABLE_DATABASE_URL leak)
  - per-table writes are idempotent on re-run
  - entity_interactions does NOT double-count on re-sync
  - alerts table is not synced (per audit Tier-1 C2 / dedup-key conflict avoidance)
  - dry-run writes nothing
  - cursor advances and second run picks up only new rows
  - audit_log gets one summary row per non-dry-run sync
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event, text

from sable_platform.db.schema import metadata as sa_metadata
from sable_platform.db.sync_from_local import SyncError, sync_org


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine(url: str = "sqlite:///:memory:"):
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ARG001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    sa_metadata.create_all(engine)
    # schema_version row is required by sync_org.
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO schema_version (version) VALUES (43)"))
    return engine


def _seed_org(engine, org_id: str = "tig"):
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :n)"),
            {"o": org_id, "n": "Test Org"},
        )


def _seed_entity(engine, org_id: str, entity_id: str | None = None):
    eid = entity_id or uuid.uuid4().hex[:12]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO entities (entity_id, org_id, display_name, status, source,"
                " config_json, created_at, updated_at)"
                " VALUES (:eid, :org, 'Test Entity', 'candidate', 'auto', '{}',"
                " '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
            ),
            {"eid": eid, "org": org_id},
        )
    return eid


def _seed_interaction(engine, org_id, src, tgt, count, last_seen):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO entity_interactions (org_id, source_handle, target_handle,"
                " interaction_type, count, last_seen, run_date)"
                " VALUES (:o, :s, :t, 'reply', :c, :ls, '2026-05-10')"
            ),
            {"o": org_id, "s": src, "t": tgt, "c": count, "ls": last_seen},
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_schema_version_mismatch_aborts():
    src = _make_engine()
    tgt = _make_engine()
    # Bump source past target.
    with src.begin() as conn:
        conn.execute(text("UPDATE schema_version SET version = 44"))
    _seed_org(src)
    _seed_org(tgt)

    with pytest.raises(SyncError, match="Schema-version mismatch"):
        sync_org(src, tgt, "tig")


def test_missing_org_on_target_aborts():
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    # tgt has no orgs.

    with pytest.raises(SyncError, match="not found on target"):
        sync_org(src, tgt, "tig")


def test_postgres_source_rejected():
    src = _make_engine("sqlite:///:memory:")  # we use sqlite but pretend
    tgt = _make_engine()

    # Use a postgres URL string for the source name.
    # Simulating the check via a non-sqlite dialect would require pg available;
    # instead we directly call sync_org with a postgres-like engine attribute.
    class _FakePg:
        class dialect:  # noqa: N801
            name = "postgresql"
    with pytest.raises(SyncError, match="Source must be SQLite"):
        sync_org(_FakePg(), tgt, "tig")


def test_basic_org_sync_copies_entities_and_diagnostics():
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    eid = _seed_entity(src, "tig")
    with src.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO diagnostic_runs (org_id, run_type, status, cult_run_id,"
                " project_slug, run_date, overall_grade)"
                " VALUES ('tig', 'diagnostic', 'ok', 'cult-abc-1', 'tig', '2026-05-10', 'A')"
            )
        )

    report = sync_org(src, tgt, "tig")

    assert report.error is None
    table_results = {t.table: t for t in report.tables}
    assert table_results["entities"].rows_written == 1
    assert table_results["diagnostic_runs"].rows_written == 1

    with tgt.connect() as conn:
        n_entities = conn.execute(
            text("SELECT COUNT(*) FROM entities WHERE org_id='tig'")
        ).scalar()
        assert n_entities == 1
        cult = conn.execute(
            text("SELECT cult_run_id FROM diagnostic_runs WHERE org_id='tig'")
        ).scalar()
        assert cult == "cult-abc-1"
        # Audit summary stamped.
        audit_n = conn.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE action='sync_from_local'"
                 " AND source='sync-from-local' AND org_id='tig'")
        ).scalar()
        assert audit_n == 1


def test_second_run_is_idempotent_no_duplicates():
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    _seed_entity(src, "tig", entity_id="ent_one")
    with src.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO diagnostic_runs (org_id, run_type, status, cult_run_id,"
                " run_date) VALUES ('tig', 'diagnostic', 'ok', 'cult-x', '2026-05-10')"
            )
        )

    sync_org(src, tgt, "tig")
    sync_org(src, tgt, "tig")  # run again

    with tgt.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM entities")).scalar() == 1
        assert (
            conn.execute(text("SELECT COUNT(*) FROM diagnostic_runs")).scalar()
            == 1
        )


def test_entity_interactions_does_not_double_count():
    """Audit finding: sync_interaction_edges() helper is count-additive;
    sync must use raw INSERT/UPDATE that REPLACES count, not increments it."""
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    _seed_interaction(src, "tig", "alice", "bob", count=5, last_seen="2026-05-10T00:00:00")

    sync_org(src, tgt, "tig")
    sync_org(src, tgt, "tig")

    with tgt.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count FROM entity_interactions WHERE org_id='tig'"
                " AND source_handle='alice' AND target_handle='bob'"
            )
        ).scalar()
        assert count == 5, f"interactions should equal source count, got {count}"


def test_interaction_update_picks_up_new_count():
    """If local increments count to 8 between syncs, target should reflect 8."""
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    _seed_interaction(src, "tig", "alice", "bob", count=5, last_seen="2026-05-10T00:00:00")
    sync_org(src, tgt, "tig")

    with src.begin() as conn:
        conn.execute(
            text(
                "UPDATE entity_interactions SET count=8, last_seen='2026-05-11T00:00:00'"
                " WHERE source_handle='alice' AND target_handle='bob'"
            )
        )
    sync_org(src, tgt, "tig")

    with tgt.connect() as conn:
        count = conn.execute(
            text("SELECT count FROM entity_interactions WHERE source_handle='alice'")
        ).scalar()
        assert count == 8


def test_dry_run_writes_nothing():
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    _seed_entity(src, "tig")
    with src.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO diagnostic_runs (org_id, run_type, status, cult_run_id,"
                " run_date) VALUES ('tig', 'diagnostic', 'ok', 'cult-dry', '2026-05-10')"
            )
        )

    report = sync_org(src, tgt, "tig", dry_run=True)

    with tgt.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM entities")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM diagnostic_runs")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM audit_log")).scalar() == 0
    # dry-run report still surfaces the would-write counts.
    by_table = {t.table: t for t in report.tables}
    assert by_table["entities"].rows_written == 1
    assert by_table["diagnostic_runs"].rows_written == 1


def test_diagnostic_runs_natural_key_dedup_via_cult_run_id():
    """If target already has a run with the same cult_run_id, source UPDATE
    must hit it (no duplicate row, no PK collision)."""
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    with src.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO diagnostic_runs (org_id, run_type, status, cult_run_id,"
                " run_date, overall_grade) VALUES ('tig', 'diagnostic', 'completed',"
                " 'cult-shared', '2026-05-10', 'A+')"
            )
        )
    # Target already has the row from a different sync path.
    with tgt.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO diagnostic_runs (org_id, run_type, status, cult_run_id,"
                " run_date, overall_grade) VALUES ('tig', 'diagnostic', 'pending',"
                " 'cult-shared', '2026-05-10', 'B')"
            )
        )

    sync_org(src, tgt, "tig")

    with tgt.connect() as conn:
        rows = conn.execute(
            text("SELECT cult_run_id, status, overall_grade FROM diagnostic_runs")
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "completed"
        assert rows[0][2] == "A+"


def test_entity_tags_full_state_replace():
    """entity_tags rows mutate (is_current, deactivated_at). Sync wipes
    org-scoped state and re-copies."""
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    eid = _seed_entity(src, "tig", entity_id="ent_a")
    _seed_entity(tgt, "tig", entity_id="ent_a")
    # Source has the current tag.
    with src.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO entity_tags (entity_id, tag, source, confidence,"
                " is_current, added_at) VALUES (:eid, 'top_contributor', 'cult',"
                " 0.9, 1, '2026-05-10')"
            ),
            {"eid": eid},
        )
    # Target previously had a stale tag (later deactivated locally).
    with tgt.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO entity_tags (entity_id, tag, is_current, added_at)"
                " VALUES (:eid, 'old_stale_tag', 1, '2026-01-01')"
            ),
            {"eid": eid},
        )

    sync_org(src, tgt, "tig")

    with tgt.connect() as conn:
        tags = [
            r[0] for r in conn.execute(
                text("SELECT tag FROM entity_tags WHERE entity_id=:eid"),
                {"eid": eid},
            ).fetchall()
        ]
        assert tags == ["top_contributor"]


def test_alerts_table_not_synced():
    """Per audit: alerts MUST NOT be copied. dedup_key conflicts and
    Cult Grader doesn't create alerts."""
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    with src.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO alerts (alert_id, org_id, alert_type, severity, title,"
                " status, dedup_key) VALUES ('a1', 'tig', 'test', 'warning',"
                " 'Test alert', 'new', 'dedup:tig:1')"
            )
        )

    report = sync_org(src, tgt, "tig")

    assert "alerts" not in {t.table for t in report.tables}
    with tgt.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM alerts")).scalar() == 0


def test_cost_events_cursor_advances_only_new_rows_on_resync():
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    with src.begin() as conn:
        for i, ts in enumerate(["2026-05-01T00:00:00", "2026-05-02T00:00:00"]):
            conn.execute(
                text(
                    "INSERT INTO cost_events (org_id, call_type, cost_usd, created_at)"
                    " VALUES ('tig', 'llm', :c, :t)"
                ),
                {"c": 0.1 * (i + 1), "t": ts},
            )

    sync_org(src, tgt, "tig")

    # Add a new row on source post-cursor.
    with src.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO cost_events (org_id, call_type, cost_usd, created_at)"
                " VALUES ('tig', 'llm', 0.3, '2026-05-03T00:00:00')"
            )
        )
    report = sync_org(src, tgt, "tig")
    by = {t.table: t for t in report.tables}
    # First sync wrote 2; this second sync should write only 1.
    assert by["cost_events"].rows_written == 1
    with tgt.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM cost_events")).scalar()
        assert n == 3


def test_cost_events_scrubs_unknown_job_id():
    """cost_events.job_id FKs to jobs; if target doesn't have the job, set NULL."""
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    with src.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (job_id, org_id, job_type) VALUES"
                " ('local-job-1', 'tig', 'cult_grader')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO cost_events (org_id, job_id, call_type, cost_usd,"
                " created_at) VALUES ('tig', 'local-job-1', 'llm', 0.5,"
                " '2026-05-10T00:00:00')"
            )
        )

    sync_org(src, tgt, "tig")

    with tgt.connect() as conn:
        job_id = conn.execute(text("SELECT job_id FROM cost_events")).scalar()
        assert job_id is None  # scrubbed


def test_since_filter_lower_bound():
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    with src.begin() as conn:
        for ts in ["2026-04-01T00:00:00", "2026-05-10T00:00:00"]:
            conn.execute(
                text(
                    "INSERT INTO cost_events (org_id, call_type, cost_usd, created_at)"
                    " VALUES ('tig', 'llm', 0.1, :t)"
                ),
                {"t": ts},
            )

    sync_org(src, tgt, "tig", since="2026-05-01T00:00:00")

    with tgt.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM cost_events")).scalar()
        assert n == 1


def test_workflow_tables_not_synced():
    """workflow_runs/steps/events are excluded — local workflow state is
    not authoritative for prod."""
    src = _make_engine()
    tgt = _make_engine()
    _seed_org(src)
    _seed_org(tgt)
    with src.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workflow_runs (run_id, org_id, workflow_name, status)"
                " VALUES ('wf-1', 'tig', 'test_wf', 'completed')"
            )
        )

    report = sync_org(src, tgt, "tig")
    assert "workflow_runs" not in {t.table for t in report.tables}
    with tgt.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM workflow_runs")).scalar()
        assert n == 0
