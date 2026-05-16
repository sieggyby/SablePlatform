"""Incremental sync from a local SQLite sable.db into a remote target.

Use case: operators run Cult Grader (or any local writer) against
``~/.sable/sable.db``. The production VPS owns the canonical Postgres
database. This module copies the org-scoped tables that local writers
mutate, idempotently, with per-table cursors.

Tables synced (org-scoped, FK-safe order):

    orgs                       (precheck only — must exist on target)
    entities                   (upsert by entity_id)
    entity_handles             (insert on (platform, handle) conflict-skip)
    diagnostic_runs            (upsert by cult_run_id; strip local run_id)
    sync_runs                  (insert if (org_id, cult_run_id) absent)
    artifacts                  (insert if (org_id, path) absent)
    entity_tags                (full state replace per org)
    entity_tag_history         (insert on history_id conflict-skip)
    entity_interactions        (cursor on last_seen)
    entity_decay_scores        (upsert by (org_id, entity_id))
    entity_centrality_scores   (upsert by (org_id, entity_id))
    discord_pulse_runs         (upsert by (org_id, project_slug, run_date))
    metric_snapshots           (upsert by (org_id, snapshot_date))
    playbook_targets           (cursor on created_at)
    playbook_outcomes          (cursor on created_at)
    cost_events                (cursor on created_at)
    merge_candidates           (insert on (entity_a, entity_b) conflict-skip)

Excluded (intentional):

    workflow_runs/steps/events  — local workflow state, not authoritative
    jobs / job_steps            — Slopper-internal
    alerts / alert_configs      — per-environment; alerts evaluated on host
    webhook_subscriptions       — per-environment
    entity_watchlist / snaps    — operator-local state
    prospect_scores             — Lead Identifier surface, separate sync
    KOL tables (032-042)        — SableKOL sidecar owns its sync
    diagnostic_deltas           — references local run_id; recomputed
    audit_log                   — sync stamps ONE summary row, never replays
    discord_streak_events       — fitcheck bot owns its sync
    platform_meta               — environment-specific
    content_items / actions / outcomes  — not written by Cult Grader

Cursors are persisted in ``platform_meta`` under keys of the form
``sync_from_local:<table>:<org_id>``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class TableSyncResult:
    table: str
    rows_read: int = 0
    rows_written: int = 0
    rows_skipped: int = 0
    cursor_before: str | None = None
    cursor_after: str | None = None
    status: str = "ok"  # "ok" | "skipped" | "error"
    error: str | None = None


@dataclass
class SyncReport:
    org_id: str
    dry_run: bool
    source_schema_version: int | None = None
    target_schema_version: int | None = None
    tables: list[TableSyncResult] = field(default_factory=list)
    error: str | None = None

    @property
    def total_written(self) -> int:
        return sum(t.rows_written for t in self.tables)


class SyncError(Exception):
    """Raised when sync cannot proceed safely (e.g. schema-version mismatch)."""


def sync_org(
    source_engine: Engine,
    target_engine: Engine,
    org_id: str,
    *,
    since: str | None = None,
    dry_run: bool = False,
) -> SyncReport:
    """Sync one org's data from a local SQLite source into the target database.

    *since* is an ISO 8601 lower bound (``created_at >= since``) applied
    in addition to the per-table cursor. Useful for "redo this window."

    When *dry_run* is True nothing is written, but the report shows the
    counts that would have been written.
    """
    if source_engine.dialect.name != "sqlite":
        raise SyncError(
            f"Source must be SQLite, got {source_engine.dialect.name!r}"
        )

    report = SyncReport(org_id=org_id, dry_run=dry_run)

    src_ver = _read_schema_version(source_engine)
    tgt_ver = _read_schema_version(target_engine)
    report.source_schema_version = src_ver
    report.target_schema_version = tgt_ver

    if src_ver is None or tgt_ver is None:
        raise SyncError(
            f"Could not read schema_version (source={src_ver}, target={tgt_ver})"
        )
    if src_ver != tgt_ver:
        raise SyncError(
            f"Schema-version mismatch: source={src_ver}, target={tgt_ver}. "
            "Run 'sable-platform init' on target and update the source DB "
            "to the same migration version before retrying."
        )

    _require_org_on_target(target_engine, org_id)

    # Each table is its own transaction so a partial failure on one table
    # doesn't poison the others. Cursors only advance on commit.
    for sync_fn in _TABLE_SYNCS:
        try:
            result = sync_fn(
                source_engine, target_engine, org_id,
                since=since, dry_run=dry_run,
            )
        except Exception as exc:  # noqa: BLE001 — capture per-table failure
            log.exception("sync table failed")
            result = TableSyncResult(
                table=sync_fn.__name__.removeprefix("_sync_"),
                status="error",
                error=str(exc),
            )
        report.tables.append(result)

    if not dry_run:
        _write_audit_summary(target_engine, org_id, report)

    return report


# ---------------------------------------------------------------------------
# Cursor / schema-version helpers
# ---------------------------------------------------------------------------


def _read_schema_version(engine: Engine) -> int | None:
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version FROM schema_version")).fetchone()
            return int(row[0]) if row else None
    except Exception:
        return None


def _require_org_on_target(target_engine: Engine, org_id: str) -> None:
    with target_engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM orgs WHERE org_id=:org_id"),
            {"org_id": org_id},
        ).fetchone()
        if not row:
            raise SyncError(
                f"Org {org_id!r} not found on target. Create it on the target "
                "before running sync."
            )


def _cursor_key(table: str, org_id: str) -> str:
    return f"sync_from_local:{table}:{org_id}"


def _read_cursor(conn: Connection, table: str, org_id: str) -> str | None:
    row = conn.execute(
        text("SELECT value FROM platform_meta WHERE key=:k"),
        {"k": _cursor_key(table, org_id)},
    ).fetchone()
    return row[0] if row else None


def _write_cursor(conn: Connection, table: str, org_id: str, value: str) -> None:
    """Upsert a cursor row dialect-agnostically."""
    key = _cursor_key(table, org_id)
    existing = conn.execute(
        text("SELECT 1 FROM platform_meta WHERE key=:k"),
        {"k": key},
    ).fetchone()
    if existing:
        conn.execute(
            text("UPDATE platform_meta SET value=:v, updated_at=CURRENT_TIMESTAMP WHERE key=:k"),
            {"v": value, "k": key},
        )
    else:
        conn.execute(
            text(
                "INSERT INTO platform_meta (key, value) VALUES (:k, :v)"
            ),
            {"k": key, "v": value},
        )


def _write_audit_summary(target_engine: Engine, org_id: str, report: SyncReport) -> None:
    """Stamp a single audit_log row summarizing the sync run."""
    summary = {
        "tables": {t.table: {
            "read": t.rows_read,
            "written": t.rows_written,
            "skipped": t.rows_skipped,
            "status": t.status,
            "error": t.error,
        } for t in report.tables},
        "total_written": report.total_written,
        "schema_version": report.target_schema_version,
    }
    try:
        with target_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO audit_log (actor, action, org_id, detail_json, source)"
                    " VALUES (:actor, :action, :org_id, :detail, :source)"
                ),
                {
                    "actor": _resolve_audit_actor(),
                    "action": "sync_from_local",
                    "org_id": org_id,
                    "detail": json.dumps(summary),
                    "source": "sync-from-local",
                },
            )
    except Exception:  # noqa: BLE001
        # Audit failure should not fail the sync. Log loudly though.
        log.exception("audit summary write failed for org=%s", org_id)


def _resolve_audit_actor() -> str:
    import os
    return os.environ.get("SABLE_OPERATOR_ID", "unknown")


# ---------------------------------------------------------------------------
# Per-table sync functions
# ---------------------------------------------------------------------------
#
# Each function is responsible for:
#   1. Reading the cursor from target.platform_meta.
#   2. Reading new/changed rows from source for the given org.
#   3. Writing them to target idempotently inside ONE transaction.
#   4. Advancing the cursor on success.
#
# A function returns a TableSyncResult and never raises (errors are caught
# in the caller).


def _read_org_rows(
    source_engine: Engine,
    sql: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    with source_engine.connect() as conn:
        result = conn.execute(text(sql), params)
        return [dict(r._mapping) for r in result]


def _max_str(values: list[str | None]) -> str | None:
    real = [v for v in values if v is not None]
    return max(real) if real else None


def _sync_entities(source_engine, target_engine, org_id, *, since, dry_run):
    table = "entities"
    sql = (
        "SELECT entity_id, org_id, display_name, status, source, config_json, "
        " created_at, updated_at"
        " FROM entities WHERE org_id=:org_id"
    )
    params: dict[str, Any] = {"org_id": org_id}
    if since:
        sql += " AND updated_at >= :since"
        params["since"] = since
    sql += " ORDER BY updated_at ASC"
    rows = _read_org_rows(source_engine, sql, params)

    result = TableSyncResult(table=table, rows_read=len(rows))
    if not rows or dry_run:
        result.rows_written = 0 if dry_run else 0
        if dry_run:
            result.rows_written = len(rows)  # would-write
        return result

    with target_engine.begin() as conn:
        for row in rows:
            existing = conn.execute(
                text("SELECT 1 FROM entities WHERE entity_id=:eid"),
                {"eid": row["entity_id"]},
            ).fetchone()
            if existing:
                conn.execute(
                    text(
                        "UPDATE entities SET display_name=:display_name,"
                        " status=:status, source=:source, config_json=:config_json,"
                        " updated_at=:updated_at WHERE entity_id=:entity_id"
                    ),
                    row,
                )
                result.rows_skipped += 1  # update-in-place; counted under skipped for clarity
            else:
                conn.execute(
                    text(
                        "INSERT INTO entities (entity_id, org_id, display_name,"
                        " status, source, config_json, created_at, updated_at)"
                        " VALUES (:entity_id, :org_id, :display_name, :status,"
                        " :source, :config_json, :created_at, :updated_at)"
                    ),
                    row,
                )
                result.rows_written += 1
    return result


def _sync_entity_handles(source_engine, target_engine, org_id, *, since, dry_run):
    table = "entity_handles"
    # Handles join through entities.org_id (handles themselves have no org column).
    sql = (
        "SELECT h.entity_id, h.platform, h.handle, h.is_primary, h.added_at"
        " FROM entity_handles h JOIN entities e ON e.entity_id = h.entity_id"
        " WHERE e.org_id=:org_id"
    )
    params: dict[str, Any] = {"org_id": org_id}
    if since:
        sql += " AND h.added_at >= :since"
        params["since"] = since
    rows = _read_org_rows(source_engine, sql, params)
    result = TableSyncResult(table=table, rows_read=len(rows))
    if not rows:
        return result
    if dry_run:
        result.rows_written = len(rows)
        return result

    with target_engine.begin() as conn:
        for row in rows:
            existing = conn.execute(
                text("SELECT 1 FROM entity_handles WHERE platform=:p AND handle=:h"),
                {"p": row["platform"], "h": row["handle"]},
            ).fetchone()
            if existing:
                result.rows_skipped += 1
                continue
            try:
                conn.execute(
                    text(
                        "INSERT INTO entity_handles (entity_id, platform, handle,"
                        " is_primary, added_at)"
                        " VALUES (:entity_id, :platform, :handle, :is_primary, :added_at)"
                    ),
                    row,
                )
                result.rows_written += 1
            except IntegrityError:
                result.rows_skipped += 1
    return result


def _sync_diagnostic_runs(source_engine, target_engine, org_id, *, since, dry_run):
    table = "diagnostic_runs"
    # cult_run_id is the natural key (UNIQUE partial index). Strip local run_id.
    sql = (
        "SELECT org_id, run_type, status, started_at, completed_at, result_json,"
        " error, cult_run_id, project_slug, run_date, research_mode, checkpoint_path,"
        " overall_grade, fit_score, recommended_action, sable_verdict, total_cost_usd,"
        " run_summary_json"
        " FROM diagnostic_runs WHERE org_id=:org_id AND cult_run_id IS NOT NULL"
    )
    params: dict[str, Any] = {"org_id": org_id}
    if since:
        sql += " AND started_at >= :since"
        params["since"] = since
    rows = _read_org_rows(source_engine, sql, params)
    result = TableSyncResult(table=table, rows_read=len(rows))
    if not rows:
        return result
    if dry_run:
        result.rows_written = len(rows)
        return result

    with target_engine.begin() as conn:
        for row in rows:
            existing = conn.execute(
                text(
                    "SELECT run_id FROM diagnostic_runs WHERE cult_run_id=:cid"
                ),
                {"cid": row["cult_run_id"]},
            ).fetchone()
            if existing:
                # Update mutable fields on already-synced run.
                conn.execute(
                    text(
                        "UPDATE diagnostic_runs SET status=:status,"
                        " completed_at=:completed_at, result_json=:result_json,"
                        " error=:error, overall_grade=:overall_grade,"
                        " fit_score=:fit_score, recommended_action=:recommended_action,"
                        " sable_verdict=:sable_verdict, total_cost_usd=:total_cost_usd,"
                        " run_summary_json=:run_summary_json"
                        " WHERE cult_run_id=:cult_run_id"
                    ),
                    row,
                )
                result.rows_skipped += 1
            else:
                conn.execute(
                    text(
                        "INSERT INTO diagnostic_runs (org_id, run_type, status,"
                        " started_at, completed_at, result_json, error, cult_run_id,"
                        " project_slug, run_date, research_mode, checkpoint_path,"
                        " overall_grade, fit_score, recommended_action, sable_verdict,"
                        " total_cost_usd, run_summary_json) VALUES (:org_id, :run_type,"
                        " :status, :started_at, :completed_at, :result_json, :error,"
                        " :cult_run_id, :project_slug, :run_date, :research_mode,"
                        " :checkpoint_path, :overall_grade, :fit_score,"
                        " :recommended_action, :sable_verdict, :total_cost_usd,"
                        " :run_summary_json)"
                    ),
                    row,
                )
                result.rows_written += 1
    return result


def _sync_sync_runs(source_engine, target_engine, org_id, *, since, dry_run):
    table = "sync_runs"
    sql = (
        "SELECT org_id, sync_type, status, started_at, completed_at,"
        " records_synced, error, cult_run_id, entities_created, entities_updated,"
        " handles_added, tags_added, tags_replaced, merge_candidates_created"
        " FROM sync_runs WHERE org_id=:org_id"
    )
    params: dict[str, Any] = {"org_id": org_id}
    if since:
        sql += " AND started_at >= :since"
        params["since"] = since
    rows = _read_org_rows(source_engine, sql, params)
    result = TableSyncResult(table=table, rows_read=len(rows))
    if not rows:
        return result
    if dry_run:
        result.rows_written = len(rows)
        return result

    with target_engine.begin() as conn:
        for row in rows:
            cid = row.get("cult_run_id")
            if cid:
                existing = conn.execute(
                    text(
                        "SELECT 1 FROM sync_runs WHERE org_id=:o AND cult_run_id=:c"
                    ),
                    {"o": org_id, "c": cid},
                ).fetchone()
            else:
                # No natural key — use (org_id, started_at, sync_type).
                existing = conn.execute(
                    text(
                        "SELECT 1 FROM sync_runs WHERE org_id=:o AND started_at=:s"
                        " AND sync_type=:t"
                    ),
                    {"o": org_id, "s": row["started_at"], "t": row["sync_type"]},
                ).fetchone()
            if existing:
                result.rows_skipped += 1
                continue
            conn.execute(
                text(
                    "INSERT INTO sync_runs (org_id, sync_type, status, started_at,"
                    " completed_at, records_synced, error, cult_run_id,"
                    " entities_created, entities_updated, handles_added, tags_added,"
                    " tags_replaced, merge_candidates_created) VALUES (:org_id,"
                    " :sync_type, :status, :started_at, :completed_at, :records_synced,"
                    " :error, :cult_run_id, :entities_created, :entities_updated,"
                    " :handles_added, :tags_added, :tags_replaced,"
                    " :merge_candidates_created)"
                ),
                row,
            )
            result.rows_written += 1
    return result


def _sync_artifacts(source_engine, target_engine, org_id, *, since, dry_run):
    table = "artifacts"
    sql = (
        "SELECT org_id, job_id, artifact_type, path, metadata_json, stale, degraded,"
        " created_at FROM artifacts WHERE org_id=:org_id"
    )
    params: dict[str, Any] = {"org_id": org_id}
    if since:
        sql += " AND created_at >= :since"
        params["since"] = since
    rows = _read_org_rows(source_engine, sql, params)
    result = TableSyncResult(table=table, rows_read=len(rows))
    if not rows:
        return result
    if dry_run:
        result.rows_written = len(rows)
        return result

    with target_engine.begin() as conn:
        for row in rows:
            # Cult-Grader artifacts may have a NULL job_id; FK is permissive.
            # Skip artifacts whose source job_id refers to a row we won't sync.
            if row.get("job_id"):
                tgt_job = conn.execute(
                    text("SELECT 1 FROM jobs WHERE job_id=:j"),
                    {"j": row["job_id"]},
                ).fetchone()
                if not tgt_job:
                    # Drop the FK reference rather than skip the artifact.
                    row = {**row, "job_id": None}

            existing = conn.execute(
                text(
                    "SELECT 1 FROM artifacts WHERE org_id=:o AND path=:p"
                    " AND artifact_type=:t AND created_at=:c"
                ),
                {"o": org_id, "p": row["path"], "t": row["artifact_type"],
                 "c": row["created_at"]},
            ).fetchone()
            if existing:
                result.rows_skipped += 1
                continue
            conn.execute(
                text(
                    "INSERT INTO artifacts (org_id, job_id, artifact_type, path,"
                    " metadata_json, stale, degraded, created_at) VALUES"
                    " (:org_id, :job_id, :artifact_type, :path, :metadata_json,"
                    " :stale, :degraded, :created_at)"
                ),
                row,
            )
            result.rows_written += 1
    return result


def _sync_entity_tags(source_engine, target_engine, org_id, *, since, dry_run):
    """Full state replace per org. Tag rows mutate (is_current/deactivated_at),
    so an incremental cursor would miss updates. Cheap for typical org sizes
    (thousands of rows max)."""
    table = "entity_tags"
    sql = (
        "SELECT t.entity_id, t.tag, t.source, t.confidence, t.is_current,"
        " t.expires_at, t.added_at, t.deactivated_at"
        " FROM entity_tags t JOIN entities e ON e.entity_id = t.entity_id"
        " WHERE e.org_id=:org_id ORDER BY t.added_at ASC"
    )
    rows = _read_org_rows(source_engine, sql, {"org_id": org_id})
    result = TableSyncResult(table=table, rows_read=len(rows))
    if dry_run:
        result.rows_written = len(rows)
        return result
    if not rows and since:
        # If --since was used and source has nothing, do not wipe target.
        return result

    with target_engine.begin() as conn:
        # Wipe org-scoped state first; entity_tags has no inbound FK.
        conn.execute(
            text(
                "DELETE FROM entity_tags WHERE entity_id IN"
                " (SELECT entity_id FROM entities WHERE org_id=:org_id)"
            ),
            {"org_id": org_id},
        )
        for row in rows:
            conn.execute(
                text(
                    "INSERT INTO entity_tags (entity_id, tag, source, confidence,"
                    " is_current, expires_at, added_at, deactivated_at)"
                    " VALUES (:entity_id, :tag, :source, :confidence, :is_current,"
                    " :expires_at, :added_at, :deactivated_at)"
                ),
                row,
            )
            result.rows_written += 1
    return result


def _sync_entity_tag_history(source_engine, target_engine, org_id, *, since, dry_run):
    table = "entity_tag_history"
    cursor_value: str | None = None
    if not dry_run:
        with target_engine.connect() as conn:
            cursor_value = _read_cursor(conn, table, org_id)
    effective_since = _max_str([cursor_value, since])

    sql = (
        "SELECT history_id, entity_id, org_id, change_type, tag, confidence,"
        " source, source_ref, expires_at, effective_at"
        " FROM entity_tag_history WHERE org_id=:org_id"
    )
    params: dict[str, Any] = {"org_id": org_id}
    if effective_since:
        sql += " AND effective_at > :since"
        params["since"] = effective_since
    sql += " ORDER BY effective_at ASC"
    rows = _read_org_rows(source_engine, sql, params)

    result = TableSyncResult(
        table=table, rows_read=len(rows), cursor_before=cursor_value,
    )
    if not rows:
        return result
    if dry_run:
        result.rows_written = len(rows)
        return result

    new_cursor: str | None = cursor_value
    with target_engine.begin() as conn:
        for row in rows:
            existing = conn.execute(
                text("SELECT 1 FROM entity_tag_history WHERE history_id=:hid"),
                {"hid": row["history_id"]},
            ).fetchone()
            if existing:
                result.rows_skipped += 1
            else:
                conn.execute(
                    text(
                        "INSERT INTO entity_tag_history (history_id, entity_id,"
                        " org_id, change_type, tag, confidence, source, source_ref,"
                        " expires_at, effective_at) VALUES (:history_id, :entity_id,"
                        " :org_id, :change_type, :tag, :confidence, :source,"
                        " :source_ref, :expires_at, :effective_at)"
                    ),
                    row,
                )
                result.rows_written += 1
            eff = row["effective_at"]
            if eff and (new_cursor is None or eff > new_cursor):
                new_cursor = eff
        if new_cursor and new_cursor != cursor_value:
            _write_cursor(conn, table, org_id, new_cursor)
            result.cursor_after = new_cursor
    return result


def _sync_entity_interactions(source_engine, target_engine, org_id, *, since, dry_run):
    table = "entity_interactions"
    cursor_value: str | None = None
    if not dry_run:
        with target_engine.connect() as conn:
            cursor_value = _read_cursor(conn, table, org_id)
    effective_since = _max_str([cursor_value, since])

    # entity_interactions rows mutate (count, last_seen). Cursor on last_seen
    # is fine because helpers only push last_seen forward (interactions.py:51).
    sql = (
        "SELECT org_id, source_handle, target_handle, interaction_type, count,"
        " first_seen, last_seen, run_date FROM entity_interactions"
        " WHERE org_id=:org_id"
    )
    params: dict[str, Any] = {"org_id": org_id}
    if effective_since:
        sql += " AND (last_seen IS NULL OR last_seen > :since)"
        params["since"] = effective_since
    sql += " ORDER BY last_seen ASC"
    rows = _read_org_rows(source_engine, sql, params)
    result = TableSyncResult(
        table=table, rows_read=len(rows), cursor_before=cursor_value,
    )
    if not rows:
        return result
    if dry_run:
        result.rows_written = len(rows)
        return result

    new_cursor: str | None = cursor_value
    with target_engine.begin() as conn:
        for row in rows:
            existing = conn.execute(
                text(
                    "SELECT id FROM entity_interactions WHERE org_id=:o"
                    " AND source_handle=:s AND target_handle=:t"
                    " AND interaction_type=:ty"
                ),
                {"o": org_id, "s": row["source_handle"], "t": row["target_handle"],
                 "ty": row["interaction_type"]},
            ).fetchone()
            if existing:
                # Replace count/last_seen with source state (NOT additive).
                conn.execute(
                    text(
                        "UPDATE entity_interactions SET count=:c, first_seen=:fs,"
                        " last_seen=:ls, run_date=:rd WHERE id=:id"
                    ),
                    {"c": row["count"], "fs": row["first_seen"],
                     "ls": row["last_seen"], "rd": row["run_date"],
                     "id": existing[0]},
                )
                result.rows_skipped += 1
            else:
                conn.execute(
                    text(
                        "INSERT INTO entity_interactions (org_id, source_handle,"
                        " target_handle, interaction_type, count, first_seen,"
                        " last_seen, run_date) VALUES (:org_id, :source_handle,"
                        " :target_handle, :interaction_type, :count, :first_seen,"
                        " :last_seen, :run_date)"
                    ),
                    row,
                )
                result.rows_written += 1
            ls = row.get("last_seen")
            if ls and (new_cursor is None or ls > new_cursor):
                new_cursor = ls
        if new_cursor and new_cursor != cursor_value:
            _write_cursor(conn, table, org_id, new_cursor)
            result.cursor_after = new_cursor
    return result


def _sync_decay_scores(source_engine, target_engine, org_id, *, since, dry_run):
    return _sync_unique_org_entity(
        "entity_decay_scores",
        "SELECT org_id, entity_id, decay_score, risk_tier, scored_at, run_date,"
        " factors_json FROM entity_decay_scores WHERE org_id=:org_id",
        "INSERT INTO entity_decay_scores (org_id, entity_id, decay_score, risk_tier,"
        " scored_at, run_date, factors_json) VALUES (:org_id, :entity_id,"
        " :decay_score, :risk_tier, :scored_at, :run_date, :factors_json)",
        "UPDATE entity_decay_scores SET decay_score=:decay_score,"
        " risk_tier=:risk_tier, scored_at=:scored_at, run_date=:run_date,"
        " factors_json=:factors_json WHERE org_id=:org_id AND entity_id=:entity_id",
        source_engine, target_engine, org_id,
        since=since, dry_run=dry_run, since_col="scored_at",
    )


def _sync_centrality_scores(source_engine, target_engine, org_id, *, since, dry_run):
    return _sync_unique_org_entity(
        "entity_centrality_scores",
        "SELECT org_id, entity_id, degree_centrality, betweenness_centrality,"
        " eigenvector_centrality, scored_at, run_date, in_centrality, out_centrality"
        " FROM entity_centrality_scores WHERE org_id=:org_id",
        "INSERT INTO entity_centrality_scores (org_id, entity_id, degree_centrality,"
        " betweenness_centrality, eigenvector_centrality, scored_at, run_date,"
        " in_centrality, out_centrality) VALUES (:org_id, :entity_id,"
        " :degree_centrality, :betweenness_centrality, :eigenvector_centrality,"
        " :scored_at, :run_date, :in_centrality, :out_centrality)",
        "UPDATE entity_centrality_scores SET degree_centrality=:degree_centrality,"
        " betweenness_centrality=:betweenness_centrality,"
        " eigenvector_centrality=:eigenvector_centrality, scored_at=:scored_at,"
        " run_date=:run_date, in_centrality=:in_centrality,"
        " out_centrality=:out_centrality WHERE org_id=:org_id AND entity_id=:entity_id",
        source_engine, target_engine, org_id,
        since=since, dry_run=dry_run, since_col="scored_at",
    )


def _sync_unique_org_entity(
    table: str,
    select_sql: str,
    insert_sql: str,
    update_sql: str,
    source_engine: Engine,
    target_engine: Engine,
    org_id: str,
    *,
    since: str | None,
    dry_run: bool,
    since_col: str,
):
    sql = select_sql
    params: dict[str, Any] = {"org_id": org_id}
    if since:
        sql += f" AND {since_col} >= :since"
        params["since"] = since
    rows = _read_org_rows(source_engine, sql, params)
    result = TableSyncResult(table=table, rows_read=len(rows))
    if not rows:
        return result
    if dry_run:
        result.rows_written = len(rows)
        return result

    with target_engine.begin() as conn:
        for row in rows:
            existing = conn.execute(
                text(
                    f"SELECT 1 FROM {table} WHERE org_id=:org_id AND entity_id=:eid"
                ),
                {"org_id": org_id, "eid": row["entity_id"]},
            ).fetchone()
            if existing:
                conn.execute(text(update_sql), row)
                result.rows_skipped += 1
            else:
                conn.execute(text(insert_sql), row)
                result.rows_written += 1
    return result


def _sync_discord_pulse(source_engine, target_engine, org_id, *, since, dry_run):
    table = "discord_pulse_runs"
    sql = (
        "SELECT org_id, project_slug, run_date, wow_retention_rate, echo_rate,"
        " avg_silence_gap_hours, weekly_active_posters, retention_delta,"
        " echo_rate_delta, created_at FROM discord_pulse_runs WHERE org_id=:org_id"
    )
    params: dict[str, Any] = {"org_id": org_id}
    if since:
        sql += " AND created_at >= :since"
        params["since"] = since
    rows = _read_org_rows(source_engine, sql, params)
    result = TableSyncResult(table=table, rows_read=len(rows))
    if not rows:
        return result
    if dry_run:
        result.rows_written = len(rows)
        return result

    with target_engine.begin() as conn:
        for row in rows:
            existing = conn.execute(
                text(
                    "SELECT 1 FROM discord_pulse_runs WHERE org_id=:o"
                    " AND project_slug=:s AND run_date=:d"
                ),
                {"o": org_id, "s": row["project_slug"], "d": row["run_date"]},
            ).fetchone()
            if existing:
                conn.execute(
                    text(
                        "UPDATE discord_pulse_runs SET wow_retention_rate=:wow,"
                        " echo_rate=:er, avg_silence_gap_hours=:asg,"
                        " weekly_active_posters=:wap, retention_delta=:rd,"
                        " echo_rate_delta=:erd WHERE org_id=:org_id"
                        " AND project_slug=:project_slug AND run_date=:run_date"
                    ),
                    {**row,
                     "wow": row["wow_retention_rate"],
                     "er": row["echo_rate"],
                     "asg": row["avg_silence_gap_hours"],
                     "wap": row["weekly_active_posters"],
                     "rd": row["retention_delta"],
                     "erd": row["echo_rate_delta"]},
                )
                result.rows_skipped += 1
            else:
                conn.execute(
                    text(
                        "INSERT INTO discord_pulse_runs (org_id, project_slug,"
                        " run_date, wow_retention_rate, echo_rate,"
                        " avg_silence_gap_hours, weekly_active_posters,"
                        " retention_delta, echo_rate_delta, created_at) VALUES"
                        " (:org_id, :project_slug, :run_date, :wow_retention_rate,"
                        " :echo_rate, :avg_silence_gap_hours,"
                        " :weekly_active_posters, :retention_delta,"
                        " :echo_rate_delta, :created_at)"
                    ),
                    row,
                )
                result.rows_written += 1
    return result


def _sync_metric_snapshots(source_engine, target_engine, org_id, *, since, dry_run):
    table = "metric_snapshots"
    sql = (
        "SELECT org_id, snapshot_date, metrics_json, source, created_at"
        " FROM metric_snapshots WHERE org_id=:org_id"
    )
    params: dict[str, Any] = {"org_id": org_id}
    if since:
        sql += " AND created_at >= :since"
        params["since"] = since
    rows = _read_org_rows(source_engine, sql, params)
    result = TableSyncResult(table=table, rows_read=len(rows))
    if not rows:
        return result
    if dry_run:
        result.rows_written = len(rows)
        return result

    with target_engine.begin() as conn:
        for row in rows:
            existing = conn.execute(
                text(
                    "SELECT 1 FROM metric_snapshots WHERE org_id=:o"
                    " AND snapshot_date=:d"
                ),
                {"o": org_id, "d": row["snapshot_date"]},
            ).fetchone()
            if existing:
                conn.execute(
                    text(
                        "UPDATE metric_snapshots SET metrics_json=:metrics_json,"
                        " source=:source WHERE org_id=:org_id"
                        " AND snapshot_date=:snapshot_date"
                    ),
                    row,
                )
                result.rows_skipped += 1
            else:
                conn.execute(
                    text(
                        "INSERT INTO metric_snapshots (org_id, snapshot_date,"
                        " metrics_json, source, created_at) VALUES (:org_id,"
                        " :snapshot_date, :metrics_json, :source, :created_at)"
                    ),
                    row,
                )
                result.rows_written += 1
    return result


def _sync_playbook_targets(source_engine, target_engine, org_id, *, since, dry_run):
    return _sync_append_with_cursor(
        "playbook_targets",
        "SELECT org_id, artifact_id, targets_json, created_at FROM playbook_targets"
        " WHERE org_id=:org_id",
        "INSERT INTO playbook_targets (org_id, artifact_id, targets_json, created_at)"
        " VALUES (:org_id, :artifact_id, :targets_json, :created_at)",
        source_engine, target_engine, org_id,
        since=since, dry_run=dry_run, cursor_col="created_at",
    )


def _sync_playbook_outcomes(source_engine, target_engine, org_id, *, since, dry_run):
    return _sync_append_with_cursor(
        "playbook_outcomes",
        "SELECT org_id, targets_artifact_id, outcomes_json, created_at"
        " FROM playbook_outcomes WHERE org_id=:org_id",
        "INSERT INTO playbook_outcomes (org_id, targets_artifact_id, outcomes_json,"
        " created_at) VALUES (:org_id, :targets_artifact_id, :outcomes_json,"
        " :created_at)",
        source_engine, target_engine, org_id,
        since=since, dry_run=dry_run, cursor_col="created_at",
    )


def _sync_cost_events(source_engine, target_engine, org_id, *, since, dry_run):
    return _sync_append_with_cursor(
        "cost_events",
        "SELECT org_id, job_id, call_type, model, input_tokens, output_tokens,"
        " cost_usd, call_status, created_at FROM cost_events WHERE org_id=:org_id",
        "INSERT INTO cost_events (org_id, job_id, call_type, model, input_tokens,"
        " output_tokens, cost_usd, call_status, created_at) VALUES (:org_id, :job_id,"
        " :call_type, :model, :input_tokens, :output_tokens, :cost_usd, :call_status,"
        " :created_at)",
        source_engine, target_engine, org_id,
        since=since, dry_run=dry_run, cursor_col="created_at",
        # cost_events.job_id is FK; null it out if the target doesn't know the job.
        scrub_job_id=True,
    )


def _sync_append_with_cursor(
    table: str,
    select_sql: str,
    insert_sql: str,
    source_engine: Engine,
    target_engine: Engine,
    org_id: str,
    *,
    since: str | None,
    dry_run: bool,
    cursor_col: str,
    scrub_job_id: bool = False,
):
    cursor_value: str | None = None
    if not dry_run:
        with target_engine.connect() as conn:
            cursor_value = _read_cursor(conn, table, org_id)
    effective_since = _max_str([cursor_value, since])

    sql = select_sql
    params: dict[str, Any] = {"org_id": org_id}
    if effective_since:
        sql += f" AND {cursor_col} > :since"
        params["since"] = effective_since
    sql += f" ORDER BY {cursor_col} ASC"
    rows = _read_org_rows(source_engine, sql, params)
    result = TableSyncResult(
        table=table, rows_read=len(rows), cursor_before=cursor_value,
    )
    if not rows:
        return result
    if dry_run:
        result.rows_written = len(rows)
        return result

    new_cursor: str | None = cursor_value
    with target_engine.begin() as conn:
        for row in rows:
            row_to_write = dict(row)
            if scrub_job_id and row_to_write.get("job_id"):
                tgt_job = conn.execute(
                    text("SELECT 1 FROM jobs WHERE job_id=:j"),
                    {"j": row_to_write["job_id"]},
                ).fetchone()
                if not tgt_job:
                    row_to_write["job_id"] = None
            conn.execute(text(insert_sql), row_to_write)
            result.rows_written += 1
            ts = row.get(cursor_col)
            if ts and (new_cursor is None or ts > new_cursor):
                new_cursor = ts
        if new_cursor and new_cursor != cursor_value:
            _write_cursor(conn, table, org_id, new_cursor)
            result.cursor_after = new_cursor
    return result


def _sync_merge_candidates(source_engine, target_engine, org_id, *, since, dry_run):
    table = "merge_candidates"
    sql = (
        "SELECT mc.entity_a_id, mc.entity_b_id, mc.confidence, mc.reason, mc.status,"
        " mc.created_at, mc.updated_at"
        " FROM merge_candidates mc JOIN entities e ON e.entity_id = mc.entity_a_id"
        " WHERE e.org_id=:org_id"
    )
    params: dict[str, Any] = {"org_id": org_id}
    if since:
        sql += " AND mc.updated_at >= :since"
        params["since"] = since
    rows = _read_org_rows(source_engine, sql, params)
    result = TableSyncResult(table=table, rows_read=len(rows))
    if not rows:
        return result
    if dry_run:
        result.rows_written = len(rows)
        return result

    with target_engine.begin() as conn:
        for row in rows:
            existing = conn.execute(
                text(
                    "SELECT 1 FROM merge_candidates"
                    " WHERE entity_a_id=:a AND entity_b_id=:b"
                ),
                {"a": row["entity_a_id"], "b": row["entity_b_id"]},
            ).fetchone()
            if existing:
                conn.execute(
                    text(
                        "UPDATE merge_candidates SET confidence=:confidence,"
                        " reason=:reason, status=:status, updated_at=:updated_at"
                        " WHERE entity_a_id=:entity_a_id AND entity_b_id=:entity_b_id"
                    ),
                    row,
                )
                result.rows_skipped += 1
            else:
                conn.execute(
                    text(
                        "INSERT INTO merge_candidates (entity_a_id, entity_b_id,"
                        " confidence, reason, status, created_at, updated_at)"
                        " VALUES (:entity_a_id, :entity_b_id, :confidence, :reason,"
                        " :status, :created_at, :updated_at)"
                    ),
                    row,
                )
                result.rows_written += 1
    return result


# FK-safe order. Each function takes (source, target, org_id, *, since, dry_run).
_TABLE_SYNCS = [
    _sync_entities,
    _sync_entity_handles,
    _sync_diagnostic_runs,
    _sync_sync_runs,
    _sync_artifacts,
    _sync_entity_tags,
    _sync_entity_tag_history,
    _sync_entity_interactions,
    _sync_decay_scores,
    _sync_centrality_scores,
    _sync_discord_pulse,
    _sync_metric_snapshots,
    _sync_playbook_targets,
    _sync_playbook_outcomes,
    _sync_cost_events,
    _sync_merge_candidates,
]
