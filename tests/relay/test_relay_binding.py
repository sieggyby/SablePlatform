"""C2.2 §15.3 chat-binding lifecycle tests.

Each transition runs inside ONE ``BEGIN IMMEDIATE`` (via the binding module).

  - TG supergroup migration re-points the active binding to the new chat_id AND
    re-points in-flight submissions' source_chat_id (so reactions in the
    migrated supergroup still resolve via relay_submissions_control_lookup).
  - bot-kicked / my_chat_member flips the binding to 'kicked', expires pending
    submissions, and kills in-flight publication jobs to 'dead' (the only
    CHECK-allowed halted state).
  - Discord 403/404 binding-flip fires only once the consecutive-failure count
    crosses the configured threshold.
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.relay.bot import binding


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
def _seed_org_client(conn, org_id, *, config="{}"):
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"),
        {"o": org_id},
    )
    conn.execute(
        text(
            "INSERT INTO relay_clients (org_id, enabled, config) "
            "VALUES (:o, 1, :c)"
        ),
        {"o": org_id, "c": config},
    )


def _seed_binding(conn, org_id, platform, chat_id, role="operator", status="active"):
    conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:o, :p, :c, :r, :s)"
        ),
        {"o": org_id, "p": platform, "c": chat_id, "r": role, "s": status},
    )


def _seed_tweet(conn) -> int:
    conn.execute(
        text(
            "INSERT INTO relay_tweets (x_id, x_author_handle, text) "
            "VALUES ('999', 'auth', 'hi')"
        )
    )
    return conn.execute(text("SELECT id FROM relay_tweets WHERE x_id='999'")).fetchone()[0]


def _seed_member(conn) -> int:
    conn.execute(text("INSERT INTO relay_members (display_name) VALUES ('m')"))
    return conn.execute(text("SELECT id FROM relay_members WHERE display_name='m'")).fetchone()[0]


def _seed_submission(conn, org_id, tweet_id, member_id, chat_id, status="pending"):
    conn.execute(
        text(
            "INSERT INTO relay_submissions "
            "(org_id, tweet_id, submitter_id, source_chat_id, source_message_id, "
            " control_message_id, source_role, status, expires_at) "
            "VALUES (:o, :t, :m, :c, 'srcmsg', 'ctrlmsg', 'operator', :st, "
            "        strftime('%Y-%m-%dT%H:%M:%SZ','now','+1 hour'))"
        ),
        {"o": org_id, "t": tweet_id, "m": member_id, "c": chat_id, "st": status},
    )
    return conn.execute(
        text("SELECT id FROM relay_submissions WHERE source_chat_id=:c"),
        {"c": chat_id},
    ).fetchone()[0]


def _seed_job(conn, org_id, tweet_id, platform, chat_id, state="pending"):
    conn.execute(
        text(
            "INSERT INTO relay_publication_jobs "
            "(org_id, tweet_id, destination_platform, destination_chat_id, state) "
            "VALUES (:o, :t, :p, :c, :s)"
        ),
        {"o": org_id, "t": tweet_id, "p": platform, "c": chat_id, "s": state},
    )


def _binding_row(conn, platform, chat_id):
    return conn.execute(
        text(
            "SELECT status, superseded_by_chat_id, last_error FROM relay_chat_bindings "
            "WHERE platform=:p AND chat_id=:c"
        ),
        {"p": platform, "c": chat_id},
    ).fetchone()


# ---------------------------------------------------------------------------
# 1. Telegram supergroup migration
# ---------------------------------------------------------------------------
def test_migration_repoints_binding_and_submissions(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgM")
    _seed_binding(sa_conn, "orgM", "telegram", "-100")
    tid = _seed_tweet(sa_conn)
    mid = _seed_member(sa_conn)
    _seed_submission(sa_conn, "orgM", tid, mid, "-100", status="pending")
    sa_conn.commit()

    result = binding.migrate_chat_binding(sa_conn, "-100", "-100123456")
    assert result.migrated is True
    assert result.org_id == "orgM"
    assert result.submissions_repointed == 1

    # Old binding marked migrated, pointing at the new chat.
    old = _binding_row(sa_conn, "telegram", "-100")
    assert old[0] == "migrated"
    assert old[1] == "-100123456"
    # New active binding exists at the new chat id.
    new = _binding_row(sa_conn, "telegram", "-100123456")
    assert new[0] == "active"
    # Submission's source_chat_id re-pointed → control lookup still resolves.
    repointed = sa_conn.execute(
        text("SELECT source_chat_id FROM relay_submissions WHERE id IS NOT NULL")
    ).fetchall()
    assert all(r[0] == "-100123456" for r in repointed)

    # The active-role unique index is not violated (only ONE active binding).
    active_count = sa_conn.execute(
        text(
            "SELECT COUNT(*) FROM relay_chat_bindings "
            "WHERE org_id='orgM' AND platform='telegram' AND role='operator' "
            "AND status='active'"
        )
    ).fetchone()[0]
    assert active_count == 1


def test_migration_noop_when_no_active_binding(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgM2")
    sa_conn.commit()
    result = binding.migrate_chat_binding(sa_conn, "-555", "-555999")
    assert result.migrated is False
    assert result.submissions_repointed == 0


# ---------------------------------------------------------------------------
# 2. Bot kicked / my_chat_member
# ---------------------------------------------------------------------------
def test_kick_flips_binding_expires_submissions_kills_jobs(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgK")
    _seed_binding(sa_conn, "orgK", "telegram", "-200")
    tid = _seed_tweet(sa_conn)
    mid = _seed_member(sa_conn)
    _seed_submission(sa_conn, "orgK", tid, mid, "-200", status="pending")
    _seed_job(sa_conn, "orgK", tid, "telegram", "-200", state="pending")
    _seed_job(sa_conn, "orgK", tid, "telegram", "-200", state="retry")
    sa_conn.commit()

    result = binding.kick_chat_binding(sa_conn, "-200", platform="telegram")
    assert result.flipped is True
    assert result.org_id == "orgK"
    assert result.submissions_expired == 1
    assert result.jobs_killed == 2

    # Binding flipped to kicked with a last_error.
    row = _binding_row(sa_conn, "telegram", "-200")
    assert row[0] == "kicked"
    assert row[2] == "bot removed"

    # Submission expired.
    sub_status = sa_conn.execute(
        text("SELECT status FROM relay_submissions WHERE source_chat_id='-200'")
    ).fetchone()[0]
    assert sub_status == "expired"

    # Jobs are 'dead' (the ONLY CHECK-allowed halted state) with the §15.3 error.
    jobs = sa_conn.execute(
        text(
            "SELECT state, last_error FROM relay_publication_jobs "
            "WHERE destination_chat_id='-200'"
        )
    ).fetchall()
    assert {j[0] for j in jobs} == {"dead"}
    assert all(j[1] == "destination chat kicked the bot" for j in jobs)


def test_kick_is_idempotent_noop_without_active_binding(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgK2")
    sa_conn.commit()
    result = binding.kick_chat_binding(sa_conn, "-999", platform="telegram")
    assert result.flipped is False
    assert result.jobs_killed == 0


def test_kick_only_touches_inflight_job_states(sa_conn) -> None:
    # A 'done' job for the same chat must NOT be flipped to dead.
    _seed_org_client(sa_conn, "orgK3")
    _seed_binding(sa_conn, "orgK3", "telegram", "-300")
    tid = _seed_tweet(sa_conn)
    _seed_job(sa_conn, "orgK3", tid, "telegram", "-300", state="done")
    sa_conn.commit()
    result = binding.kick_chat_binding(sa_conn, "-300", platform="telegram")
    assert result.flipped is True
    assert result.jobs_killed == 0
    done_state = sa_conn.execute(
        text("SELECT state FROM relay_publication_jobs WHERE destination_chat_id='-300'")
    ).fetchone()[0]
    assert done_state == "done"


# ---------------------------------------------------------------------------
# 3. Discord 403/404 binding-flip (threshold-gated)
# ---------------------------------------------------------------------------
def test_discord_flip_below_threshold_is_noop(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgD")  # default config → threshold 5
    _seed_binding(sa_conn, "orgD", "discord", "chan-1")
    sa_conn.commit()
    result = binding.flip_discord_binding_on_failure(sa_conn, "orgD", "chan-1", 4)
    assert result.flipped is False
    assert _binding_row(sa_conn, "discord", "chan-1")[0] == "active"


def test_discord_flip_at_threshold_kicks_binding(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgD2")
    _seed_binding(sa_conn, "orgD2", "discord", "chan-2")
    tid = _seed_tweet(sa_conn)
    _seed_job(sa_conn, "orgD2", tid, "discord", "chan-2", state="pending")
    sa_conn.commit()
    result = binding.flip_discord_binding_on_failure(sa_conn, "orgD2", "chan-2", 5)
    assert result.flipped is True
    assert result.jobs_killed == 1
    row = _binding_row(sa_conn, "discord", "chan-2")
    assert row[0] == "kicked"
    assert "discord 403/404" in row[2]


def test_discord_flip_respects_config_threshold_override(sa_conn) -> None:
    # config.publish.kicked_after_consecutive_failures = 2 → flips at 2.
    _seed_org_client(
        sa_conn, "orgD3", config='{"publish": {"kicked_after_consecutive_failures": 2}}'
    )
    _seed_binding(sa_conn, "orgD3", "discord", "chan-3")
    sa_conn.commit()
    assert binding.flip_discord_binding_on_failure(sa_conn, "orgD3", "chan-3", 1).flipped is False
    assert binding.flip_discord_binding_on_failure(sa_conn, "orgD3", "chan-3", 2).flipped is True
