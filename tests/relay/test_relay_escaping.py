"""C2.2 §15.2 output escaping / accidental-ping prevention tests.

These assert the HARD invariant (not assumed): a mirrored message containing
``@everyone`` / ``@here`` (Discord) and ``@all`` (Telegram) renders as PLAIN
TEXT and produces ZERO ping.

  - Discord: every send carries ``AllowedMentions.none()`` (transport gate) AND
    raw input is escaped via ``escape_markdown`` + ``escape_mentions`` (content
    gate) — both layers asserted inert against ``@everyone``/``@here``.
  - Telegram: HTML mode + ``html.escape`` on every user substring; ``@all`` is
    literal text; ``<...>`` injection is neutralized; the bot markup whitelist
    is exactly ``<b>``/``<i>``/``<a href>``.
"""
from __future__ import annotations

import discord

from sable_platform.relay.bot import escaping


# ---------------------------------------------------------------------------
# Discord transport gate: AllowedMentions.none()
# ---------------------------------------------------------------------------
def test_discord_allowed_mentions_is_none_all_off() -> None:
    am = escaping.discord_allowed_mentions()
    # ``none()`` is the discord.py idiom equivalent to API ``{'parse': []}`` —
    # every mention class is suppressed at the transport layer.
    assert am.everyone is False
    assert am.users is False
    assert am.roles is False
    assert am.replied_user is False
    # Field-wise parity with the canonical none() (AllowedMentions has no __eq__).
    canonical = discord.AllowedMentions.none()
    assert (am.everyone, am.users, am.roles, am.replied_user) == (
        canonical.everyone,
        canonical.users,
        canonical.roles,
        canonical.replied_user,
    )


def test_discord_allowed_mentions_returns_fresh_instance() -> None:
    # A caller mutating one must not poison the shared default.
    a = escaping.discord_allowed_mentions()
    a.everyone = True
    b = escaping.discord_allowed_mentions()
    assert b.everyone is False


# ---------------------------------------------------------------------------
# Discord content gate: escape_markdown + escape_mentions
# ---------------------------------------------------------------------------
def test_discord_everyone_here_escaped_to_plain_text() -> None:
    raw = "@everyone gm @here check this"
    out = escaping.escape_discord(raw)
    # The literal ping tokens must NOT survive as resolvable mentions. discord's
    # escape_mentions inserts a zero-width space after the @, so the contiguous
    # "@everyone"/"@here" token is broken.
    assert "@everyone " not in out
    assert "@here " not in out
    # The words themselves remain visible (it renders as text, just inert).
    assert "everyone" in out
    assert "here" in out


def test_discord_id_mentions_suppressed_by_transport_gate() -> None:
    # discord.py's escape_mentions deliberately does NOT touch <@id> / <@&id>
    # syntax — ID-based user/role pings are suppressed by the TRANSPORT gate
    # (AllowedMentions.none()), not by content escaping. This documents the
    # division of responsibility: the content gate neutralizes the textual
    # @everyone/@here mass-ping; the transport gate neutralizes ID mentions.
    am = escaping.discord_allowed_mentions()
    assert am.users is False  # <@id> can never resolve to a user ping
    assert am.roles is False  # <@&id> can never resolve to a role ping
    # And the escaped body is unchanged for ID syntax (content gate is scoped
    # to the mass-ping tokens, which the transport gate would also catch).
    raw = "<@123456> <@&987654> ping"
    out = escaping.escape_discord(raw)
    assert "ping" in out


def test_discord_markdown_escaped() -> None:
    raw = "**bold** _italic_ `code`"
    out = escaping.escape_discord(raw)
    assert "\\*\\*bold\\*\\*" in out
    assert "\\`code\\`" in out


def test_discord_none_input_safe() -> None:
    assert escaping.escape_discord(None) == ""


# ---------------------------------------------------------------------------
# Telegram content gate: html.escape + tag whitelist
# ---------------------------------------------------------------------------
def test_telegram_all_renders_as_plain_text() -> None:
    raw = "@all gm everyone"
    out = escaping.escape_telegram_text(raw)
    # Telegram has no @all mass-ping; escaped, it's just literal text.
    assert out == "@all gm everyone"


def test_telegram_html_injection_neutralized() -> None:
    raw = '<b>evil</b> <a href="x">link</a> & <script>'
    out = escaping.escape_telegram_text(raw)
    # All angle brackets / ampersands are entity-escaped so user text can never
    # inject a bot tag or break the HTML payload.
    assert "<b>" not in out
    assert "<script>" not in out
    assert "&lt;b&gt;" in out
    assert "&lt;script&gt;" in out
    assert "&amp;" in out


def test_telegram_bold_italic_wrap_escaped_inner() -> None:
    inner = escaping.escape_telegram_text("R&D <ops>")
    assert escaping.tg_bold(inner) == "<b>R&amp;D &lt;ops&gt;</b>"
    assert escaping.tg_italic(inner) == "<i>R&amp;D &lt;ops&gt;</i>"


def test_telegram_link_escapes_href_and_text() -> None:
    out = escaping.tg_link('https://x.com/a?q="b"&c=1', "see <here>")
    # href quotes/amps escaped so the attribute can't be broken out of;
    # link text angle brackets escaped.
    assert 'href="https://x.com/a?q=&quot;b&quot;&amp;c=1"' in out
    assert ">see &lt;here&gt;</a>" in out
    assert out.startswith("<a href=")
    assert out.endswith("</a>")


def test_telegram_none_input_safe() -> None:
    assert escaping.escape_telegram_text(None) == ""


def test_telegram_tag_whitelist_is_exactly_b_i_a() -> None:
    # The §15.2 whitelist must not silently widen (no <script>, <img>, etc.).
    assert escaping.TELEGRAM_TAG_WHITELIST == ("b", "i", "a")
