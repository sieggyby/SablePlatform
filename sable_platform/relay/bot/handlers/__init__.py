"""SableRelay listener handlers (SableRelay PLAN §5.3 ``relay/bot/handlers/``).

Each handler module owns one operator-/team-facing flow and runs all of its DB
side effects inside ONE ``BEGIN IMMEDIATE`` (via
:func:`sable_platform.relay.bot.txn.immediate_txn`), embedding NO raw SQL (every
statement is a named ``relay/db.py`` helper — the C2.1 §5.3 layering boundary).
No external Telegram/Discord/SocialData call ever happens inside the transaction
(the C2.2 §3.1 audit invariant); handlers return small result objects the
listener uses to drive the OUTSIDE-the-txn sends.

C2.3a lands the two most concurrency-sensitive flows:

  * :mod:`~sable_platform.relay.bot.handlers.amplify` — Flow B (operator
    ``/amplify`` → a ``pending`` submission whose quorum is reached via reactions)
    and Flow C (shared-chat ``/amplify`` → immediate single-approval publish).
  * :mod:`~sable_platform.relay.bot.handlers.quorum` — the §3.1 guarded
    transition: a ``MessageReactionUpdated`` upserts/deletes a vote, recomputes
    the distinct-operator tally, and on threshold does the guarded
    ``pending → ready_to_publish`` UPDATE + fan-out outbox enqueue, all in one
    transaction (the load-bearing exactly-once enqueue).

C2.3b lands the operator-/member-facing flows:

  * :mod:`~sable_platform.relay.bot.handlers.flag_reply` — Flow D v1
    (``/flag-reply`` → record a ``relay_reply_opportunity`` + notify opted-in
    members with the X Web Intent compose deeplink + the media caveat).
  * :mod:`~sable_platform.relay.bot.handlers.preferences` — ``/optin-replies`` /
    ``/optout-replies`` / ``/mute-replies`` / ``/whoami`` (reply-ping preferences
    + the self-claim ``/whoami`` identity entry point).
  * :mod:`~sable_platform.relay.bot.handlers.admin` — ``/register-operator`` (the
    THREE §8 resolution modes: numeric ``tg_user_id`` / self-claim-via-recent-DM /
    forwarded-message — NO bare-handle resolution) and ``/bind-chat``, both
    admin-gated with an in-txn audit row.
  * :mod:`~sable_platform.relay.bot.handlers.sweep_config` — ``/sweep-config`` (mig
    062): admin-gated management of the per-client reply-opportunity sweep query
    set (mention_handles / topic_queries / from_set / operator_handles / enabled /
    expiry_hours). The daily cost cap is NOT settable here (it stays in
    ``relay_clients.config.polling``). Registers onto the C2.7 command registry.

C2.3c lands the PII / identity-integrity flows:

  * :mod:`~sable_platform.relay.bot.handlers.identity` — ``/forget-me`` (§15.5 PII
    deletion: removes the caller's preferences + identity rows but ANONYMIZES and
    KEEPS the ``relay_members`` row so ``member_id`` audit refs survive) and
    ``/link-x`` (§8 / Phase 4.6+ identity link: adds ``platform='x'`` to an
    existing member, with the ``(platform, external_user_id)`` collision-rejection
    + the admin-only DB merge — NO v1 self-serve merge UI). ``/link-x`` has NO live
    consumer until C2.4 reply-tracking; its C2.3c tests assert ONLY the
    collision-rejection + admin-merge invariants in isolation.
"""
from __future__ import annotations
