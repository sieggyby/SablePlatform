"""Discord listener — discord.py ``Client`` + low-level interaction routing.

This is the **low-level dispatch primitive** for Discord (MEGAPLAN C2.2). It
builds the discord.py ``Client`` with the server-default ping suppression
(``AllowedMentions.none()``) baked into the client so EVERY send is ping-safe
even if a call site forgets the per-send arg, and routes interactions through
the persistent dedupe gate + the **3-second defer** pattern (PLAN §3.3). It
does NOT build the AutoCM-facing handler-registration / callback-routing API
(that is C2.7).

Key invariants implemented here:

  * **``AllowedMentions.none()`` everywhere** (PLAN §15.2): the ``Client`` is
    constructed with ``allowed_mentions=discord.AllowedMentions.none()`` as the
    client-wide default, and helper sends explicitly pass it again — so a
    mirrored tweet containing ``@everyone`` / ``@here`` produces ZERO ping.
  * **3-second defer** (PLAN §3.3): the interaction router does the cheap DB
    work — persist the dedupe row inside one ``BEGIN IMMEDIATE`` — and signals
    the caller to ``defer()`` immediately; the expensive work + ``followup``
    happens after, within the 15-minute follow-up token TTL.
  * **Persistent restart-safe dedupe** (PLAN §3.3): the interaction is deduped
    on ``(platform='discord', update_id=interaction.id)``; a redelivered
    interaction is silently dropped.
  * **§15.3 Discord 403/404 binding-flip** is wired via
    :func:`~sable_platform.relay.bot.binding.flip_discord_binding_on_failure`,
    invoked by the publisher (C2.4) on a channel-deleted send; the routing
    surface here exposes :meth:`note_send_failure` so a send failure feeds the
    consecutive-failure counter and flips the binding once the threshold is
    crossed — all inside one ``BEGIN IMMEDIATE``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from sqlalchemy.engine import Connection

from sable_platform.relay.bot.binding import flip_discord_binding_on_failure
from sable_platform.relay.bot.dedupe import Deduper
from sable_platform.relay.bot.escaping import discord_allowed_mentions
from sable_platform.relay.bot.txn import immediate_txn

logger = logging.getLogger(__name__)


def build_intents() -> discord.Intents:
    """The minimal intents the relay listener needs.

    ``guilds`` (binding lifecycle / channel resolution) + ``guild_reactions``
    (Flow A reaction reply-flagging, v1.5) + ``guild_messages``. We do NOT
    request the privileged ``message_content`` intent in v1 — the relay does
    not read free-text message bodies, it routes interactions and reactions.
    """
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_reactions = True
    intents.guild_messages = True
    return intents


@dataclass(frozen=True)
class InteractionRouteResult:
    """Outcome of routing a Discord interaction's pre-defer DB step.

    ``should_defer`` is ``True`` when the interaction is new and the caller
    should immediately ``await interaction.response.defer(...)`` (meeting the
    3-second deadline) and then proceed to the work + ``followup``. ``False``
    means the interaction was a duplicate and must be silently dropped (no
    defer, no followup).
    """

    should_defer: bool


class DiscordListener:
    """The Discord half of the relay listener.

    Wraps a discord.py ``Client`` (constructed with the ping-safe default), a
    ``Deduper`` bound to ``platform='discord'``, and the SP DB ``Connection``
    it runs its ``BEGIN IMMEDIATE`` transactions against. The ``route_*`` /
    ``note_send_failure`` methods ARE the low-level dispatch primitive and are
    directly unit-testable without a live Discord gateway connection.
    """

    def __init__(
        self,
        conn: Connection,
        *,
        client: discord.Client | None = None,
    ) -> None:
        self._conn = conn
        self._dedupe = Deduper("discord")
        if client is not None:
            self.client = client
        else:
            self.client = discord.Client(
                intents=build_intents(),
                # Client-wide ping suppression: even a send that forgets the
                # per-call allowed_mentions inherits none() (§15.2).
                allowed_mentions=discord_allowed_mentions(),
            )

    # -- low-level dispatch primitive (sync, directly testable) --------------
    def route_interaction(self, interaction_id: object) -> InteractionRouteResult:
        """Pre-defer DB step for a Discord interaction (PLAN §3.3 step 1).

        Persists the dedupe row inside one ``BEGIN IMMEDIATE`` and returns
        whether the caller should ``defer()`` and proceed. This is the part
        that MUST complete in <3s — it does only the dedupe insert, no external
        API call. The async caller wraps it::

            res = listener.route_interaction(interaction.id)
            if not res.should_defer:
                return  # duplicate — silent
            await interaction.response.defer(ephemeral=True)  # 3s deadline met
            ...  # work
            await interaction.followup.send(..., allowed_mentions=...none())
        """
        with immediate_txn(self._conn):
            is_new = self._dedupe.claim(self._conn, interaction_id)
        return InteractionRouteResult(should_defer=is_new)

    def route_reaction_event(self, event_id: object) -> bool:
        """Dedupe a Discord reaction event (v1.5, PLAN §3.3 tail).

        Discord reactions are NOT interactions — they carry no 3s deadline — and
        are deduped on ``(platform='discord', update_id=<event_id>)``. Returns
        ``True`` if new (proceed), ``False`` if duplicate.
        """
        with immediate_txn(self._conn):
            return self._dedupe.claim(self._conn, event_id)

    def note_send_failure(
        self,
        org_id: str,
        chat_id: str,
        consecutive_failures: int,
    ) -> bool:
        """Feed a Discord 403/404 send failure to the binding-flip logic (§15.3).

        The publisher (C2.4) calls this with the per-destination consecutive
        failure count after a channel-deleted / no-access send. Once the count
        crosses ``relay_clients.config.publish.kicked_after_consecutive_failures``
        (default 5), the binding flips to ``kicked`` and the cleanup runs inside
        one ``BEGIN IMMEDIATE``. Returns ``True`` if the binding was flipped.
        """
        result = flip_discord_binding_on_failure(
            self._conn, org_id, chat_id, consecutive_failures
        )
        if result.flipped:
            logger.info(
                "relay discord binding flipped to kicked: org=%s chat=%s "
                "(%d jobs killed) after %d consecutive failures",
                org_id,
                chat_id,
                result.jobs_killed,
                consecutive_failures,
            )
        return result.flipped


def build_discord_listener(conn: Connection) -> DiscordListener:
    """Construct a :class:`DiscordListener` ready to connect.

    The caller (the loop runner) drives ``listener.client.start(token)`` on the
    shared event loop.
    """
    return DiscordListener(conn)
