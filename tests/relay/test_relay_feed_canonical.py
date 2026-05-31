"""C2.4 tests — URL canonicalization + tweet-hydration-rejection (PLAN §15.1).

No real network / SocialData: the C1.2 client is driven by a deterministic fake
``http_get`` exactly as ``test_relay_socialdata.py`` does.

Coverage (per MEGAPLAN C2.4 tests line):
  * a valid x.com|twitter.com/<user>/status/<id> (incl. mobile./i/web/query) →
    canonicalizes to the tweet id; the hydrated x_id (NOT the URL) is canonical
  * a disallowed / non-tweet URL (and a t.co shortener) is REJECTED with no
    submission/opportunity/publication
  * a deleted / not-found / private / suspended tweet on hydration is rejected
    with the precise reason and creates no relay_tweets row
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.relay import socialdata as sd
from sable_platform.relay.feed import canonical


# --------------------------------------------------------------------------
# Fakes / seeding (mirror test_relay_socialdata.py)
# --------------------------------------------------------------------------
class FakeHttp:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        resp = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        return resp() if callable(resp) else resp


def _seed_org(conn, org_id):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id})


def _client(conn, http):
    return sd.SocialDataClient(http_get=http, conn=conn, sleep=lambda *_: None, jitter=lambda: 1.0)


def _hydrated_body(x_id, *, handle="archerfit"):
    return sd.HttpResponse(
        status_code=200,
        json_body={
            "id_str": x_id,
            "id": int(x_id),
            "full_text": "great voice",
            "user": {"id_str": "555", "screen_name": handle},
            "conversation_id_str": x_id,
        },
    )


# ==========================================================================
# URL canonicalization — accept the valid shapes
# ==========================================================================
def test_accepts_plain_x_status_url():
    r = canonical.canonicalize_tweet_url("https://x.com/archerfit/status/1812345")
    assert isinstance(r, canonical.CanonicalUrl)
    assert r.tweet_id == "1812345"
    assert r.handle == "archerfit"


def test_accepts_twitter_dot_com():
    r = canonical.canonicalize_tweet_url("https://twitter.com/foo/status/999")
    assert isinstance(r, canonical.CanonicalUrl)
    assert r.tweet_id == "999"


def test_strips_query_string():
    r = canonical.canonicalize_tweet_url("https://x.com/foo/status/42?s=20&t=abc")
    assert isinstance(r, canonical.CanonicalUrl)
    assert r.tweet_id == "42"


def test_normalizes_mobile_subdomain():
    r = canonical.canonicalize_tweet_url("https://mobile.x.com/foo/status/77")
    assert isinstance(r, canonical.CanonicalUrl)
    assert r.tweet_id == "77"


def test_normalizes_i_web_status_shape():
    r = canonical.canonicalize_tweet_url("https://x.com/i/web/status/314")
    assert isinstance(r, canonical.CanonicalUrl)
    assert r.tweet_id == "314"
    assert r.handle is None


def test_accepts_schemeless_paste():
    r = canonical.canonicalize_tweet_url("x.com/foo/status/5")
    assert isinstance(r, canonical.CanonicalUrl)
    assert r.tweet_id == "5"


# ==========================================================================
# URL canonicalization — REJECT everything else (no submission created)
# ==========================================================================
def test_rejects_non_tweet_url():
    r = canonical.canonicalize_tweet_url("https://x.com/archerfit")  # profile, not a status
    assert isinstance(r, canonical.Rejection)
    assert r.code == canonical.REJECT_NOT_A_TWEET_URL


def test_rejects_foreign_host():
    r = canonical.canonicalize_tweet_url("https://evil.example.com/x.com/foo/status/1")
    assert isinstance(r, canonical.Rejection)
    assert r.code == canonical.REJECT_NOT_A_TWEET_URL


def test_rejects_tco_shortener():
    r = canonical.canonicalize_tweet_url("https://t.co/abc123")
    assert isinstance(r, canonical.Rejection)
    assert r.code == canonical.REJECT_UNRESOLVABLE_SHORTENER


def test_rejects_non_numeric_status_id():
    r = canonical.canonicalize_tweet_url("https://x.com/foo/status/notanid")
    assert isinstance(r, canonical.Rejection)
    assert r.code == canonical.REJECT_NOT_A_TWEET_URL


def test_rejects_empty():
    assert isinstance(canonical.canonicalize_tweet_url(""), canonical.Rejection)
    assert isinstance(canonical.canonicalize_tweet_url("   "), canonical.Rejection)


# ==========================================================================
# Hydration — success uses the hydrated x_id (NOT the URL) as canonical
# ==========================================================================
def test_hydrate_success_upserts_tweet_with_canonical_x_id(sa_conn):
    _seed_org(sa_conn, "orgh")
    sa_conn.commit()
    # The URL carries "1812345" but the hydrated body's id_str is "999999" — the
    # canonical id MUST be the hydrated x_id, never the URL's digits (§15.1).
    http = FakeHttp([_hydrated_body("999999")])
    client = _client(sa_conn, http)

    result = canonical.hydrate_or_reject(sa_conn, client, "orgh", "1812345")
    assert isinstance(result, canonical.Hydrated)
    assert result.x_id == "999999"

    row = sa_conn.execute(
        text("SELECT x_id, x_author_handle, conversation_x_id FROM relay_tweets WHERE id = :id"),
        {"id": result.tweet_row_id},
    ).fetchone()
    assert row[0] == "999999"
    assert row[1] == "archerfit"


def test_hydrate_404_rejects_and_writes_no_tweet(sa_conn):
    _seed_org(sa_conn, "orgnf")
    sa_conn.commit()
    http = FakeHttp([sd.HttpResponse(status_code=404)])
    client = _client(sa_conn, http)

    result = canonical.hydrate_or_reject(sa_conn, client, "orgnf", "1")
    assert isinstance(result, canonical.Rejection)
    assert result.code == canonical.REJECT_NOT_FOUND
    count = sa_conn.execute(text("SELECT COUNT(*) FROM relay_tweets")).scalar()
    assert count == 0


def test_hydrate_suspended_rejects(sa_conn):
    _seed_org(sa_conn, "orgsus")
    sa_conn.commit()
    body = sd.HttpResponse(
        status_code=200,
        json_body={"id_str": "5", "user": {"suspended": True, "screen_name": "x"}},
    )
    http = FakeHttp([body])
    client = _client(sa_conn, http)
    result = canonical.hydrate_or_reject(sa_conn, client, "orgsus", "5")
    assert isinstance(result, canonical.Rejection)
    assert result.code == canonical.REJECT_SUSPENDED
    assert sa_conn.execute(text("SELECT COUNT(*) FROM relay_tweets")).scalar() == 0


def test_hydrate_private_rejects(sa_conn):
    _seed_org(sa_conn, "orgpriv")
    sa_conn.commit()
    body = sd.HttpResponse(
        status_code=200,
        json_body={"id_str": "6", "user": {"protected": True, "screen_name": "x"}},
    )
    client = _client(sa_conn, http=FakeHttp([body]))
    result = canonical.hydrate_or_reject(sa_conn, client, "orgpriv", "6")
    assert isinstance(result, canonical.Rejection)
    assert result.code == canonical.REJECT_PRIVATE


def test_hydrate_deleted_marker_rejects(sa_conn):
    _seed_org(sa_conn, "orgdel")
    sa_conn.commit()
    body = sd.HttpResponse(status_code=200, json_body={"status": "deleted"})
    client = _client(sa_conn, http=FakeHttp([body]))
    result = canonical.hydrate_or_reject(sa_conn, client, "orgdel", "7")
    assert isinstance(result, canonical.Rejection)
    assert result.code == canonical.REJECT_DELETED


def test_hydrate_negative_cache_then_reuse(sa_conn):
    """A re-hydrate of a 404 id is served from the wrapper's negative cache."""
    _seed_org(sa_conn, "orgneg")
    sa_conn.commit()
    http = FakeHttp([sd.HttpResponse(status_code=404)])
    client = _client(sa_conn, http)
    canonical.hydrate_or_reject(sa_conn, client, "orgneg", "1")
    canonical.hydrate_or_reject(sa_conn, client, "orgneg", "1")
    assert len(http.calls) == 1  # second hydrate served from negative cache
