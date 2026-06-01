"""Telegram listener — PTB ``Application`` + low-level per-update routing.

This is the **low-level dispatch primitive** for Telegram (MEGAPLAN C2.2): it
builds the python-telegram-bot ``Application``, registers the handlers, and
routes each incoming update through the persistent dedupe gate inside one
``BEGIN IMMEDIATE`` before doing any work. It does NOT build the AutoCM-facing
handler-registration API (that is C2.7) — it only wires the transport-level
routing the registry will later sit on top of.

Key invariants implemented here:

  * **``allowed_updates`` opt-in for reactions** (PLAN §7 item 7): the Bot API
    default EXCLUDES ``message_reaction`` / ``message_reaction_count`` /
    ``my_chat_member``, so the listener passes :data:`ALLOWED_UPDATES`
    explicitly to ``run_polling`` — otherwise quorum reactions and kick events
    never arrive. (The bot must also be a chat administrator to receive
    reaction updates at all; that is an ops/provisioning requirement.)
  * **Anonymous-reaction drop** (PLAN §3.1 step 3 / §15.4): a
    ``MessageReactionUpdated`` with no ``user`` (anonymous group admin) is
    dropped — it cannot be attributed to an operator, so it can never count
    toward quorum and is not recorded.
  * **Persistent restart-safe dedupe** (PLAN §3.1 step 1 / §3.3): a routed
    REACTION/interaction is first claimed in ``relay_processed_updates`` inside
    the txn (:meth:`route_reaction`); a duplicate ``update_id`` is dropped. The
    §15.3 migration / kick handlers (:meth:`route_migration` /
    :meth:`route_my_chat_member`) are intentionally NOT deduped — they match the
    LOCKED PLAN §15.3 SQL (which starts at the binding UPDATE, no dedupe claim)
    and are independently idempotent via the ``WHERE status='active'`` guard, so
    a redelivered migration/kick is a no-op once the binding is already
    migrated/kicked.
  * **§15.3 chat-binding lifecycle**: ``MessageHandler(StatusUpdate.MIGRATE)``
    re-points a supergroup migration; ``ChatMemberHandler(MY_CHAT_MEMBER)``
    flips a kicked binding — each inside one ``BEGIN IMMEDIATE`` via
    :mod:`sable_platform.relay.bot.binding`.

The listener owns its DB ``Connection`` (a single connection from SP's pool,
used exclusively for the manual ``BEGIN IMMEDIATE`` transactions). It is passed
in by the caller (the loop runner) so the listener never creates an engine.
"""
from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy.engine import Connection
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ChatMemberHandler,
    MessageHandler,
    MessageReactionHandler,
    filters,
)

from sable_platform.relay.bot.binding import kick_chat_binding, migrate_chat_binding
from sable_platform.relay.bot.dedupe import Deduper
from sable_platform.relay.bot.txn import immediate_txn

logger = logging.getLogger(__name__)

# The explicit allowed_updates the listener MUST opt into (PLAN §7 item 7).
# Without message_reaction the quorum tally never fires; without my_chat_member
# the bot-kicked lifecycle never fires.
ALLOWED_UPDATES: tuple[str, ...] = (
    Update.MESSAGE,
    Update.MESSAGE_REACTION,
    Update.MESSAGE_REACTION_COUNT,
    Update.MY_CHAT_MEMBER,
)

# A TG user_id is anonymous when MessageReactionUpdated.user is None (an
# anonymous group-admin reaction). Such reactions are dropped (PLAN §3.1 step
# 3 / §15.4) — they can never be attributed to an operator.

# The per-message handler hook the higher-level (C2.7) registry will provide.
# C2.2 only carries the type so the routing code can call into it once
# dedupe/anon-drop have passed; C2.2 itself registers no consumer.
MessageHook = Callable[[Connection, Update], None]


class TelegramListener:
    """The Telegram half of the relay listener.

    Holds a PTB ``Application``, a ``Deduper`` bound to ``platform='telegram'``,
    and the SP DB ``Connection`` it runs its ``BEGIN IMMEDIATE`` transactions
    against. The public ``route_*`` methods ARE the low-level dispatch
    primitive: they implement dedupe + anon-drop + lifecycle and are directly
    unit-testable without a live Telegram connection.
    """

    def __init__(
        self,
        conn: Connection,
        *,
        bot_token: str | None = None,
        application: Application | None = None,
    ) -> None:
        self._conn = conn
        self._dedupe = Deduper("telegram")
        if application is not None:
            self.app = application
        elif bot_token:
            self.app = ApplicationBuilder().token(bot_token).build()
        else:
            self.app = None  # type: ignore[assignment]

    # -- handler registration (transport wiring, NOT the AutoCM registry) ----
    def install_handlers(self) -> None:
        """Register the transport-level handlers on the PTB Application.

        This wires reactions → :meth:`route_reaction`, supergroup migration →
        :meth:`route_migration`, and ``my_chat_member`` → :meth:`route_my_chat_member`.
        The AutoCM-facing per-message handler-registry is C2.7 and is NOT
        installed here.
        """
        if self.app is None:
            raise RuntimeError("TelegramListener has no Application to install handlers on")
        self.app.add_handler(MessageReactionHandler(self._on_reaction))
        self.app.add_handler(
            MessageHandler(filters.StatusUpdate.MIGRATE, self._on_migration)
        )
        self.app.add_handler(
            ChatMemberHandler(self._on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
        )

    # -- PTB callback shims (async) → sync routing primitives ----------------
    async def _on_reaction(self, update: Update, context) -> None:  # noqa: ANN001
        self.route_reaction(update)

    async def _on_migration(self, update: Update, context) -> None:  # noqa: ANN001
        self.route_migration(update)

    async def _on_my_chat_member(self, update: Update, context) -> None:  # noqa: ANN001
        self.route_my_chat_member(update)

    # -- low-level dispatch primitive (sync, directly testable) --------------
    def route_reaction(self, update: Update) -> bool:
        """Route a ``MessageReactionUpdated``: dedupe + anon-drop.

        Returns ``True`` if the reaction was accepted for downstream processing
        (it is new AND attributable to a user), ``False`` if it was dropped
        (duplicate update OR anonymous). The actual quorum tally (PLAN §3.1
        steps 4-8) is owned by C2.3a and would run on the ``True`` path inside
        the SAME transaction — C2.2 stops at the dedupe + anon-drop gate.
        """
        mru = update.message_reaction
        if mru is None:
            return False
        # Anonymous-reaction drop FIRST — but the dedupe row must still be
        # claimed so a redelivered anonymous reaction does not re-enter routing.
        with immediate_txn(self._conn):
            is_new = self._dedupe.claim(self._conn, update.update_id)
            if not is_new:
                return False  # duplicate update_id — already processed
            if mru.user is None:
                # Anonymous reaction: claimed in dedupe (so it is not
                # reprocessed) but dropped from any quorum/processing.
                return False
            # NEW + attributable: C2.3a's quorum tally runs here, inside this
            # same BEGIN IMMEDIATE. C2.2 returns the accept signal.
            return True

    def route_migration(self, update: Update) -> bool:
        """Route a Telegram supergroup migration (§15.3).

        Reads ``migrate_to_chat_id`` from the status message and re-points the
        binding + in-flight submissions inside one ``BEGIN IMMEDIATE`` (via
        :func:`migrate_chat_binding`). Returns ``True`` if a binding was
        re-pointed.
        """
        msg = update.message
        if msg is None or msg.migrate_to_chat_id is None:
            return False
        old_chat_id = str(msg.chat.id)
        new_chat_id = str(msg.migrate_to_chat_id)
        result = migrate_chat_binding(self._conn, old_chat_id, new_chat_id)
        if result.migrated:
            logger.info(
                "relay tg supergroup migration: org=%s %s -> %s (%d submissions repointed)",
                result.org_id,
                old_chat_id,
                new_chat_id,
                result.submissions_repointed,
            )
        return result.migrated

    def route_my_chat_member(self, update: Update) -> bool:
        """Route a ``my_chat_member`` update (§15.3 bot-kicked cleanup).

        When the bot's new status is ``kicked`` / ``left``, flip the binding
        and halt the chat's in-flight work inside one ``BEGIN IMMEDIATE`` (via
        :func:`kick_chat_binding`). Returns ``True`` if a binding was flipped.
        The admin-notify (an external API call) is the caller's responsibility
        and happens OUTSIDE the transaction.
        """
        mcm = update.my_chat_member
        if mcm is None:
            return False
        new_status = mcm.new_chat_member.status
        if new_status not in ("kicked", "left"):
            return False
        chat_id = str(mcm.chat.id)
        result = kick_chat_binding(self._conn, chat_id, platform="telegram")
        if result.flipped:
            logger.info(
                "relay tg bot removed from chat=%s org=%s "
                "(%d submissions expired, %d jobs killed)",
                chat_id,
                result.org_id,
                result.submissions_expired,
                result.jobs_killed,
            )
        return result.flipped


def build_telegram_listener(
    conn: Connection,
    bot_token: str,
) -> TelegramListener:
    """Construct + wire a :class:`TelegramListener` ready to run.

    The caller (the loop runner) then drives ``listener.app.run_polling`` (or
    the manual ``initialize`` / ``start`` / ``updater.start_polling`` lifecycle)
    with ``allowed_updates=list(ALLOWED_UPDATES)``.
    """
    listener = TelegramListener(conn, bot_token=bot_token)
    listener.install_handlers()
    return listener
