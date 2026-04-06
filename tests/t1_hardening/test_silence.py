"""T1-SILENCE: verify all former bare except:pass sites now log warnings."""
from __future__ import annotations

import logging
import sqlite3
from unittest.mock import patch

import pytest

from sable_platform.db.connection import ensure_schema


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    return conn


def _insert_org(conn, org_id="test_org"):
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES (?, ?, ?)",
        (org_id, "Test Org", "active"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# alert_checks.py: _check_member_decay config parse failure
# ---------------------------------------------------------------------------

def test_member_decay_config_parse_logs_warning(caplog):
    """Malformed config_json triggers log.warning, not silent pass."""
    from sable_platform.workflows.alert_checks import _check_member_decay

    conn = _make_conn()
    _insert_org(conn)
    # Insert malformed config_json
    conn.execute("UPDATE orgs SET config_json='{bad json' WHERE org_id='test_org'")
    conn.commit()

    with caplog.at_level(logging.WARNING, logger="sable_platform.workflows.alert_checks"):
        _check_member_decay(conn, "test_org")

    assert any("Failed to parse decay config" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# alert_checks.py: _check_member_decay important tag query failure
# ---------------------------------------------------------------------------

def test_member_decay_tag_check_logs_warning(caplog):
    """entity_tags query failure triggers log.warning, not silent pass."""
    from sable_platform.workflows.alert_checks import _check_member_decay

    conn = _make_conn()
    _insert_org(conn)

    # Insert an entity with high decay score
    entity_id = "e" * 32
    conn.execute(
        "INSERT INTO entities (entity_id, org_id, status) VALUES (?, ?, 'confirmed')",
        (entity_id, "test_org"),
    )
    conn.execute(
        "INSERT INTO entity_decay_scores (org_id, entity_id, decay_score, risk_tier) VALUES (?, ?, 1.0, 'critical')",
        ("test_org", entity_id),
    )
    conn.commit()

    # Drop entity_tags table to force the query to fail
    conn.execute("DROP TABLE entity_tags")

    with caplog.at_level(logging.WARNING, logger="sable_platform.workflows.alert_checks"):
        _check_member_decay(conn, "test_org")

    assert any("Failed to check important tags" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# alert_checks.py: _check_bridge_decay config parse failure
# ---------------------------------------------------------------------------

def test_bridge_decay_config_parse_logs_warning(caplog):
    """Malformed config_json in bridge_decay triggers log.warning."""
    from sable_platform.workflows.alert_checks import _check_bridge_decay

    conn = _make_conn()
    _insert_org(conn)
    conn.execute("UPDATE orgs SET config_json='{bad json' WHERE org_id='test_org'")
    conn.commit()

    with caplog.at_level(logging.WARNING, logger="sable_platform.workflows.alert_checks"):
        _check_bridge_decay(conn, "test_org")

    assert any("Failed to parse bridge decay config" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# alert_delivery.py: webhook dispatch failure
# ---------------------------------------------------------------------------

def test_alert_delivery_webhook_failure_logs_warning(caplog):
    """Webhook dispatch failure in _deliver triggers log.warning."""
    from sable_platform.workflows.alert_delivery import _deliver

    conn = _make_conn()
    _insert_org(conn)
    # Configure alert_configs so delivery proceeds
    conn.execute(
        "INSERT INTO alert_configs (org_id, min_severity, enabled, cooldown_hours) VALUES (?, 'info', 1, 0)",
        ("test_org",),
    )
    conn.commit()

    with patch("sable_platform.webhooks.dispatch.dispatch_event", side_effect=RuntimeError("boom")), \
         caplog.at_level(logging.WARNING, logger="sable_platform.workflows.alert_delivery"):
        _deliver(conn, "test_org", "critical", "test alert", dedup_key="test:1")

    assert any("Webhook dispatch failed during alert delivery" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# alert_delivery.py: config load failure
# ---------------------------------------------------------------------------

def test_alert_delivery_config_load_failure_logs_warning(caplog):
    """Config query failure in _deliver triggers log.warning."""
    from sable_platform.workflows.alert_delivery import _deliver

    conn = _make_conn()
    # Drop alert_configs to force query failure
    conn.execute("DROP TABLE IF EXISTS alert_configs")

    with caplog.at_level(logging.WARNING, logger="sable_platform.workflows.alert_delivery"):
        _deliver(conn, "test_org", "critical", "test alert")

    assert any("Failed to load alert config" in m for m in caplog.messages)


# ---------------------------------------------------------------------------
# workflow_store.py: webhook dispatch failure in emit_workflow_event
# ---------------------------------------------------------------------------

def test_workflow_event_webhook_failure_logs_warning(caplog):
    """Webhook dispatch failure in emit_workflow_event triggers log.warning."""
    from sable_platform.db.workflow_store import emit_workflow_event, create_workflow_run

    conn = _make_conn()
    _insert_org(conn)
    run_id = create_workflow_run(conn, "test_org", "test_wf", "v1", {})

    with patch("sable_platform.db.workflow_store.dispatch_event", side_effect=RuntimeError("boom")), \
         caplog.at_level(logging.WARNING, logger="sable_platform.db.workflow_store"):
        emit_workflow_event(conn, run_id, "step_completed", payload={"step": "test"})

    assert any("Webhook dispatch failed during workflow event" in m for m in caplog.messages)
