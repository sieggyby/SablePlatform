"""C2.3b tests — ``/flag-reply`` Flow D v1 (reply opportunity → DM opted-in members).

No real Telegram/Discord/SocialData: hydration goes through a duck-typed
``FakeClient`` exposing ``hydrate_tweet`` (the only method
``canonical.hydrate_or_reject`` calls). DB work runs against the in-memory
``sa_conn`` schema; no external send happens inside any txn (the handler returns
a result the listener uses to DM each target the compose deeplink).

Coverage (per MEGAPLAN C2.3b exit):
  * ``/flag-reply`` DMs ONLY opted-in (and not-muted) members, with the compose
    deeplink (``intent/tweet?in_reply_to=…``) + the media caveat when the tweet has
    media.
  * authorization is role-gated: a non-operator ``/flag-reply`` is rejected, nothing
    created.
  * a disallowed/non-tweet URL and a deleted/not-found tweet are REJECTED with no
    opportunity created (§15.1).
  * explicit ``target=@handle`` resolution overrides the opted-in default; an
    unresolved handle is reported (not silently granted).
  * notifications are idempotent (a member targeted twice is DMed once).
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.handlers import flag_reply
from sable_platform.relay.socialdata import SocialDataNotFound


URL = "https://x.com/archerfit/status/1812345"


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


def _ok_body(x_id="1812345", handle="archerfit", media=None):
    body = {
        "id_str": x_id,
        "id": int(x_id),
        "full_text": "great voice",
        "user": {"id_str": "555", "screen_name": handle},
        "conversation_id_str": x_id,
    }
    if media is not None:
        body["media_urls"] = media
    return body


def _seed(conn, *, org_id="orgA", config="{}"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, :c)"),
        {"o": org_id, "c": config},
    )


def _grant(conn, org_id, tg_user_id, role):
    mid = relay_db.auto_create_member_identity(
        conn, "telegram", str(tg_user_id), handle=f"u{tg_user_id}"
    )
    conn.execute(
        text("INSERT INTO relay_member_roles (member_id, org_id, role) VALUES (:m, :o, :r)"),
        {"m": mid, "o": org_id, "r": role},
    )
    return mid


def _member(conn, tg_user_id, handle):
    return relay_db.auto_create_member_identity(
        conn, "telegram", str(tg_user_id), handle=handle
    )


def _optin(conn, member_id, org_id, *, mute_until=None):
    relay_db.upsert_member_preference(
        conn, member_id, org_id, replies_optin=True, mute_until=mute_until
    )


# ==========================================================================
def test_flag_reply_dms_only_optedin_members_with_compose_deeplink(sa_conn):
    _seed(sa_conn)
    _grant(sa_conn, "orgA", 10, "sable_operator")
    m_in = _member(sa_conn, 20, "alice")
    m_out = _member(sa_conn, 30, "bob")
    _optin(sa_conn, m_in, "orgA")  # opted in
    # m_out has no preference row → not in the fan-out
    sa_conn.commit()

    client = FakeClient(body=_ok_body())
    res = flag_reply.flag_reply(
        sa_conn, client,
        org_id="orgA", platform="telegram", flagger_external_user_id="10",
        raw_url=URL, note="please reply", flagger_handle="op1",
    )
    assert res.code == flag_reply.FLAG_REPLY_CREATED
    assert res.opportunity_id is not None
    # Only the opted-in member is targeted.
    member_ids = {t.member_id for t in res.targets}
    assert member_ids == {m_in}
    assert m_out not in member_ids
    # The DM carries the compose deeplink with in_reply_to + prefill.
    assert "intent/tweet" in res.compose_url
    assert "in_reply_to=1812345" in res.compose_url
    assert "please+reply" in res.compose_url or "please%20reply" in res.compose_url
    # No media → no caveat.
    assert res.media_caveat is False
    # The opted-in member has a TG DM target.
    t = next(t for t in res.targets if t.member_id == m_in)
    assert t.tg_user_id == "20"
    assert t.notification_id is not None
    # A notification row + opportunity-target junction were written.
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_reply_notifications WHERE opportunity_id = :o"),
        {"o": res.opportunity_id},
    ).fetchone()[0]
    assert n == 1


def test_flag_reply_media_sets_caveat(sa_conn):
    _seed(sa_conn)
    _grant(sa_conn, "orgA", 10, "sable_operator")
    m_in = _member(sa_conn, 20, "alice")
    _optin(sa_conn, m_in, "orgA")
    sa_conn.commit()

    client = FakeClient(body=_ok_body(media=["https://pbs.twimg.com/x.jpg"]))
    res = flag_reply.flag_reply(
        sa_conn, client,
        org_id="orgA", platform="telegram", flagger_external_user_id="10",
        raw_url=URL, flagger_handle="op1",
    )
    assert res.code == flag_reply.FLAG_REPLY_CREATED
    # The compose deeplink cannot pre-attach media → the listener must DM the file.
    assert res.media_caveat is True


def test_flag_reply_excludes_muted_member(sa_conn):
    _seed(sa_conn)
    _grant(sa_conn, "orgA", 10, "sable_operator")
    m_muted = _member(sa_conn, 20, "alice")
    m_active = _member(sa_conn, 30, "carol")
    _optin(sa_conn, m_muted, "orgA", mute_until="2999-01-01T00:00:00Z")  # muted far future
    _optin(sa_conn, m_active, "orgA")
    sa_conn.commit()

    client = FakeClient(body=_ok_body())
    res = flag_reply.flag_reply(
        sa_conn, client,
        org_id="orgA", platform="telegram", flagger_external_user_id="10",
        raw_url=URL, flagger_handle="op1",
    )
    member_ids = {t.member_id for t in res.targets}
    assert member_ids == {m_active}


def test_flag_reply_non_operator_rejected(sa_conn):
    _seed(sa_conn)
    _member(sa_conn, 99, "rando")  # not an operator
    m_in = _member(sa_conn, 20, "alice")
    _optin(sa_conn, m_in, "orgA")
    sa_conn.commit()

    client = FakeClient(body=_ok_body())
    res = flag_reply.flag_reply(
        sa_conn, client,
        org_id="orgA", platform="telegram", flagger_external_user_id="99",
        raw_url=URL, flagger_handle="rando",
    )
    assert res.code == flag_reply.FLAG_REPLY_NOT_AUTHORIZED
    # Nothing created.
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_reply_opportunities")
    ).fetchone()[0] == 0
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_reply_notifications")
    ).fetchone()[0] == 0


def test_flag_reply_disallowed_url_rejected(sa_conn):
    _seed(sa_conn)
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()

    client = FakeClient(body=_ok_body())
    res = flag_reply.flag_reply(
        sa_conn, client,
        org_id="orgA", platform="telegram", flagger_external_user_id="10",
        raw_url="https://evil.example.com/not-a-tweet", flagger_handle="op1",
    )
    assert res.code == flag_reply.FLAG_REPLY_REJECTED
    assert res.rejection is not None
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_reply_opportunities")
    ).fetchone()[0] == 0
    # No SocialData call for a URL that never canonicalized.
    assert client.calls == []


def test_flag_reply_deleted_tweet_rejected(sa_conn):
    _seed(sa_conn)
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()

    client = FakeClient(not_found=True)
    res = flag_reply.flag_reply(
        sa_conn, client,
        org_id="orgA", platform="telegram", flagger_external_user_id="10",
        raw_url=URL, flagger_handle="op1",
    )
    assert res.code == flag_reply.FLAG_REPLY_REJECTED
    assert res.rejection is not None
    assert sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_reply_opportunities")
    ).fetchone()[0] == 0


def test_flag_reply_explicit_targets_override_optedin(sa_conn):
    _seed(sa_conn)
    _grant(sa_conn, "orgA", 10, "sable_operator")
    m_opt = _member(sa_conn, 20, "alice")  # opted in but NOT explicitly targeted
    m_tgt = _member(sa_conn, 30, "carol")  # explicitly targeted, NOT opted in
    _optin(sa_conn, m_opt, "orgA")
    sa_conn.commit()

    client = FakeClient(body=_ok_body())
    res = flag_reply.flag_reply(
        sa_conn, client,
        org_id="orgA", platform="telegram", flagger_external_user_id="10",
        raw_url=URL, flagger_handle="op1",
        arg_tokens=["please", "reply", "target=@carol"],
    )
    assert res.code == flag_reply.FLAG_REPLY_CREATED
    member_ids = {t.member_id for t in res.targets}
    assert member_ids == {m_tgt}  # explicit target only, opted-in default ignored
    assert res.note == "please reply"
    assert res.unresolved_targets == ()


def test_flag_reply_unresolved_handle_reported(sa_conn):
    _seed(sa_conn)
    _grant(sa_conn, "orgA", 10, "sable_operator")
    sa_conn.commit()

    client = FakeClient(body=_ok_body())
    res = flag_reply.flag_reply(
        sa_conn, client,
        org_id="orgA", platform="telegram", flagger_external_user_id="10",
        raw_url=URL, flagger_handle="op1",
        arg_tokens=["target=@ghost"],
    )
    assert res.code == flag_reply.FLAG_REPLY_CREATED
    assert res.targets == ()  # ghost resolves to nobody
    assert "@ghost" in res.unresolved_targets


def test_flag_reply_notification_idempotent(sa_conn):
    """A member targeted explicitly AND opted-in is notified exactly once."""
    _seed(sa_conn)
    _grant(sa_conn, "orgA", 10, "sable_operator")
    m = _member(sa_conn, 20, "alice")
    _optin(sa_conn, m, "orgA")
    sa_conn.commit()

    client = FakeClient(body=_ok_body())
    # Target the same member explicitly by handle (also opted-in).
    res = flag_reply.flag_reply(
        sa_conn, client,
        org_id="orgA", platform="telegram", flagger_external_user_id="10",
        raw_url=URL, flagger_handle="op1",
        arg_tokens=["target=@alice", "target=@alice"],
    )
    assert res.code == flag_reply.FLAG_REPLY_CREATED
    # de-duped to one target.
    assert len([t for t in res.targets if t.member_id == m]) == 1
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_reply_notifications WHERE opportunity_id = :o"),
        {"o": res.opportunity_id},
    ).fetchone()[0]
    assert n == 1
