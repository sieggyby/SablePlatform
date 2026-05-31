"""SableRelay → AutoCM handler-registration API (MEGAPLAN C2.7).

This is the **AutoCM-facing handler-REGISTRATION API**, built ON TOP of the
C2.2 low-level dispatch primitive (``telegram_app`` / ``discord_app`` routing +
``dedupe`` + ``txn``). C2.2 stops at transport-level per-update routing; the
registry that lets AutoCM register its handlers at boot lives **here**, not in
C2.2 (registry-ownership split, MEGAPLAN C2.2/C2.7).

It implements the four coordination items the AutoCM specs assume
(``SABLE_RELAY_INTEGRATION.md §1/§5/§7``, currently OPEN):

  (a) **Listener handler-registry** — AutoCM registers a per-message handler at
      boot (`§5`: "AutoCM registers its handlers with Relay's listener registry
      at boot"). Per the §2 topology note the relay-bot process hosts AutoCM's
      online handlers **in-process** (single SablePlatform process serves both
      layers — `§5`), so registration is an in-process Python callback, NOT an
      RPC/queue. The registry ALSO dispatches **member JOIN / leave** events to
      registered consumers (needed for the greeting flow).

  (b) **Per-client operator-chat provisioning** (`§1 "Per-client operator
      chat"`, `§7` item 3) — the HITL surface. The DB side lives in
      :mod:`sable_platform.relay.db` (`provision_operator_chat` /
      `get_operator_chat`); this module re-exposes it on the registry so AutoCM
      has one object to talk to.

  (c) **TG typing-indicator** set/clear (`§7` item 8) — no-ops gracefully on
      unsupported transports (Discord/X).

  (d) **Inline-button callback routing** (`§1`: "Inline button callbacks routed
      through Relay's TG handler back to AutoCM") — a TG ``CallbackQuery`` is
      deduped (C2.2 primitive) and then routed to the registered AutoCM
      consumer's callback handler.

The dispatch path reuses C2.2's invariants verbatim: a routed message/event/
callback is first claimed in ``relay_processed_updates`` inside ONE
``immediate_txn`` (restart-safe dedupe), the inbound message is persisted into
``relay_messages`` in that SAME transaction, and ONLY THEN — **outside** the
transaction — is the in-process AutoCM consumer invoked. No AutoCM/LLM work ever
runs inside a ``BEGIN IMMEDIATE`` (the C2.2 audit invariant).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.dedupe import Deduper
from sable_platform.relay.bot.txn import immediate_txn

logger = logging.getLogger(__name__)

PLATFORMS = ("telegram", "discord")
MEMBER_EVENTS = ("join", "leave")


# ---------------------------------------------------------------------------
# The events the registry hands to consumers (transport-neutral)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InboundMessage:
    """A transport-neutral inbound message handed to a registered consumer.

    ``message_row_id`` is the persisted ``relay_messages.id`` (the FK target for
    ``autocm_drafts.source_message_id``); ``chat_row_id`` is the
    ``relay_chats.id`` (FK target for ``autocm_drafts.source_chat_id``). The
    consumer never sees a raw PTB ``Update`` / discord ``Message`` — the registry
    normalizes both transports to this shape so AutoCM's handler is
    transport-symmetric (the TG handler in v1, X/Discord symmetric per §1).
    """

    org_id: str
    platform: str
    chat_id: str
    chat_row_id: int
    external_message_id: str
    external_user_id: Optional[str]
    member_id: Optional[int]
    text: Optional[str]
    message_row_id: int
    reply_to_external_message_id: Optional[str] = None


@dataclass(frozen=True)
class MemberEvent:
    """A member JOIN / leave event handed to a registered consumer.

    Drives AutoCM's greeting flow (``event='join'``) and any leave bookkeeping.
    """

    org_id: str
    platform: str
    chat_id: str
    event: str  # 'join' | 'leave'
    external_user_id: str
    member_id: Optional[int] = None
    display_name: Optional[str] = None


@dataclass(frozen=True)
class CallbackEvent:
    """An inline-button callback handed back to the registered consumer.

    ``data`` is the callback payload (TG ``CallbackQuery.data`` — e.g.
    ``"autocm:approve:1234"``); ``callback_id`` is the platform callback id used
    for dedupe and (by the caller) the answer-callback ack. The registry routes
    this to the consumer that registered the matching callback prefix.
    """

    org_id: Optional[str]
    platform: str
    chat_id: Optional[str]
    callback_id: str
    data: str
    external_user_id: Optional[str] = None
    message_row_id: Optional[int] = None


# ---------------------------------------------------------------------------
# Consumer protocols (what AutoCM registers)
# ---------------------------------------------------------------------------
MessageHandler = Callable[[InboundMessage], None]
MemberEventHandler = Callable[[MemberEvent], None]
CallbackHandler = Callable[[CallbackEvent], None]


@runtime_checkable
class RelayConsumer(Protocol):
    """The full consumer interface AutoCM may implement (all methods optional).

    A consumer can either implement this Protocol and register itself with
    :meth:`RelayHandlerRegistry.register_consumer`, or register individual
    callables with the granular ``register_*`` methods. The Protocol exists so
    the interface/import test (C2.7 exit criterion) can assert the documented
    signatures without importing AutoCM.
    """

    def on_message(self, message: InboundMessage) -> None: ...

    def on_member_event(self, event: MemberEvent) -> None: ...

    def on_callback(self, event: CallbackEvent) -> None: ...


class RelayHandlerRegistry:
    """The boot-time handler registry the relay-bot hosts for AutoCM (in-process).

    Construct ONE per listener process (``relay-bot`` hosts AutoCM's online
    handlers in-process, §2 topology). AutoCM calls ``register_*`` at boot; the
    C2.2 transport routing calls ``dispatch_*`` per update. The registry owns the
    dedupe + ``relay_messages`` persistence inside one ``immediate_txn`` and then
    invokes the in-process consumer OUTSIDE the transaction.

    The connection is the listener's single long-lived SP-pool ``Connection``
    (the same one C2.2 uses) — the registry creates NO engine.
    """

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._message_handler: Optional[MessageHandler] = None
        self._member_handler: Optional[MemberEventHandler] = None
        # Inline-button callbacks are routed by data prefix so multiple
        # consumers can coexist (e.g. AutoCM review-queue vs. a future feature).
        self._callback_handlers: dict[str, CallbackHandler] = {}
        self._default_callback_handler: Optional[CallbackHandler] = None
        self._dedupers = {p: Deduper(p) for p in PLATFORMS}

    # -- registration (called by AutoCM at boot) -----------------------------
    def register_message_handler(self, handler: MessageHandler) -> None:
        """Register the single per-message handler (AutoCM's pipeline entry).

        Replacing an already-registered handler logs a warning — there is one
        AutoCM message pipeline per process; a second registration is almost
        always a boot-wiring bug.
        """
        if self._message_handler is not None:
            logger.warning("relay registry: message handler re-registered (overwriting)")
        self._message_handler = handler

    def register_member_event_handler(self, handler: MemberEventHandler) -> None:
        """Register the member JOIN/leave handler (AutoCM greeting flow)."""
        if self._member_handler is not None:
            logger.warning("relay registry: member-event handler re-registered (overwriting)")
        self._member_handler = handler

    def register_callback_handler(
        self, handler: CallbackHandler, *, prefix: Optional[str] = None
    ) -> None:
        """Register an inline-button callback consumer.

        ``prefix`` routes only callbacks whose ``data`` starts with it (e.g.
        ``"autocm:"``); ``prefix=None`` registers the default handler used when
        no prefix matches. The prefix split lets the review-queue ([Approve]/
        [Edit]/[Demote]) callbacks (C3.5b) coexist with other inline surfaces.
        """
        if prefix is None:
            if self._default_callback_handler is not None:
                logger.warning(
                    "relay registry: default callback handler re-registered (overwriting)"
                )
            self._default_callback_handler = handler
        else:
            if prefix in self._callback_handlers:
                logger.warning(
                    "relay registry: callback handler for prefix %r re-registered", prefix
                )
            self._callback_handlers[prefix] = handler

    def register_consumer(self, consumer: RelayConsumer, *, prefix: Optional[str] = None) -> None:
        """Register a single object implementing any of the consumer methods.

        Convenience over the three granular ``register_*`` calls — wires up
        whichever of ``on_message`` / ``on_member_event`` / ``on_callback`` the
        consumer actually defines.
        """
        if hasattr(consumer, "on_message"):
            self.register_message_handler(consumer.on_message)
        if hasattr(consumer, "on_member_event"):
            self.register_member_event_handler(consumer.on_member_event)
        if hasattr(consumer, "on_callback"):
            self.register_callback_handler(consumer.on_callback, prefix=prefix)

    # -- introspection (used by the interface test + the routing layer) -------
    @property
    def has_message_handler(self) -> bool:
        return self._message_handler is not None

    @property
    def has_member_handler(self) -> bool:
        return self._member_handler is not None

    def _resolve_callback_handler(self, data: str) -> Optional[CallbackHandler]:
        """Pick the consumer for a callback ``data`` by longest matching prefix."""
        best: Optional[CallbackHandler] = None
        best_len = -1
        for prefix, handler in self._callback_handlers.items():
            if data.startswith(prefix) and len(prefix) > best_len:
                best, best_len = handler, len(prefix)
        if best is not None:
            return best
        return self._default_callback_handler

    # -- dispatch (called by the C2.2 transport routing per update) -----------
    def dispatch_message(
        self,
        *,
        platform: str,
        update_id: object,
        org_id: str,
        chat_id: str,
        external_message_id: str,
        external_user_id: Optional[str] = None,
        member_id: Optional[int] = None,
        text: Optional[str] = None,
        reply_to_external_message_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> bool:
        """Persist + dispatch one inbound message to the registered handler.

        The dedupe claim + ``relay_chats`` upsert + ``relay_messages`` insert all
        run inside ONE ``immediate_txn`` (C2.2 invariant). The in-process AutoCM
        handler is invoked OUTSIDE that transaction (no AutoCM/LLM work ever runs
        inside a ``BEGIN IMMEDIATE``). Returns ``True`` if the message was
        dispatched (newly persisted + a handler is registered), ``False`` if it
        was a duplicate update_id, was already persisted, or no handler is
        registered.

        **Exactly-once-per-message dispatch.** Dedupe is keyed on
        ``(platform, update_id)``, but a single underlying message can legitimately
        arrive under MULTIPLE ``update_id``s — an edited TG ``message`` carries a
        NEW ``update_id`` for the SAME ``message_id``, and a long-poll offset reset
        can re-deliver. Such a redelivery passes the ``update_id`` dedupe gate but
        :func:`relay_db.persist_inbound_message` returns the EXISTING
        ``relay_messages`` row (deduped by ``relay_messages_unique``). We gate the
        AutoCM consumer on whether a NEW row was actually inserted, so the
        engage-check/draft/review pipeline runs exactly once per underlying
        message — never twice on the same ``message_row_id`` (downstream C3.1
        assumes one-shot). An already-persisted message returns ``False`` (not
        dispatched). The ``relay_messages_unique`` index is thus the durable
        backstop for BOTH the row AND the dispatch if the ``update_id`` gate is
        bypassed. A consumer exception is logged and swallowed so one bad message
        cannot crash the shared listener loop (the AutoCM gate/audit owns its own
        error handling; the relay substrate stays up).
        """
        if platform not in PLATFORMS:
            raise ValueError(f"unknown relay platform {platform!r}")
        with immediate_txn(self._conn):
            is_new = self._dedupers[platform].claim(self._conn, update_id)
            if not is_new:
                return False
            chat_row_id = relay_db.upsert_chat(
                self._conn, org_id, chat_id, platform=platform, title=title
            )
            message_row_id, inserted = relay_db.persist_inbound_message(
                self._conn,
                org_id=org_id,
                chat_row_id=chat_row_id,
                platform=platform,
                external_message_id=external_message_id,
                external_user_id=external_user_id,
                member_id=member_id,
                text_body=text,
                reply_to_external_message_id=reply_to_external_message_id,
                with_inserted_flag=True,
            )
        # A redelivery under a DIFFERENT update_id passes the dedupe gate but maps
        # to an already-persisted row — do NOT re-run the AutoCM pipeline on it.
        if not inserted:
            return False
        # Outside the transaction: invoke the in-process AutoCM handler.
        if self._message_handler is None:
            return False
        msg = InboundMessage(
            org_id=org_id,
            platform=platform,
            chat_id=chat_id,
            chat_row_id=chat_row_id,
            external_message_id=external_message_id,
            external_user_id=external_user_id,
            member_id=member_id,
            text=text,
            message_row_id=message_row_id,
            reply_to_external_message_id=reply_to_external_message_id,
        )
        try:
            self._message_handler(msg)
        except Exception:  # pragma: no cover - defensive isolation
            logger.exception(
                "relay registry: message handler raised (org=%s msg=%s); swallowed",
                org_id,
                message_row_id,
            )
        return True

    def dispatch_member_event(
        self,
        *,
        platform: str,
        update_id: object,
        org_id: str,
        chat_id: str,
        event: str,
        external_user_id: str,
        member_id: Optional[int] = None,
        display_name: Optional[str] = None,
    ) -> bool:
        """Dispatch a member JOIN / leave event to the registered consumer.

        Deduped inside one ``immediate_txn`` (restart-safe — a redelivered join
        does not double-greet). The consumer is invoked OUTSIDE the transaction.
        Returns ``True`` if dispatched, ``False`` if duplicate or no handler.
        """
        if platform not in PLATFORMS:
            raise ValueError(f"unknown relay platform {platform!r}")
        if event not in MEMBER_EVENTS:
            raise ValueError(f"unknown member event {event!r}; expected one of {MEMBER_EVENTS}")
        with immediate_txn(self._conn):
            is_new = self._dedupers[platform].claim(self._conn, update_id)
            if not is_new:
                return False
        if self._member_handler is None:
            return False
        evt = MemberEvent(
            org_id=org_id,
            platform=platform,
            chat_id=chat_id,
            event=event,
            external_user_id=external_user_id,
            member_id=member_id,
            display_name=display_name,
        )
        try:
            self._member_handler(evt)
        except Exception:  # pragma: no cover - defensive isolation
            logger.exception(
                "relay registry: member-event handler raised (org=%s user=%s); swallowed",
                org_id,
                external_user_id,
            )
        return True

    def dispatch_callback(
        self,
        *,
        platform: str,
        update_id: object,
        callback_id: str,
        data: str,
        org_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        external_user_id: Optional[str] = None,
        message_row_id: Optional[int] = None,
    ) -> bool:
        """Route an inline-button callback back to the registered AutoCM consumer.

        The callback is deduped inside one ``immediate_txn`` (a redelivered
        callback — TG retries the ``CallbackQuery`` on a missed ack — does not
        double-apply the [Approve]/[Demote] action). The matching consumer is
        resolved by callback-data prefix and invoked OUTSIDE the transaction.
        Returns ``True`` if routed to a consumer, ``False`` if it was a duplicate
        or no consumer matched the prefix.

        The 3-second TG ``answerCallbackQuery`` ack is the caller's
        responsibility (an external API call — it MUST NOT happen inside the
        transaction); the caller acks first, then calls this.
        """
        if platform not in PLATFORMS:
            raise ValueError(f"unknown relay platform {platform!r}")
        with immediate_txn(self._conn):
            is_new = self._dedupers[platform].claim(self._conn, update_id)
            if not is_new:
                return False
        handler = self._resolve_callback_handler(data)
        if handler is None:
            logger.warning(
                "relay registry: callback data %r matched no registered consumer", data
            )
            return False
        evt = CallbackEvent(
            org_id=org_id,
            platform=platform,
            chat_id=chat_id,
            callback_id=callback_id,
            data=data,
            external_user_id=external_user_id,
            message_row_id=message_row_id,
        )
        try:
            handler(evt)
        except Exception:  # pragma: no cover - defensive isolation
            logger.exception(
                "relay registry: callback handler raised (data=%s); swallowed", data
            )
        return True

    # -- (b) operator-chat provisioning (HITL surface) ------------------------
    def get_operator_chat(self, org_id: str, platform: str = "telegram") -> Optional[str]:
        """Return the active operator-chat ``chat_id`` for a client (or None).

        Thin pass-through to :func:`sable_platform.relay.db.get_operator_chat`
        so AutoCM resolves its HITL surface through the registry object.
        """
        return relay_db.get_operator_chat(self._conn, org_id, platform=platform)

    def provision_operator_chat(
        self,
        org_id: str,
        chat_id: str,
        *,
        platform: str = "telegram",
        title: Optional[str] = None,
    ) -> str:
        """Provision the per-client operator chat (idempotent) — the HITL surface.

        Runs the DB-only provisioning inside one ``immediate_txn`` (chat-surface
        insert + operator-binding flip/insert atomic). Returns the operator
        ``chat_id``. See :func:`sable_platform.relay.db.provision_operator_chat`.
        """
        with immediate_txn(self._conn):
            return relay_db.provision_operator_chat(
                self._conn, org_id, chat_id, platform=platform, title=title
            )


# ---------------------------------------------------------------------------
# (c) TG typing-indicator set/clear helper (§7 item 8)
# ---------------------------------------------------------------------------
class TypingIndicator:
    """Set / clear the TG typing indicator; no-op on unsupported transports.

    AutoCM's engage-check sets typing when it decides to draft (so the chat sees
    "typing…") and clears it on publish. Telegram has a ``send_chat_action`` /
    ``ChatAction.TYPING`` primitive (auto-expires after ~5s, so "set" re-pings
    and "clear" is implicit); Discord exposes ``channel.typing()``; X has NO
    typing primitive. To keep AutoCM's call sites transport-symmetric, the helper
    **no-ops gracefully** when the transport (or the underlying bot client) does
    not support typing — it never raises, it returns whether the action was
    actually sent.

    The bot client is injected (the PTB ``Bot`` / discord ``Channel``), so this
    is unit-testable with a fake that records calls, and graceful no-op is
    asserted by passing an unsupported transport.
    """

    def __init__(self, platform: str, *, bot: object = None) -> None:
        self._platform = platform
        self._bot = bot

    @property
    def supported(self) -> bool:
        """True iff this transport has a typing primitive (telegram/discord)."""
        return self._platform in ("telegram", "discord")

    async def set(self, chat_id: object) -> bool:
        """Send a typing action for ``chat_id``. Returns True if actually sent.

        No-ops (returns ``False``) on an unsupported transport (e.g. ``x``) or
        when the injected bot client lacks the typing primitive — so the AutoCM
        call site stays a single ``await typing.set(chat_id)`` regardless of
        transport.
        """
        if self._platform == "telegram":
            if self._bot is None or not hasattr(self._bot, "send_chat_action"):
                return False
            try:
                await self._bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:  # pragma: no cover - typing is best-effort cosmetics
                logger.debug("relay typing set failed (tg chat=%s); ignored", chat_id)
                return False
            return True
        if self._platform == "discord":
            # discord.py exposes channel.typing() as a context manager; a bare
            # trigger_typing-style coroutine is the simplest contract for the
            # helper. We treat any awaitable returned by the injected client's
            # send_typing as "sent".
            if self._bot is None or not hasattr(self._bot, "send_typing"):
                return False
            try:
                await self._bot.send_typing(chat_id)
            except Exception:  # pragma: no cover
                logger.debug("relay typing set failed (discord chat=%s); ignored", chat_id)
                return False
            return True
        # Unsupported transport (e.g. X) — graceful no-op.
        return False

    async def clear(self, chat_id: object) -> bool:
        """Clear typing for ``chat_id``. Returns True if an explicit clear was sent.

        Telegram typing auto-expires (~5s) so there is no explicit "stop typing"
        Bot API call — ``clear`` is a graceful no-op there (returns ``False``,
        meaning "nothing to send, the indicator self-expires"). On transports
        with an explicit stop primitive the helper would call it; today every
        supported transport self-expires, so ``clear`` is uniformly a safe no-op.
        """
        return False


def build_registry(conn: Connection) -> RelayHandlerRegistry:
    """Construct the per-process :class:`RelayHandlerRegistry`.

    Called once by the listener loop at boot; AutoCM then calls ``register_*``
    on the returned object before the transports start polling.
    """
    return RelayHandlerRegistry(conn)
