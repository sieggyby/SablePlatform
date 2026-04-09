"""Tests for SQLite -> Postgres data migration (sable_platform.db.migrate_pg).

All tests use two in-memory SQLite engines to exercise the core migration
logic.  Postgres-specific codepaths (DISABLE TRIGGER ALL, setval) are
skipped when the target is SQLite — those require manual integration testing
against a real Postgres instance.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import Integer, create_engine, event, text

from sable_platform.db.migrate_pg import (
    BATCH_SIZE,
    MigrationError,
    MigrationReport,
    SEQUENCE_TABLES,
    TABLE_LOAD_ORDER,
    _check_target_empty,
    _insert_batch,
    _read_all_rows,
    _validate_counts,
    run_migration,
)
from sable_platform.db.schema import metadata


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_engine():
    """Create an in-memory SQLite engine with full schema and FK enforcement."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):  # noqa: ARG001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(engine)
    return engine


@pytest.fixture
def source_engine():
    engine = _make_engine()
    yield engine
    engine.dispose()


@pytest.fixture
def target_engine():
    engine = _make_engine()
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_org(engine, org_id="test_org"):
    with engine.begin() as conn:
        conn.execute(text(
            'INSERT INTO orgs (org_id, display_name) VALUES (:oid, :name)'
        ), {"oid": org_id, "name": "Test Org"})
    return org_id


def _insert_entity(engine, org_id, entity_id=None):
    eid = entity_id or f"ent_{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(text(
            'INSERT INTO entities (entity_id, org_id, display_name) '
            'VALUES (:eid, :oid, :name)'
        ), {"eid": eid, "oid": org_id, "name": "Test Entity"})
    return eid


def _insert_handle(engine, entity_id, platform="twitter", handle="@test"):
    with engine.begin() as conn:
        conn.execute(text(
            'INSERT INTO entity_handles (entity_id, platform, handle) '
            'VALUES (:eid, :plat, :handle)'
        ), {"eid": entity_id, "plat": platform, "handle": handle})


def _insert_content_item(engine, org_id, entity_id, item_id=None):
    iid = item_id or f"item_{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(text(
            'INSERT INTO content_items (item_id, org_id, entity_id) '
            'VALUES (:iid, :oid, :eid)'
        ), {"iid": iid, "oid": org_id, "eid": entity_id})
    return iid


def _insert_job(engine, org_id, job_id=None):
    jid = job_id or f"job_{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(text(
            'INSERT INTO jobs (job_id, org_id, job_type) VALUES (:jid, :oid, :jtype)'
        ), {"jid": jid, "oid": org_id, "jtype": "test"})
    return jid


def _insert_workflow_run(engine, org_id, run_id=None):
    rid = run_id or f"run_{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(text(
            'INSERT INTO workflow_runs (run_id, org_id, workflow_name, workflow_version) '
            'VALUES (:rid, :oid, :wf, :ver)'
        ), {"rid": rid, "oid": org_id, "wf": "test_wf", "ver": "1.0"})
    return rid


def _insert_action(engine, org_id, entity_id=None, content_item_id=None, action_id=None):
    aid = action_id or f"act_{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(text(
            'INSERT INTO actions (action_id, org_id, entity_id, content_item_id, title) '
            'VALUES (:aid, :oid, :eid, :cid, :title)'
        ), {"aid": aid, "oid": org_id, "eid": entity_id, "cid": content_item_id, "title": "Test"})
    return aid


def _insert_alert(engine, org_id, entity_id=None, action_id=None, run_id=None):
    alert_id = f"alert_{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(text(
            'INSERT INTO alerts (alert_id, org_id, entity_id, action_id, run_id, '
            'alert_type, severity, title) '
            'VALUES (:aid, :oid, :eid, :actid, :rid, :atype, :sev, :title)'
        ), {
            "aid": alert_id, "oid": org_id, "eid": entity_id,
            "actid": action_id, "rid": run_id,
            "atype": "test", "sev": "info", "title": "Test Alert",
        })
    return alert_id


def _count_rows(engine, table_name):
    with engine.connect() as conn:
        return conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigrateEmptyDB:
    def test_succeeds_with_zero_rows(self, source_engine, target_engine):
        report = run_migration(source_engine, target_engine)
        assert report.status == "success"
        assert report.total_source_rows == 0
        assert report.total_target_rows == 0
        assert all(tr.status in ("skipped", "ok") for tr in report.tables)


class TestMigrateWithFKChain:
    def test_full_fk_chain_copies(self, source_engine, target_engine):
        """org -> entity -> handle + content_item -> action -> alert."""
        org_id = _insert_org(source_engine)
        eid = _insert_entity(source_engine, org_id)
        _insert_handle(source_engine, eid)
        cid = _insert_content_item(source_engine, org_id, eid)
        jid = _insert_job(source_engine, org_id)
        rid = _insert_workflow_run(source_engine, org_id)
        aid = _insert_action(source_engine, org_id, entity_id=eid, content_item_id=cid)
        _insert_alert(source_engine, org_id, entity_id=eid, action_id=aid, run_id=rid)

        report = run_migration(source_engine, target_engine)
        assert report.status == "success"

        # Verify key tables
        assert _count_rows(target_engine, "orgs") == 1
        assert _count_rows(target_engine, "entities") == 1
        assert _count_rows(target_engine, "entity_handles") == 1
        assert _count_rows(target_engine, "content_items") == 1
        assert _count_rows(target_engine, "actions") == 1
        assert _count_rows(target_engine, "alerts") == 1
        assert _count_rows(target_engine, "jobs") == 1
        assert _count_rows(target_engine, "workflow_runs") == 1


class TestMigrateRefusesNonemptyTarget:
    def test_raises_without_force(self, source_engine, target_engine):
        _insert_org(source_engine)
        _insert_org(target_engine, org_id="existing_org")

        with pytest.raises(MigrationError, match="not empty"):
            run_migration(source_engine, target_engine)

    def test_force_truncates_and_copies(self, source_engine, target_engine):
        _insert_org(source_engine, org_id="source_org")
        _insert_org(target_engine, org_id="existing_org")

        report = run_migration(source_engine, target_engine, force=True)
        assert report.status == "success"
        assert _count_rows(target_engine, "orgs") == 1

        # Verify the source org made it, not the old target org
        with target_engine.connect() as conn:
            row = conn.execute(text("SELECT org_id FROM orgs")).fetchone()
            assert row[0] == "source_org"


class TestMigrateBatchSize:
    def test_large_table_batches_correctly(self, source_engine, target_engine):
        org_id = _insert_org(source_engine)
        count = BATCH_SIZE * 2 + 500  # 2500 rows
        with source_engine.begin() as conn:
            for i in range(count):
                conn.execute(text(
                    'INSERT INTO cost_events (org_id, call_type, cost_usd) '
                    'VALUES (:oid, :ct, :cost)'
                ), {"oid": org_id, "ct": f"test_{i}", "cost": 0.01})

        report = run_migration(source_engine, target_engine)
        assert report.status == "success"
        assert _count_rows(target_engine, "cost_events") == count


class TestTableLoadOrderCoversAll:
    def test_all_tables_present(self):
        schema_tables = set(metadata.tables.keys())
        load_order_tables = set(TABLE_LOAD_ORDER)
        assert load_order_tables == schema_tables, (
            f"Missing from TABLE_LOAD_ORDER: {schema_tables - load_order_tables}\n"
            f"Extra in TABLE_LOAD_ORDER: {load_order_tables - schema_tables}"
        )


class TestSequenceTablesMatchAutoincrement:
    def test_all_autoincrement_tables_covered(self):
        autoincrement_tables: dict[str, str] = {}
        for table_name, table in metadata.tables.items():
            for col in table.columns:
                if col.primary_key and col.autoincrement is True and isinstance(col.type, Integer):
                    autoincrement_tables[table_name] = col.name
                    break

        assert autoincrement_tables == SEQUENCE_TABLES, (
            f"Missing from SEQUENCE_TABLES: "
            f"{set(autoincrement_tables) - set(SEQUENCE_TABLES)}\n"
            f"Extra in SEQUENCE_TABLES: "
            f"{set(SEQUENCE_TABLES) - set(autoincrement_tables)}"
        )


class TestMigrateRollbackOnError:
    def test_target_stays_empty_on_failure(self, source_engine, target_engine):
        _insert_org(source_engine)

        with patch(
            "sable_platform.db.migrate_pg._insert_batch",
            side_effect=RuntimeError("injected failure"),
        ):
            with pytest.raises(MigrationError, match="injected failure"):
                run_migration(source_engine, target_engine)

        # Target should be empty — transaction rolled back
        assert _count_rows(target_engine, "orgs") == 0


class TestValidateCountsMismatch:
    def test_detects_mismatch(self, source_engine, target_engine):
        _insert_org(source_engine, org_id="org_a")
        _insert_org(source_engine, org_id="org_b")
        # Target has only one org (simulating a partial copy)
        _insert_org(target_engine, org_id="org_a")

        results = _validate_counts(source_engine, target_engine, ["orgs"])
        assert len(results) == 1
        assert results[0].status == "error"
        assert results[0].source_rows == 2
        assert results[0].target_rows == 1


class TestCheckTargetEmpty:
    def test_empty_returns_true(self, target_engine):
        assert _check_target_empty(target_engine, TABLE_LOAD_ORDER) is True

    def test_nonempty_returns_false(self, target_engine):
        _insert_org(target_engine)
        assert _check_target_empty(target_engine, TABLE_LOAD_ORDER) is False


class TestReadAllRows:
    def test_reads_all(self, source_engine):
        _insert_org(source_engine, org_id="a")
        _insert_org(source_engine, org_id="b")
        rows = _read_all_rows(source_engine, "orgs")
        assert len(rows) == 2
        assert all(isinstance(r, dict) for r in rows)
        org_ids = {r["org_id"] for r in rows}
        assert org_ids == {"a", "b"}


class TestAlembicUpgradeCalled:
    def test_alembic_called_for_postgres(self, source_engine):
        """Verify _run_alembic_upgrade is called when target is Postgres."""
        # We can't easily create a real Postgres engine in tests, so we
        # mock the target engine's dialect and verify the call.
        with patch("sable_platform.db.migrate_pg._run_alembic_upgrade") as mock_alembic, \
             patch("sable_platform.db.migrate_pg._check_target_empty", return_value=True):
            # Create a mock-ish target engine that claims to be PostgreSQL
            target = _make_engine()
            # Patch the dialect name
            with patch.object(target.dialect, "name", "postgresql"):
                try:
                    run_migration(source_engine, target)
                except Exception:
                    pass  # May fail on PG-specific SQL, but alembic should be called
                mock_alembic.assert_called_once()
            target.dispose()
