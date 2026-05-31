"""``/flag-reply`` — Flow D v1 (reply opportunity → DM opted-in members) (C2.3b).

PLAN §2 Flow D v1 (the explicit operator command — v1.5 reaction-based flagging
is DEFERRED, §10 Phase 5):

  Operator in operator chat: ``/flag-reply <tweet-url> [note] [target=@handle…]``
      → hydrate the URL (§15.1, EXTERNAL — its own short txn)
      → ROLE-GATE: a non-operator caller is rejected
      → record a ``relay_reply_opportunity`` (origin='explicit_command')
      → resolve the target set: explicit ``target=@handle…`` if given, else ALL
        opted-in (and not-muted) members for the org (§11 #1)
      → insert one ``relay_reply_notification`` per resolvable target
      → return the target list so the LISTENER can DM each one the compose
        deeplink OUTSIDE the txn (no external send happens here).

**Compose-deeplink + media caveat (PLAN §2 Flow D "Compose-deeplink
limitations").** The X Web Intent endpoint
(``https://x.com/intent/tweet?in_reply_to=<id>&text=<prefill>``) supports
``text``/``url``/``in_reply_to`` etc. but has **NO media parameter**. So this
handler surfaces a ``media_caveat`` flag on the result when the flagged tweet
carries attached media: the listener then DMs the media file alongside the
deeplink and tells the member to download-and-attach manually. v1 UX must NOT
promise "open composer with media pre-attached."

Per the LOCKED C2.1 §5.3 layering boundary this module embeds NO raw SQL (every
statement is a named ``relay/db.py`` helper). The URL canonicalization + the
SocialData hydration (the only EXTERNAL call) happen BEFORE the opportunity txn
via the C2.4 :mod:`~sable_platform.relay.feed.canonical` helpers — NO external
call ever happens inside the ``immediate_txn`` (the §3.1 / C2.2 invariant).
Authorization is ALWAYS role-gated via ``relay_member_roles`` (§8); the external
``user_id`` is the source of truth, never the handle (§15.4).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn
from sable_platform.relay.feed import canonical
from sable_platform.relay.feed.canonical import Hydrated, Rejection
from sable_platform.relay.socialdata import SocialDataClient

logger = logging.getLogger(__name__)

# The X Web Intent compose endpoint (PLAN §2 Flow D). No media parameter exists.
_COMPOSE_INTENT_BASE = "https://x.com/intent/tweet"


# Machine-stable outcome codes (asserted by tests / used for the listener reply).
FLAG_REPLY_REJECTED = "rejected"  # URL/hydration rejected (§15.1) — nothing created
FLAG_REPLY_NOT_AUTHORIZED = "not_authorized"  # caller lacks the operator role
FLAG_REPLY_CREATED = "created"  # opportunity created, targets notified


@dataclass(frozen=True)
class ReplyTarget:
    """One member to DM the compose deeplink (drives the OUTSIDE-the-txn DM send).

    ``tg_user_id`` is the TG identity to DM (``None`` if the member has no linked
    TG identity — the listener cannot DM them, so it skips them; the opportunity
    is still recorded against them). ``notification_id`` is the
    ``relay_reply_notifications`` row id (``None`` if a notification already
    existed — the member was already targeted for this opportunity, so it is NOT
    re-DMed).
    """

    member_id: int
    tg_user_id: str | None
    handle: str | None
    notification_id: int | None


@dataclass(frozen=True)
class FlagReplyResult:
    """Outcome of a ``/flag-reply`` invocation (drives the DM fan-out + ack reply).

    ``code`` is one of the ``FLAG_REPLY_*`` constants. When ``code ==
    FLAG_REPLY_CREATED`` the listener DMs each ``targets`` entry (that has a
    ``tg_user_id`` and a fresh ``notification_id``) the ``compose_url`` /
    ``intent_url`` deeplink; ``media_caveat`` is True when the flagged tweet has
    attached media (the listener then sends the media + the download-and-attach
    note, §2 Flow D limitation). ``unresolved_targets`` lists explicit
    ``target=@handle`` tokens that resolved to no (or an ambiguous) member so the
    ack can report them. ``rejection`` carries the §15.1 precise reason when
    rejected.
    """

    code: str
    opportunity_id: int | None = None
    org_id: str | None = None
    tweet_row_id: int | None = None
    x_id: str | None = None
    tweet_url: str | None = None
    compose_url: str | None = None
    note: str | None = None
    media_caveat: bool = False
    targets: tuple = ()
    unresolved_targets: tuple = ()
    rejection: Rejection | None = None
    extra: dict = field(default_factory=dict)


def _parse_targets(tokens: list[str]) -> tuple[list[str], str | None]:
    """Split a ``/flag-reply`` arg tail into ``target=@handle`` tokens + the note.

    PLAN §11 #1: ``/flag-reply <url> [note] [target=@handle…]``. ``target=`` may
    appear as ``target=@a`` (one each) or ``target=@a,@b`` (comma-joined); the
    remaining non-``target=`` tokens form the free-text note (order preserved).
    Returns ``(handles, note_or_None)``.
    """
    handles: list[str] = []
    note_parts: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low.startswith("target=") or low.startswith("targets="):
            value = tok.split("=", 1)[1]
            for h in value.replace(",", " ").split():
                if h.strip():
                    handles.append(h.strip())
        else:
            note_parts.append(tok)
    note = " ".join(note_parts).strip() or None
    return handles, note


def _compose_url(x_id: str, note: str | None) -> str:
    """Build the X Web Intent reply-compose deeplink for a tweet x_id (§2 Flow D).

    ``https://x.com/intent/tweet?in_reply_to=<x_id>[&text=<prefill>]``. The
    operator ``note`` (if any) is offered as the prefill ``text`` — manually
    URL-encoded (only ``in_reply_to`` + ``text`` are used, so a minimal encoder
    suffices and we avoid an import at module top for a tiny surface). There is
    intentionally NO media parameter — the endpoint has none (see module docstring
    / ``media_caveat``).
    """
    from urllib.parse import urlencode

    params = {"in_reply_to": str(x_id)}
    if note:
        params["text"] = note
    return f"{_COMPOSE_INTENT_BASE}?{urlencode(params)}"


def _tweet_url(x_id: str, handle: str | None) -> str:
    """Canonical ``https://x.com/<user>/status/<id>`` URL for the DM body."""
    user = handle or "i"
    return f"https://x.com/{user}/status/{x_id}"


def _has_media(conn: Connection, tweet_row_id: int) -> bool:
    """True iff the hydrated tweet carries attached media (drives ``media_caveat``).

    Reads the persisted ``relay_tweets.media_urls`` (a JSON TEXT array) via
    ``get_tweet_by_row_id`` — the hydration step already upserted it. A non-empty
    list means the compose deeplink cannot pre-attach the media (§2 Flow D), so the
    listener falls back to DMing the file + a download-and-attach note.
    """
    row = relay_db.get_tweet_by_row_id(conn, tweet_row_id)
    if row is None:
        return False
    raw = row.get("media_urls")
    if not raw:
        return False
    try:
        media = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return isinstance(media, list) and len(media) > 0


def _hydrate(
    conn: Connection, client: SocialDataClient, org_id: str, raw_url: str
) -> Hydrated | Rejection:
    """Canonicalize + hydrate a tweet URL (§15.1) — the EXTERNAL step, pre-txn.

    Mirrors :func:`amplify._hydrate`: the ``relay_tweets`` upsert inside
    ``hydrate_or_reject`` is a write, so the caller wraps THIS in its own short
    ``immediate_txn``; the opportunity write happens in a second txn after this
    returns (keeping the SocialData call out of the opportunity-write txn, §3.1).
    """
    canon = canonical.canonicalize_tweet_url(raw_url)
    if isinstance(canon, Rejection):
        return canon
    return canonical.hydrate_or_reject(
        conn, client, org_id, canon.tweet_id, fallback_handle=canon.handle
    )


def flag_reply(
    conn: Connection,
    client: SocialDataClient,
    *,
    org_id: str,
    platform: str,
    flagger_external_user_id: str,
    raw_url: str,
    note: str | None = None,
    target_handles: list[str] | None = None,
    arg_tokens: list[str] | None = None,
    flagger_handle: str | None = None,
) -> FlagReplyResult:
    """Flow D v1: create a reply opportunity and notify opted-in members.

    ``note`` / ``target_handles`` may be passed pre-parsed; alternatively pass the
    raw ``arg_tokens`` (the whitespace-split tail of ``/flag-reply <url> …``) and
    this parses ``target=@handle`` tokens + the note from them (the listener may do
    either). Steps:

      1. Hydrate the URL (§15.1, EXTERNAL — its own short txn). A rejection creates
         NOTHING and returns the precise reason.
      2. Resolve/auto-create the flagger and ROLE-GATE: a non-operator caller is
         rejected (``FLAG_REPLY_NOT_AUTHORIZED``).
      3. Inside ONE ``immediate_txn``: create the opportunity, resolve the target
         set (explicit handles, else opted-in members), insert one notification per
         target, and gather the DM target list.

    The returned :class:`FlagReplyResult` drives the listener's DM fan-out + ack —
    NO external send happens inside the txn.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"unknown relay platform {platform!r}")

    if arg_tokens is not None:
        parsed_handles, parsed_note = _parse_targets(arg_tokens)
        if target_handles is None:
            target_handles = parsed_handles
        if note is None:
            note = parsed_note

    # 1. Hydrate (external) — its own short txn so the SocialData call is not
    #    inside the opportunity-write transaction.
    with immediate_txn(conn):
        hydrated = _hydrate(conn, client, org_id, raw_url)
    if isinstance(hydrated, Rejection):
        return FlagReplyResult(
            code=FLAG_REPLY_REJECTED, org_id=org_id, rejection=hydrated
        )

    with immediate_txn(conn):
        # 2. Resolve/auto-create the flagger identity; role-gate.
        flagger_id = relay_db.auto_create_member_identity(
            conn, platform, str(flagger_external_user_id), handle=flagger_handle
        )
        if not relay_db.is_relay_operator(conn, flagger_id, org_id):
            return FlagReplyResult(
                code=FLAG_REPLY_NOT_AUTHORIZED,
                org_id=org_id,
                tweet_row_id=hydrated.tweet_row_id,
                x_id=hydrated.x_id,
            )

        # 3. Create the opportunity.
        opportunity_id = relay_db.create_reply_opportunity(
            conn,
            org_id=org_id,
            tweet_id=hydrated.tweet_row_id,
            flagger_id=flagger_id,
            origin="explicit_command",
            note=note,
        )

        # Resolve the target set.
        unresolved: list[str] = []
        target_members: list[dict] = []  # {member_id, tg_user_id, handle}
        if target_handles:
            resolved = relay_db.resolve_members_by_telegram_handle(conn, target_handles)
            seen_ids: set[int] = set()
            for handle, member_id in resolved.items():
                if member_id is None:
                    unresolved.append(handle)
                    continue
                if member_id in seen_ids:
                    continue
                seen_ids.add(member_id)
                identity = relay_db.get_member_identity(conn, member_id, "telegram")
                target_members.append(
                    {
                        "member_id": member_id,
                        "tg_user_id": identity["external_user_id"] if identity else None,
                        "handle": (identity["handle"] if identity else None) or handle,
                    }
                )
        else:
            target_members = relay_db.list_optedin_members(conn, org_id)

        media_caveat = _has_media(conn, hydrated.tweet_row_id)

        targets: list[ReplyTarget] = []
        for tm in target_members:
            member_id = int(tm["member_id"])
            notification_id = relay_db.insert_reply_notification(
                conn, opportunity_id, member_id
            )
            targets.append(
                ReplyTarget(
                    member_id=member_id,
                    tg_user_id=tm.get("tg_user_id"),
                    handle=tm.get("handle"),
                    notification_id=notification_id,
                )
            )

        compose_url = _compose_url(hydrated.x_id, note)
        tweet_url = _tweet_url(hydrated.x_id, hydrated.author_handle)
        return FlagReplyResult(
            code=FLAG_REPLY_CREATED,
            opportunity_id=opportunity_id,
            org_id=org_id,
            tweet_row_id=hydrated.tweet_row_id,
            x_id=hydrated.x_id,
            tweet_url=tweet_url,
            compose_url=compose_url,
            note=note,
            media_caveat=media_caveat,
            targets=tuple(targets),
            unresolved_targets=tuple(unresolved),
        )


__all__ = [
    "FlagReplyResult",
    "ReplyTarget",
    "flag_reply",
    "FLAG_REPLY_REJECTED",
    "FLAG_REPLY_NOT_AUTHORIZED",
    "FLAG_REPLY_CREATED",
]
