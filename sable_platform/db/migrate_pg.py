"""SQLite -> Postgres data migration for sable.db.

Reads all rows from a SQLite sable.db, creates the Postgres schema via
Alembic, copies data in FK-safe order, resets sequences, and validates
row counts.

Usage (via CLI):
    sable-platform migrate to-postgres --target-url postgresql://...
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, text

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE = 1000

# All 40 tables in FK-safe insertion order (parents before children).
# Derived from sable_platform/db/schema.py ForeignKey declarations.
TABLE_LOAD_ORDER: list[str] = [
    # Tier 0 — no FKs
    "schema_version",
    "platform_meta",
    "orgs",
    # Tier 1 — FK -> orgs, then cascading entity/job/workflow deps
    "entities",
    "jobs",
    "workflow_runs",
    "content_items",         # FK -> orgs, entities
    "actions",               # FK -> orgs, entities, content_items
    "outcomes",              # FK -> orgs, entities, actions
    "alerts",                # FK -> orgs, entities, actions, workflow_runs
    "diagnostic_runs",
    "diagnostic_deltas",
    "sync_runs",
    "cost_events",           # FK -> orgs, jobs
    "artifacts",             # FK -> orgs, jobs
    "alert_configs",
    "entity_interactions",
    "entity_decay_scores",
    "entity_centrality_scores",
    "entity_watchlist",
    "watchlist_snapshots",
    "audit_log",
    "webhook_subscriptions",
    "prospect_scores",
    "playbook_targets",
    "playbook_outcomes",
    "metric_snapshots",
    "discord_pulse_runs",
    # SableKOL bank (mig 032) — kol_candidates has no FKs; project_profiles_external
    # has no FKs; kol_handle_resolution_conflicts FK -> kol_candidates.
    "kol_candidates",
    "project_profiles_external",
    # SableKOL follow-graph (mig 037) — kol_extract_runs has no FKs;
    # kol_follow_edges FK -> kol_extract_runs.
    "kol_extract_runs",
    # Tier 2 — FK -> entities
    "entity_handles",
    "entity_tags",
    "entity_notes",
    "merge_candidates",      # FK -> entities (both a and b)
    "entity_tag_history",
    # Tier 3 — FK -> merge_candidates
    "merge_events",
    # Tier 4 — FK -> jobs, workflow_runs, kol_candidates
    "job_steps",
    "workflow_steps",
    "workflow_events",
    "kol_handle_resolution_conflicts",  # FK -> kol_candidates
    "kol_follow_edges",  # FK -> kol_extract_runs
    "kol_operator_relationships",  # no FKs (client_id + handle_normalized are loose)
    "kol_create_audit",  # FK -> jobs (mig 040)
]

# Tables with Integer autoincrement PKs that need Postgres sequence resets.
# Maps table_name -> pk_column_name.
SEQUENCE_TABLES: dict[str, str] = {
    "entity_handles": "handle_id",
    "entity_tags": "tag_id",
    "entity_notes": "note_id",
    "merge_candidates": "candidate_id",
    "merge_events": "event_id",
    "diagnostic_runs": "run_id",
    "job_steps": "step_id",
    "artifacts": "artifact_id",
    "cost_events": "event_id",
    "sync_runs": "sync_id",
    "discord_pulse_runs": "id",
    "entity_interactions": "id",
    "entity_decay_scores": "id",
    "entity_centrality_scores": "id",
    "entity_watchlist": "id",
    "watchlist_snapshots": "id",
    "audit_log": "id",
    "webhook_subscriptions": "id",
    "prospect_scores": "id",
    "playbook_targets": "id",
    "playbook_outcomes": "id",
    "metric_snapshots": "id",
    "kol_candidates": "candidate_id",
    "kol_handle_resolution_conflicts": "conflict_id",
    "kol_operator_relationships": "id",
    "kol_create_audit": "id",
}

# Tables with Text primary keys that SQLite allowed to be NULL.
# Used to generate UUIDs for NULL PKs during migration.
_TEXT_PK_COLUMNS: dict[str, str] = {
    "orgs": "org_id",
    "entities": "entity_id",
    "content_items": "item_id",
    "diagnostic_deltas": "delta_id",
    "jobs": "job_id",
    "workflow_runs": "run_id",
    "workflow_steps": "step_id",
    "workflow_events": "event_id",
    "actions": "action_id",
    "outcomes": "outcome_id",
    "entity_tag_history": "history_id",
    "alert_configs": "config_id",
    "alerts": "alert_id",
    "platform_meta": "key",
    "project_profiles_external": "handle_normalized",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TableResult:
    table_name: str
    source_rows: int
    target_rows: int
    status: str  # "ok" | "skipped" | "error"
    error: str | None = None


@dataclass
class MigrationReport:
    status: str  # "success" | "failed"
    tables: list[TableResult] = field(default_factory=list)
    total_source_rows: int = 0
    total_target_rows: int = 0
    error: str | None = None


class MigrationError(Exception):
    """Raised when migration fails — transaction is rolled back."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_migration(
    source_engine: Engine,
    target_engine: Engine,
    *,
    force: bool = False,
) -> MigrationReport:
    """Orchestrate full SQLite -> Postgres data migration.

    Steps:
      1. Validate engines (source=SQLite, target=Postgres or SQLite for tests)
      2. Run Alembic upgrade head on target (Postgres only)
      3. Check target is empty (or ``--force`` to truncate)
      4. Copy all tables in FK-safe order
      5. Reset Postgres sequences for autoincrement tables
      6. Validate row counts match

    Returns a :class:`MigrationReport`.  Raises :class:`MigrationError` on
    failure (transaction is rolled back, target is unchanged).
    """
    is_pg = target_engine.dialect.name == "postgresql"

    # 1. Validate source
    if source_engine.dialect.name != "sqlite":
        raise MigrationError(
            f"Source must be SQLite, got {source_engine.dialect.name!r}"
        )

    # 2. Alembic schema creation (Postgres only)
    if is_pg:
        target_url = str(target_engine.url)
        log.info("Running Alembic upgrade head on target...")
        _run_alembic_upgrade(target_url)

    # 3. Check target emptiness
    needs_truncate = False
    if not _check_target_empty(target_engine, TABLE_LOAD_ORDER):
        if not force:
            raise MigrationError(
                "Target database is not empty. Use --force to truncate before migration."
            )
        needs_truncate = True

    # 4. Copy all tables (single transaction — truncate + copy are atomic)
    report = MigrationReport(status="success")
    with target_engine.begin() as conn:
        try:
            if needs_truncate:
                log.warning("--force: truncating target tables...")
                _truncate_target(conn, TABLE_LOAD_ORDER, is_pg=is_pg)
            # Disable FK triggers for the duration of the copy (Postgres only)
            if is_pg:
                for tbl in TABLE_LOAD_ORDER:
                    conn.execute(text(f'ALTER TABLE "{tbl}" DISABLE TRIGGER ALL'))

            for table_name in TABLE_LOAD_ORDER:
                rows = _read_all_rows(source_engine, table_name)
                if not rows:
                    report.tables.append(TableResult(
                        table_name=table_name, source_rows=0,
                        target_rows=0, status="skipped",
                    ))
                    continue

                # Fix NULL Text PKs — SQLite allows them, Postgres doesn't.
                pk_col = _TEXT_PK_COLUMNS.get(table_name)
                if pk_col:
                    fixed = 0
                    for row in rows:
                        if row.get(pk_col) is None:
                            row[pk_col] = uuid.uuid4().hex
                            fixed += 1
                    if fixed:
                        log.warning(
                            "Fixed %d NULL %s values in %s",
                            fixed, pk_col, table_name,
                        )

                columns = list(rows[0].keys())
                inserted = 0
                for i in range(0, len(rows), BATCH_SIZE):
                    batch = rows[i : i + BATCH_SIZE]
                    inserted += _insert_batch(conn, table_name, columns, batch)

                report.tables.append(TableResult(
                    table_name=table_name, source_rows=len(rows),
                    target_rows=inserted, status="ok",
                ))
                log.info("Copied %s: %d rows", table_name, inserted)

            # 5. Reset sequences (Postgres only)
            if is_pg:
                _reset_sequences(conn, SEQUENCE_TABLES)

            # Re-enable FK triggers (Postgres only)
            if is_pg:
                for tbl in TABLE_LOAD_ORDER:
                    conn.execute(text(f'ALTER TABLE "{tbl}" ENABLE TRIGGER ALL'))

        except Exception as exc:
            report.status = "failed"
            report.error = str(exc)
            raise MigrationError(f"Migration failed during copy: {exc}") from exc

    # 6. Validate counts
    validation = _validate_counts(source_engine, target_engine, TABLE_LOAD_ORDER)
    mismatches = [r for r in validation if r.source_rows != r.target_rows]
    if mismatches:
        details = ", ".join(
            f"{r.table_name} (src={r.source_rows}, tgt={r.target_rows})"
            for r in mismatches
        )
        report.status = "failed"
        report.error = f"Row count mismatch: {details}"
        raise MigrationError(report.error)

    report.total_source_rows = sum(r.source_rows for r in report.tables)
    report.total_target_rows = sum(r.target_rows for r in report.tables)
    return report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_all_rows(engine: Engine, table_name: str) -> list[dict[str, Any]]:
    """Read all rows from a table as a list of dicts."""
    with engine.connect() as conn:
        result = conn.execute(text(f'SELECT * FROM "{table_name}"'))
        return [dict(row._mapping) for row in result]


def _insert_batch(
    conn: Any,
    table_name: str,
    columns: list[str],
    rows: list[dict[str, Any]],
) -> int:
    """Insert a batch of rows using SA text() with :named params.

    Returns the number of rows inserted.
    """
    if not rows:
        return 0

    col_list = ", ".join(f'"{c}"' for c in columns)
    param_list = ", ".join(f":{c}" for c in columns)
    sql = f'INSERT INTO "{table_name}" ({col_list}) VALUES ({param_list})'

    conn.execute(text(sql), rows)
    return len(rows)


def _reset_sequences(conn: Any, tables: dict[str, str]) -> None:
    """Reset Postgres sequences to match the current max PK value.

    For each table with an autoincrement Integer PK:
    - If table has rows: setval(seq, max_val, true) -> next value = max_val + 1
    - If table is empty: setval(seq, 1, false) -> next value = 1

    Uses pg_get_serial_sequence with fallback to the conventional
    ``{table}_{col}_seq`` name (Alembic-created columns may not have
    the ownership link that pg_get_serial_sequence requires).
    """
    for table_name, pk_col in tables.items():
        # Resolve sequence name: try pg_get_serial_sequence first, fall back
        # to conventional naming if it returns NULL.
        seq_row = conn.execute(text(
            "SELECT pg_get_serial_sequence(:table_name, :column_name) AS seq"
        ), {"table_name": table_name, "column_name": pk_col}).fetchone()
        seq_name = seq_row[0] if seq_row and seq_row[0] else None

        if not seq_name:
            # Conventional Postgres sequence name for SERIAL columns
            seq_name = f"{table_name}_{pk_col}_seq"
            log.debug(
                "pg_get_serial_sequence returned NULL for %s.%s, "
                "using conventional name: %s",
                table_name, pk_col, seq_name,
            )

        conn.execute(text(f"""
            SELECT setval(
                :seq_name,
                COALESCE((SELECT MAX("{pk_col}") FROM "{table_name}"), 1),
                (SELECT MAX("{pk_col}") IS NOT NULL FROM "{table_name}")
            )
        """), {"seq_name": seq_name})
        log.debug("Reset sequence %s for %s.%s", seq_name, table_name, pk_col)


def _validate_counts(
    source_engine: Engine,
    target_engine: Engine,
    tables: list[str],
) -> list[TableResult]:
    """Compare row counts per table between source and target."""
    results: list[TableResult] = []
    for table_name in tables:
        with source_engine.connect() as src_conn:
            src_count = src_conn.execute(
                text(f'SELECT COUNT(*) FROM "{table_name}"')
            ).scalar() or 0
        with target_engine.connect() as tgt_conn:
            tgt_count = tgt_conn.execute(
                text(f'SELECT COUNT(*) FROM "{table_name}"')
            ).scalar() or 0
        status = "ok" if src_count == tgt_count else "error"
        results.append(TableResult(
            table_name=table_name,
            source_rows=src_count,
            target_rows=tgt_count,
            status=status,
            error=f"count mismatch: {src_count} vs {tgt_count}" if status == "error" else None,
        ))
    return results


def _check_target_empty(engine: Engine, tables: list[str]) -> bool:
    """Return True if all tables in the target have zero rows."""
    with engine.connect() as conn:
        for table_name in tables:
            try:
                count = conn.execute(
                    text(f'SELECT COUNT(*) FROM "{table_name}"')
                ).scalar() or 0
            except Exception as exc:
                # Table may not exist yet (pre-Alembic) — treat as empty
                log.debug("Skipping emptiness check for %s: %s", table_name, exc)
                continue
            if count > 0:
                return False
    return True


def _truncate_target(conn: Any, tables: list[str], *, is_pg: bool) -> None:
    """Clear all tables in reverse FK order."""
    for table_name in reversed(tables):
        if is_pg:
            conn.execute(text(f'TRUNCATE TABLE "{table_name}" CASCADE'))
        else:
            conn.execute(text(f'DELETE FROM "{table_name}"'))


def _run_alembic_upgrade(database_url: str) -> None:
    """Programmatically run ``alembic upgrade head``."""
    import importlib.resources

    from alembic import command
    from alembic.config import Config

    # Resolve script_location from package-owned assets so migrations work
    # from installed wheels and container images, not just source checkouts.
    alembic_root = importlib.resources.files("sable_platform.alembic")

    with importlib.resources.as_file(alembic_root) as script_dir:
        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", str(script_dir))
        alembic_cfg.set_main_option("sqlalchemy.url", database_url)

        # Suppress Alembic's default logging to avoid leaking credentials
        # in the connection URL.  Errors still propagate as exceptions.
        logging.getLogger("alembic").setLevel(logging.WARNING)

        command.upgrade(alembic_cfg, "head")
