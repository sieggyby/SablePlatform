"""Tests for sable_platform.db.stale module."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from sable_platform.db.stale import mark_artifacts_stale


def _insert_artifact(conn, org_id, artifact_type, stale=0):
    conn.execute(
        text("INSERT INTO artifacts (org_id, artifact_type, stale) VALUES (:org_id, :atype, :stale)"),
        {"org_id": org_id, "atype": artifact_type, "stale": stale},
    )
    conn.commit()


class TestMarkArtifactsStale:
    def test_marks_matching_artifacts(self, sa_org):
        conn, org_id = sa_org
        _insert_artifact(conn, org_id, "strategy_brief")
        _insert_artifact(conn, org_id, "playbook")

        mark_artifacts_stale(conn, org_id, ["strategy_brief"])

        rows = conn.execute(
            text("SELECT artifact_type, stale FROM artifacts WHERE org_id=:org_id ORDER BY artifact_type"),
            {"org_id": org_id},
        ).mappings().fetchall()
        stale_map = {r["artifact_type"]: r["stale"] for r in rows}
        assert stale_map["strategy_brief"] == 1
        assert stale_map["playbook"] == 0

    def test_marks_multiple_types(self, sa_org):
        conn, org_id = sa_org
        _insert_artifact(conn, org_id, "strategy_brief")
        _insert_artifact(conn, org_id, "playbook")
        _insert_artifact(conn, org_id, "diagnostic")

        mark_artifacts_stale(conn, org_id, ["strategy_brief", "playbook"])

        rows = conn.execute(
            text("SELECT artifact_type, stale FROM artifacts WHERE org_id=:org_id"),
            {"org_id": org_id},
        ).mappings().fetchall()
        stale_map = {r["artifact_type"]: r["stale"] for r in rows}
        assert stale_map["strategy_brief"] == 1
        assert stale_map["playbook"] == 1
        assert stale_map["diagnostic"] == 0

    def test_idempotent(self, sa_org):
        conn, org_id = sa_org
        _insert_artifact(conn, org_id, "strategy_brief")

        mark_artifacts_stale(conn, org_id, ["strategy_brief"])
        mark_artifacts_stale(conn, org_id, ["strategy_brief"])

        row = conn.execute(
            text("SELECT stale FROM artifacts WHERE org_id=:org_id AND artifact_type='strategy_brief'"),
            {"org_id": org_id},
        ).mappings().fetchone()
        assert row["stale"] == 1

    def test_scoped_to_org(self, sa_org):
        conn, org_id = sa_org
        conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES ('other', 'Other')"))
        conn.commit()
        _insert_artifact(conn, org_id, "strategy_brief")
        _insert_artifact(conn, "other", "strategy_brief")

        mark_artifacts_stale(conn, org_id, ["strategy_brief"])

        other_row = conn.execute(
            text("SELECT stale FROM artifacts WHERE org_id='other' AND artifact_type='strategy_brief'"),
        ).mappings().fetchone()
        assert other_row["stale"] == 0

    def test_no_match_noop(self, sa_org):
        conn, org_id = sa_org
        _insert_artifact(conn, org_id, "playbook")
        mark_artifacts_stale(conn, org_id, ["nonexistent_type"])
        row = conn.execute(
            text("SELECT stale FROM artifacts WHERE org_id=:org_id"),
            {"org_id": org_id},
        ).mappings().fetchone()
        assert row["stale"] == 0

    def test_committed(self, sa_org):
        conn, org_id = sa_org
        _insert_artifact(conn, org_id, "strategy_brief")
        mark_artifacts_stale(conn, org_id, ["strategy_brief"])
        row = conn.execute(
            text("SELECT stale FROM artifacts WHERE org_id=:org_id AND artifact_type='strategy_brief'"),
            {"org_id": org_id},
        ).mappings().fetchone()
        assert row["stale"] == 1

    def test_empty_list_is_noop(self, sa_org):
        """Empty artifact_types list generates IN() which matches nothing."""
        conn, org_id = sa_org
        _insert_artifact(conn, org_id, "strategy_brief")
        mark_artifacts_stale(conn, org_id, [])
        row = conn.execute(
            text("SELECT stale FROM artifacts WHERE org_id=:org_id"),
            {"org_id": org_id},
        ).mappings().fetchone()
        assert row["stale"] == 0
