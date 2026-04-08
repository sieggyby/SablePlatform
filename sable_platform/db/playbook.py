"""Playbook outcome tagging helpers for sable.db.

Playbook targets are extracted by Cult Grader from playbook input metrics.
Playbook outcomes are measured by comparing prior targets against current metrics.
Platform stores and queries — it does not compute.
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.engine import Connection


def upsert_playbook_targets(
    conn: Connection,
    org_id: str,
    targets: list[dict],
    *,
    artifact_id: str | None = None,
) -> int:
    """Insert a playbook targets record.

    Returns the row id of the inserted record.
    """
    row_id = conn.execute(
        text(
            "INSERT INTO playbook_targets (org_id, artifact_id, targets_json)"
            " VALUES (:org_id, :artifact_id, :targets_json)"
        ),
        {"org_id": org_id, "artifact_id": artifact_id, "targets_json": json.dumps(targets)},
    ).lastrowid
    conn.commit()
    return row_id


def get_latest_playbook_targets(
    conn: Connection,
    org_id: str,
):
    """Return the most recent playbook targets for an org, or None."""
    return conn.execute(
        text("SELECT * FROM playbook_targets WHERE org_id=:org_id ORDER BY created_at DESC LIMIT 1"),
        {"org_id": org_id},
    ).fetchone()


def list_playbook_targets(
    conn: Connection,
    org_id: str,
    *,
    limit: int = 20,
) -> list:
    """List playbook targets for an org, newest first."""
    return conn.execute(
        text("SELECT * FROM playbook_targets WHERE org_id=:org_id ORDER BY created_at DESC LIMIT :lim"),
        {"org_id": org_id, "lim": limit},
    ).fetchall()


def record_playbook_outcomes(
    conn: Connection,
    org_id: str,
    outcomes: dict,
    *,
    targets_artifact_id: str | None = None,
) -> int:
    """Insert a playbook outcomes record.

    Returns the row id of the inserted record.
    """
    row_id = conn.execute(
        text(
            "INSERT INTO playbook_outcomes (org_id, targets_artifact_id, outcomes_json)"
            " VALUES (:org_id, :targets_artifact_id, :outcomes_json)"
        ),
        {"org_id": org_id, "targets_artifact_id": targets_artifact_id, "outcomes_json": json.dumps(outcomes)},
    ).lastrowid
    conn.commit()
    return row_id


def get_latest_playbook_outcomes(
    conn: Connection,
    org_id: str,
):
    """Return the most recent playbook outcomes for an org, or None."""
    return conn.execute(
        text("SELECT * FROM playbook_outcomes WHERE org_id=:org_id ORDER BY created_at DESC LIMIT 1"),
        {"org_id": org_id},
    ).fetchone()


def list_playbook_outcomes(
    conn: Connection,
    org_id: str,
    *,
    limit: int = 20,
) -> list:
    """List playbook outcomes for an org, newest first."""
    return conn.execute(
        text("SELECT * FROM playbook_outcomes WHERE org_id=:org_id ORDER BY created_at DESC LIMIT :lim"),
        {"org_id": org_id, "lim": limit},
    ).fetchall()
