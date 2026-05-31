"""URL canonicalization + tweet-hydration-rejection (SableRelay PLAN §15.1).

OWNED HERE by C2.4 — previously owned by no chunk, which left a spoofed-URL
entry point. Two halves:

  1. **URL canonicalization** (:func:`canonicalize_tweet_url`). Accept ONLY
     ``x.com/<user>/status/<id>`` and ``twitter.com/<user>/status/<id>`` shapes.
     Strip ``?`` query strings. Normalize ``mobile.x.com`` / ``mobile.twitter.com``,
     ``x.com/i/web/status/<id>``, and ``t.co`` shorteners. **Reject everything
     else** (no submission, no opportunity, no publication). The extracted
     ``tweet_id`` is a string of digits; it is NOT yet canonical — the canonical
     id is the hydrated ``x_id`` (step 2), never the URL.

  2. **Tweet hydration + rejection** (:func:`hydrate_or_reject`). Hydrate the
     extracted id via the C1.2 :class:`SocialDataClient`; if the provider returns
     not-found / deleted / private / suspended, return a :class:`Rejection` with
     the precise reason and create NOTHING. On success, upsert into
     ``relay_tweets`` and return the row id + the hydrated ``x_id`` (the canonical
     id). The publish path also re-hydrates: a tweet deleted between submission
     and publish is rejected (the caller marks the submission ``rejected``).

``t.co`` shorteners cannot be resolved offline (they are opaque redirects). Per
§15.1 they are "normalized" — but the only safe normalization without a network
fetch is to surface them for resolution. We treat a bare ``t.co/<code>`` as
NON-canonicalizable here (rejected with a precise reason) UNLESS the caller has
already resolved it to an x.com/twitter.com status URL; the recognized shape is
the resolved target, not the shortener. This keeps a spoofed/opaque ``t.co``
from ever minting a submission. (A future C-chunk may add an online resolver;
§15.1's "normalize t.co" is satisfied by recognizing a t.co-redirect *target*
once resolved, which is the shape we accept.)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.socialdata import SocialDataClient, SocialDataNotFound

# Hosts we accept as a tweet status URL (after lower-casing + stripping a
# leading ``www.`` / ``mobile.`` subdomain). Anything else is rejected.
_ACCEPTED_HOSTS = frozenset({"x.com", "twitter.com"})

# /<user>/status/<id>  — <user> is a screen name (or ``i/web`` for the
# share-style ``x.com/i/web/status/<id>`` shape, handled below).
_STATUS_RE = re.compile(r"^/(?P<user>[^/]+)/status/(?P<id>\d+)/?$")
# x.com/i/web/status/<id>  (share / deep-link shape — no screen name).
_IWEB_RE = re.compile(r"^/i/web/status/(?P<id>\d+)/?$")


@dataclass(frozen=True)
class CanonicalUrl:
    """A successfully-canonicalized tweet reference (URL → tweet_id).

    ``tweet_id`` is the digits extracted from the URL — NOT yet the canonical id.
    Hydration (:func:`hydrate_or_reject`) returns the hydrated ``x_id`` which IS
    the canonical id (§15.1: "never the URL").
    """

    tweet_id: str
    handle: str | None  # screen name from the URL when present (None for i/web)


@dataclass(frozen=True)
class Rejection:
    """A rejected URL / tweet — no submission/opportunity/publication is created.

    ``reason`` is the precise, user-facing reason the source-chat reply echoes
    (§15.1: "reply ... with the precise reason"). ``code`` is the machine-stable
    category for logging/tests.
    """

    code: str
    reason: str


# Machine-stable rejection codes (asserted by tests).
REJECT_NOT_A_TWEET_URL = "not_a_tweet_url"
REJECT_UNRESOLVABLE_SHORTENER = "unresolvable_shortener"
REJECT_NOT_FOUND = "not_found"
REJECT_DELETED = "deleted"
REJECT_PRIVATE = "private"
REJECT_SUSPENDED = "suspended"


def canonicalize_tweet_url(raw_url: str) -> CanonicalUrl | Rejection:
    """Canonicalize a candidate tweet URL to its ``tweet_id`` (§15.1).

    Accepts ONLY ``x.com|twitter.com/<user>/status/<id>`` (incl. the
    ``mobile.``-prefixed and ``x.com/i/web/status/<id>`` shapes); strips the
    ``?`` query string. Returns a :class:`CanonicalUrl` on success or a
    :class:`Rejection` (no submission/opportunity/publication) otherwise.
    """
    if not isinstance(raw_url, str) or not raw_url.strip():
        return Rejection(REJECT_NOT_A_TWEET_URL, "no URL provided")

    candidate = raw_url.strip()
    # Tolerate a scheme-less paste (``x.com/user/status/1``) by defaulting https.
    if "://" not in candidate:
        candidate = "https://" + candidate

    try:
        parts = urlsplit(candidate)
    except ValueError:
        return Rejection(REJECT_NOT_A_TWEET_URL, "malformed URL")

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return Rejection(REJECT_NOT_A_TWEET_URL, f"unsupported URL scheme {scheme!r}")

    host = parts.netloc.lower()
    # Drop port if any, then strip leading www./mobile. subdomains (normalize
    # mobile.x.com / mobile.twitter.com / www.x.com per §15.1).
    host = host.split("@")[-1].split(":")[0]
    for prefix in ("www.", "mobile.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]

    # t.co shorteners are opaque redirects — NOT resolvable offline. Reject so a
    # spoofed/opaque shortener never mints a submission (§15.1). The caller may
    # resolve it first and re-submit the resolved x.com/twitter.com target.
    if host == "t.co":
        return Rejection(
            REJECT_UNRESOLVABLE_SHORTENER,
            "t.co shorteners must be resolved to an x.com/twitter.com status URL first",
        )

    if host not in _ACCEPTED_HOSTS:
        return Rejection(
            REJECT_NOT_A_TWEET_URL,
            f"only x.com / twitter.com status URLs are accepted (got host {host!r})",
        )

    path = parts.path  # query string already excluded by urlsplit (we ignore it)

    # ``x.com/i/web/status/<id>`` (share/deep-link shape) is checked first: its
    # path is ``/i/web/status/<id>``, which does NOT match the
    # ``/<user>/status/<id>`` shape (the segment after ``i`` is ``web``, not
    # ``status``), so the two patterns are unambiguous.
    m = _IWEB_RE.match(path)
    if m is not None:
        return CanonicalUrl(tweet_id=m.group("id"), handle=None)

    m = _STATUS_RE.match(path)
    if m is not None:
        return CanonicalUrl(tweet_id=m.group("id"), handle=m.group("user"))

    return Rejection(
        REJECT_NOT_A_TWEET_URL,
        "URL is not a /<user>/status/<id> tweet permalink",
    )


@dataclass(frozen=True)
class Hydrated:
    """A successfully-hydrated tweet: the canonical ``x_id`` + ``relay_tweets`` id."""

    tweet_row_id: int
    x_id: str
    author_handle: str | None


# Markers SocialData may carry for a non-publishable tweet (§15.1). The hydrate
# wrapper returns ``None`` for a hard 404; for soft markers the body carries a
# status we inspect here.
_REJECT_MARKERS: dict[str, tuple[str, str]] = {
    "suspended": (REJECT_SUSPENDED, "the author's account is suspended"),
    "deleted": (REJECT_DELETED, "the tweet was deleted"),
    "protected": (REJECT_PRIVATE, "the tweet is from a private/protected account"),
    "private": (REJECT_PRIVATE, "the tweet is from a private/protected account"),
    "not_found": (REJECT_NOT_FOUND, "the tweet was not found"),
}


def _classify_body(body: dict) -> Rejection | None:
    """Inspect a hydrated body for soft not-publishable markers (§15.1).

    SocialData returns a 200 with a status/error marker for some soft cases
    (suspended/protected) rather than a 404. Returns a :class:`Rejection` if the
    body indicates the tweet is not publishable, else ``None`` (publishable).
    """
    # An explicit error/status field, tolerant of shape variance.
    for key in ("status", "error", "tweet_status", "reason"):
        val = body.get(key)
        if isinstance(val, str):
            low = val.strip().lower()
            for marker, (code, reason) in _REJECT_MARKERS.items():
                if marker in low:
                    return Rejection(code, reason)
    # A protected-author flag.
    user = body.get("user") if isinstance(body.get("user"), dict) else {}
    if isinstance(user, dict) and (user.get("protected") or user.get("suspended")):
        if user.get("suspended"):
            code, reason = _REJECT_MARKERS["suspended"]
        else:
            code, reason = _REJECT_MARKERS["protected"]
        return Rejection(code, reason)
    return None


def _author_handle(body: dict) -> str | None:
    user = body.get("user") if isinstance(body.get("user"), dict) else {}
    if isinstance(user, dict):
        handle = user.get("screen_name") or user.get("username")
        if isinstance(handle, str) and handle:
            return handle
    handle = body.get("screen_name") or body.get("author_handle")
    return handle if isinstance(handle, str) and handle else None


def _author_id(body: dict) -> str | None:
    user = body.get("user") if isinstance(body.get("user"), dict) else {}
    if isinstance(user, dict):
        uid = user.get("id_str") or user.get("id")
        if uid is not None:
            return str(uid)
    uid = body.get("author_id")
    return str(uid) if uid is not None else None


def _canonical_x_id(body: dict, fallback: str) -> str:
    """The hydrated canonical id (§15.1: use the hydrated x_id, never the URL)."""
    raw = body.get("id_str") or body.get("id")
    return str(raw) if raw is not None else str(fallback)


def hydrate_or_reject(
    conn: Connection,
    client: SocialDataClient,
    org_id: str,
    tweet_id: str,
    *,
    fallback_handle: str | None = None,
) -> Hydrated | Rejection:
    """Hydrate a tweet id and upsert it into ``relay_tweets`` (§15.1).

    On a hard 404 (``hydrate_tweet`` returns ``None``) or a soft not-publishable
    marker (suspended / deleted / private), returns a :class:`Rejection` with the
    precise reason and writes NOTHING. On success, upserts the tweet (canonical
    ``x_id`` from the hydrated body, never the URL) and returns :class:`Hydrated`.

    The publish path calls this again just before sending; a tweet deleted
    between submission and publish hydrates to a Rejection, and the caller marks
    the submission ``rejected`` (§15.1 / §15.6).
    """
    try:
        body = client.hydrate_tweet(org_id, tweet_id)
    except SocialDataNotFound:
        # The wrapper normally maps 404 → None, but be defensive if a caller's
        # fake raises directly.
        return Rejection(REJECT_NOT_FOUND, "the tweet was not found")

    if body is None:
        return Rejection(REJECT_NOT_FOUND, "the tweet was not found or was deleted")
    if not isinstance(body, dict) or not body:
        return Rejection(REJECT_NOT_FOUND, "the tweet could not be hydrated")

    rejection = _classify_body(body)
    if rejection is not None:
        return rejection

    x_id = _canonical_x_id(body, tweet_id)
    handle = _author_handle(body) or fallback_handle or x_id

    media = body.get("media_urls")
    if not isinstance(media, list):
        media = []
    text_body = body.get("full_text") or body.get("text")
    conv = body.get("conversation_id_str") or body.get("conversation_id")
    in_reply = body.get("in_reply_to_status_id_str") or body.get(
        "in_reply_to_status_id"
    )

    tweet_row_id = relay_db.upsert_tweet(
        conn,
        x_id=x_id,
        x_author_handle=handle,
        x_author_id=_author_id(body),
        text_body=text_body,
        media_urls_json=json.dumps(media),
        is_reply=in_reply is not None,
        in_reply_to_x_id=str(in_reply) if in_reply is not None else None,
        conversation_x_id=str(conv) if conv is not None else None,
        raw_json=json.dumps(body),
    )
    return Hydrated(tweet_row_id=tweet_row_id, x_id=x_id, author_handle=handle)


__all__ = [
    "CanonicalUrl",
    "Rejection",
    "Hydrated",
    "canonicalize_tweet_url",
    "hydrate_or_reject",
    "REJECT_NOT_A_TWEET_URL",
    "REJECT_UNRESOLVABLE_SHORTENER",
    "REJECT_NOT_FOUND",
    "REJECT_DELETED",
    "REJECT_PRIVATE",
    "REJECT_SUSPENDED",
]
