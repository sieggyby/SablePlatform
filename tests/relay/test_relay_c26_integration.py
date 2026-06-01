"""C2.6 — relay integration suite + §15 threat-model coverage.

This ties the relay flows together END TO END (not unit-by-unit) and explicitly
NAMES every SableRelay PLAN §15 threat-model case the MEGAPLAN C2.6 scope
enumerates:

  * Flow A  — poll → hydrate → mirror (publication-job fan-out → publisher send)
  * Flow B  — operator paste → quorum → outbox → publish (reaction-driven, the
              §3.1 exactly-once enqueue all the way to a recorded publication)
  * Flow C  — shared-chat immediate publish (single approval → publish)
  * Flow D  — flag-reply → notify opted-in member → reply follow-through tracked

  §15 threat-model cases (the C2.6 threat set), each a NAMED test:
    - §15.4 spoofing            : external user_id is truth, not the handle; a
                                  handle change grants no new authority.
    - §15.4 reaction abuse      : anonymous reactions and non-operator reactions
                                  never count toward quorum.
    - §8/§15.4 identity coll.   : /link-x rejects an X-id already linked to a
                                  different member; admin-merge resolves it.
    - §15.1 spoofed/disallowed  : a non-tweet / disallowed URL is rejected — no
                                  submission/opportunity/publication created.
    - §15.1/§15.6 deleted-tweet : a tweet deleted between submission and publish
                                  is rejected at publish-hydration (job dead,
                                  submission rejected) — never broadcast.
    - §15.2 output escaping     : a mirrored tweet containing @everyone/@here
                                  (Discord) / @all (Telegram) renders as PLAIN
                                  TEXT — zero ping (AllowedMentions.none() +
                                  escape_markdown/escape_mentions / html.escape).

NO real Telegram / Discord / SocialData / network is touched anywhere: hydration
goes through a duck-typed fake exposing ``hydrate_tweet`` /
``fetch_conversation_replies`` (the only methods the relay calls), and the
publisher send goes through a recording fake :class:`Sender`. DB work runs
against the in-memory ``sa_conn`` schema (full 057 relay_* tables).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot import escaping
from sable_platform.relay.bot.dedupe import mark_processed
from sable_platform.relay.bot.handlers import amplify, flag_reply, identity
from sable_platform.relay.bot.handlers.quorum import (
    QUORUM_ANONYMOUS_DROPPED,
    QUORUM_NON_OPERATOR_DROPPED,
    QUORUM_REACHED,
    handle_reaction,
)
from sable_platform.relay.feed import canonical, poller, publisher
from sable_platform.relay.feed.publisher import SendOutcome
from sable_platform.relay.socialdata import HttpResponse, SocialDataClient

EMOJI = "\U0001F4E2"  # 📢, the default quorum emoji


# ==========================================================================
# Fakes — NO real network anywhere
# ==========================================================================
class FakeHydrator:
    """Duck-typed SocialData client used by ``canonical.hydrate_or_reject``.

    Only ``hydrate_tweet`` is consumed by the canonicalization/hydration path.
    Maps a tweet_id → a scripted hydrated body (or ``None`` for a hard 404 /
    deleted tweet). The matching real client is never instantiated.
    """

    def __init__(self, bodies: dict[str, dict | None]):
        self._bodies = bodies
        self.calls: list[tuple[str, str]] = []

    def hydrate_tweet(self, org_id, tweet_id):
        self.calls.append((org_id, str(tweet_id)))
        return self._bodies.get(str(tweet_id))


class RecordingSender:
    """A :class:`publisher.Sender` that records every send instead of hitting an API.

    ``sent`` accumulates the (platform, chat_id, tweet) of each successful send so
    a test can assert exactly what was mirrored — and run the escaped body through
    the §15.2 escaping module to prove no ping leaks.
    """

    def __init__(self):
        self.sent: list[dict] = []
        self._seq = 0

    def send(self, *, org_id, destination_platform, destination_chat_id, tweet, submission_id):
        self._seq += 1
        self.sent.append(
            {
                "org_id": org_id,
                "platform": destination_platform,
                "chat_id": destination_chat_id,
                "tweet": tweet,
                "submission_id": submission_id,
            }
        )
        return SendOutcome(external_message_id=f"ext-{self._seq}")

    def find_recent_message(self, *, destination_platform, destination_chat_id, tweet):
        return None


def _ok_body(x_id, *, handle="archerfit", author_id="555", text_body="great voice", conv=None):
    return {
        "id_str": x_id,
        "id": int(x_id),
        "full_text": text_body,
        "user": {"id_str": author_id, "screen_name": handle},
        "conversation_id_str": conv or x_id,
    }


def _sd_timeline_client(conn, tweets):
    """A real :class:`SocialDataClient` whose HTTP layer returns a fixed timeline.

    Used to drive the Flow A poller end-to-end through the C1.2 client (cache +
    cost logging) with NO network — the injected ``http_get`` is a pure function.
    """
    def http_get(path, params):
        return HttpResponse(status_code=200, json_body={"tweets": tweets})

    return SocialDataClient(http_get=http_get, conn=conn, sleep=lambda *_: None, jitter=lambda: 1.0)


# ==========================================================================
# Seeding helpers
# ==========================================================================
def _seed_client(conn, org_id="orgA", *, config="{}", enabled=1):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, :e, :c)"),
        {"o": org_id, "e": enabled, "c": config},
    )


def _binding(conn, org_id, platform, chat_id, role):
    conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:o, :p, :c, :r, 'active')"
        ),
        {"o": org_id, "p": platform, "c": chat_id, "r": role},
    )


def _dests(conn, org_id):
    _binding(conn, org_id, "discord", "chan-disc", "broadcast")
    _binding(conn, org_id, "telegram", "-999comm", "community")


def _grant(conn, org_id, tg_user_id, role, *, handle=None):
    mid = relay_db.auto_create_member_identity(
        conn, "telegram", str(tg_user_id), handle=handle or f"u{tg_user_id}"
    )
    conn.execute(
        text("INSERT INTO relay_member_roles (member_id, org_id, role) VALUES (:m, :o, :r)"),
        {"m": mid, "o": org_id, "r": role},
    )
    return mid


def _submission_status(conn, sid):
    return conn.execute(
        text("SELECT status FROM relay_submissions WHERE id = :id"), {"id": sid}
    ).fetchone()[0]


def _job_states(conn, org_id):
    return [
        r[0]
        for r in conn.execute(
            text("SELECT state FROM relay_publication_jobs WHERE org_id = :o ORDER BY id"),
            {"o": org_id},
        ).fetchall()
    ]


# ==========================================================================
# FLOW A — poll → hydrate → mirror (publication jobs → publisher send)
# ==========================================================================
def test_flow_a_poll_to_mirror_end_to_end(sa_conn):
    """Flow A: poll a source timeline, hydrate the tweets, enqueue a job per
    active broadcast/community binding, then drive the publisher to MIRROR each.

    Asserts the whole substrate path: poller writes ``relay_tweets`` + pending
    jobs; the publisher claims each, sends (recorded fake), records the
    ``relay_publications`` row, and marks the job ``done`` (the §3.1 DB-exactly-once
    + external effectively-once guarantee).
    """
    _seed_client(sa_conn, "orgA", config=json.dumps({"polling": {"source_x_user_id": "55"}}))
    _dests(sa_conn, "orgA")
    sa_conn.commit()

    tweets = [_ok_body("9001", handle="solstitch"), _ok_body("9002", handle="solstitch")]
    client = _sd_timeline_client(sa_conn, tweets)

    [poll_res] = poller.poll_all_enabled(sa_conn, client)
    assert poll_res.new_tweets == 2
    # 2 tweets x 2 destinations = 4 pending jobs.
    assert poll_res.jobs_enqueued == 4
    assert _job_states(sa_conn, "orgA") == ["pending"] * 4

    sender = RecordingSender()
    results = publisher.drain_due_jobs(sa_conn, sender)
    assert len(results) == 4
    assert all(r.final_state == "done" and r.published for r in results)
    # Every job mirrored exactly once → 4 recorded sends + 4 publications.
    assert len(sender.sent) == 4
    assert _job_states(sa_conn, "orgA") == ["done"] * 4
    pubs = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_publications WHERE org_id = 'orgA'")
    ).scalar()
    assert pubs == 4

    # Draining again is a no-op (nothing due) — external effectively-once.
    assert publisher.drain_due_jobs(sa_conn, sender) == []
    assert len(sender.sent) == 4


# ==========================================================================
# FLOW B — operator paste → quorum → outbox → publish (reaction-driven)
# ==========================================================================
def test_flow_b_quorum_to_publish_end_to_end(sa_conn):
    """Flow B end-to-end: operator pastes a tweet (pending, 1/2), a SECOND
    operator reacts 📢 → quorum → fan-out → publisher mirrors.

    This exercises the load-bearing §3.1 path: the guarded pending→ready
    transition + outbox enqueue (in the reaction handler's one BEGIN IMMEDIATE),
    then the publisher claim→send→record→done cycle. No external call inside any
    txn; the publisher send is the recording fake.
    """
    _seed_client(
        sa_conn, "orgA", config=json.dumps({"quorum": {"threshold": 2, "emoji": EMOJI}})
    )
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")  # submitter
    _grant(sa_conn, "orgA", 20, "sable_operator")  # the quorum-completing reactor
    sa_conn.commit()

    hydrator = FakeHydrator({"7777": _ok_body("7777")})

    # 1. Operator 10 pastes the tweet into the operator chat → PENDING (1/2).
    res = amplify.amplify_operator(
        sa_conn,
        hydrator,
        org_id="orgA",
        platform="telegram",
        submitter_external_user_id="10",
        source_chat_id="op-chat",
        source_message_id="m1",
        raw_url="https://x.com/archerfit/status/7777",
    )
    assert res.code == amplify.AMPLIFY_PENDING
    assert res.operator_count == 1 and res.threshold == 2
    assert _submission_status(sa_conn, res.submission_id) == "pending"

    # The listener posts an ack and back-fills its control-message id.
    amplify.record_control_message(sa_conn, res.submission_id, "ctrl-1")
    sa_conn.commit()

    # 2. Operator 20 reacts 📢 on the control message → quorum reached + fan-out.
    qres = handle_reaction(
        sa_conn,
        platform="telegram",
        update_id="u-100",
        source_chat_id="op-chat",
        control_message_id="ctrl-1",
        external_user_id="20",
        emoji_added=EMOJI,
    )
    assert qres.code == QUORUM_REACHED
    assert qres.transitioned is True  # the single exactly-once writer
    assert qres.operator_count == 2
    assert qres.jobs_enqueued == 2  # one per destination binding
    assert _submission_status(sa_conn, res.submission_id) == "ready_to_publish"

    # 3. Publisher mirrors both jobs.
    sender = RecordingSender()
    pubres = publisher.drain_due_jobs(sa_conn, sender)
    assert {r.final_state for r in pubres} == {"done"}
    assert len(sender.sent) == 2
    assert _job_states(sa_conn, "orgA") == ["done", "done"]


def test_flow_b_quorum_enqueue_is_exactly_once_under_replayed_reaction(sa_conn):
    """§3.1 exactly-once: a REPLAYED reaction update (same update_id) is a dedupe
    no-op and does NOT re-transition or double-enqueue."""
    _seed_client(
        sa_conn, "orgA", config=json.dumps({"quorum": {"threshold": 2, "emoji": EMOJI}})
    )
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")
    _grant(sa_conn, "orgA", 20, "sable_operator")
    sa_conn.commit()

    res = amplify.amplify_operator(
        sa_conn,
        FakeHydrator({"8888": _ok_body("8888")}),
        org_id="orgA",
        platform="telegram",
        submitter_external_user_id="10",
        source_chat_id="op-chat",
        source_message_id="m1",
        raw_url="https://x.com/archerfit/status/8888",
    )
    amplify.record_control_message(sa_conn, res.submission_id, "ctrl-9")
    sa_conn.commit()

    kwargs = dict(
        platform="telegram",
        source_chat_id="op-chat",
        control_message_id="ctrl-9",
        external_user_id="20",
        emoji_added=EMOJI,
    )
    first = handle_reaction(sa_conn, update_id="dup-1", **kwargs)
    assert first.code == QUORUM_REACHED and first.transitioned is True
    # Replay the EXACT same update_id → dedupe drops it (no second transition).
    second = handle_reaction(sa_conn, update_id="dup-1", **kwargs)
    assert second.transitioned is False
    # Exactly one fan-out happened: 2 jobs total (not 4).
    assert len(_job_states(sa_conn, "orgA")) == 2


# ==========================================================================
# FLOW C — shared-chat immediate publish (single approval)
# ==========================================================================
def test_flow_c_immediate_publish_end_to_end(sa_conn):
    """Flow C: a single /amplify in the shared chat publishes immediately (no
    quorum) and the publisher mirrors it."""
    _seed_client(sa_conn, "orgA")
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 30, "sable_operator")
    sa_conn.commit()

    res = amplify.amplify_shared(
        sa_conn,
        FakeHydrator({"4242": _ok_body("4242")}),
        org_id="orgA",
        platform="telegram",
        submitter_external_user_id="30",
        source_chat_id="shared-chat",
        source_message_id="m1",
        raw_url="https://x.com/archerfit/status/4242",
    )
    assert res.code == amplify.AMPLIFY_PUBLISHED
    assert res.published is True and res.jobs_enqueued == 2
    assert _submission_status(sa_conn, res.submission_id) == "published"

    sender = RecordingSender()
    pubres = publisher.drain_due_jobs(sa_conn, sender)
    assert {r.final_state for r in pubres} == {"done"}
    assert len(sender.sent) == 2


# ==========================================================================
# FLOW D — flag-reply → notify → reply follow-through tracked
# ==========================================================================
def test_flow_d_flag_reply_to_followthrough_end_to_end(sa_conn):
    """Flow D end-to-end: an operator /flag-reply notifies an opted-in member,
    then the 4.6 reply-tracker detects that member's reply (matched on X user id,
    not handle) and writes the follow-through."""
    _seed_client(sa_conn, "orgA", config=json.dumps({"polling": {"source_x_user_id": "55"}}))
    _grant(sa_conn, "orgA", 40, "sable_operator")  # flagger

    # An opted-in member with a linked X identity (so 4.6 can match their reply).
    target_mid = relay_db.auto_create_member_identity(sa_conn, "telegram", "777", handle="brian")
    relay_db.upsert_member_preference(sa_conn, target_mid, "orgA", replies_optin=True)
    sa_conn.execute(
        text(
            "INSERT INTO relay_member_identities (member_id, platform, external_user_id, handle) "
            "VALUES (:m, 'x', '987', 'influenza')"
        ),
        {"m": target_mid},
    )
    sa_conn.commit()

    # 1. Operator 40 flags a reply opportunity on a community tweet (conv id 5500).
    hydrator = FakeHydrator({"5500": _ok_body("5500", handle="archerfit", conv="5500")})
    fr = flag_reply.flag_reply(
        sa_conn,
        hydrator,
        org_id="orgA",
        platform="telegram",
        flagger_external_user_id="40",
        raw_url="https://x.com/archerfit/status/5500",
        note="great voice — would love a team reply",
    )
    assert fr.code == flag_reply.FLAG_REPLY_CREATED
    # Exactly the opted-in member was notified.
    assert [t.member_id for t in fr.targets] == [target_mid]
    sa_conn.commit()

    notif_id = sa_conn.execute(
        text("SELECT id FROM relay_reply_notifications WHERE member_id = :m"),
        {"m": target_mid},
    ).fetchone()[0]

    # 2. The 4.6 reply-tracker polls the conversation and finds the member's reply.
    def http_get(path, params):
        return HttpResponse(
            status_code=200,
            json_body={
                "tweets": [
                    {"id_str": "6000", "user": {"id_str": "111", "screen_name": "rando"}},
                    {"id_str": "6001", "user": {"id_str": "987", "screen_name": "influenza"}},
                ]
            },
        )

    sd_client = SocialDataClient(
        http_get=http_get, conn=sa_conn, sleep=lambda *_: None, jitter=lambda: 1.0
    )
    track = poller.track_reply_followups(sa_conn, sd_client, "orgA")
    assert track.followthroughs_recorded == 1
    assert notif_id in track.matched_notification_ids

    row = sa_conn.execute(
        text("SELECT replied_at, replied_tweet_id FROM relay_reply_notifications WHERE id = :id"),
        {"id": notif_id},
    ).fetchone()
    assert row[0] is not None  # replied_at written
    assert row[1] == "6001"  # the member's reply x_id (not the rando's)


# ==========================================================================
# §15.4 — SPOOFING (external user_id is truth, never the handle)
# ==========================================================================
def test_s15_4_spoofing_handle_change_grants_no_authority(sa_conn):
    """§15.4: authority is keyed on the external user_id, not the handle.

    An impostor who later sets the SAME display handle as a real operator — but
    has a DIFFERENT telegram user_id — gets NO operator authority. Their /amplify
    in the operator chat is rejected. The real operator (same id, changed handle)
    keeps authority.
    """
    _seed_client(sa_conn, "orgA", config=json.dumps({"quorum": {"threshold": 1, "emoji": EMOJI}}))
    _dests(sa_conn, "orgA")
    real_op = _grant(sa_conn, "orgA", 100, "sable_operator", handle="trusted_op")
    sa_conn.commit()

    hydrator = FakeHydrator({"1001": _ok_body("1001"), "1002": _ok_body("1002")})

    # Impostor: a DIFFERENT user_id (999) who spoofs the trusted handle.
    impostor = amplify.amplify_operator(
        sa_conn,
        hydrator,
        org_id="orgA",
        platform="telegram",
        submitter_external_user_id="999",
        source_chat_id="op-chat",
        source_message_id="m1",
        raw_url="https://x.com/archerfit/status/1001",
        submitter_handle="trusted_op",  # spoofed display handle
    )
    assert impostor.code == amplify.AMPLIFY_NOT_AUTHORIZED

    # The real operator (id 100), even after a handle change, keeps authority.
    relay_db.auto_create_member_identity(sa_conn, "telegram", "100", handle="renamed_op")
    real = amplify.amplify_operator(
        sa_conn,
        hydrator,
        org_id="orgA",
        platform="telegram",
        submitter_external_user_id="100",
        source_chat_id="op-chat",
        source_message_id="m2",
        raw_url="https://x.com/archerfit/status/1002",
        submitter_handle="renamed_op",
    )
    assert real.code == amplify.AMPLIFY_PUBLISHED  # threshold==1 → immediate
    # The role row still belongs to member 100.
    assert relay_db.is_relay_operator(sa_conn, real_op, "orgA") is True


# ==========================================================================
# §15.4 — REACTION ABUSE (anonymous + non-operator reactions never count)
# ==========================================================================
def test_s15_4_reaction_abuse_anonymous_and_non_operator_dropped(sa_conn):
    """§15.4 / §3: an anonymous reaction (no user) and a non-operator reaction
    NEVER count toward quorum and never transition the submission."""
    _seed_client(
        sa_conn, "orgA", config=json.dumps({"quorum": {"threshold": 2, "emoji": EMOJI}})
    )
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")  # submitter (vote 1/2)
    sa_conn.commit()

    res = amplify.amplify_operator(
        sa_conn,
        FakeHydrator({"3001": _ok_body("3001")}),
        org_id="orgA",
        platform="telegram",
        submitter_external_user_id="10",
        source_chat_id="op-chat",
        source_message_id="m1",
        raw_url="https://x.com/archerfit/status/3001",
    )
    amplify.record_control_message(sa_conn, res.submission_id, "ctrl-r")
    sa_conn.commit()

    # ANONYMOUS reaction (TG group-as-actor, external_user_id=None) → dropped.
    anon = handle_reaction(
        sa_conn,
        platform="telegram",
        update_id="anon-1",
        source_chat_id="op-chat",
        control_message_id="ctrl-r",
        external_user_id=None,
        emoji_added=EMOJI,
    )
    assert anon.code == QUORUM_ANONYMOUS_DROPPED

    # NON-OPERATOR reaction (a real but unprivileged user) → dropped, not recorded.
    nonop = handle_reaction(
        sa_conn,
        platform="telegram",
        update_id="nonop-1",
        source_chat_id="op-chat",
        control_message_id="ctrl-r",
        external_user_id="500",  # never granted a role
        emoji_added=EMOJI,
    )
    assert nonop.code == QUORUM_NON_OPERATOR_DROPPED

    # Submission stayed pending; only the submitter's lone vote stands; no fan-out.
    assert _submission_status(sa_conn, res.submission_id) == "pending"
    assert relay_db.count_distinct_quorum_operators(sa_conn, res.submission_id, "orgA", EMOJI) == 1
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_publication_jobs WHERE org_id='orgA'")
    ).scalar() == 0


# ==========================================================================
# §8 / §15.4 — IDENTITY COLLISIONS (/link-x reject + admin merge)
# ==========================================================================
def test_s8_identity_collision_rejected_then_admin_merge(sa_conn):
    """§8: an X id already linked to a DIFFERENT member is a collision — /link-x
    REJECTS it (writes nothing); the admin-merge re-points it."""
    _seed_client(sa_conn, "orgA")
    # member_a owns X id 555; member_b later tries to claim the same X id.
    member_a = relay_db.auto_create_member_identity(sa_conn, "telegram", "1", handle="a")
    member_b = relay_db.auto_create_member_identity(sa_conn, "telegram", "2", handle="b")
    _grant(sa_conn, "orgA", 9, "admin")  # the admin who can merge
    relay_db.link_x_identity(sa_conn, member_a, "555", handle="shared_x")
    sa_conn.commit()

    # member_b's /link-x collides → rejected, no write.
    coll = identity.link_x(
        sa_conn,
        platform="telegram",
        external_user_id="2",
        x_user_id="555",
        x_handle="shared_x",
    )
    assert coll.code == identity.LINK_COLLISION
    assert coll.existing_member_id == member_a
    # The X id is STILL linked to member_a (nothing was written for member_b).
    assert int(relay_db.get_x_identity(sa_conn, "555")["member_id"]) == member_a

    # Admin merge re-points the X id to member_b (the intended owner).
    merge = identity.admin_merge_x_identity(
        sa_conn,
        org_id="orgA",
        platform="telegram",
        admin_external_user_id="9",
        x_user_id="555",
        target_member_id=member_b,
    )
    assert merge.code == identity.MERGE_OK
    assert int(relay_db.get_x_identity(sa_conn, "555")["member_id"]) == member_b


# ==========================================================================
# §15.1 — SPOOFED / DISALLOWED URL rejection (no submission created)
# ==========================================================================
@pytest.mark.parametrize(
    "bad_url",
    [
        "https://evil.com/archerfit/status/123",  # wrong host
        "https://x.com/archerfit/likes",  # not a status permalink
        "https://t.co/abcd",  # opaque shortener (not resolvable offline)
        "ftp://x.com/archerfit/status/123",  # bad scheme
        "not a url at all",
    ],
)
def test_s15_1_disallowed_url_rejected_no_submission(sa_conn, bad_url):
    """§15.1: a disallowed / non-tweet URL is REJECTED — canonicalization returns
    a Rejection and /amplify creates NO submission / opportunity / publication."""
    _seed_client(sa_conn, "orgA")
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()

    # The canonicalizer rejects the URL before any hydration call.
    assert isinstance(canonical.canonicalize_tweet_url(bad_url), canonical.Rejection)

    # A hydrator that would 500 if ever called — it MUST NOT be called.
    class ExplodingHydrator:
        def hydrate_tweet(self, *a, **k):  # pragma: no cover - asserted not called
            raise AssertionError("hydration must not run for a disallowed URL")

    res = amplify.amplify_operator(
        sa_conn,
        ExplodingHydrator(),
        org_id="orgA",
        platform="telegram",
        submitter_external_user_id="10",
        source_chat_id="op-chat",
        source_message_id="m1",
        raw_url=bad_url,
    )
    assert res.code == amplify.AMPLIFY_REJECTED
    assert res.rejection is not None
    # Nothing minted.
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_submissions")).scalar() == 0
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_publication_jobs")).scalar() == 0
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_reply_opportunities")).scalar() == 0


def test_s15_1_deleted_tweet_on_submission_rejected(sa_conn):
    """§15.1: a hydration that returns deleted/not-found is rejected with the
    precise reason and creates NO submission."""
    _seed_client(sa_conn, "orgA")
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()

    # The tweet hydrates to None → hard 404 / deleted.
    hydrator = FakeHydrator({"404404": None})
    res = amplify.amplify_operator(
        sa_conn,
        hydrator,
        org_id="orgA",
        platform="telegram",
        submitter_external_user_id="10",
        source_chat_id="op-chat",
        source_message_id="m1",
        raw_url="https://x.com/archerfit/status/404404",
    )
    assert res.code == amplify.AMPLIFY_REJECTED
    assert res.rejection.code == canonical.REJECT_NOT_FOUND
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_submissions")).scalar() == 0


# ==========================================================================
# §15.1 / §15.6 — DELETED-BETWEEN-SUBMIT-AND-PUBLISH rejection (never broadcast)
# ==========================================================================
def test_s15_6_tweet_deleted_between_submit_and_publish_rejected(sa_conn):
    """§15.1/§15.6: a tweet that reaches quorum (submission ``ready_to_publish``
    + pending jobs) but is DELETED before the publisher claims it is re-hydrated
    at publish time → rejected.

    The publisher marks the job ``dead`` and the submission ``rejected`` and sends
    NOTHING (the recording sender records zero sends). Driven through Flow B with
    a single-operator threshold so the submission sits in ``ready_to_publish``
    (the rejectable state) when the publisher runs — Flow C would already be
    terminal ``published``.
    """
    _seed_client(
        sa_conn, "orgA", config=json.dumps({"quorum": {"threshold": 1, "emoji": EMOJI}})
    )
    _dests(sa_conn, "orgA")
    _grant(sa_conn, "orgA", 30, "sable_operator")
    sa_conn.commit()

    # Flow B with threshold==1: the submitter's lone /amplify reaches quorum,
    # transitioning the submission to ``ready_to_publish`` + 2 pending jobs (tweet
    # 7000 is live at submit time). The submission is NOT yet ``published`` — the
    # publisher records that only after a successful send.
    submit_hydrator = FakeHydrator({"7000": _ok_body("7000")})
    res = amplify.amplify_operator(
        sa_conn,
        submit_hydrator,
        org_id="orgA",
        platform="telegram",
        submitter_external_user_id="30",
        source_chat_id="op-chat",
        source_message_id="m1",
        raw_url="https://x.com/archerfit/status/7000",
    )
    assert res.code == amplify.AMPLIFY_PUBLISHED  # threshold==1 → fanned out
    assert _submission_status(sa_conn, res.submission_id) == "ready_to_publish"
    sa_conn.commit()

    # Now the tweet is DELETED before the publisher runs: publish-time hydration
    # returns None for 7000. The publisher rejects instead of sending.
    delete_hydrator = FakeHydrator({"7000": None})
    notified = []

    class _Notifier:
        def notify_rejected(self, *, org_id, source_chat_id, source_message_id, reason):
            notified.append((org_id, reason))

    sender = RecordingSender()
    pubres = publisher.drain_due_jobs(
        sa_conn, sender, sd_client=delete_hydrator, source_notifier=_Notifier()
    )
    assert pubres  # jobs were claimed
    assert all(r.rejected and r.final_state == "dead" for r in pubres)
    # NOTHING was broadcast.
    assert sender.sent == []
    # Job(s) dead, submission rejected, source chat notified.
    assert set(_job_states(sa_conn, "orgA")) == {"dead"}
    assert _submission_status(sa_conn, res.submission_id) == "rejected"
    assert notified and notified[0][0] == "orgA"
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_publications WHERE org_id='orgA'")
    ).scalar() == 0


# ==========================================================================
# §15.2 — OUTPUT ESCAPING / ACCIDENTAL-PING PREVENTION
# ==========================================================================
def test_s15_2_discord_mirror_no_everyone_ping(sa_conn):
    """§15.2 (Discord): a mirrored tweet containing @everyone/@here renders as
    PLAIN TEXT — the escaped body breaks the ping token AND every send carries
    AllowedMentions.none()."""
    raw = "@everyone @here gm — alpha drop <@&12345>"
    escaped = escaping.escape_discord(raw)
    # The literal @everyone / @here tokens cannot resolve to a ping (a zero-width
    # space is inserted between @ and everyone/here).
    assert "@everyone" not in escaped
    assert "@here" not in escaped
    # The visible words survive (it renders as readable text, just inert).
    assert "everyone" in escaped and "here" in escaped
    # The transport-layer guarantee: a fresh AllowedMentions.none() each call.
    am = escaping.discord_allowed_mentions()
    assert am.everyone is False and am.users is False and am.roles is False
    # Independent instances (a caller cannot mutate a shared singleton).
    assert escaping.discord_allowed_mentions() is not am


def test_s15_2_telegram_mirror_no_all_ping_and_no_tag_injection(sa_conn):
    """§15.2 (Telegram): an @all in user text is inert plain text, and any
    HTML/tag injection in the mirrored body is neutralized by html.escape."""
    raw = '@all <b>fake bold</b> <a href="evil">x</a> & co'
    escaped = escaping.escape_telegram_text(raw)
    # No raw tag survives — they are HTML-escaped to entities.
    assert "<b>" not in escaped and "<a href=" not in escaped
    assert "&lt;b&gt;" in escaped and "&amp;" in escaped
    # @all is just text (Telegram has no @all mass-ping primitive).
    assert "@all" in escaped
    # A whitelisted bot link escapes a hostile href + text (no breakout).
    link = escaping.tg_link('https://x.com/a"onmouseover="x', "click <b>me</b>")
    assert 'onmouseover="x"' not in link  # the attribute cannot break out
    assert "<b>me</b>" not in link  # the link text is escaped too
    # The §15.2 tag whitelist is exactly b/i/a (no widening to <script> etc.).
    assert escaping.TELEGRAM_TAG_WHITELIST == ("b", "i", "a")


def test_s15_2_processed_dedupe_is_restart_safe(sa_conn):
    """A persisted processed-update row makes the §15.2/§3 dedupe restart-safe:
    a second ``mark_processed`` of the same update_id returns False even across
    a notional restart (the row is in ``relay_processed_updates``)."""
    assert mark_processed(sa_conn, "telegram", "evt-1") is True
    sa_conn.commit()
    # Same id again → already processed.
    assert mark_processed(sa_conn, "telegram", "evt-1") is False
    persisted = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_processed_updates WHERE update_id = 'evt-1'")
    ).scalar()
    assert persisted == 1
