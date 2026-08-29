"""canonical TEXT timestamp spelling (migration 090)

Every timestamp in this schema is TEXT. That works only while one spelling is used, and it
was not: ``server_default=func.now()`` renders ``now()`` on PostgreSQL and
``CURRENT_TIMESTAMP`` on SQLite, both producing ``'2026-08-29 12:00:00...'`` with a SPACE,
while 61 Python call sites write ``'2026-08-29T12:00:00Z'`` with a ``T``. Space is ``0x20``
and ``T`` is ``0x54``. Every comparison, ``ORDER BY``, ``MIN`` and ``MAX`` over one of these
columns was therefore decided by the separator character before it reached the clock.

Campaign 2 fixed the read side one call site at a time and two audit rounds each found more.
That is the signature of a defect in the schema, not in the callers: 191 sites still match
the shape and every future writer is free to reintroduce the mix. This migration fixes the
WRITE side once, for all 162 columns.

Two statements per column:

1. ``SET DEFAULT`` to ``to_char(now() AT TIME ZONE 'UTC', ...)``. Catalog only, instant.
   ``AT TIME ZONE 'UTC'`` first, so the session TimeZone cannot reach the value: measured, a
   naive render under Asia/Tokyo is nine hours out.
2. Backfill the existing rows into the same spelling.

**Second precision with no fractional part is load-bearing, not a rounding choice.**
Lexicographic order equals chronological order only while every value is the same width.
``'...T12:00:00.5Z'`` sorts BELOW ``'...T12:00:00Z'`` because ``.`` is ``0x2E`` and ``Z`` is
``0x5A``. A backfill that kept sub-second precision would leave the defect in place for any
row that had it.

**The backfill converts only what it can read.** A value outside ``_PARSEABLE`` is left
untouched and counted rather than fed to a cast that would abort the whole migration. The
count is printed. A non-zero count is not a failure, it is a list of rows to look at.

**Lock profile.** ``SET DEFAULT`` takes ACCESS EXCLUSIVE for the length of a catalog write.
The ``UPDATE`` takes ROW EXCLUSIVE and does not block readers, but it rewrites every
non-canonical row and Alembic holds one transaction for the whole migration. On a large
``cost_events`` or ``relay_messages`` that is minutes, not seconds. Run it in a window.

**What this does NOT do.** It does not change the column TYPE. Native ``timestamptz`` is the
better end state and it changes what a read RETURNS: ``datetime`` on PostgreSQL against
``str`` on SQLite, from the raw ``text()`` queries this codebase uses everywhere. That breaks
callers by the hundred, so it is separate work. It also does not add a CHECK constraint. A
constraint converts today's silent wrong answer into a hard failure for any writer this
migration missed, which is a trade for the operator to make, not this migration.

SQLite keeps the old default. SQLite cannot ``ALTER COLUMN``, and its databases are built
from ``schema.py`` rather than from this chain, so a new SQLite database already gets the
canonical default. An existing one gets the backfill and keeps its old default until it is
rebuilt.

Revision ID: b0c1d2e3f090
Revises: a9b0c1d2e089
Create Date: 2026-08-29
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b0c1d2e3f090"
down_revision = "a9b0c1d2e089"
branch_labels = None
depends_on = None


# Pinned copies of sable_platform.db.ts_format. A migration must keep doing the same thing
# after the module moves on, so these are duplicated deliberately rather than imported.
_PG_NOW = "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
_CANONICAL = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
_PARSEABLE = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]+)?(Z|[+-][0-9]{2}(:?[0-9]{2})?)?$"
)

# Every TEXT column whose server default was ``func.now()`` at this revision, generated from
# sable_platform/db/schema.py and pinned here for the same reason.
COLUMNS = [
    ("actions", "created_at"),
    ("alert_configs", "created_at"),
    ("alerts", "created_at"),
    ("allowlist_entries", "created_at"),
    ("allowlist_entries", "updated_at"),
    ("api_tokens", "created_at"),
    ("artifacts", "created_at"),
    ("audit_log", "timestamp"),
    ("autocm_adversarial_runs", "ran_at"),
    ("autocm_category_state", "updated_at"),
    ("autocm_clients", "created_at"),
    ("autocm_clients", "updated_at"),
    ("autocm_digest_interactions", "created_at"),
    ("autocm_drafts", "created_at"),
    ("autocm_escalations", "created_at"),
    ("autocm_flagged_users", "flagged_at"),
    ("autocm_kb_chunks", "indexed_at"),
    ("autocm_kb_constants", "updated_at"),
    ("autocm_kb_sources", "created_at"),
    ("autocm_personas", "created_at"),
    ("autocm_personas", "updated_at"),
    ("autocm_reviews", "reviewed_at"),
    ("autocm_time_saved_baseline", "created_at"),
    ("autocm_time_saved_baseline", "updated_at"),
    ("client_accounts", "created_at"),
    ("client_docs", "created_at"),
    ("client_intake", "created_at"),
    ("client_intake", "updated_at"),
    ("community_audit_benchmark", "updated_at"),
    ("community_audit_findings", "created_at"),
    ("community_audit_guilds", "created_at"),
    ("community_audit_guilds", "joined_at"),
    ("community_audit_guilds", "updated_at"),
    ("community_audit_identity_links", "created_at"),
    ("community_audit_leads", "created_at"),
    ("community_audit_member_activity", "updated_at"),
    ("community_audit_member_scores", "updated_at"),
    ("community_audit_rate_limits", "updated_at"),
    ("community_audit_reaction_ledger", "created_at"),
    ("community_audit_runs", "created_at"),
    ("community_audit_runs", "started_at"),
    ("community_audit_security_checks", "created_at"),
    ("community_audit_settings_snapshot", "created_at"),
    ("content_candidates", "created_at"),
    ("content_deck_decisions", "created_at"),
    ("content_deck_operator_state", "created_at"),
    ("content_items", "created_at"),
    ("content_publish_jobs", "created_at"),
    ("content_publish_jobs", "updated_at"),
    ("content_quality", "updated_at"),
    ("cost_events", "created_at"),
    ("deck_consumed_assertions", "consumed_at"),
    ("diagnostic_deltas", "created_at"),
    ("diagnostic_runs", "started_at"),
    ("discord_burn_blocklist", "blocked_at"),
    ("discord_burn_optins", "opted_in_at"),
    ("discord_burn_random_log", "roasted_at"),
    ("discord_fitcheck_emoji_milestones", "created_at"),
    ("discord_fitcheck_scores", "created_at"),
    ("discord_fitcheck_scores", "updated_at"),
    ("discord_guild_config", "updated_at"),
    ("discord_invite_snapshot", "captured_at"),
    ("discord_member_admit", "joined_at"),
    ("discord_message_observations", "captured_at"),
    ("discord_peer_roast_flags", "flagged_at"),
    ("discord_peer_roast_tokens", "granted_at"),
    ("discord_pulse_runs", "created_at"),
    ("discord_scoring_config", "created_at"),
    ("discord_scoring_config", "updated_at"),
    ("discord_state_pins", "created_at"),
    ("discord_state_pins", "updated_at"),
    ("discord_streak_events", "created_at"),
    ("discord_streak_events", "updated_at"),
    ("discord_team_inviters", "added_at"),
    ("discord_user_observations", "computed_at"),
    ("discord_user_vibes", "inferred_at"),
    ("entities", "created_at"),
    ("entities", "updated_at"),
    ("entity_centrality_scores", "scored_at"),
    ("entity_decay_scores", "scored_at"),
    ("entity_handles", "added_at"),
    ("entity_notes", "created_at"),
    ("entity_tag_history", "effective_at"),
    ("entity_tags", "added_at"),
    ("entity_watchlist", "created_at"),
    ("jobs", "created_at"),
    ("jobs", "updated_at"),
    ("kol_candidates", "first_seen_at"),
    ("kol_candidates", "last_seen_at"),
    ("kol_create_audit", "at_utc"),
    ("kol_enrichment", "fetched_at"),
    ("kol_extract_runs", "started_at"),
    ("kol_follow_edges", "fetched_at"),
    ("kol_handle_resolution_conflicts", "detected_at"),
    ("kol_operator_relationships", "created_at"),
    ("media_assets", "created_at"),
    ("media_embeddings", "updated_at"),
    ("media_quality", "updated_at"),
    ("media_rec_events", "created_at"),
    ("merge_candidates", "created_at"),
    ("merge_candidates", "updated_at"),
    ("merge_events", "created_at"),
    ("metric_snapshots", "created_at"),
    ("mod_slot_sessions", "created_at"),
    ("mod_slot_sessions", "started_at"),
    ("operator_meme_budget", "updated_at"),
    ("operator_reply_quota", "updated_at"),
    ("operator_work_events", "created_at"),
    ("operator_work_events", "occurred_at"),
    ("org_entitlements", "created_at"),
    ("org_entitlements", "updated_at"),
    ("orgs", "created_at"),
    ("orgs", "updated_at"),
    ("outcomes", "created_at"),
    ("platform_meta", "updated_at"),
    ("playbook_outcomes", "created_at"),
    ("playbook_targets", "created_at"),
    ("project_profiles_external", "created_at"),
    ("project_profiles_external", "last_used_at"),
    ("prospect_scores", "scored_at"),
    ("relay_chat_bindings", "created_at"),
    ("relay_chats", "created_at"),
    ("relay_clients", "created_at"),
    ("relay_member_identities", "linked_at"),
    ("relay_member_preferences", "updated_at"),
    ("relay_member_roles", "granted_at"),
    ("relay_members", "created_at"),
    ("relay_messages", "received_at"),
    ("relay_operator_heartbeat", "last_seen"),
    ("relay_opportunity_feedback", "created_at"),
    ("relay_opportunity_operator_state", "created_at"),
    ("relay_processed_updates", "processed_at"),
    ("relay_publication_jobs", "created_at"),
    ("relay_publication_jobs", "next_attempt_at"),
    ("relay_publications", "published_at"),
    ("relay_quality_accounts", "added_at"),
    ("relay_quality_tweets", "first_seen_at"),
    ("relay_reply_notifications", "notified_at"),
    ("relay_reply_opportunities", "created_at"),
    ("relay_search_windows", "completed_at"),
    ("relay_submission_reactions", "reacted_at"),
    ("relay_submissions", "created_at"),
    ("relay_sweep_config", "updated_at"),
    ("relay_sweep_cursor", "updated_at"),
    ("relay_topic_picks", "picked_at"),
    ("relay_topic_suggestions", "created_at"),
    ("relay_topic_suggestions", "refreshed_at"),
    ("relay_trending_stories", "created_at"),
    ("relay_trending_stories", "first_seen_at"),
    ("relay_trending_stories", "last_seen_at"),
    ("relay_tweet_snapshots", "taken_at"),
    ("relay_tweets", "fetched_at"),
    ("reply_campaign_assignments", "created_at"),
    ("reply_campaigns", "created_at"),
    ("reply_outcomes", "recorded_at"),
    ("reply_suggestions", "generated_at"),
    ("sync_runs", "started_at"),
    ("tweetbank_entries", "created_at"),
    ("watchlist_snapshots", "snapshot_at"),
    ("webhook_subscriptions", "created_at"),
    ("workflow_events", "created_at"),
    ("workflow_runs", "created_at"),
]


def _pg_instant(column: str) -> str:
    """*column* as an instant, the same answer under any session TimeZone.

    A value carrying an offset is a ``timestamptz`` already. A naive one is UTC. ``TRIM``
    first: the offset test is anchored to end-of-string, so one trailing space sends a value
    that DOES carry an offset down the naive branch and shifts it by the session's offset.
    """
    trimmed = f"TRIM(NULLIF({column}, ''))"
    return (
        f"(CASE WHEN {trimmed} ~ '(Z|[+-][0-9]{{2}}(:?[0-9]{{2}})?)$'"
        f" THEN {trimmed}::timestamptz"
        f" ELSE ({trimmed}::timestamp AT TIME ZONE 'UTC') END)"
    )


# A column NAME that holds a timestamp, by this schema's own convention. The backfill uses
# this rather than the pinned list, because the pinned list only covers columns with a
# DEFAULT. `alerts.acknowledged_at`, `actions.claimed_at` and `workflow_runs.completed_at`
# have no default at all: they are written only by SQL that used to say CURRENT_TIMESTAMP,
# and they carry exactly the same mix. Leaving them out would canonicalize half the schema.
_TS_NAMES = ("timestamp", "last_seen", "at_utc")


def _is_ts_name(name: str) -> bool:
    return name.endswith("_at") or name in _TS_NAMES


def _text_columns(bind) -> tuple:
    """What this database actually has, split into TEXT and not-TEXT.

    Reading the server rather than trusting ``COLUMNS`` is not defensive padding. Measured
    on a database built by this migration chain, 12 of the 162 columns ``schema.py``
    declares as ``Text`` are ``timestamp with time zone`` on PostgreSQL. They need no
    canonical spelling, and ``SET DEFAULT to_char(...)`` on one of them is a type error that
    aborts the migration:

        column "created_at" is of type timestamp with time zone
        but default expression is of type text
    """
    inspector = sa.inspect(bind)
    is_text, other = set(), set()
    for table in inspector.get_table_names():
        for col in inspector.get_columns(table):
            key = (table, col["name"])
            rendered = str(col["type"]).upper()
            scalar_text = "[]" not in rendered and rendered.startswith(
                ("TEXT", "VARCHAR", "CHARACTER VARYING")
            )
            if scalar_text or (isinstance(col["type"], sa.Text) and "[]" not in rendered):
                is_text.add(key)
            else:
                other.add(key)
    return is_text, other


_FALLBACK_FN = "pg_temp.sable_090_readable"

# Module-level so a test can create it and drive it directly. The test server is
# PostgreSQL 16, which takes the fast path, so the BODY would otherwise never run anywhere.
FALLBACK_FN_SQL = f"""
CREATE FUNCTION {_FALLBACK_FN}(v text) RETURNS boolean AS $sable$
DECLARE
    parsed timestamptz;
BEGIN
    parsed := v::timestamptz;
    RETURN true;
EXCEPTION WHEN OTHERS THEN
    RETURN false;
END;
$sable$ LANGUAGE plpgsql;
"""


def _install_readable_fallback(bind) -> None:
    """A per-session "can PostgreSQL parse this?" helper for servers older than 16.

    ``pg_input_is_valid`` arrived in PostgreSQL 16. CI pins 16, and the only other declared
    server is the deployment, whose version this migration cannot assume. Without a fallback
    the guard silently disappears on an older server and one out-of-range value aborts the
    whole upgrade.

    The exception block costs a subtransaction per call, which is why it is the FALLBACK and
    not the default: on a large table that is the difference between minutes and a lot more.
    """
    op.execute(FALLBACK_FN_SQL)


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    is_text, other = _text_columns(bind)

    # The validity guard, and which one this server can run.
    readable_fn = None
    if is_pg:
        version = bind.execute(sa.text("SHOW server_version_num")).scalar()
        if int(version) >= 160000:
            readable_fn = "pg_input_is_valid({0}, 'timestamptz')"
        else:
            _install_readable_fallback(bind)
            readable_fn = _FALLBACK_FN + "({0})"

    # Phase 1: the DEFAULT. Pinned list, PostgreSQL only. SQLite cannot ALTER a default and
    # builds its databases from schema.py, which already carries the canonical one.
    defaults_set = 0
    already_typed = 0
    absent = 0
    for table, column in COLUMNS:
        if (table, column) in other:
            already_typed += 1        # a real timestamp type; nothing to canonicalize
            continue
        if (table, column) not in is_text:
            absent += 1
            continue
        if is_pg:
            op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" SET DEFAULT {_PG_NOW}')
            defaults_set += 1

    # Phase 2: the DATA. Every TEXT column named like a timestamp, which is a superset of
    # the pinned list. The value-shape guard is what makes a name-based sweep safe: a value
    # that is not timestamp-shaped is left untouched whatever the column is called.
    converted = 0
    unreadable = 0
    corrupt = 0
    scanned = 0
    for table, column in sorted(is_text):
        if not _is_ts_name(column):
            continue
        scanned += 1
        qt = f'"{table}"'
        qc = f'"{column}"'
        if is_pg:
            canon = (f"to_char({_pg_instant(qc)} AT TIME ZONE 'UTC',"
                     f" 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')")
            # Test the RAW value, not the trimmed one. Codex round 5, MAJOR: a padded
            # ' 2026-07-30T23:00:00Z ' trims to something canonical, so a TRIM-based test
            # skipped the row and left the padding in the column, where it still sorts
            # wrong and is never counted.
            not_canonical = (f"{qc} IS NOT NULL AND TRIM({qc}) <> ''"
                             f" AND {qc} !~ '{_CANONICAL}'")
            # _PARSEABLE is a SHAPE, and a shape is not a date. Codex round 5, MAJOR:
            # '2026-13-01T00:00:00Z' matches it and then aborts the whole migration with
            # "date/time field value out of range". pg_input_is_valid (PostgreSQL 16) asks
            # the parser instead of guessing, so a bad row is SKIPPED and counted as
            # unreadable rather than killing the upgrade.
            readable = (f"TRIM({qc}) ~ '{_PARSEABLE}'"
                        f" AND {readable_fn.format(f'TRIM({qc})')}")
            result = bind.execute(sa.text(
                f"UPDATE {qt} SET {qc} = {canon}"
                f" WHERE {not_canonical} AND {readable}"
            ))
            converted += result.rowcount or 0
            # Counted AFTER the update, so this is what the backfill could not read.
            left = bind.execute(sa.text(
                f"SELECT COUNT(*) FROM {qt} WHERE {not_canonical}"
            )).scalar()
            unreadable += left or 0
            # And separately: a value that LOOKS canonical and is not a real date.
            # Codex round 6, MAJOR. `_CANONICAL` is a digit shape, so '2026-13-01T00:00:00Z'
            # matches it. Such a row is skipped by `not_canonical` before the validity check
            # ever runs, and the count above then misses it too, so the report said "0 left
            # unreadable" while corrupt rows sat in the table. A zero has to mean something.
            bad = bind.execute(sa.text(
                f"SELECT COUNT(*) FROM {qt}"
                f" WHERE {qc} ~ '{_CANONICAL}'"
                f"   AND NOT {readable_fn.format(qc)}"
            )).scalar()
            corrupt += bad or 0
        else:
            # SQLite: strftime returns NULL for anything it cannot read, which is the guard.
            # TRIM inside, for the same reason as the PostgreSQL branch: strftime rejects a
            # padded value outright, so without it a padded row keeps its whitespace.
            canon = f"strftime('%Y-%m-%dT%H:%M:%SZ', NULLIF(TRIM({qc}), ''))"
            result = bind.execute(sa.text(
                f"UPDATE {qt} SET {qc} = {canon}"
                f" WHERE {canon} IS NOT NULL AND {qc} <> {canon}"
            ))
            converted += result.rowcount or 0

    print(
        f"[090] canonical timestamps: {defaults_set} defaults changed,"
        f" {scanned} TEXT timestamp columns scanned,"
        f" {converted} values rewritten, {unreadable} left unreadable,"
        f" {corrupt} canonical-shaped but not a real date,"
        f" {already_typed} columns already a real timestamp type,"
        f" {absent} columns absent from this database"
    )


def downgrade() -> None:
    """Restore the ``now()`` defaults. The original spellings are NOT restored.

    The backfill is one-way by design: it discards the separator and the offset that made
    the value ambiguous, and there is nothing left to reconstruct them from. A downgrade
    therefore returns the DEFAULT to its old behaviour and leaves the data canonical.

    That is the safe direction for the SQL comparisons, which is what the pre-090 code did
    with these columns. It is not a universal claim, and Codex round 5 named the exception:
    pre-090 ``api/tokens.py`` compared a stored expiry in PYTHON against a current time it
    rendered WITHOUT the ``Z``. A canonical stored value carries one, and ``'Z'`` sorts above
    the empty string, so a token expiring on the exact second reads as valid for up to one
    more second. That needs a downgrade of the CODE as well as this migration, and one
    second is the whole size of it.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    is_text, _other = _text_columns(bind)
    for table, column in COLUMNS:
        if (table, column) in is_text:
            op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" SET DEFAULT now()')
