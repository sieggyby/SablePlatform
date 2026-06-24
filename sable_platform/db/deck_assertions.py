"""Single-use store for SableWeb-signed deck/produce authorization assertions (migration 079).

Codex Tier-1 replay defense. The deck_authz assertion (``sable/serve/deck_authz.py``) is verified by
Slopper before every schedule/handoff/post/cancel/produce, but the HMAC binds only
``(action, id, org, target_handle, actor, exp)`` and was REPLAYABLE until ``exp``: a captured-but-valid
assertion could be re-POSTed within its TTL to drive a repeated PAID meme ideation (bounded only by
the per-operator weekly budget) or to re-fire a state flip with mutated UNSIGNED request fields
(``publish_at`` / ``posted_ref`` / ``num|topic|band``).

``consume_assertion`` makes each assertion SINGLE-USE by inserting its SIGNATURE (the HMAC hex -- the
most-signed value, unforgeable without the shared secret) into ``deck_consumed_assertions`` keyed on
``PRIMARY KEY(sig)``. The Slopper boundary calls it exactly once BEFORE any budget reserve / state
change; the FIRST POST carrying a given ``sig`` wins and every later replay -- even one with tampered
``num/topic/band/publish_at/posted_ref`` -- hits the unique constraint and is rejected. The unique
constraint (not an in-process cache) makes this race/replay-safe across workers + processes.

This closes the captured-assertion replay of an UNCHANGED signature. The COMPLEMENTARY first-use
binding -- an in-flight tamper of the honest first request's mutable fields (``publish_at`` /
``posted_ref`` / ``num|band|topic``) -- is now handled by the action-specific ``request_hash`` folded
into the signed HMAC payload itself (``sable/serve/deck_authz.py``), so a tamper changes the expected
signature and 403s at VERIFY (before this consume). This store's job is unchanged: it makes the
SIGNATURE single-use (the ``jti`` role); the request_hash binds the fields. No payload concern lives
here -- ``consume_assertion`` only ever stores the already-verified ``sig``.

NO cost column, ever. The caller owns no transaction here -- ``consume_assertion`` runs its own
``immediate_txn`` so the single-use claim is committed (durable + visible to other workers) BEFORE the
caller's reserve/state-flip txn. ``gc_expired_assertions`` prunes rows whose ``exp`` has long passed.
"""
from __future__ import annotations

from sqlalchemy import text as _sa_text
from sqlalchemy.exc import IntegrityError

from sable_platform.db.content_deck import _utc_now_iso


def consume_assertion(
    conn,
    *,
    sig: str,
    action: str,
    org_id: str,
    actor: str,
    exp: int,
    now: str | None = None,
) -> bool:
    """Atomically claim ``sig`` as USED. Returns ``True`` on the FIRST use (the caller may proceed)
    and ``False`` if it was already consumed (a replay -- the caller must fail closed, 403).

    Race/replay-safe: the insert runs inside an ``immediate_txn`` (BEGIN IMMEDIATE on SQLite /
    SERIALIZABLE on Postgres) and the ``PRIMARY KEY(sig)`` makes a concurrent/duplicate insert raise
    ``IntegrityError``, which is caught and reported as ``False`` (the connection is left clean for the
    caller's next transaction). ``sig`` MUST be the HMAC hex of an assertion the caller ALREADY verified
    (valid + unexpired) -- consuming an unverified/forged sig is pointless but harmless.

    Accepts either a SablePlatform ``CompatConnection`` (Slopper's ``get_db()``) or a raw SQLAlchemy
    ``Connection`` (SablePlatform tests); the underlying SA connection is unwrapped to drive the txn,
    exactly like ``sable.shared.txn.serialized_txn``."""
    from sable_platform.relay.bot.txn import immediate_txn

    sa_conn = getattr(conn, "_conn", conn)  # CompatConnection -> underlying SA conn
    ts = now or _utc_now_iso()
    try:
        with immediate_txn(sa_conn):
            conn.execute(
                _sa_text(
                    "INSERT INTO deck_consumed_assertions (sig, action, org_id, actor, exp, consumed_at) "
                    "VALUES (:sig, :action, :org, :actor, :exp, :ts)"
                ),
                {
                    "sig": str(sig),
                    "action": str(action),
                    "org": str(org_id),
                    "actor": str(actor),
                    "exp": int(exp),
                    "ts": ts,
                },
            )
        return True
    except IntegrityError:
        # Duplicate sig -> already consumed -> replay. immediate_txn rolled the failed insert back, so
        # the connection is clean for the caller's subsequent reserve/state-flip transaction.
        return False


def gc_expired_assertions(conn, *, now_unix: int, grace_seconds: int = 3600) -> int:
    """Delete consumed-assertion rows whose ``exp`` passed more than ``grace_seconds`` ago (an expired
    assertion can never verify again, so its single-use row is dead weight). Returns the row count.
    Caller owns the transaction (run inside an ``immediate_txn``). Bounded, low-volume housekeeping --
    deck actions are human-driven (creator/admin only), so the table stays tiny between GC runs."""
    cutoff = int(now_unix) - int(grace_seconds)
    res = conn.execute(
        _sa_text("DELETE FROM deck_consumed_assertions WHERE exp < :cutoff"),
        {"cutoff": cutoff},
    )
    return int(res.rowcount or 0)
