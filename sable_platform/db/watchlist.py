"""Entity watchlist helpers for sable.db.

Operators curate a per-org watchlist of entities to monitor.
Snapshots capture periodic state for change detection.
"""
from __future__ import annotations

import json
import sqlite3

from sable_platform.errors import SableError, ORG_NOT_FOUND


def add_to_watchlist(
    conn: sqlite3.Connection,
    org_id: str,
    entity_id: str,
    added_by: str,
    note: str | None = None,
) -> bool:
    """Add entity to watchlist. Returns True if inserted, False if already watched."""
    row = conn.execute("SELECT 1 FROM orgs WHERE org_id=?", (org_id,)).fetchone()
    if not row:
        raise SableError(ORG_NOT_FOUND, f"Org '{org_id}' not found")

    cursor = conn.execute(
        "INSERT OR IGNORE INTO entity_watchlist (org_id, entity_id, added_by, note) VALUES (?, ?, ?, ?)",
        (org_id, entity_id, added_by, note),
    )
    conn.commit()

    inserted = cursor.rowcount > 0
    if inserted:
        _take_snapshot(conn, org_id, entity_id)
    return inserted


def remove_from_watchlist(
    conn: sqlite3.Connection,
    org_id: str,
    entity_id: str,
) -> bool:
    """Remove entity from watchlist. Returns True if deleted, False if not found."""
    cursor = conn.execute(
        "DELETE FROM entity_watchlist WHERE org_id=? AND entity_id=?",
        (org_id, entity_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def list_watchlist(
    conn: sqlite3.Connection,
    org_id: str,
    *,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """Return all watched entities for org, ordered by created_at DESC."""
    return conn.execute(
        "SELECT * FROM entity_watchlist WHERE org_id=? ORDER BY created_at DESC LIMIT ?",
        (org_id, limit),
    ).fetchall()


def _take_snapshot(conn: sqlite3.Connection, org_id: str, entity_id: str) -> None:
    """Capture current state of a watched entity."""
    # Decay score
    decay_row = conn.execute(
        "SELECT decay_score FROM entity_decay_scores WHERE org_id=? AND entity_id=?",
        (org_id, entity_id),
    ).fetchone()
    decay_score = decay_row["decay_score"] if decay_row else None

    # Active tags (matching _ACTIVE_PREDICATE from tags.py)
    tag_rows = conn.execute(
        """
        SELECT tag FROM entity_tags
        WHERE entity_id=? AND is_current=1
          AND (expires_at IS NULL OR expires_at > datetime('now'))
        """,
        (entity_id,),
    ).fetchall()
    tags = [r["tag"] for r in tag_rows]
    tags_json = json.dumps(sorted(tags))

    # Interaction count — resolve entity_id to handles first
    handle_rows = conn.execute(
        "SELECT handle FROM entity_handles WHERE entity_id=?",
        (entity_id,),
    ).fetchall()

    interaction_count = 0
    if handle_rows:
        handles = [r["handle"] for r in handle_rows]
        placeholders = ",".join("?" for _ in handles)
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(count), 0) as total
            FROM entity_interactions
            WHERE org_id=? AND (source_handle IN ({placeholders}) OR target_handle IN ({placeholders}))
            """,
            (org_id, *handles, *handles),
        ).fetchone()
        interaction_count = row["total"] if row else 0
    else:
        # Fall back to using entity_id as a handle
        row = conn.execute(
            """
            SELECT COALESCE(SUM(count), 0) as total
            FROM entity_interactions
            WHERE org_id=? AND (source_handle=? OR target_handle=?)
            """,
            (org_id, entity_id, entity_id),
        ).fetchone()
        interaction_count = row["total"] if row else 0

    conn.execute(
        """
        INSERT INTO watchlist_snapshots (org_id, entity_id, decay_score, tags_json, interaction_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        (org_id, entity_id, decay_score, tags_json, interaction_count),
    )
    conn.commit()


def take_all_snapshots(conn: sqlite3.Connection, org_id: str) -> int:
    """Take snapshots for all watched entities in an org. Returns count."""
    rows = conn.execute(
        "SELECT entity_id FROM entity_watchlist WHERE org_id=?",
        (org_id,),
    ).fetchall()
    for r in rows:
        _take_snapshot(conn, org_id, r["entity_id"])
    return len(rows)


def get_watchlist_changes(conn: sqlite3.Connection, org_id: str) -> list[dict]:
    """Compare two most recent snapshots per watched entity. Returns changes."""
    watched = conn.execute(
        "SELECT entity_id FROM entity_watchlist WHERE org_id=?",
        (org_id,),
    ).fetchall()

    results = []
    for w in watched:
        eid = w["entity_id"]
        snaps = conn.execute(
            """
            SELECT decay_score, tags_json, interaction_count
            FROM watchlist_snapshots
            WHERE org_id=? AND entity_id=?
            ORDER BY snapshot_at DESC LIMIT 2
            """,
            (org_id, eid),
        ).fetchall()

        if len(snaps) < 1:
            continue

        if len(snaps) == 1:
            results.append({"entity_id": eid, "changes": ["newly watched"]})
            continue

        new, old = snaps[0], snaps[1]
        changes: list[str] = []

        # Decay score change
        if new["decay_score"] != old["decay_score"]:
            old_s = old["decay_score"] if old["decay_score"] is not None else "None"
            new_s = new["decay_score"] if new["decay_score"] is not None else "None"
            changes.append(f"decay_score: {old_s} -> {new_s}")

        # Tag changes
        old_tags = set(json.loads(old["tags_json"])) if old["tags_json"] else set()
        new_tags = set(json.loads(new["tags_json"])) if new["tags_json"] else set()
        for t in sorted(new_tags - old_tags):
            changes.append(f"tag added: {t}")
        for t in sorted(old_tags - new_tags):
            changes.append(f"tag removed: {t}")

        # Interaction count change
        if new["interaction_count"] != old["interaction_count"]:
            changes.append(
                f"interaction_count: {old['interaction_count']} -> {new['interaction_count']}"
            )

        if changes:
            results.append({"entity_id": eid, "changes": changes})

    return results
