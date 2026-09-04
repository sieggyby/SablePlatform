"""Shared connection factory and migration runner for sable.db.

Legacy ``get_db()`` returns a raw ``sqlite3.Connection``.  The new
``get_sa_engine()`` / ``get_sa_connection()`` functions return SQLAlchemy
objects and are the migration path toward Postgres support.
"""
from __future__ import annotations

import importlib.resources
import logging
import os
import sqlite3
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.engine import Connection as SAConnection

log = logging.getLogger(__name__)

_MIGRATIONS = [
    ("001_initial.sql", 1),
    ("002_sync_runs_run_id.sql", 2),
    ("003_diagnostic_runs_cult_columns.sql", 3),
    ("004_jobs_extend.sql", 4),
    ("005_artifacts_degraded.sql", 5),
    ("006_workflow_tables.sql", 6),
    ("007_actions_outcomes.sql", 7),
    ("008_entity_journey.sql", 8),
    ("009_alerts.sql", 9),
    ("010_discord_pulse_runs.sql", 10),
    ("011_alert_cooldown.sql", 11),
    ("012_workflow_version.sql", 12),
    ("013_alert_delivery_error.sql", 13),
    ("014_entity_interactions.sql", 14),
    ("015_entity_decay_scores.sql", 15),
    ("016_entity_centrality.sql", 16),
    ("017_entity_watchlist.sql", 17),
    ("018_audit_log.sql", 18),
    ("019_webhooks.sql", 19),
    ("020_prospect_scores.sql", 20),
    ("021_run_summary_blob.sql", 21),
    ("022_playbook_tagging.sql", 22),
    ("023_centrality_schema_align.sql", 23),
    ("024_operator_identity_and_indexes.sql", 24),
    ("025_prospect_graduation.sql", 25),
    ("026_prospect_rejection.sql", 26),
    ("027_workflow_active_lock.sql", 27),
    ("028_platform_meta.sql", 28),
    ("029_prospect_score_fields.sql", 29),
    ("030_performance_indexes.sql", 30),
    ("031_metric_snapshots.sql", 31),
    ("032_kol_bank.sql", 32),
    ("033_kol_strength_score.sql", 33),
    ("034_kol_grok_enrich.sql", 34),
    ("035_kol_location.sql", 35),
    ("036_kol_platform_presence.sql", 36),
    ("037_kol_follow_edges.sql", 37),
    ("038_kol_operator_relationships.sql", 38),
    ("039_kol_extract_runs_client_id.sql", 39),
    ("040_kol_wizard_infra.sql", 40),
    ("041_kol_enrichment.sql", 41),
    ("042_kol_create_audit_review.sql", 42),
    ("043_discord_streak_events.sql", 43),
    ("044_api_tokens.sql", 44),
    ("045_discord_guild_config.sql", 45),
    ("046_discord_burn.sql", 46),
    ("047_roast_personalization.sql", 47),
    ("048_airlock.sql", 48),
    ("049_discord_streak_events_phash.sql", 49),
    ("050_discord_fitcheck_scores.sql", 50),
    ("051_discord_scoring_config.sql", 51),
    ("052_discord_fitcheck_emoji_milestones.sql", 52),
    ("053_align_timestamp_columns_to_text.sql", 53),
    ("054_discord_state_pins.sql", 54),
    ("055_media_assets.sql", 55),
    ("056_operator_reply_suggestions.sql", 56),
    ("057_relay.sql", 57),
    ("058_autocm.sql", 58),
    ("059_work_tracking.sql", 59),
    ("060_reply_clip_media_kind.sql", 60),
    ("061_reply_campaigns.sql", 61),
    ("062_reply_opportunity_feed.sql", 62),
    ("063_reply_learning.sql", 63),
    ("064_relay_trending_stories.sql", 64),
    ("065_relay_quality_corpus.sql", 65),
    ("066_media_rec_center.sql", 66),
    ("067_community_audit.sql", 67),
    ("068_make_opportunity_feedback_optional.sql", 68),
    ("069_reply_outcomes_detected_via.sql", 69),
    ("070_community_audit_leads.sql", 70),
    ("071_relay_topic_suggestions.sql", 71),
    ("072_relay_topic_picks.sql", 72),
    ("073_client_onboarding.sql", 73),
    ("074_tweetbank.sql", 74),
    ("075_allowlist_entries.sql", 75),
    ("076_content_deck.sql", 76),
    ("077_content_publish_jobs.sql", 77),
    ("078_operator_meme_budget.sql", 78),
    ("079_deck_assertion_nonce.sql", 79),
    ("080_content_quality_elo.sql", 80),
    ("081_cost_operator_attribution.sql", 81),
    ("082_shared_tweet_cache.sql", 82),
    ("083_community_tweet_kind.sql", 83),
    ("084_content_duels.sql", 84),
    ("085_quality_media_reply.sql", 85),
    ("086_conversation_flags.sql", 86),
    ("087_community_audit_vocab.sql", 87),
    ("088_reply_outcomes_posted_text.sql", 88),
    ("089_cost_events_vendor_units.sql", 89),
    ("090_canonical_text_timestamps.sql", 90),
    ("091_text_timestamp_type_parity.sql", 91),
    ("092_sqlite_canonical_defaults.sql", 92),
]


def sable_db_path() -> Path:
    """Return the resolved path to sable.db (from ``SABLE_DB_PATH`` or default)."""
    env = os.environ.get("SABLE_DB_PATH")
    if env:
        return Path(env)
    return Path.home() / ".sable" / "sable.db"


# Keep private alias for any internal callers.
_sable_db_path = sable_db_path


def sqlite_file_for_url(url: str) -> Path | None:
    """The file a SQLite URL names, or ``None`` if it names no file.

    ``None`` covers three cases that are all "there is no file here": the URL is not SQLite,
    it is in-memory (``sqlite://`` or ``sqlite:///:memory:``), or it is malformed.

    Parse with this, never by splitting on ``"sqlite:///"``. The string version is wrong in
    ways that look fine until they are not, measured: ``sqlite+pysqlite:///x.db`` is a real
    SQLite target that a ``startswith("sqlite:///")`` test misses, bare ``sqlite://`` reads
    as a path, and a query string such as ``?mode=ro`` ends up inside the filename.

    The path comes back EXACTLY as SQLite will see it, with no ``expanduser``. SQLite does
    not expand ``~``: measured, ``sqlite:///~/probe.db`` creates ``./~/probe.db`` relative to
    the working directory, not a file in the home directory. Expanding here would point a
    ``mkdir`` at one directory while the engine opened another, and would answer "does this
    file exist" about the wrong file. :func:`sable_db_path` does not expand either, so
    nothing in the platform does.
    """
    from sqlalchemy.engine import make_url

    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - a malformed URL is the engine's error to raise
        # Swallowing this is deliberate and narrow. Callers only want the file; a URL bad
        # enough that make_url rejects it is about to fail in get_engine with a far better
        # message than anything this function could raise.
        return None
    if parsed.get_backend_name() != "sqlite":
        return None
    database = parsed.database
    if not database or database == ":memory:":
        return None
    return Path(database)


def _mkdir_for_sqlite_url(url: str) -> None:
    """Create the parent directory of a ``sqlite:///`` file URL, if it names one."""
    database = sqlite_file_for_url(url)
    if database is not None:
        database.parent.mkdir(parents=True, exist_ok=True)


def _prepared_engine(db_path: str | Path | None = None):
    """Resolve the platform database target and make it ready to connect.

    Shared by :func:`get_db` and :func:`get_raw_db` so the two cannot drift. The SQLite
    setup here is load-bearing: without the ``mkdir`` a fresh ``SABLE_DB_PATH`` has no
    parent directory, and without ``ensure_schema`` the file has no tables, so the first
    query fails with ``no such table``. PostgreSQL gets neither, because its schema comes
    from Alembic.
    """
    from sable_platform.db.engine import get_engine

    if db_path:
        # Explicit path always wins — don't let env var override a caller's
        # explicit db_path (important for tests, backup, CLI --db-path).
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path}"
    else:
        url = os.environ.get("SABLE_DATABASE_URL")
        if not url:
            path = _sable_db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{path}"
        else:
            # A sqlite URL from the environment needs the same parent directory the
            # SABLE_DB_PATH branch above creates. Without this the two branches disagree:
            # SABLE_DB_PATH=/missing/x.db works and SABLE_DATABASE_URL=sqlite:////missing/x.db
            # fails with "unable to open database file". Only a real file gets a mkdir;
            # ":memory:" and a bare "sqlite://" have no directory to make.
            _mkdir_for_sqlite_url(url)

    engine = get_engine(url)

    if engine.dialect.name == "sqlite":
        # For SQLite, ensure schema via legacy migration path on the raw
        # DBAPI connection, then use SA for everything else.
        raw_proxy = engine.raw_connection()
        try:
            dbapi_conn = raw_proxy.dbapi_connection
            dbapi_conn.row_factory = sqlite3.Row
            ensure_schema(dbapi_conn)
        finally:
            raw_proxy.close()

    return engine


def get_db(db_path: str | Path | None = None):
    """Return a database connection for the platform.

    Returns a :class:`CompatConnection` wrapping a SQLAlchemy connection.
    The wrapper supports both ``?``-positional and ``:named`` parameter
    styles plus ``row["col"]`` dict access, so existing code works unchanged.
    """
    from sable_platform.db.compat_conn import CompatConnection

    return CompatConnection(_prepared_engine(db_path).connect())


def get_raw_db(db_path: str | Path | None = None):
    """Return the RAW SQLAlchemy connection, with the same setup :func:`get_db` performs.

    Same target resolution, same SQLite ``mkdir`` and ``ensure_schema``. The ONLY
    difference from :func:`get_db` is the missing :class:`CompatConnection` wrapper.

    Use this where the caller needs the SQLAlchemy transaction methods that wrapper does
    not carry: ``in_transaction``, ``exec_driver_sql`` and ``execution_options``. The
    relay write boundary calls all three on every message, so
    ``scripts/run_relay_bot.py`` and ``scripts/run_relay_poller.py`` need this rather
    than ``get_db``. Passing them a ``CompatConnection`` raised
    ``AttributeError: 'CompatConnection' object has no attribute 'in_transaction'``.

    A caller that goes straight to ``get_engine(...).connect()`` gets a raw connection
    WITHOUT the SQLite setup, which is a different thing and fails on a fresh database.

    **What deliberately does NOT use this.** Grouped by family rather than counted, because
    a count goes stale the moment someone adds a command.

    * **Reporting on a database.** ``cli/main.py`` ``db-health``. It must not UPGRADE the
      thing it is measuring: ``get_raw_db`` runs ``ensure_schema`` on the SQLite branch, so
      db-health would silently migrate a stale database and then report it healthy. Pinned
      by ``tests/db/test_raw_connection_setup.py``.

      An earlier version of this note said instead that switching openers would make
      db-health's "Database not found" branch unreachable. That is FALSE, and two tests
      written from it could not fail. ``db_health`` returns early for a missing SQLite path
      before it opens anything, so the early return protects that case and the choice of
      opener does not. The hazard is an EXISTING but unmigrated database.
    * **Connecting to an explicit source and target.** ``db/migrate_pg.py``,
      ``db/sync_from_local.py``, ``cli/migrate_cmds.py``, ``cli/sync_cmds.py``. These are
      arbitrary databases rather than "the platform DB", and running the migration path
      against either end would be wrong.
    * **Backing one up.** ``db/backup.py`` opens the SQLite file with raw ``sqlite3`` for
      the online-backup API, and shells out to ``pg_dump`` on PostgreSQL. A backup must
      copy what is there, never migrate it first.
    * **Alembic.** ``alembic/env.py`` owns the PostgreSQL schema and must not have it built
      underneath it.
    * **The ``schema.py`` build path.** ``get_sa_engine`` and ``get_sa_connection``, used by
      the two ``scripts/seed_*.py`` tools. ``get_sa_engine`` builds the SQLite schema with
      ``metadata.create_all`` from ``schema.py``, which is a DIFFERENT path from
      ``ensure_schema``. The two are not interchangeable: ``create_all`` writes no
      ``schema_version`` row, so ``ensure_schema`` reads the version as 0 and replays from
      migration 1 onto existing tables. Measured: ``duplicate column name: cult_run_id``.
      Pinned by ``tests/db/test_raw_connection_setup.py``.

      **This split is an open defect**, recorded as ``DEFECTS_FOUND`` item 12. A database
      built by one path cannot later be opened by the other. Deciding which path the seeds
      should use is that item's work, not this module's. PostgreSQL is unaffected: none of
      this runs there.

    Anything else that opens the platform database raw is a defect, not an exception.
    """
    return _prepared_engine(db_path).connect()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Apply pending migrations to bring sable.db up to current version."""
    try:
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        current = row[0] if row else 0
    except sqlite3.OperationalError:
        current = 0

    migrations_pkg = importlib.resources.files("sable_platform.db") / "migrations"

    for filename, target_version in _MIGRATIONS:
        if current < target_version:
            sql_file = migrations_pkg / filename
            sql = sql_file.read_text(encoding="utf-8")
            stmts = [s.strip() for s in sql.split(";") if s.strip()]
            with conn:
                for stmt in stmts:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                    (target_version,),
                )
            current = target_version
            if target_version == 27:
                _warn_migration_027_autofails(conn)


def _warn_migration_027_autofails(conn: sqlite3.Connection) -> None:
    """Emit a log warning if migration 027 auto-failed any duplicate active runs."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM workflow_runs WHERE error LIKE 'auto-failed by migration 027%'"
        ).fetchone()
        n = row[0] if row else 0
        if n > 0:
            log.warning(
                "Migration 027: auto-failed %d duplicate active workflow run(s) — "
                "query workflow_runs WHERE error LIKE 'auto-failed by migration 027%%' for details",
                n,
            )
    except (sqlite3.OperationalError, SAOperationalError):
        pass  # workflow_runs table absent — migration applied to empty DB


# ---------------------------------------------------------------------------
# SQLAlchemy connection path (new — coexists with legacy get_db)
# ---------------------------------------------------------------------------


def get_sa_engine(url: str | None = None) -> Engine:
    """Return a SQLAlchemy :class:`Engine` for the platform database.

    For SQLite engines the schema is created via :func:`metadata.create_all`
    (idempotent).  For Postgres, Alembic manages migrations separately.

    .. important::
        ``schema.py`` must stay in sync with the SQL migration files listed
        in ``_MIGRATIONS``.  The parity tests in ``tests/db/test_schema.py``
        verify this mechanically.
    """
    from sable_platform.db.engine import get_engine
    from sable_platform.db.schema import metadata

    engine = get_engine(url)
    if engine.dialect.name == "sqlite":
        metadata.create_all(engine)
    return engine


def get_sa_connection(url: str | None = None) -> SAConnection:
    """Convenience: return an open SQLAlchemy :class:`Connection`.

    Callers are responsible for calling ``conn.close()`` when done (or using
    the connection as a context manager).
    """
    return get_sa_engine(url).connect()
