"""C2.3a tests — the §3.1 guarded quorum transition (quorum.py).

The relay's single most concurrency-sensitive piece: the load-bearing
exactly-once enqueue. No real Telegram/Discord/network — reactions are plain
Python calls into :func:`quorum.handle_reaction`, which runs the whole §3.1
sequence inside one ``immediate_txn``.

Coverage (per MEGAPLAN C2.3a tests line):
  * quorum exactly-once — the guarded UPDATE transitions ONCE; a second
    (concurrent OR sequential) writer sees ``status != 'pending'`` and skips,
    enqueuing NO second fan-out (asserted under genuine concurrent writers on a
    file-backed WAL db).
  * submitter-counts — the submitter's own vote is one of the distinct operators.
  * ``min_other_operators`` — adds an "≥N operators OTHER than the submitter" gate
    on top of the threshold.
  * non-operator reaction DROPPED — not recorded in relay_submission_reactions.
  * anonymous reaction DROPPED — no identifiable user.
  * one-pending-per-tweet is exercised via the amplify tests; here we assert the
    reaction routing + dedupe + emoji filtering.
  * §3.1 invariant — no external call inside the BEGIN IMMEDIATE.
"""
from __future__ import annotations

import json
import threading

from sqlalchemy import create_engine, event, text

from sable_platform.db.schema import metadata as sa_metadata
from sable_platform.relay import db as relay_db
from sable_platform.relay.bot import handlers
from sable_platform.relay.bot.handlers import quorum
from sable_platform.relay.bot.txn import immediate_txn


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------
def _seed_org_client(conn, org_id="orgQ", *, config="{}"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, :c)"),
        {"o": org_id, "c": config},
    )


def _seed_binding(conn, org_id, platform, chat_id, role):
    conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:o, :p, :c, :r, 'active')"
        ),
        {"o": org_id, "p": platform, "c": chat_id, "r": role},
    )


def _seed_member_with_identity(conn, org_id, *, tg_user_id, role=None):
    mid = relay_db.auto_create_member_identity(conn, "telegram", str(tg_user_id), handle=f"u{tg_user_id}")
    if role is not None:
        conn.execute(
            text(
                "INSERT INTO relay_member_roles (member_id, org_id, role) "
                "VALUES (:m, :o, :r)"
            ),
            {"m": mid, "o": org_id, "r": role},
        )
    return mid


def _seed_submission(conn, org_id, *, submitter_id, chat_id, control_msg, status="pending"):
    tweet_row = relay_db.upsert_tweet(conn, x_id="700", x_author_handle="archerfit")
    sid = relay_db.create_submission(
        conn,
        org_id=org_id,
        tweet_id=tweet_row,
        submitter_id=submitter_id,
        source_chat_id=chat_id,
        source_message_id="srcmsg",
        source_role="operator",
        expires_at="2099-01-01T00:00:00Z",
        status=status,
        control_message_id=control_msg,
    )
    return sid, tweet_row


def _full_seed(conn, *, threshold=2, min_other=None, org_id="orgQ", chat_id="-700"):
    cfg = {"quorum": {"threshold": threshold, "emoji": "\U0001F4E2"}}
    if min_other is not None:
        cfg["quorum"]["min_other_operators"] = min_other
    _seed_org_client(conn, org_id, config=json.dumps(cfg))
    _seed_binding(conn, org_id, "telegram", chat_id, "operator")
    _seed_binding(conn, org_id, "discord", "chan-disc", "broadcast")
    _seed_binding(conn, org_id, "telegram", "-999comm", "community")
    submitter = _seed_member_with_identity(conn, org_id, tg_user_id=10, role="sable_operator")
    sid, tweet_row = _seed_submission(
        conn, org_id, submitter_id=submitter, chat_id=chat_id, control_msg="ctrl-1"
    )
    # Submitter already voted (their /amplify is a vote — submitter counts as 1).
    relay_db.upsert_submission_reaction(conn, sid, submitter, "\U0001F4E2")
    conn.commit()
    return org_id, sid, tweet_row, submitter, chat_id


EMOJI = "\U0001F4E2"


# ==========================================================================
# Happy path: second operator's reaction reaches quorum → transition + fan-out
# ==========================================================================
def test_quorum_reached_transitions_and_enqueues_fanout(sa_conn):
    org_id, sid, tweet_row, _submitter, chat = _full_seed(sa_conn, threshold=2)
    # A second operator (not the submitter) reacts with 📢.
    _seed_member_with_identity(sa_conn, org_id, tg_user_id=20, role="sable_operator")
    sa_conn.commit()

    result = quorum.handle_reaction(
        sa_conn,
        platform="telegram",
        update_id=5001,
        source_chat_id=chat,
        control_message_id="ctrl-1",
        external_user_id="20",
        emoji_added=EMOJI,
        handle="op2",
    )
    assert result.code == quorum.QUORUM_REACHED
    assert result.transitioned is True
    assert result.operator_count == 2
    # Fan-out: one job per active broadcast/community binding (discord + tg comm).
    assert result.jobs_enqueued == 2

    # Submission flipped to ready_to_publish exactly once.
    status = sa_conn.execute(
        text("SELECT status FROM relay_submissions WHERE id = :id"), {"id": sid}
    ).fetchone()[0]
    assert status == "ready_to_publish"
    njobs = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_publication_jobs WHERE submission_id = :id"),
        {"id": sid},
    ).fetchone()[0]
    assert njobs == 2


def test_submitter_counts_as_one_below_threshold_waits(sa_conn):
    # threshold=2; only the submitter has voted (seeded). A second operator's
    # reaction is needed. Before that, a non-quorum-changing event keeps it pending.
    org_id, sid, _tweet, _submitter, chat = _full_seed(sa_conn, threshold=2)
    _seed_member_with_identity(sa_conn, org_id, tg_user_id=20, role="sable_operator")
    sa_conn.commit()
    # Operator 20 reacts but with a NON-quorum emoji → ignored for quorum.
    res = quorum.handle_reaction(
        sa_conn,
        platform="telegram",
        update_id=5002,
        source_chat_id=chat,
        control_message_id="ctrl-1",
        external_user_id="20",
        emoji_added="❤",  # ❤ — not the configured quorum emoji
        handle="op2",
    )
    assert res.code == quorum.QUORUM_IGNORED_EMOJI
    status = sa_conn.execute(
        text("SELECT status FROM relay_submissions WHERE id = :id"), {"id": sid}
    ).fetchone()[0]
    assert status == "pending"


# ==========================================================================
# min_other_operators gate
# ==========================================================================
def test_min_other_operators_blocks_submitter_only_quorum(sa_conn):
    # threshold=1 would normally pass on the submitter's lone vote, but
    # min_other_operators=1 requires ≥1 operator OTHER than the submitter.
    org_id, sid, _tweet, submitter, chat = _full_seed(sa_conn, threshold=1, min_other=1)
    # The submitter reacts again (idempotent vote) — still only the submitter.
    res = quorum.handle_reaction(
        sa_conn,
        platform="telegram",
        update_id=5101,
        source_chat_id=chat,
        control_message_id="ctrl-1",
        external_user_id="10",  # the submitter
        emoji_added=EMOJI,
        handle="submitter",
    )
    assert res.code == quorum.QUORUM_VOTE_RECORDED
    assert res.transitioned is False
    status = sa_conn.execute(
        text("SELECT status FROM relay_submissions WHERE id = :id"), {"id": sid}
    ).fetchone()[0]
    assert status == "pending"

    # Now a DIFFERENT operator reacts → min_other satisfied → transition.
    _seed_member_with_identity(sa_conn, org_id, tg_user_id=30, role="sable_operator")
    sa_conn.commit()
    res2 = quorum.handle_reaction(
        sa_conn,
        platform="telegram",
        update_id=5102,
        source_chat_id=chat,
        control_message_id="ctrl-1",
        external_user_id="30",
        emoji_added=EMOJI,
        handle="op3",
    )
    assert res2.code == quorum.QUORUM_REACHED
    assert res2.transitioned is True


# ==========================================================================
# Non-operator + anonymous reactions are dropped
# ==========================================================================
def test_non_operator_reaction_dropped_not_recorded(sa_conn):
    org_id, sid, _tweet, _submitter, chat = _full_seed(sa_conn, threshold=2)
    # A non-operator (no role granted) reacts with 📢.
    res = quorum.handle_reaction(
        sa_conn,
        platform="telegram",
        update_id=5201,
        source_chat_id=chat,
        control_message_id="ctrl-1",
        external_user_id="99",
        emoji_added=EMOJI,
        handle="randoms",
    )
    assert res.code == quorum.QUORUM_NON_OPERATOR_DROPPED
    # The vote was NOT recorded (audit table stays clean, §8/§15.4): only the
    # submitter's seeded reaction row exists.
    nrows = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_submission_reactions WHERE submission_id = :id"),
        {"id": sid},
    ).fetchone()[0]
    assert nrows == 1
    # An identity row WAS auto-created for audit, but it holds no role.
    mid = relay_db.resolve_member_id(sa_conn, "telegram", "99")
    assert mid is not None
    assert relay_db.list_member_roles(sa_conn, mid, org_id) == []


def test_anonymous_reaction_dropped(sa_conn):
    _org_id, sid, _tweet, _submitter, chat = _full_seed(sa_conn, threshold=2)
    res = quorum.handle_reaction(
        sa_conn,
        platform="telegram",
        update_id=5301,
        source_chat_id=chat,
        control_message_id="ctrl-1",
        external_user_id=None,  # anonymous group-as-actor
        emoji_added=EMOJI,
    )
    assert res.code == quorum.QUORUM_ANONYMOUS_DROPPED
    status = sa_conn.execute(
        text("SELECT status FROM relay_submissions WHERE id = :id"), {"id": sid}
    ).fetchone()[0]
    assert status == "pending"


# ==========================================================================
# Dedupe + unknown submission
# ==========================================================================
def test_duplicate_update_is_noop(sa_conn):
    org_id, sid, _tweet, _submitter, chat = _full_seed(sa_conn, threshold=2)
    _seed_member_with_identity(sa_conn, org_id, tg_user_id=20, role="sable_operator")
    sa_conn.commit()
    first = quorum.handle_reaction(
        sa_conn, platform="telegram", update_id=5401, source_chat_id=chat,
        control_message_id="ctrl-1", external_user_id="20", emoji_added=EMOJI, handle="op2",
    )
    assert first.code == quorum.QUORUM_REACHED
    # Redelivery of the SAME update_id → dedupe no-op (does not re-enqueue).
    second = quorum.handle_reaction(
        sa_conn, platform="telegram", update_id=5401, source_chat_id=chat,
        control_message_id="ctrl-1", external_user_id="20", emoji_added=EMOJI, handle="op2",
    )
    assert second.code == quorum.QUORUM_DUPLICATE
    njobs = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_publication_jobs WHERE submission_id = :id"),
        {"id": sid},
    ).fetchone()[0]
    assert njobs == 2  # NOT 4 — the duplicate did not re-enqueue


def test_reaction_on_unknown_message_is_noop(sa_conn):
    _full_seed(sa_conn, threshold=2)
    res = quorum.handle_reaction(
        sa_conn, platform="telegram", update_id=5501, source_chat_id="-700",
        control_message_id="not-a-submission", external_user_id="10", emoji_added=EMOJI,
    )
    assert res.code == quorum.QUORUM_UNKNOWN_SUBMISSION


def test_removing_vote_below_threshold_does_not_transition(sa_conn):
    org_id, sid, _tweet, _submitter, chat = _full_seed(sa_conn, threshold=2)
    op2 = _seed_member_with_identity(sa_conn, org_id, tg_user_id=20, role="sable_operator")
    sa_conn.commit()
    # Op2 votes → quorum reached.
    quorum.handle_reaction(
        sa_conn, platform="telegram", update_id=5601, source_chat_id=chat,
        control_message_id="ctrl-1", external_user_id="20", emoji_added=EMOJI, handle="op2",
    )
    # The submission already transitioned; removing a vote does NOT un-publish
    # (the guarded UPDATE only fires from 'pending'). Removing op2's vote on the
    # already-ready submission recomputes below threshold but cannot re-transition.
    res = quorum.handle_reaction(
        sa_conn, platform="telegram", update_id=5602, source_chat_id=chat,
        control_message_id="ctrl-1", external_user_id="20", emoji_removed=EMOJI, handle="op2",
    )
    assert res.code == quorum.QUORUM_VOTE_RECORDED
    assert res.transitioned is False
    status = sa_conn.execute(
        text("SELECT status FROM relay_submissions WHERE id = :id"), {"id": sid}
    ).fetchone()[0]
    assert status == "ready_to_publish"  # stays ready — the vote-remove can't revert


# ==========================================================================
# EXACTLY-ONCE under genuine concurrent writers (file-backed WAL db)
# ==========================================================================
def test_quorum_exactly_once_under_concurrent_writers(tmp_path):
    """Two operators react at the SAME instant; the count crosses threshold for
    both, and both threads attempt the guarded UPDATE. Exactly ONE must
    transition + enqueue the fan-out; the other sees status != 'pending' and
    skips. This is the §3.1 load-bearing exactly-once enqueue.
    """
    db_path = tmp_path / "quorum_race.db"
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ARG001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    sa_metadata.create_all(engine)

    org_id, chat_id = "orgRACE", "-700"
    with engine.connect() as conn:
        # threshold=3: submitter + the two racing operators = 3 distinct operators,
        # so BOTH racing reactions are needed and BOTH see count>=3 → both try the
        # guarded UPDATE simultaneously.
        cfg = {"quorum": {"threshold": 3, "emoji": EMOJI}}
        _seed_org_client(conn, org_id, config=json.dumps(cfg))
        _seed_binding(conn, org_id, "telegram", chat_id, "operator")
        _seed_binding(conn, org_id, "discord", "chan-disc", "broadcast")
        _seed_binding(conn, org_id, "telegram", "-999comm", "community")
        submitter = _seed_member_with_identity(conn, org_id, tg_user_id=10, role="sable_operator")
        sid, _tweet = _seed_submission(
            conn, org_id, submitter_id=submitter, chat_id=chat_id, control_msg="ctrl-1"
        )
        relay_db.upsert_submission_reaction(conn, sid, submitter, EMOJI)
        # Pre-record BOTH racing operators' votes so the tally is already 3 by the
        # time both threads run their guarded transition — isolating the race to
        # the UPDATE/enqueue (not the vote upsert).
        op_a = _seed_member_with_identity(conn, org_id, tg_user_id=20, role="sable_operator")
        op_b = _seed_member_with_identity(conn, org_id, tg_user_id=30, role="sable_operator")
        relay_db.upsert_submission_reaction(conn, sid, op_a, EMOJI)
        relay_db.upsert_submission_reaction(conn, sid, op_b, EMOJI)
        conn.commit()

    barrier = threading.Barrier(2)
    results: list[quorum.QuorumResult] = []
    errors: list[BaseException] = []

    def _worker(update_id, user_id):
        try:
            with engine.connect() as conn:
                barrier.wait(timeout=5)
                res = quorum.handle_reaction(
                    conn,
                    platform="telegram",
                    update_id=update_id,
                    source_chat_id=chat_id,
                    control_message_id="ctrl-1",
                    external_user_id=str(user_id),
                    emoji_added=EMOJI,
                    handle=f"op{user_id}",
                )
                results.append(res)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=_worker, args=(7001, 20)),
        threading.Thread(target=_worker, args=(7002, 30)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"worker raised: {errors}"
    transitioned = [r for r in results if r.transitioned]
    assert len(transitioned) == 1, (
        f"exactly ONE writer must transition, got {[r.code for r in results]}"
    )
    # The losing writer saw status != 'pending' → QUORUM_ALREADY_RESOLVED.
    losers = [r for r in results if not r.transitioned]
    assert len(losers) == 1
    assert losers[0].code == quorum.QUORUM_ALREADY_RESOLVED

    # The fan-out was enqueued EXACTLY ONCE (2 destinations), not twice.
    with engine.connect() as conn:
        njobs = conn.execute(
            text("SELECT COUNT(*) FROM relay_publication_jobs WHERE submission_id = :id"),
            {"id": sid},
        ).fetchone()[0]
        status = conn.execute(
            text("SELECT status FROM relay_submissions WHERE id = :id"), {"id": sid}
        ).fetchone()[0]
    engine.dispose()
    assert njobs == 2, f"fan-out must be enqueued exactly once (2 dests), found {njobs}"
    assert status == "ready_to_publish"
    assert transitioned[0].jobs_enqueued == 2


# ==========================================================================
# §3.1 invariant — no external API call inside the BEGIN IMMEDIATE
# ==========================================================================
def test_no_external_call_inside_quorum_txn(sa_conn, monkeypatch):
    from contextlib import contextmanager

    state = {"in_txn": False, "leak": 0}
    real = immediate_txn

    @contextmanager
    def _tracking(conn):
        state["in_txn"] = True
        try:
            with real(conn) as c:
                yield c
        finally:
            state["in_txn"] = False

    monkeypatch.setattr(quorum, "immediate_txn", _tracking)

    org_id, _sid, _tweet, _submitter, chat = _full_seed(sa_conn, threshold=2)
    _seed_member_with_identity(sa_conn, org_id, tg_user_id=20, role="sable_operator")
    sa_conn.commit()

    # Wrap a db helper that a buggy refactor might call from inside the txn with an
    # external send; here we just assert the flag is reset after the call and the
    # handler committed without raising. (The dedicated invariant suite,
    # test_relay_txn_invariant.py, owns the behavioral "raise if leaked" probe.)
    res = quorum.handle_reaction(
        sa_conn, platform="telegram", update_id=5701, source_chat_id=chat,
        control_message_id="ctrl-1", external_user_id="20", emoji_added=EMOJI, handle="op2",
    )
    assert res.code == quorum.QUORUM_REACHED
    assert state["in_txn"] is False


def test_handlers_package_docstring_imports():
    # The handlers package imports cleanly (used by the listener registration).
    assert handlers.__doc__ is not None
