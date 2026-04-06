"""Tests for SP-AUTH: operator identity tracking."""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from sable_platform.db.connection import ensure_schema
from sable_platform.db.audit import log_audit, list_audit_log
from sable_platform.db.workflow_store import create_workflow_run


@pytest.fixture
def auth_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('auth_org', 'Auth Org')")
    conn.commit()
    return conn


class TestWorkflowRunOperatorId:
    def test_operator_id_set_from_env(self, auth_db):
        with patch.dict("os.environ", {"SABLE_OPERATOR_ID": "alice"}):
            run_id = create_workflow_run(
                auth_db, "auth_org", "test_wf", "1.0", {}
            )
        row = auth_db.execute(
            "SELECT operator_id FROM workflow_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        assert row["operator_id"] == "alice"

    def test_operator_id_unknown_when_unset(self, auth_db):
        with patch.dict("os.environ", {}, clear=True):
            run_id = create_workflow_run(
                auth_db, "auth_org", "test_wf", "1.0", {}
            )
        row = auth_db.execute(
            "SELECT operator_id FROM workflow_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        assert row["operator_id"] == "unknown"


class TestAuditLogActorResolution:
    def test_audit_actor_from_env_when_unknown(self, auth_db):
        """'unknown' actor is resolved to SABLE_OPERATOR_ID."""
        with patch.dict("os.environ", {"SABLE_OPERATOR_ID": "bob"}):
            log_audit(auth_db, "unknown", "test_action", org_id="auth_org")
        rows = list_audit_log(auth_db, org_id="auth_org")
        assert rows[0]["actor"] == "bob"

    def test_audit_actor_cli_preserved(self, auth_db):
        """'cli' and 'system' are meaningful — NOT overridden by env."""
        with patch.dict("os.environ", {"SABLE_OPERATOR_ID": "bob"}):
            log_audit(auth_db, "cli", "test_action", org_id="auth_org")
        rows = list_audit_log(auth_db, org_id="auth_org")
        assert rows[0]["actor"] == "cli"

    def test_audit_actor_preserved_when_specific(self, auth_db):
        with patch.dict("os.environ", {"SABLE_OPERATOR_ID": "bob"}):
            log_audit(auth_db, "custom_actor", "test_action", org_id="auth_org")
        rows = list_audit_log(auth_db, org_id="auth_org")
        assert rows[0]["actor"] == "custom_actor"

    def test_audit_actor_unknown_when_no_env(self, auth_db):
        with patch.dict("os.environ", {}, clear=True):
            log_audit(auth_db, "unknown", "test_action", org_id="auth_org")
        rows = list_audit_log(auth_db, org_id="auth_org")
        assert rows[0]["actor"] == "unknown"


class TestEntityTagsIndex:
    def test_compound_index_exists(self, auth_db):
        """Migration 024 creates idx_entity_tags_tag_current."""
        row = auth_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_entity_tags_tag_current'"
        ).fetchone()
        assert row is not None
