"""Mark platform artifacts stale."""
from __future__ import annotations

import sqlite3


def mark_artifacts_stale(conn: sqlite3.Connection, org_id: str, artifact_types: list[str]) -> None:
    placeholders = ",".join("?" * len(artifact_types))
    conn.execute(
        f"UPDATE artifacts SET stale=1 WHERE org_id=? AND artifact_type IN ({placeholders})",
        [org_id, *artifact_types],
    )
    conn.commit()
