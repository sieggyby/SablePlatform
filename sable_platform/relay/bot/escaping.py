"""Output escaping & accidental-ping prevention (SableRelay PLAN §15.2).

This is a **hard, TESTED invariant**, not a cosmetic helper. The relay mirrors
community tweets and operator notes into Discord channels and Telegram chats.
If a mirrored tweet contains ``@everyone`` / ``@here`` (Discord) or ``@all``
(Telegram) and we forward it naively, we mass-ping the whole server. So every
outbound message routes through this module:

  * **Discord** — two layers (defense in depth):
      1. every ``send`` MUST carry ``allowed_mentions=discord.AllowedMentions.none()``
         (exposed here as :func:`discord_allowed_mentions`) — the API-level
         guarantee that ``@everyone`` / ``@here`` / role / user mentions never
         resolve to a ping even if present in the body; AND
      2. raw user input (tweet text, operator notes) is escaped through
         ``discord.utils.escape_markdown`` + ``escape_mentions`` (exposed as
         :func:`escape_discord`) so the text renders as literal characters.
  * **Telegram** — HTML parse mode with ``html.escape`` on every user-supplied
     substring (:func:`escape_telegram_text`); bot-emitted markup is limited to
     a ``<b>`` / ``<i>`` / ``<a href>`` whitelist (:func:`tg_bold` /
     :func:`tg_italic` / :func:`tg_link`). MarkdownV2 is avoided (its escaping
     rules are user-hostile). ``@all`` in escaped user text is just plain text —
     Telegram has no ``@all`` mass-ping primitive, and ``html.escape`` neutralizes
     any ``<...>`` injection.

The net invariant (asserted by the C2.2 tests): a mirrored message containing
``@everyone`` / ``@here`` (Discord) or ``@all`` (Telegram) renders as PLAIN
TEXT and produces ZERO ping.
"""
from __future__ import annotations

import html

import discord


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------
def discord_allowed_mentions() -> discord.AllowedMentions:
    """The ``AllowedMentions`` every relay Discord send MUST carry.

    ``discord.AllowedMentions.none()`` is the discord.py v2 idiom — equivalent
    to the API ``{'parse': []}`` — which suppresses ``@everyone`` / ``@here`` /
    role / user pings even when those tokens appear in the message body or an
    embed. A fresh instance is returned each call so a caller can never mutate a
    shared singleton into a ping-allowing state.
    """
    return discord.AllowedMentions.none()


def escape_discord(raw: str) -> str:
    """Escape untrusted text for a Discord message body (§15.2).

    Runs ``escape_mentions`` (inserts a zero-width space so ``@everyone`` /
    ``@here`` / ``<@id>`` cannot resolve) and ``escape_markdown`` (so ``*`` /
    ``_`` / backticks render literally). This is the content-layer half;
    :func:`discord_allowed_mentions` is the transport-layer half. Both are
    applied — defense in depth — so a body like ``@everyone gm`` is doubly
    inert.
    """
    if raw is None:
        return ""
    # escape_mentions first (breaks the @everyone / @here / <@id> token), then
    # escape_markdown so the result also can't smuggle markdown formatting.
    escaped = discord.utils.escape_mentions(raw)
    escaped = discord.utils.escape_markdown(escaped)
    return escaped


# ---------------------------------------------------------------------------
# Telegram (HTML parse mode + tag whitelist)
# ---------------------------------------------------------------------------
def escape_telegram_text(raw: str) -> str:
    """HTML-escape an untrusted substring for a Telegram HTML-mode message.

    ``html.escape`` neutralizes ``<`` / ``>`` / ``&`` (and, with the default
    ``quote=True``, ``"`` / ``'``) so user text can never inject a bot tag or
    break the message HTML. ``@all`` survives as literal text — Telegram has no
    ``@all`` mass-ping, and an escaped ``@all`` is inert. This is applied to
    EVERY user-supplied substring before it is concatenated into the bot's
    HTML payload.
    """
    if raw is None:
        return ""
    return html.escape(raw)


def tg_bold(inner_text: str) -> str:
    """Wrap ALREADY-ESCAPED text in the whitelisted ``<b>`` tag.

    Callers pass text that has already been through :func:`escape_telegram_text`
    (or is bot-controlled literal markup) — this only adds the tag.
    """
    return f"<b>{inner_text}</b>"


def tg_italic(inner_text: str) -> str:
    """Wrap ALREADY-ESCAPED text in the whitelisted ``<i>`` tag."""
    return f"<i>{inner_text}</i>"


def tg_link(url: str, inner_text: str) -> str:
    """Build a whitelisted ``<a href>`` link.

    Both the ``href`` URL and the link text are HTML-escaped so a hostile URL
    (or link text) cannot break out of the attribute / element. This is the
    only attribute-bearing tag in the §15.2 whitelist.
    """
    safe_url = html.escape(url, quote=True)
    safe_text = html.escape(inner_text)
    return f'<a href="{safe_url}">{safe_text}</a>'


# The §15.2 Telegram tag whitelist (bot-emitted markup only). Exposed so a
# compliance test can assert the relay never widens it (e.g. never emits a
# raw <script> or an un-escaped user tag).
TELEGRAM_TAG_WHITELIST = ("b", "i", "a")
