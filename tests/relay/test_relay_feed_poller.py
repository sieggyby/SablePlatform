"""C2.4 tests — Flow A poller + proactive per-org cap + Flow D 4.6 reply tracking.

No real network / SocialData: the C1.2 client is driven by a deterministic fake.

Coverage (per MEGAPLAN C2.4 tests line):
  * per-org daily cost cap: an over-cap org's tick makes ZERO SocialData HTTP
    calls (NO fetch) while a second under-cap org in the same loop pass polls
    normally — asserted via check_daily_socialdata_budget (the proactive gate)
  * Flow A poll hydrates new tweets and enqueues a publication job per active
    broadcast/community binding, advancing the since_id cursor
  * Flow D 4.6 reply-tracking populates replied_at/replied_tweet_id and honors
    the per-opportunity budget cap (matches on X user id, not handle)
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay import socialdata as sd
from sable_platform.relay.feed import poller


# --------------------------------------------------------------------------
# Fakes / seeding
# --------------------------------------------------------------------------
class FakeHttp:
    def __init__(self, handler):
        # handler: (path, params) -> HttpResponse
        self._handler = handler
        self.calls = []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        return self._handler(path, dict(params))


def _client(conn, http):
    return sd.SocialDataClient(http_get=http, conn=conn, sleep=lambda *_: None, jitter=lambda: 1.0)


def _seed_org(conn, org_id, *, config=None, source_x_id="100"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    if config is None:
        import json
        config = json.dumps({"polling": {"source_x_user_id": source_x_id}})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, :c)"),
        {"o": org_id, "c": config},
    )


def _seed_socialdata_cost(conn, org_id, cost):
    conn.execute(
        text(
            "INSERT INTO cost_events (org_id, call_type, cost_usd, call_status) "
            "VALUES (:o, :ct, :c, 'success')"
        ),
        {"o": org_id, "ct": sd.CALL_TYPE_TIMELINE, "c": cost},
    )


def _bind_broadcast(conn, org_id, *, platform="discord", chat="posts"):
    conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:o, :p, :c, 'broadcast', 'active')"
        ),
        {"o": org_id, "p": platform, "c": chat},
    )


def _tweet(x_id, *, handle="solstitch", conv=None, author_id="100"):
    return {
        "id_str": x_id,
        "id": int(x_id),
        "full_text": f"tweet {x_id}",
        "user": {"id_str": author_id, "screen_name": handle},
        "conversation_id_str": conv or x_id,
    }


def _timeline_resp(tweet_ids):
    return sd.HttpResponse(status_code=200, json_body={"tweets": [_tweet(t) for t in tweet_ids]})


# ==========================================================================
# PROACTIVE per-org daily cap — over-cap org skipped, under-cap org polls
# ==========================================================================
def test_over_cap_org_makes_zero_calls_under_cap_org_polls(sa_conn):
    _seed_org(sa_conn, "orgover", source_x_id="11")
    _seed_org(sa_conn, "orgunder", source_x_id="22")
    _bind_broadcast(sa_conn, "orgunder")
    # Seed orgover AT the cap (default $1.00) → over_cap; orgunder well under.
    for _ in range(500):
        _seed_socialdata_cost(sa_conn, "orgover", sd.COST_PER_CALL_USD)  # 500 * 0.002 = 1.00
    _seed_socialdata_cost(sa_conn, "orgunder", 0.02)
    sa_conn.commit()

    # Both orgs share the timeline endpoint; the fake serves tweets for either.
    http = FakeHttp(lambda path, params: _timeline_resp(["300"]))
    client = _client(sa_conn, http)

    results = poller.poll_all_enabled(sa_conn, client)
    by_org = {r.org_id: r for r in results}

    # Over-cap org: skipped, ZERO SocialData HTTP calls.
    assert by_org["orgover"].skipped_over_cap is True
    assert by_org["orgover"].polled is False
    # Under-cap org: polled normally in the SAME pass.
    assert by_org["orgunder"].skipped_over_cap is False
    assert by_org["orgunder"].polled is True
    assert by_org["orgunder"].new_tweets == 1

    # Exactly ONE timeline call hit the wire — only the under-cap org's.
    assert all("/twitter/user/22/" in c[0] for c in http.calls)
    assert len(http.calls) == 1
    assert client.http_call_count == 1


def test_over_cap_threshold_is_inclusive(sa_conn):
    """spend == cap is over_cap (>=) — the org is skipped at exactly the cap."""
    _seed_org(sa_conn, "orgexact", config='{"polling": {"daily_cost_cap_usd": 0.10, "source_x_user_id": "9"}}')
    _seed_socialdata_cost(sa_conn, "orgexact", 0.10)
    sa_conn.commit()
    http = FakeHttp(lambda *_: _timeline_resp(["1"]))
    client = _client(sa_conn, http)
    [result] = poller.poll_all_enabled(sa_conn, client)
    assert result.skipped_over_cap is True
    assert len(http.calls) == 0


# ==========================================================================
# Flow A — hydrate + enqueue per active broadcast/community binding
# ==========================================================================
def test_flow_a_enqueues_one_job_per_destination_and_advances_cursor(sa_conn):
    _seed_org(sa_conn, "orgflow", source_x_id="55")
    _bind_broadcast(sa_conn, "orgflow", platform="discord", chat="posts")
    _bind_broadcast(sa_conn, "orgflow", platform="telegram", chat="-100123")
    sa_conn.commit()

    http = FakeHttp(lambda *_: _timeline_resp(["701", "702"]))
    client = _client(sa_conn, http)

    [result] = poller.poll_all_enabled(sa_conn, client)
    assert result.new_tweets == 2
    # 2 tweets x 2 destinations = 4 jobs.
    assert result.jobs_enqueued == 4

    jobs = sa_conn.execute(
        text("SELECT destination_platform, destination_chat_id, state FROM relay_publication_jobs ORDER BY id")
    ).fetchall()
    assert len(jobs) == 4
    assert all(j[2] == "pending" for j in jobs)

    # Cursor advanced to the max tweet id seen (702).
    cur = sa_conn.execute(
        text("SELECT last_seen_x_id, last_polled_at, last_error FROM relay_clients WHERE org_id = 'orgflow'")
    ).fetchone()
    assert cur[0] == "702"
    assert cur[1] is not None  # last_polled_at stamped
    assert cur[2] is None  # last_error cleared on a clean poll


def test_flow_a_no_destinations_still_hydrates_tweets(sa_conn):
    _seed_org(sa_conn, "orgnodest", source_x_id="55")
    sa_conn.commit()
    http = FakeHttp(lambda *_: _timeline_resp(["801"]))
    client = _client(sa_conn, http)
    [result] = poller.poll_all_enabled(sa_conn, client)
    assert result.new_tweets == 1
    assert result.jobs_enqueued == 0
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_tweets")).scalar() == 1


def test_flow_a_no_source_id_skips_poll(sa_conn):
    _seed_org(sa_conn, "orgnoid", config='{"polling": {}}')  # no source_x_user_id
    sa_conn.commit()
    http = FakeHttp(lambda *_: _timeline_resp(["1"]))
    client = _client(sa_conn, http)
    [result] = poller.poll_all_enabled(sa_conn, client)
    assert result.polled is False
    assert len(http.calls) == 0


# ==========================================================================
# Flow D 4.6 — reply follow-through tracking
# ==========================================================================
def _seed_reply_opportunity(conn, org_id, *, source_x_id, member_x_user_id):
    """Build a tweet → opportunity → member → notification chain.

    Returns (notification_id, member_id).
    """
    tweet_row = relay_db.upsert_tweet(conn, x_id=source_x_id, x_author_handle="archerfit")
    member_row = conn.execute(
        text("INSERT INTO relay_members (display_name) VALUES ('brian') RETURNING id")
    ).fetchone()
    member_id = int(member_row[0])
    # Link the member's X identity (matching is by X user id, not handle).
    conn.execute(
        text(
            "INSERT INTO relay_member_identities (member_id, platform, external_user_id, handle) "
            "VALUES (:m, 'x', :uid, 'influenza')"
        ),
        {"m": member_id, "uid": member_x_user_id},
    )
    opp_row = conn.execute(
        text(
            "INSERT INTO relay_reply_opportunities (org_id, tweet_id, flagger_id, origin) "
            "VALUES (:o, :t, :m, 'explicit_command') RETURNING id"
        ),
        {"o": org_id, "t": tweet_row, "m": member_id},
    ).fetchone()
    opp_id = int(opp_row[0])
    notif_row = conn.execute(
        text(
            "INSERT INTO relay_reply_notifications (opportunity_id, member_id) "
            "VALUES (:opp, :m) RETURNING id"
        ),
        {"opp": opp_id, "m": member_id},
    ).fetchone()
    return int(notif_row[0]), member_id


def test_reply_tracking_records_followthrough_on_x_user_id_match(sa_conn):
    _seed_org(sa_conn, "orgrep", source_x_id="55")
    notif_id, member_id = _seed_reply_opportunity(
        sa_conn, "orgrep", source_x_id="2001", member_x_user_id="987"
    )
    sa_conn.commit()

    # The conversation poll returns a reply authored by the member's X user id.
    def handler(path, params):
        return sd.HttpResponse(
            status_code=200,
            json_body={
                "tweets": [
                    {"id_str": "5000", "user": {"id_str": "111", "screen_name": "rando"}},
                    {"id_str": "5001", "user": {"id_str": "987", "screen_name": "influenza"}},
                ]
            },
        )

    client = _client(sa_conn, FakeHttp(handler))
    result = poller.track_reply_followups(sa_conn, client, "orgrep")

    assert result.followthroughs_recorded == 1
    assert notif_id in result.matched_notification_ids

    row = sa_conn.execute(
        text("SELECT replied_at, replied_tweet_id FROM relay_reply_notifications WHERE id = :id"),
        {"id": notif_id},
    ).fetchone()
    assert row[0] is not None  # replied_at written
    assert row[1] == "5001"  # the matched reply's x_id (not the rando's)


def test_reply_tracking_no_match_leaves_notification_open(sa_conn):
    _seed_org(sa_conn, "orgrep2", source_x_id="55")
    notif_id, _ = _seed_reply_opportunity(
        sa_conn, "orgrep2", source_x_id="2002", member_x_user_id="987"
    )
    sa_conn.commit()

    def handler(path, params):
        # No reply from the member's X id (987).
        return sd.HttpResponse(
            status_code=200,
            json_body={"tweets": [{"id_str": "6000", "user": {"id_str": "222"}}]},
        )

    client = _client(sa_conn, FakeHttp(handler))
    result = poller.track_reply_followups(sa_conn, client, "orgrep2")
    assert result.followthroughs_recorded == 0
    row = sa_conn.execute(
        text("SELECT replied_at FROM relay_reply_notifications WHERE id = :id"),
        {"id": notif_id},
    ).fetchone()
    assert row[0] is None  # still open


def test_reply_tracking_honors_per_opportunity_call_cap(sa_conn):
    """With more open opportunities than the cap, only `cap` polls run."""
    _seed_org(
        sa_conn,
        "orgcap",
        config='{"polling": {"source_x_user_id": "55"}, "reply": {"replies_poll_total_calls": 2}}',
    )
    # Three open opportunities, each with a distinct conversation.
    for i in range(3):
        _seed_reply_opportunity(
            sa_conn, "orgcap", source_x_id=f"300{i}", member_x_user_id=f"90{i}"
        )
    sa_conn.commit()

    def handler(path, params):
        # Never matches (so no notification closes; we only count calls).
        return sd.HttpResponse(status_code=200, json_body={"tweets": []})

    http = FakeHttp(handler)
    client = _client(sa_conn, http)
    result = poller.track_reply_followups(sa_conn, client, "orgcap")

    # Cap is 2 → at most 2 conversation polls hit the wire.
    assert result.calls_made == 2
    assert len([c for c in http.calls if c[0] == "/twitter/search"]) == 2


def test_reply_tracking_skipped_when_over_daily_cap(sa_conn):
    _seed_org(
        sa_conn,
        "orgrepcap",
        config='{"polling": {"daily_cost_cap_usd": 0.05, "source_x_user_id": "55"}}',
    )
    _seed_reply_opportunity(sa_conn, "orgrepcap", source_x_id="2003", member_x_user_id="987")
    _seed_socialdata_cost(sa_conn, "orgrepcap", 0.10)  # over the 0.05 cap
    sa_conn.commit()

    http = FakeHttp(lambda *_: sd.HttpResponse(status_code=200, json_body={"tweets": []}))
    client = _client(sa_conn, http)
    result = poller.track_reply_followups(sa_conn, client, "orgrepcap")
    assert result.skipped_over_cap is True
    assert result.calls_made == 0
    assert len(http.calls) == 0


def test_reply_tracking_skips_member_without_x_identity(sa_conn):
    """A notified member with no linked X identity costs ZERO calls (can't match)."""
    _seed_org(sa_conn, "orgnox", source_x_id="55")
    tweet_row = relay_db.upsert_tweet(sa_conn, x_id="2099", x_author_handle="a")
    member_row = sa_conn.execute(
        text("INSERT INTO relay_members (display_name) VALUES ('noX') RETURNING id")
    ).fetchone()
    member_id = int(member_row[0])  # NO relay_member_identities row for platform='x'
    opp_row = sa_conn.execute(
        text(
            "INSERT INTO relay_reply_opportunities (org_id, tweet_id, flagger_id, origin) "
            "VALUES ('orgnox', :t, :m, 'explicit_command') RETURNING id"
        ),
        {"t": tweet_row, "m": member_id},
    ).fetchone()
    sa_conn.execute(
        text(
            "INSERT INTO relay_reply_notifications (opportunity_id, member_id) VALUES (:opp, :m)"
        ),
        {"opp": int(opp_row[0]), "m": member_id},
    )
    sa_conn.commit()

    http = FakeHttp(lambda *_: sd.HttpResponse(status_code=200, json_body={"tweets": []}))
    client = _client(sa_conn, http)
    result = poller.track_reply_followups(sa_conn, client, "orgnox")
    assert result.calls_made == 0
    assert len(http.calls) == 0
