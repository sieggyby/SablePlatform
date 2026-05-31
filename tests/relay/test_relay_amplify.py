"""C2.3a tests — /amplify Flow B (operator quorum) + Flow C (shared immediate).

No real Telegram/Discord/SocialData: hydration goes through a duck-typed fake
:class:`FakeClient` exposing ``hydrate_tweet`` (the only method
``canonical.hydrate_or_reject`` calls). DB work runs against the in-memory
``sa_conn`` schema; no external send happens inside any txn (the handler returns
a result object the listener would use to drive sends).

Coverage:
  * Flow B opens a PENDING submission and records the submitter's own vote
    (submitter counts as 1); with threshold>1 it waits for quorum.
  * Flow B with threshold==1 (single-operator client) transitions + fans out
    immediately on the submitter's lone /amplify.
  * Flow C (shared) publishes IMMEDIATELY (ready_to_publish → published) and fans
    out — no quorum.
  * Flow C with a configured shared_chat_threshold behaves like Flow B (pending).
  * a disallowed/non-tweet URL and a deleted/not-found tweet are REJECTED with no
    submission created (§15.1).
  * authorization is role-gated: a non-operator /amplify in operator chat, and a
    non-operator/non-team /amplify in shared chat, are rejected.
  * one-pending-per-tweet merge: a duplicate /amplify of the same tweet merges.
  * record_control_message back-fills control_message_id for reaction routing.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.handlers import amplify
from sable_platform.relay.bot.handlers.quorum import handle_reaction
from sable_platform.relay.feed import canonical
from sable_platform.relay.socialdata import SocialDataNotFound


EMOJI = "\U0001F4E2"
URL = "https://x.com/archerfit/status/1812345"


# --------------------------------------------------------------------------
# Fake SocialData client (duck-typed: only hydrate_tweet is used by canonical)
# --------------------------------------------------------------------------
class FakeClient:
    """Scripted ``hydrate_tweet``. ``body`` is the hydrated dict; ``not_found``
    raises SocialDataNotFound; ``none`` returns None (hard 404)."""

    def __init__(self, *, body=None, not_found=False, none=False):
        self._body = body
        self._not_found = not_found
        self._none = none
        self.calls = []

    def hydrate_tweet(self, org_id, tweet_id):
        self.calls.append((org_id, tweet_id))
        if self._not_found:
            raise SocialDataNotFound("404")
        if self._none:
            return None
        return self._body


def _ok_body(x_id="1812345", handle="archerfit"):
    return {
        "id_str": x_id,
        "id": int(x_id),
        "full_text": "great voice",
        "user": {"id_str": "555", "screen_name": handle},
        "conversation_id_str": x_id,
    }


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------
def _seed(conn, *, org_id="orgA", config="{}"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, :c)"),
        {"o": org_id, "c": config},
    )


def _binding(conn, org_id, platform, chat_id, role):
    conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:o, :p, :c, :r, 'active')"
        ),
        {"o": org_id, "p": platform, "c": chat_id, "r": role},
    )


def _grant(conn, org_id, tg_user_id, role):
    mid = relay_db.auto_create_member_identity(conn, "telegram", str(tg_user_id), handle=f"u{tg_user_id}")
    conn.execute(
        text("INSERT INTO relay_member_roles (member_id, org_id, role) VALUES (:m, :o, :r)"),
        {"m": mid, "o": org_id, "r": role},
    )
    return mid


def _dests(conn, org_id):
    _binding(conn, org_id, "discord", "chan-disc", "broadcast")
    _binding(conn, org_id, "telegram", "-999comm", "community")


def _submission_status(conn, sid):
    return conn.execute(
        text("SELECT status FROM relay_submissions WHERE id = :id"), {"id": sid}
    ).fetchone()[0]


def _njobs(conn, sid):
    return conn.execute(
        text("SELECT COUNT(*) FROM relay_publication_jobs WHERE submission_id = :id"),
        {"id": sid},
    ).fetchone()[0]


# ==========================================================================
# Flow B — operator quorum submission
# ==========================================================================
def test_flow_b_opens_pending_submission_with_submitter_vote(sa_conn):
    _seed(sa_conn, config=json.dumps({"quorum": {"threshold": 2, "emoji": EMOJI}}))
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()

    client = FakeClient(body=_ok_body())
    res = amplify.amplify_operator(
        sa_conn, client,
        org_id="orgA", platform="telegram", submitter_external_user_id="10",
        source_chat_id="-700", source_message_id="m1", raw_url=URL,
        note="great voice", submitter_handle="op1",
    )
    assert res.code == amplify.AMPLIFY_PENDING
    assert res.published is False
    assert res.operator_count == 1  # the submitter's own vote
    assert res.threshold == 2
    assert _submission_status(sa_conn, res.submission_id) == "pending"
    # The submitter's vote is recorded.
    nvotes = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_submission_reactions WHERE submission_id = :id"),
        {"id": res.submission_id},
    ).fetchone()[0]
    assert nvotes == 1
    # No fan-out yet (still pending).
    assert _njobs(sa_conn, res.submission_id) == 0


def test_flow_b_then_second_operator_reaction_reaches_quorum(sa_conn):
    _seed(sa_conn, config=json.dumps({"quorum": {"threshold": 2, "emoji": EMOJI}}))
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")
    _grant(sa_conn, "orgA", 20, "sable_operator")
    sa_conn.commit()

    client = FakeClient(body=_ok_body())
    res = amplify.amplify_operator(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="10", source_chat_id="-700",
        source_message_id="m1", raw_url=URL, submitter_handle="op1",
    )
    assert res.code == amplify.AMPLIFY_PENDING
    # Listener posts the ack and back-fills the control message id.
    amplify.record_control_message(sa_conn, res.submission_id, "ctrl-99")
    sa_conn.commit()

    # Second operator reacts on the control message → quorum.
    qr = handle_reaction(
        sa_conn, platform="telegram", update_id=8001, source_chat_id="-700",
        control_message_id="ctrl-99", external_user_id="20", emoji_added=EMOJI, handle="op2",
    )
    assert qr.code == "quorum_reached"
    assert qr.transitioned is True
    assert qr.jobs_enqueued == 2
    assert _submission_status(sa_conn, res.submission_id) == "ready_to_publish"


def test_flow_b_threshold_one_publishes_immediately(sa_conn):
    # A single-operator client (threshold=1) publishes on the submitter's lone vote.
    _seed(sa_conn, config=json.dumps({"quorum": {"threshold": 1, "emoji": EMOJI}}))
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()

    client = FakeClient(body=_ok_body())
    res = amplify.amplify_operator(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="10", source_chat_id="-700",
        source_message_id="m1", raw_url=URL, submitter_handle="op1",
    )
    assert res.code == amplify.AMPLIFY_PUBLISHED
    assert res.published is True
    assert res.jobs_enqueued == 2
    assert _submission_status(sa_conn, res.submission_id) == "ready_to_publish"
    assert _njobs(sa_conn, res.submission_id) == 2


# ==========================================================================
# Flow C — shared chat immediate
# ==========================================================================
def test_flow_c_shared_publishes_immediately(sa_conn):
    _seed(sa_conn)  # no quorum config → defaults; no shared_chat_threshold
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 50, "client_team")  # a client-team member may amplify
    sa_conn.commit()

    client = FakeClient(body=_ok_body())
    res = amplify.amplify_shared(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="50", source_chat_id="-shared",
        source_message_id="m1", raw_url=URL, submitter_handle="team1",
    )
    assert res.code == amplify.AMPLIFY_PUBLISHED
    assert res.published is True
    assert res.jobs_enqueued == 2
    assert _submission_status(sa_conn, res.submission_id) == "published"
    assert _njobs(sa_conn, res.submission_id) == 2


def test_flow_c_sable_operator_may_amplify_shared(sa_conn):
    _seed(sa_conn)
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()
    client = FakeClient(body=_ok_body())
    res = amplify.amplify_shared(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="10", source_chat_id="-shared",
        source_message_id="m1", raw_url=URL,
    )
    assert res.code == amplify.AMPLIFY_PUBLISHED


def test_flow_c_with_shared_chat_threshold_waits_for_quorum(sa_conn):
    _seed(sa_conn, config=json.dumps(
        {"quorum": {"threshold": 2, "emoji": EMOJI, "shared_chat_threshold": 2}}
    ))
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()
    client = FakeClient(body=_ok_body())
    res = amplify.amplify_shared(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="10", source_chat_id="-shared",
        source_message_id="m1", raw_url=URL,
    )
    assert res.code == amplify.AMPLIFY_PENDING
    assert res.published is False
    assert _submission_status(sa_conn, res.submission_id) == "pending"
    assert _njobs(sa_conn, res.submission_id) == 0


# ==========================================================================
# §15.1 rejection — nothing created
# ==========================================================================
def test_disallowed_url_rejected_no_submission(sa_conn):
    _seed(sa_conn)
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()
    client = FakeClient(body=_ok_body())
    res = amplify.amplify_operator(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="10", source_chat_id="-700",
        source_message_id="m1", raw_url="https://evil.example/phish",
    )
    assert res.code == amplify.AMPLIFY_REJECTED
    assert res.rejection.code == canonical.REJECT_NOT_A_TWEET_URL
    # No submission, no tweet hydrated (the URL never reached hydration).
    n = sa_conn.execute(text("SELECT COUNT(*) FROM relay_submissions")).fetchone()[0]
    assert n == 0
    assert client.calls == []


def test_deleted_tweet_rejected_no_submission(sa_conn):
    _seed(sa_conn)
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()
    client = FakeClient(none=True)  # hard 404 → not found
    res = amplify.amplify_operator(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="10", source_chat_id="-700",
        source_message_id="m1", raw_url=URL,
    )
    assert res.code == amplify.AMPLIFY_REJECTED
    assert res.rejection.code == canonical.REJECT_NOT_FOUND
    n = sa_conn.execute(text("SELECT COUNT(*) FROM relay_submissions")).fetchone()[0]
    assert n == 0


def test_suspended_tweet_rejected(sa_conn):
    _seed(sa_conn)
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()
    body = _ok_body()
    body["user"]["suspended"] = True
    client = FakeClient(body=body)
    res = amplify.amplify_operator(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="10", source_chat_id="-700",
        source_message_id="m1", raw_url=URL,
    )
    assert res.code == amplify.AMPLIFY_REJECTED
    assert res.rejection.code == canonical.REJECT_SUSPENDED


# ==========================================================================
# Authorization — role-gated
# ==========================================================================
def test_non_operator_amplify_operator_rejected(sa_conn):
    _seed(sa_conn)
    _dests(sa_conn, "orgA")
    sa_conn.commit()
    client = FakeClient(body=_ok_body())
    # user 77 has no role.
    res = amplify.amplify_operator(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="77", source_chat_id="-700",
        source_message_id="m1", raw_url=URL,
    )
    assert res.code == amplify.AMPLIFY_NOT_AUTHORIZED
    # No submission created for an unauthorized caller.
    n = sa_conn.execute(text("SELECT COUNT(*) FROM relay_submissions")).fetchone()[0]
    assert n == 0


def test_non_member_amplify_shared_rejected(sa_conn):
    _seed(sa_conn)
    _dests(sa_conn, "orgA")
    sa_conn.commit()
    client = FakeClient(body=_ok_body())
    res = amplify.amplify_shared(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="88", source_chat_id="-shared",
        source_message_id="m1", raw_url=URL,
    )
    assert res.code == amplify.AMPLIFY_NOT_AUTHORIZED
    n = sa_conn.execute(text("SELECT COUNT(*) FROM relay_submissions")).fetchone()[0]
    assert n == 0


# ==========================================================================
# One-pending-per-tweet merge (§11 #2)
# ==========================================================================
def test_duplicate_amplify_merges_into_existing_submission(sa_conn):
    _seed(sa_conn, config=json.dumps({"quorum": {"threshold": 3, "emoji": EMOJI}}))
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")
    _grant(sa_conn, "orgA", 20, "sable_operator")
    sa_conn.commit()
    client = FakeClient(body=_ok_body())

    first = amplify.amplify_operator(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="10", source_chat_id="-700",
        source_message_id="m1", raw_url=URL, submitter_handle="op1",
    )
    assert first.code == amplify.AMPLIFY_PENDING

    # A DIFFERENT operator /amplify's the SAME tweet → merges (counts as their vote).
    second = amplify.amplify_operator(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="20", source_chat_id="-700",
        source_message_id="m2", raw_url=URL, submitter_handle="op2",
    )
    assert second.code == amplify.AMPLIFY_MERGED
    assert second.submission_id == first.submission_id
    # Exactly ONE submission row for this (org, tweet).
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_submissions WHERE org_id = 'orgA'")
    ).fetchone()[0]
    assert n == 1
    # Two distinct operator votes now recorded (op1 + op2); still below threshold 3.
    assert second.operator_count == 2
    assert _submission_status(sa_conn, first.submission_id) == "pending"


def test_record_control_message_backfills_for_reaction_routing(sa_conn):
    _seed(sa_conn, config=json.dumps({"quorum": {"threshold": 2, "emoji": EMOJI}}))
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()
    client = FakeClient(body=_ok_body())
    res = amplify.amplify_operator(
        sa_conn, client, org_id="orgA", platform="telegram",
        submitter_external_user_id="10", source_chat_id="-700",
        source_message_id="m1", raw_url=URL,
    )
    amplify.record_control_message(sa_conn, res.submission_id, "ctrl-XYZ")
    sa_conn.commit()
    found = relay_db.find_submission_by_control(sa_conn, "-700", "ctrl-XYZ")
    assert found is not None
    assert found["id"] == res.submission_id
