"""Mark platform artifacts stale."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection


def mark_artifacts_stale(conn: Connection, org_id: str, artifact_types: list[str]) -> None:
    if not artifact_types:
        return
    placeholders = ",".join(f":t{i}" for i in range(len(artifact_types)))
    params: dict = {"org_id": org_id}
    params.update({f"t{i}": t for i, t in enumerate(artifact_types)})
    conn.execute(
        text(f"UPDATE artifacts SET stale=1 WHERE org_id=:org_id AND artifact_type IN ({placeholders})"),
        params,
    )
    conn.commit()
