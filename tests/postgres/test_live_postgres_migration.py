"""Live PostgreSQL integration tests for migration and runtime writes."""
from __future__ import annotations

from sqlalchemy import create_engine, text

from sable_platform.db.audit import log_audit
from sable_platform.db.entities import add_entity_note
from sable_platform.db.jobs import add_step, create_job
from sable_platform.db.migrate_pg import run_migration
from sable_platform.db.playbook import record_playbook_outcomes, upsert_playbook_targets
from sable_platform.db.webhooks import create_subscription


def test_alembic_upgrade_creates_live_postgres_schema(postgres_db_url):
    """Alembic must create the expected schema on a real PostgreSQL database."""
    from sable_platform.db.migrate_pg import _run_alembic_upgrade

    _run_alembic_upgrade(postgres_db_url)
    engine = create_engine(postgres_db_url)
    try:
        with engine.connect() as conn:
            alembic_version = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            schema_table_exists = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables"
                    " WHERE table_schema='public' AND table_name='schema_version'"
                )
            ).scalar_one()
            org_table_exists = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables"
                    " WHERE table_schema='public' AND table_name='orgs'"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert alembic_version
    assert schema_table_exists == 1
    assert org_table_exists == 1


def test_sqlite_to_postgres_migration_resets_sequences_for_runtime_writes(
    sqlite_source_engine,
    postgres_db_url,
):
    """Migration must preserve data and reset integer sequences for new writes."""
    with sqlite_source_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO schema_version (version) VALUES (30)")
        )
        conn.execute(
            text("INSERT INTO orgs (org_id, display_name) VALUES ('mig_org', 'Migrated Org')")
        )
        conn.execute(
            text(
                "INSERT INTO entities (entity_id, org_id, display_name)"
                " VALUES ('ent_mig', 'mig_org', 'Migrated Entity')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO entity_notes (note_id, entity_id, body, source)"
                " VALUES (41, 'ent_mig', 'seed note', 'seed')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO jobs (job_id, org_id, job_type, status, config_json)"
                " VALUES ('job_mig', 'mig_org', 'seed', 'pending', '{}')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO job_steps"
                " (step_id, job_id, step_name, step_order, status, input_json)"
                " VALUES (8, 'job_mig', 'seed_step', 0, 'completed', '{}')"
            )
        )

    target_engine = create_engine(postgres_db_url)
    try:
        report = run_migration(sqlite_source_engine, target_engine)
        assert report.status == "success"

        with target_engine.connect() as sa_conn:
            from sable_platform.db.compat_conn import CompatConnection

            conn = CompatConnection(sa_conn)
            note_id = add_entity_note(conn, "ent_mig", "new postgres note", source="manual")
            step_id = add_step(conn, "job_mig", "postgres_step", step_order=1)

        assert note_id == 42
        assert step_id == 9
    finally:
        target_engine.dispose()


def test_live_postgres_helpers_return_inserted_ids(postgres_conn):
    """Core helper paths must return usable integer IDs on PostgreSQL."""
    postgres_conn.execute(
        "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)",
        ("pg_org", "Postgres Org"),
    )
    postgres_conn.execute(
        "INSERT INTO entities (entity_id, org_id, display_name) VALUES (?, ?, ?)",
        ("pg_entity", "pg_org", "PG Entity"),
    )
    postgres_conn.commit()

    note_id = add_entity_note(postgres_conn, "pg_entity", "hello from pg", source="manual")
    audit_id = log_audit(postgres_conn, "cli:test", "pg_smoke", org_id="pg_org", entity_id="pg_entity")
    job_id = create_job(postgres_conn, "pg_org", "postgres_smoke", config={"source": "test"})
    step_id = add_step(postgres_conn, job_id, "first_step", step_order=0, input_data={"ok": True})
    subscription_id = create_subscription(
        postgres_conn,
        "pg_org",
        "https://example.com/webhooks/test",
        ["workflow.run_started"],
        "1234567890abcdef",
    )
    target_id = upsert_playbook_targets(
        postgres_conn,
        "pg_org",
        [{"metric": "reply_rate", "target": 0.2}],
    )
    outcome_id = record_playbook_outcomes(
        postgres_conn,
        "pg_org",
        {"reply_rate": 0.18},
    )

    assert note_id >= 1
    assert audit_id >= 1
    assert step_id >= 1
    assert subscription_id >= 1
    assert target_id >= 1
    assert outcome_id >= 1

    assert postgres_conn.execute(
        "SELECT body FROM entity_notes WHERE note_id=?",
        (note_id,),
    ).fetchone()["body"] == "hello from pg"
    assert postgres_conn.execute(
        "SELECT action FROM audit_log WHERE id=?",
        (audit_id,),
    ).fetchone()["action"] == "pg_smoke"
    assert postgres_conn.execute(
        "SELECT step_name FROM job_steps WHERE step_id=?",
        (step_id,),
    ).fetchone()["step_name"] == "first_step"
    assert postgres_conn.execute(
        "SELECT url FROM webhook_subscriptions WHERE id=?",
        (subscription_id,),
    ).fetchone()["url"] == "https://example.com/webhooks/test"
    assert postgres_conn.execute(
        "SELECT id FROM playbook_targets WHERE id=?",
        (target_id,),
    ).fetchone()["id"] == target_id
    assert postgres_conn.execute(
        "SELECT id FROM playbook_outcomes WHERE id=?",
        (outcome_id,),
    ).fetchone()["id"] == outcome_id
