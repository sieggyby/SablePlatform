"""C3.6 — AutoCM publisher (DESIGN §4 publisher/tg + publisher/x_reply).

C3.6 is the SOLE owner of the ``[Approve]`` → publish enqueue. C3.5b only records
the operator decision + ``final_text``; this chunk reads a C3.5b-approved draft
and ENQUEUEs exactly ONE ``relay_publication_jobs`` row via
``relay/db.enqueue_publication_job`` — it NEVER calls a transport directly (the
C2.4 publisher does the actual send).

The single load-bearing C3.6 behavior is exercised here (NOT deferred to C3.10):
  (a) an approved draft → exactly ONE pending outbox row (count, org_id, payload);
  (b) idempotency — re-running over the same approved draft does NOT double-enqueue;
  (c) the direct-send path is never taken — only the outbox is written;
  (d) x_reply is unreachable while flagged off.

NO real telegram / network: the test asserts ONLY the DB outbox path. The relay
publisher's transport seam is never instantiated here.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from sable_platform.autocm.publisher import tg, x_reply
from sable_platform.autocm.publisher.tg import (
    ACTION_PUBLISH_ENQUEUED,
    NotImplementedTgPublisher,
    carrier_x_id,
    publish_approved_draft,
    publish_pending_approved,
)
from sable_platform.db.audit import list_audit_log
from sable_platform.relay import db as relay_db


# ---------------------------------------------------------------------------
# Seed helpers — org + relay_client + autocm_client + a source chat + inbound
# message the draft replies to (the destination the public reply targets).
# ---------------------------------------------------------------------------
SOURCE_CHAT_EXTERNAL = "-100555"  # the external (telegram) chat id the reply goes to
SOURCE_MSG_EXTERNAL = "tg-msg-42"  # the inbound message we reply to


def _seed_client(conn, org_id="orgRM"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id})
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled) "
            "VALUES (:o, 'RobotMoney', 'hitl', 1)"
        ),
        {"o": org_id},
    )
    conn.commit()
    client_id = conn.execute(
        text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}
    ).fetchone()[0]
    return org_id, int(client_id)


def _seed_source_message(conn, org_id, *, platform="telegram"):
    """Create the relay_chats surface + the inbound relay_messages row to reply to."""
    chat_row_id = relay_db.upsert_chat(
        conn, org_id, SOURCE_CHAT_EXTERNAL, platform=platform, title="RM community"
    )
    msg_row_id = relay_db.persist_inbound_message(
        conn,
        org_id=org_id,
        chat_row_id=chat_row_id,
        platform=platform,
        external_message_id=SOURCE_MSG_EXTERNAL,
        external_user_id="curious_degen",
        text_body="how does the vault actually work?",
    )
    conn.commit()
    return int(chat_row_id), int(msg_row_id)


def _seed_approved_draft(
    conn,
    client_id,
    *,
    source_message_id,
    source_chat_id,
    draft_text="The vault deploys treasury capital into vetted strategies.",
    cited="[7, 9]",
    status="approved",
):
    conn.execute(
        text(
            "INSERT INTO autocm_drafts "
            "(client_id, source_message_id, source_chat_id, category, tier, register, "
            " draft_text, confidence, cited_chunk_ids, status) "
            "VALUES (:c, :sm, :sc, 'mechanics', 2, 'calm', :dt, 0.72, :cited, :st)"
        ),
        {
            "c": client_id,
            "sm": source_message_id,
            "sc": source_chat_id,
            "dt": draft_text,
            "cited": cited,
            "st": status,
        },
    )
    conn.commit()
    return int(
        conn.execute(text("SELECT id FROM autocm_drafts ORDER BY id DESC LIMIT 1")).fetchone()[0]
    )


def _record_approve_review(conn, draft_id, client_id, *, reviewer="op-sieggy", edited_text=None):
    """Insert the C3.5b operator-decision row (approve, or edit with edited_text)."""
    decision = "edit" if edited_text is not None else "approve"
    conn.execute(
        text(
            "INSERT INTO autocm_reviews "
            "(draft_id, client_id, reviewer, decision, edited_text, is_clean_approval) "
            "VALUES (:d, :c, :rev, :dec, :edited, 1)"
        ),
        {"d": draft_id, "c": client_id, "rev": reviewer, "dec": decision, "edited": edited_text},
    )
    conn.commit()


def _jobs(conn, *, state=None):
    sql = (
        "SELECT id, org_id, tweet_id, destination_platform, destination_chat_id, state "
        "FROM relay_publication_jobs"
    )
    if state is not None:
        sql += " WHERE state = :st"
        return [dict(r._mapping) for r in conn.execute(text(sql), {"st": state}).fetchall()]
    return [dict(r._mapping) for r in conn.execute(text(sql)).fetchall()]


# ===========================================================================
# (a) An approved draft → exactly ONE pending relay_publication_jobs row.
# ===========================================================================
def test_approved_draft_enqueues_exactly_one_pending_job(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    chat_row_id, msg_row_id = _seed_source_message(sa_conn, org_id)
    draft_id = _seed_approved_draft(
        sa_conn, client_id, source_message_id=msg_row_id, source_chat_id=chat_row_id
    )
    _record_approve_review(sa_conn, draft_id, client_id)

    result = publish_approved_draft(sa_conn, draft_id)

    assert result.enqueued is True
    assert result.job_id is not None
    assert result.skipped_reason is None

    # exactly ONE outbox row, in state 'pending'
    pending = _jobs(sa_conn, state="pending")
    assert len(pending) == 1
    assert len(_jobs(sa_conn)) == 1  # no other states either

    job = pending[0]
    # org_id + destination payload assertions
    assert job["org_id"] == org_id
    assert job["destination_platform"] == "telegram"
    assert job["destination_chat_id"] == SOURCE_CHAT_EXTERNAL
    assert job["state"] == "pending"
    assert job["tweet_id"] == result.tweet_id

    # payload contents: the carrier relay_tweets row holds the final_text the C2.4
    # publisher will send (the body IS the payload — the outbox is X-mirror-shaped).
    carrier = relay_db.get_tweet_by_row_id(sa_conn, int(job["tweet_id"]))
    assert carrier is not None
    assert carrier["text"] == "The vault deploys treasury capital into vetted strategies."
    assert carrier["x_id"] == carrier_x_id(draft_id)
    raw = json.loads(carrier["raw"])
    assert raw["autocm_draft_id"] == draft_id
    assert raw["reply_to_external_message_id"] == SOURCE_MSG_EXTERNAL

    # the draft is flipped to 'published'
    status = sa_conn.execute(
        text("SELECT status FROM autocm_drafts WHERE id = :d"), {"d": draft_id}
    ).fetchone()[0]
    assert status == "published"

    # SAFETY §5 audit field set persisted on the enqueue (the not-yet-sent path is
    # still observable).
    rows = list_audit_log(sa_conn, action=ACTION_PUBLISH_ENQUEUED)
    assert len(rows) == 1
    detail = json.loads(rows[0]._mapping["detail_json"])
    assert detail["draft_id"] == draft_id
    assert detail["job_id"] == result.job_id
    assert detail["final_text"] == "The vault deploys treasury capital into vetted strategies."
    assert detail["draft_text"] == "The vault deploys treasury capital into vetted strategies."
    assert detail["chunk_ids"] == [7, 9]
    assert detail["reviewer"] == "op-sieggy"
    assert detail["category"] == "mechanics"
    assert detail["tier"] == 2
    assert detail["confidence"] == pytest.approx(0.72)
    assert detail["source_message_id"] == msg_row_id


def test_edited_draft_publishes_the_edited_final_text(sa_conn):
    """The published body is the operator's edited_text, not the original draft."""
    org_id, client_id = _seed_client(sa_conn)
    chat_row_id, msg_row_id = _seed_source_message(sa_conn, org_id)
    draft_id = _seed_approved_draft(
        sa_conn, client_id, source_message_id=msg_row_id, source_chat_id=chat_row_id
    )
    _record_approve_review(
        sa_conn, draft_id, client_id, edited_text="the vault routes treasury into vetted strats."
    )

    result = publish_approved_draft(sa_conn, draft_id)
    assert result.enqueued is True

    carrier = relay_db.get_tweet_by_row_id(sa_conn, int(result.tweet_id))
    assert carrier["text"] == "the vault routes treasury into vetted strats."


# ===========================================================================
# (b) Idempotency — re-running over the same approved draft does NOT double-enqueue.
# ===========================================================================
def test_rerun_does_not_double_enqueue(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    chat_row_id, msg_row_id = _seed_source_message(sa_conn, org_id)
    draft_id = _seed_approved_draft(
        sa_conn, client_id, source_message_id=msg_row_id, source_chat_id=chat_row_id
    )
    _record_approve_review(sa_conn, draft_id, client_id)

    first = publish_approved_draft(sa_conn, draft_id)
    assert first.enqueued is True

    # Re-run: the status guard (draft now 'published') short-circuits → no-op.
    second = publish_approved_draft(sa_conn, draft_id)
    assert second.enqueued is False
    assert second.skipped_reason == "not_approved"

    assert len(_jobs(sa_conn)) == 1  # still exactly one outbox row
    # still exactly one enqueue audit row (no re-audit on the no-op).
    assert len(list_audit_log(sa_conn, action=ACTION_PUBLISH_ENQUEUED)) == 1


def test_outbox_dedupe_collapses_a_second_enqueue_even_if_status_guard_bypassed(sa_conn):
    """Defence-in-depth: force the draft back to 'approved' so the status guard does
    NOT catch the re-run; the partial-unique outbox dedupe must still collapse it
    (the deterministic carrier x_id → same tweet_id → same dedupe key → None)."""
    org_id, client_id = _seed_client(sa_conn)
    chat_row_id, msg_row_id = _seed_source_message(sa_conn, org_id)
    draft_id = _seed_approved_draft(
        sa_conn, client_id, source_message_id=msg_row_id, source_chat_id=chat_row_id
    )
    _record_approve_review(sa_conn, draft_id, client_id)

    first = publish_approved_draft(sa_conn, draft_id)
    assert first.enqueued is True

    # Bypass idempotency layer #1 by re-marking the draft 'approved'.
    sa_conn.execute(
        text("UPDATE autocm_drafts SET status = 'approved' WHERE id = :d"), {"d": draft_id}
    )
    sa_conn.commit()

    second = publish_approved_draft(sa_conn, draft_id)
    assert second.enqueued is False
    assert second.skipped_reason == "already_enqueued"
    # same carrier tweet (idempotent upsert), still exactly one outbox row.
    assert second.tweet_id == first.tweet_id
    assert len(_jobs(sa_conn)) == 1
    assert len(list_audit_log(sa_conn, action=ACTION_PUBLISH_ENQUEUED)) == 1


# ===========================================================================
# (c) The direct-send path is never taken — only the outbox is written.
# ===========================================================================
def test_publish_never_calls_a_transport_directly(sa_conn, monkeypatch):
    """C3.6 must publish ONLY by writing the outbox. We (1) assert the only relay
    write is enqueue_publication_job, and (2) assert the direct-transport stub
    raises if anyone ever calls it (proving the direct path is a dead seam)."""
    org_id, client_id = _seed_client(sa_conn)
    chat_row_id, msg_row_id = _seed_source_message(sa_conn, org_id)
    draft_id = _seed_approved_draft(
        sa_conn, client_id, source_message_id=msg_row_id, source_chat_id=chat_row_id
    )
    _record_approve_review(sa_conn, draft_id, client_id)

    enqueue_calls = {"n": 0}
    real_enqueue = relay_db.enqueue_publication_job

    def _counting_enqueue(*args, **kwargs):
        enqueue_calls["n"] += 1
        return real_enqueue(*args, **kwargs)

    monkeypatch.setattr(tg.relay_db, "enqueue_publication_job", _counting_enqueue)

    result = publish_approved_draft(sa_conn, draft_id)
    assert result.enqueued is True

    # the ONLY publish path used is the outbox enqueue.
    assert enqueue_calls["n"] == 1
    assert len(_jobs(sa_conn, state="pending")) == 1

    # the direct-transport seam is a dead path — any call to it raises.
    with pytest.raises(NotImplementedError):
        NotImplementedTgPublisher().publish(org_id, SOURCE_CHAT_EXTERNAL, "hi", reply_to="1")


# ===========================================================================
# Guard paths — a non-approved draft / a draft with no destination is a no-op.
# ===========================================================================
def test_non_approved_draft_is_a_noop(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    chat_row_id, msg_row_id = _seed_source_message(sa_conn, org_id)
    draft_id = _seed_approved_draft(
        sa_conn,
        client_id,
        source_message_id=msg_row_id,
        source_chat_id=chat_row_id,
        status="hitl_pending",  # NOT approved
    )

    result = publish_approved_draft(sa_conn, draft_id)
    assert result.enqueued is False
    assert result.skipped_reason == "not_approved"
    assert _jobs(sa_conn) == []


def test_missing_destination_is_a_reported_skip(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    # approved draft with NO source_message_id → no resolvable destination.
    draft_id = _seed_approved_draft(
        sa_conn, client_id, source_message_id=None, source_chat_id=None
    )
    _record_approve_review(sa_conn, draft_id, client_id)

    result = publish_approved_draft(sa_conn, draft_id)
    assert result.enqueued is False
    assert result.skipped_reason == "no_destination"
    assert _jobs(sa_conn) == []
    # the draft stays 'approved' for operator inspection.
    status = sa_conn.execute(
        text("SELECT status FROM autocm_drafts WHERE id = :d"), {"d": draft_id}
    ).fetchone()[0]
    assert status == "approved"


def test_sweep_drains_all_approved_drafts(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    chat_row_id, _ = _seed_source_message(sa_conn, org_id)
    # two distinct inbound messages → two approved drafts.
    m1 = relay_db.persist_inbound_message(
        sa_conn, org_id=org_id, chat_row_id=chat_row_id, platform="telegram",
        external_message_id="m-1", text_body="q1",
    )
    m2 = relay_db.persist_inbound_message(
        sa_conn, org_id=org_id, chat_row_id=chat_row_id, platform="telegram",
        external_message_id="m-2", text_body="q2",
    )
    sa_conn.commit()
    d1 = _seed_approved_draft(sa_conn, client_id, source_message_id=int(m1), source_chat_id=chat_row_id)
    d2 = _seed_approved_draft(sa_conn, client_id, source_message_id=int(m2), source_chat_id=chat_row_id)
    _record_approve_review(sa_conn, d1, client_id)
    _record_approve_review(sa_conn, d2, client_id)

    results = publish_pending_approved(sa_conn, org_id=org_id)
    assert sum(1 for r in results if r.enqueued) == 2
    assert len(_jobs(sa_conn, state="pending")) == 2


# ===========================================================================
# (d) x_reply is unreachable while flagged off.
# ===========================================================================
def test_x_reply_disabled_by_default():
    assert x_reply.X_REPLY_ENABLED_DEFAULT is False
    assert x_reply.x_reply_enabled() is False


def test_x_reply_enqueue_unreachable_while_flagged_off(sa_conn):
    org_id, client_id = _seed_client(sa_conn)
    chat_row_id, msg_row_id = _seed_source_message(sa_conn, org_id)
    draft_id = _seed_approved_draft(
        sa_conn, client_id, source_message_id=msg_row_id, source_chat_id=chat_row_id
    )
    with pytest.raises(x_reply.XReplyDisabled):
        x_reply.enqueue_x_reply(sa_conn, draft_id)
    # nothing was enqueued through the disabled X path.
    assert _jobs(sa_conn) == []


def test_x_reply_publisher_raises_while_off():
    pub = x_reply.XReplyPublisher()  # default: off
    with pytest.raises(x_reply.XReplyDisabled):
        pub.publish("orgRM", "tweet-1", "hello")


def test_x_reply_publisher_v2_seam_present_but_not_implemented():
    """When the flag is flipped (v2), the seam exists but is not implemented yet —
    proving it is STRUCTURAL code, not a removed/absent feature."""
    pub = x_reply.XReplyPublisher(enabled=True)
    with pytest.raises(NotImplementedError):
        pub.publish("orgRM", "tweet-1", "hello")
    with pytest.raises(NotImplementedError):
        x_reply.enqueue_x_reply(None, 1, enabled=True)
