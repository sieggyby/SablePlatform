"""Tests for sable_platform.db.alerts module."""
from __future__ import annotations

import pytest

from sable_platform.db.alerts import (
    acknowledge_alert,
    create_alert,
    get_alert_config,
    get_last_delivered_at,
    list_alerts,
    mark_delivered,
    mark_delivery_failed,
    resolve_alert,
    upsert_alert_config,
)


# ---------------------------------------------------------------------------
# upsert_alert_config
# ---------------------------------------------------------------------------

class TestUpsertAlertConfig:
    def test_create_new(self, org_db):
        conn, org_id = org_db
        cid = upsert_alert_config(conn, org_id, min_severity="critical")
        assert isinstance(cid, str) and len(cid) == 32
        row = get_alert_config(conn, org_id)
        assert row["min_severity"] == "critical"
        assert row["enabled"] == 1

    def test_update_existing(self, org_db):
        conn, org_id = org_db
        cid1 = upsert_alert_config(conn, org_id, min_severity="warning")
        cid2 = upsert_alert_config(conn, org_id, min_severity="critical")
        assert cid1 == cid2  # same config_id on update
        row = get_alert_config(conn, org_id)
        assert row["min_severity"] == "critical"

    def test_telegram_and_discord(self, org_db):
        conn, org_id = org_db
        upsert_alert_config(
            conn, org_id,
            telegram_chat_id="123456",
            discord_webhook_url="https://discord.com/api/webhooks/test",
        )
        row = get_alert_config(conn, org_id)
        assert row["telegram_chat_id"] == "123456"
        assert row["discord_webhook_url"] == "https://discord.com/api/webhooks/test"

    def test_cooldown_hours_set(self, org_db):
        conn, org_id = org_db
        upsert_alert_config(conn, org_id, cooldown_hours=8)
        row = get_alert_config(conn, org_id)
        assert row["cooldown_hours"] == 8

    def test_cooldown_hours_preserved_on_update_if_none(self, org_db):
        """COALESCE(?, cooldown_hours) preserves existing value when None passed."""
        conn, org_id = org_db
        upsert_alert_config(conn, org_id, cooldown_hours=8)
        upsert_alert_config(conn, org_id, min_severity="critical")  # cooldown_hours=None
        row = get_alert_config(conn, org_id)
        assert row["cooldown_hours"] == 8

    def test_cooldown_hours_overwritten_on_update(self, org_db):
        """Explicit cooldown_hours on update should overwrite the previous value."""
        conn, org_id = org_db
        upsert_alert_config(conn, org_id, cooldown_hours=8)
        upsert_alert_config(conn, org_id, cooldown_hours=12)
        row = get_alert_config(conn, org_id)
        assert row["cooldown_hours"] == 12

    def test_disabled(self, org_db):
        conn, org_id = org_db
        upsert_alert_config(conn, org_id, enabled=False)
        row = get_alert_config(conn, org_id)
        assert row["enabled"] == 0

    def test_committed(self, org_db):
        conn, org_id = org_db
        upsert_alert_config(conn, org_id)
        row = conn.execute("SELECT * FROM alert_configs WHERE org_id=?", (org_id,)).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# get_alert_config
# ---------------------------------------------------------------------------

class TestGetAlertConfig:
    def test_returns_none_when_missing(self, org_db):
        conn, org_id = org_db
        assert get_alert_config(conn, org_id) is None


# ---------------------------------------------------------------------------
# create_alert
# ---------------------------------------------------------------------------

class TestCreateAlert:
    def test_create_minimal(self, org_db):
        conn, org_id = org_db
        aid = create_alert(conn, "stale_tracking", "warning", "Data stale", org_id=org_id)
        assert isinstance(aid, str) and len(aid) == 32

    def test_create_full_params(self, org_db):
        conn, org_id = org_db
        aid = create_alert(
            conn, "member_decay", "critical", "Decay alert",
            org_id=org_id, body="Entity at risk",
            data_json='{"score": 0.9}', dedup_key="member_decay:ent1",
        )
        row = conn.execute("SELECT * FROM alerts WHERE alert_id=?", (aid,)).fetchone()
        assert row["severity"] == "critical"
        assert row["body"] == "Entity at risk"
        assert row["dedup_key"] == "member_decay:ent1"
        assert row["status"] == "new"

    def test_dedup_blocks_duplicate_new(self, org_db):
        conn, org_id = org_db
        aid1 = create_alert(conn, "stale_tracking", "warning", "A",
                            org_id=org_id, dedup_key="stale:org1")
        assert aid1 is not None
        aid2 = create_alert(conn, "stale_tracking", "warning", "B",
                            org_id=org_id, dedup_key="stale:org1")
        assert aid2 is None

    def test_dedup_allows_after_acknowledge(self, org_db):
        """Acknowledged alert should not block new alert with same dedup_key."""
        conn, org_id = org_db
        aid1 = create_alert(conn, "stale_tracking", "warning", "A",
                            org_id=org_id, dedup_key="stale:org1")
        acknowledge_alert(conn, aid1, "op")
        aid2 = create_alert(conn, "stale_tracking", "warning", "B",
                            org_id=org_id, dedup_key="stale:org1")
        assert aid2 is not None

    def test_dedup_allows_after_resolve(self, org_db):
        conn, org_id = org_db
        aid1 = create_alert(conn, "stale_tracking", "warning", "A",
                            org_id=org_id, dedup_key="stale:org1")
        resolve_alert(conn, aid1)
        aid2 = create_alert(conn, "stale_tracking", "warning", "B",
                            org_id=org_id, dedup_key="stale:org1")
        assert aid2 is not None

    def test_no_dedup_key_always_creates(self, org_db):
        conn, org_id = org_db
        aid1 = create_alert(conn, "stale_tracking", "warning", "A", org_id=org_id)
        aid2 = create_alert(conn, "stale_tracking", "warning", "B", org_id=org_id)
        assert aid1 is not None and aid2 is not None and aid1 != aid2

    def test_committed(self, org_db):
        conn, org_id = org_db
        aid = create_alert(conn, "test", "info", "Test", org_id=org_id)
        row = conn.execute("SELECT * FROM alerts WHERE alert_id=?", (aid,)).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# acknowledge_alert
# ---------------------------------------------------------------------------

class TestAcknowledgeAlert:
    def test_acknowledge(self, org_db):
        conn, org_id = org_db
        aid = create_alert(conn, "test", "warning", "T", org_id=org_id)
        acknowledge_alert(conn, aid, "sieggy")
        row = conn.execute("SELECT * FROM alerts WHERE alert_id=?", (aid,)).fetchone()
        assert row["status"] == "acknowledged"
        assert row["acknowledged_by"] == "sieggy"
        assert row["acknowledged_at"] is not None

    def test_acknowledge_writes_audit_log(self, org_db):
        conn, org_id = org_db
        aid = create_alert(conn, "test", "warning", "T", org_id=org_id)
        acknowledge_alert(conn, aid, "sieggy")
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action='alert_acknowledge'",
        ).fetchall()
        assert len(rows) == 1
        assert aid in rows[0]["detail_json"]


# ---------------------------------------------------------------------------
# resolve_alert
# ---------------------------------------------------------------------------

class TestResolveAlert:
    def test_resolve(self, org_db):
        conn, org_id = org_db
        aid = create_alert(conn, "test", "warning", "T", org_id=org_id)
        resolve_alert(conn, aid)
        row = conn.execute("SELECT * FROM alerts WHERE alert_id=?", (aid,)).fetchone()
        assert row["status"] == "resolved"
        assert row["resolved_at"] is not None

    def test_resolve_writes_audit_log(self, org_db):
        conn, org_id = org_db
        aid = create_alert(conn, "test", "warning", "T", org_id=org_id)
        resolve_alert(conn, aid)
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE action='alert_resolve'",
        ).fetchall()
        assert len(rows) == 1
        assert aid in rows[0]["detail_json"]


# ---------------------------------------------------------------------------
# Delivery tracking
# ---------------------------------------------------------------------------

class TestDeliveryTracking:
    def test_get_last_delivered_at_none(self, org_db):
        conn, _ = org_db
        assert get_last_delivered_at(conn, "nonexistent") is None

    def test_mark_delivered(self, org_db):
        conn, org_id = org_db
        create_alert(conn, "test", "warning", "T", org_id=org_id, dedup_key="dk1")
        mark_delivered(conn, "dk1")
        ts = get_last_delivered_at(conn, "dk1")
        assert ts is not None

    def test_mark_delivered_clears_error(self, org_db):
        conn, org_id = org_db
        create_alert(conn, "test", "warning", "T", org_id=org_id, dedup_key="dk1")
        mark_delivery_failed(conn, "dk1", "timeout")
        mark_delivered(conn, "dk1")
        row = conn.execute(
            "SELECT last_delivery_error FROM alerts WHERE dedup_key='dk1'",
        ).fetchone()
        assert row["last_delivery_error"] is None

    def test_mark_delivery_failed(self, org_db):
        conn, org_id = org_db
        create_alert(conn, "test", "warning", "T", org_id=org_id, dedup_key="dk1")
        mark_delivery_failed(conn, "dk1", "Connection refused")
        row = conn.execute(
            "SELECT last_delivery_error FROM alerts WHERE dedup_key='dk1'",
        ).fetchone()
        assert row["last_delivery_error"] == "Connection refused"

    def test_mark_delivery_failed_truncates_to_500(self, org_db):
        conn, org_id = org_db
        create_alert(conn, "test", "warning", "T", org_id=org_id, dedup_key="dk1")
        long_error = "x" * 1000
        mark_delivery_failed(conn, "dk1", long_error)
        row = conn.execute(
            "SELECT last_delivery_error FROM alerts WHERE dedup_key='dk1'",
        ).fetchone()
        assert len(row["last_delivery_error"]) == 500

    def test_mark_delivered_only_affects_new_status(self, org_db):
        """mark_delivered targets status='new' — acknowledged alerts unaffected."""
        conn, org_id = org_db
        aid = create_alert(conn, "test", "warning", "T", org_id=org_id, dedup_key="dk1")
        acknowledge_alert(conn, aid, "op")
        mark_delivered(conn, "dk1")
        assert get_last_delivered_at(conn, "dk1") is None

    def test_mark_delivery_failed_only_affects_new_status(self, org_db):
        """mark_delivery_failed targets status='new' — acknowledged alerts unaffected."""
        conn, org_id = org_db
        aid = create_alert(conn, "test", "warning", "T", org_id=org_id, dedup_key="dk1")
        acknowledge_alert(conn, aid, "op")
        mark_delivery_failed(conn, "dk1", "timeout")
        row = conn.execute(
            "SELECT last_delivery_error FROM alerts WHERE alert_id=?", (aid,),
        ).fetchone()
        assert row["last_delivery_error"] is None


# ---------------------------------------------------------------------------
# list_alerts
# ---------------------------------------------------------------------------

class TestListAlerts:
    def test_list_empty(self, org_db):
        conn, org_id = org_db
        assert list_alerts(conn, org_id=org_id) == []

    def test_list_defaults_to_new(self, org_db):
        conn, org_id = org_db
        aid = create_alert(conn, "test", "warning", "New", org_id=org_id)
        acknowledge_alert(conn, aid, "op")
        create_alert(conn, "test", "warning", "Still new", org_id=org_id)
        rows = list_alerts(conn, org_id=org_id)
        assert len(rows) == 1
        assert rows[0]["title"] == "Still new"

    def test_list_filter_severity(self, org_db):
        conn, org_id = org_db
        create_alert(conn, "test", "warning", "W", org_id=org_id)
        create_alert(conn, "test", "critical", "C", org_id=org_id)
        rows = list_alerts(conn, org_id=org_id, severity="critical")
        assert len(rows) == 1
        assert rows[0]["severity"] == "critical"

    def test_list_filter_status_acknowledged(self, org_db):
        conn, org_id = org_db
        aid = create_alert(conn, "test", "warning", "Acked", org_id=org_id)
        acknowledge_alert(conn, aid, "op")
        rows = list_alerts(conn, org_id=org_id, status="acknowledged")
        assert len(rows) == 1

    def test_list_respects_limit(self, org_db):
        conn, org_id = org_db
        for i in range(5):
            create_alert(conn, "test", "warning", f"Alert {i}", org_id=org_id)
        assert len(list_alerts(conn, org_id=org_id, limit=3)) == 3

    def test_list_scoped_to_org(self, org_db):
        conn, org_id = org_db
        conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('other', 'Other')")
        conn.commit()
        create_alert(conn, "test", "warning", "Org A", org_id=org_id)
        create_alert(conn, "test", "warning", "Org B", org_id="other")
        assert len(list_alerts(conn, org_id=org_id)) == 1
        assert len(list_alerts(conn, org_id="other")) == 1

    def test_list_all_statuses(self, org_db):
        """status=None skips status filter, returning all alerts."""
        conn, org_id = org_db
        aid = create_alert(conn, "test", "warning", "A", org_id=org_id)
        acknowledge_alert(conn, aid, "op")
        create_alert(conn, "test", "warning", "B", org_id=org_id)
        rows = list_alerts(conn, org_id=org_id, status=None)
        assert len(rows) == 2
