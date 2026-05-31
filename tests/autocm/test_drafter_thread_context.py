"""C3.3 thread-context tests: last-N=5 multi-turn coherence + truncation.

The drafter's thread-context assembler:
  * pure :func:`truncate_thread_context` keeps only the most-recent N turns (the
    coherence-window cap that bounds the variable prompt bytes);
  * :func:`load_thread_context` reads the last N=5 NON-EMPTY messages for a chat,
    oldest-first, labels self vs other, excludes media-only rows, and can exclude
    the message currently being answered.

All offline SQLite; no LLM, no network.
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.autocm.drafter.thread_context import (
    OTHER_LABEL,
    SELF_LABEL,
    THREAD_CONTEXT_N,
    load_thread_context,
    truncate_thread_context,
)


# ---------------------------------------------------------------------------
# truncate_thread_context — the N=5 coherence cap (pure).
# ---------------------------------------------------------------------------
def test_truncate_keeps_most_recent_n_tail() -> None:
    turns = [f"m{i}" for i in range(10)]
    out = truncate_thread_context(turns, n=5)
    assert out == ["m5", "m6", "m7", "m8", "m9"]  # the TAIL (closest context)


def test_truncate_default_n_is_5() -> None:
    assert THREAD_CONTEXT_N == 5
    turns = [f"m{i}" for i in range(8)]
    assert len(truncate_thread_context(turns)) == 5


def test_truncate_shorter_than_n_unchanged() -> None:
    turns = ["a", "b"]
    assert truncate_thread_context(turns, n=5) == ["a", "b"]


def test_truncate_zero_or_negative_is_empty() -> None:
    assert truncate_thread_context(["a", "b"], n=0) == []
    assert truncate_thread_context(["a", "b"], n=-3) == []


def test_truncate_does_not_mutate_input() -> None:
    turns = [f"m{i}" for i in range(7)]
    _ = truncate_thread_context(turns, n=3)
    assert len(turns) == 7  # input untouched


# ---------------------------------------------------------------------------
# load_thread_context — the windowed DB read.
# ---------------------------------------------------------------------------
def _seed_chat(conn, org_id):
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id}
    )
    conn.execute(
        text(
            "INSERT INTO relay_chats (org_id, platform, chat_id, title) "
            "VALUES (:o, 'telegram', '-100', 'c')"
        ),
        {"o": org_id},
    )
    return conn.execute(
        text("SELECT id FROM relay_chats WHERE chat_id = '-100'")
    ).fetchone()[0]


def _member(conn, name):
    conn.execute(text("INSERT INTO relay_members (display_name) VALUES (:d)"), {"d": name})
    return conn.execute(
        text("SELECT id FROM relay_members WHERE display_name = :d ORDER BY id DESC"),
        {"d": name},
    ).fetchone()[0]


def _msg(conn, org_id, chat_id, member_id, body, emi):
    conn.execute(
        text(
            "INSERT INTO relay_messages "
            "(org_id, chat_id, member_id, platform, external_message_id, text) "
            "VALUES (:o, :c, :m, 'telegram', :emi, :t)"
        ),
        {"o": org_id, "c": chat_id, "m": member_id, "emi": emi, "t": body},
    )


def test_load_returns_last_n_oldest_first(sa_org) -> None:
    conn, org_id = sa_org
    chat_id = _seed_chat(conn, org_id)
    m = _member(conn, "asker")
    for i in range(8):
        _msg(conn, org_id, chat_id, m, f"msg {i}", f"e{i}")
    conn.commit()

    turns = load_thread_context(conn, chat_id, n=5)
    # exactly the last 5, oldest-first.
    assert turns == [f"{OTHER_LABEL}: msg {i}" for i in range(3, 8)]


def test_load_default_n_is_5(sa_org) -> None:
    conn, org_id = sa_org
    chat_id = _seed_chat(conn, org_id)
    m = _member(conn, "asker")
    for i in range(9):
        _msg(conn, org_id, chat_id, m, f"line {i}", f"e{i}")
    conn.commit()
    assert len(load_thread_context(conn, chat_id)) == THREAD_CONTEXT_N


def test_load_labels_self_vs_other(sa_org) -> None:
    conn, org_id = sa_org
    chat_id = _seed_chat(conn, org_id)
    asker = _member(conn, "asker")
    bot = _member(conn, "nulo")
    _msg(conn, org_id, chat_id, asker, "how does the vault work", "e0")
    _msg(conn, org_id, chat_id, bot, "deposit eth, get a share.", "e1")
    conn.commit()

    turns = load_thread_context(conn, chat_id, bot_member_id=bot)
    assert turns == [
        f"{OTHER_LABEL}: how does the vault work",
        f"{SELF_LABEL}: deposit eth, get a share.",
    ]


def test_load_excludes_media_only_empty_rows(sa_org) -> None:
    conn, org_id = sa_org
    chat_id = _seed_chat(conn, org_id)
    m = _member(conn, "asker")
    _msg(conn, org_id, chat_id, m, "real text", "e0")
    # media-only: NULL text + empty text — both must be excluded.
    conn.execute(
        text(
            "INSERT INTO relay_messages "
            "(org_id, chat_id, member_id, platform, external_message_id, text) "
            "VALUES (:o, :c, :m, 'telegram', 'e1', NULL)"
        ),
        {"o": org_id, "c": chat_id, "m": m},
    )
    _msg(conn, org_id, chat_id, m, "", "e2")
    _msg(conn, org_id, chat_id, m, "another", "e3")
    conn.commit()

    turns = load_thread_context(conn, chat_id)
    assert turns == [f"{OTHER_LABEL}: real text", f"{OTHER_LABEL}: another"]


def test_load_excludes_current_message_with_before_id(sa_org) -> None:
    conn, org_id = sa_org
    chat_id = _seed_chat(conn, org_id)
    m = _member(conn, "asker")
    for i in range(4):
        _msg(conn, org_id, chat_id, m, f"prior {i}", f"e{i}")
    conn.commit()
    # the message being answered is the most-recently inserted row.
    current_id = conn.execute(
        text("SELECT MAX(id) FROM relay_messages")
    ).fetchone()[0]

    turns = load_thread_context(conn, chat_id, before_message_row_id=current_id)
    # the current message (prior 3) is excluded; only 0,1,2 remain.
    assert turns == [f"{OTHER_LABEL}: prior {i}" for i in range(3)]


def test_load_empty_chat_is_empty(sa_org) -> None:
    conn, org_id = sa_org
    chat_id = _seed_chat(conn, org_id)
    conn.commit()
    assert load_thread_context(conn, chat_id) == []


def test_load_n_zero_is_empty(sa_org) -> None:
    conn, org_id = sa_org
    chat_id = _seed_chat(conn, org_id)
    m = _member(conn, "asker")
    _msg(conn, org_id, chat_id, m, "x", "e0")
    conn.commit()
    assert load_thread_context(conn, chat_id, n=0) == []


def test_load_scoped_to_chat(sa_org) -> None:
    conn, org_id = sa_org
    chat_id = _seed_chat(conn, org_id)
    # a SECOND chat in the same org; its messages must not bleed in.
    conn.execute(
        text(
            "INSERT INTO relay_chats (org_id, platform, chat_id, title) "
            "VALUES (:o, 'telegram', '-200', 'other')"
        ),
        {"o": org_id},
    )
    other_chat = conn.execute(
        text("SELECT id FROM relay_chats WHERE chat_id = '-200'")
    ).fetchone()[0]
    m = _member(conn, "asker")
    _msg(conn, org_id, chat_id, m, "in-chat", "e0")
    _msg(conn, org_id, other_chat, m, "other-chat", "e1")
    conn.commit()

    turns = load_thread_context(conn, chat_id)
    assert turns == [f"{OTHER_LABEL}: in-chat"]
