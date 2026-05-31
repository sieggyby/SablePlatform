"""C2.7 AutoCM-facing handler-registration API tests.

Exercises :mod:`sable_platform.relay.bot.registry` — the registry that sits ON
TOP of the C2.2 dispatch primitive. Covers the four C2.7 surfaces + the exit
criterion (the documented API signatures asserted by an interface/import test):

  (a) listener handler-registry — a registered handler receives a dispatched
      message; a registered member-event handler receives a member-JOIN event;
      dedupe drops a redelivered message/event; the inbound message is persisted
      into relay_messages with the FK-target row ids
  (b) operator-chat provisioning — provision then resolve returns the operator
      chat; re-provisioning a new chat re-points the single active binding
  (c) TG typing-indicator set/clear — set sends on telegram, clear no-ops
      (auto-expire), and BOTH no-op gracefully on an unsupported transport (x)
  (d) inline-button callback routing — a callback routes to the correct
      registered consumer by data-prefix; a duplicate callback is dropped
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from sable_platform.relay.bot.registry import (
    CallbackEvent,
    InboundMessage,
    MemberEvent,
    RelayConsumer,
    RelayHandlerRegistry,
    TypingIndicator,
    build_registry,
)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
def _seed_org_client(conn, org_id):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id}
    )
    conn.commit()


# ---------------------------------------------------------------------------
# (a) handler-registry: message dispatch
# ---------------------------------------------------------------------------
def test_registered_handler_receives_dispatched_message(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgA")
    reg = build_registry(sa_conn)
    received: list[InboundMessage] = []
    reg.register_message_handler(received.append)

    dispatched = reg.dispatch_message(
        platform="telegram",
        update_id=1001,
        org_id="orgA",
        chat_id="-100",
        external_message_id="55",
        external_user_id="777",
        text="gm",
    )
    assert dispatched is True
    assert len(received) == 1
    msg = received[0]
    assert isinstance(msg, InboundMessage)
    assert msg.org_id == "orgA"
    assert msg.text == "gm"
    assert msg.message_row_id > 0
    assert msg.chat_row_id > 0

    # The inbound message was persisted PER message into relay_messages, with
    # the FK-target row id matching what the consumer received.
    row = sa_conn.execute(
        text("SELECT id, org_id, text, chat_id FROM relay_messages WHERE external_message_id='55'")
    ).fetchone()
    assert row is not None
    assert row[0] == msg.message_row_id
    assert row[1] == "orgA"
    assert row[2] == "gm"
    assert row[3] == msg.chat_row_id


def test_duplicate_message_update_id_dropped(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgDup")
    reg = build_registry(sa_conn)
    received: list[InboundMessage] = []
    reg.register_message_handler(received.append)

    first = reg.dispatch_message(
        platform="telegram",
        update_id=2002,
        org_id="orgDup",
        chat_id="-200",
        external_message_id="60",
        text="hi",
    )
    second = reg.dispatch_message(
        platform="telegram",
        update_id=2002,  # same update_id redelivered
        org_id="orgDup",
        chat_id="-200",
        external_message_id="60",
        text="hi",
    )
    assert first is True
    assert second is False  # deduped
    assert len(received) == 1  # consumer invoked exactly once
    # Exactly one persisted relay_messages row.
    count = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_messages WHERE external_message_id='60'")
    ).fetchone()[0]
    assert count == 1


def test_same_message_under_different_update_id_dispatched_once(sa_conn) -> None:
    # A single underlying message can arrive under MULTIPLE update_ids: an edited
    # TG message carries a NEW update_id for the SAME message_id, and a long-poll
    # offset reset can re-deliver. Both pass the (platform, update_id) dedupe gate
    # as "new", but persist_inbound_message dedupes the ROW on relay_messages_unique
    # — so the AutoCM consumer must fire EXACTLY ONCE (exactly-once-per-message),
    # never twice on the same message_row_id (downstream C3.1 assumes one-shot).
    _seed_org_client(sa_conn, "orgEdit")
    reg = build_registry(sa_conn)
    received: list[InboundMessage] = []
    reg.register_message_handler(received.append)

    first = reg.dispatch_message(
        platform="telegram",
        update_id=2100,
        org_id="orgEdit",
        chat_id="-210",
        external_message_id="61",
        text="gm",
    )
    # Same (platform, chat, external_message_id) but a DIFFERENT update_id — the
    # update_id dedupe gate lets it through, the row dedupe catches it.
    second = reg.dispatch_message(
        platform="telegram",
        update_id=2200,  # new update_id, same underlying message
        org_id="orgEdit",
        chat_id="-210",
        external_message_id="61",
        text="gm (edited)",
    )
    assert first is True
    assert second is False  # already-persisted row → NOT re-dispatched
    assert len(received) == 1  # consumer fired exactly once
    # Both saw the same persisted row; exactly one relay_messages row exists.
    count = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_messages WHERE external_message_id='61'")
    ).fetchone()[0]
    assert count == 1


def test_dispatch_without_registered_handler_is_noop(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgNoH")
    reg = build_registry(sa_conn)
    # No handler registered → returns False but STILL persists the message (the
    # relay_messages corpus is independent of whether AutoCM is wired).
    dispatched = reg.dispatch_message(
        platform="telegram",
        update_id=3003,
        org_id="orgNoH",
        chat_id="-300",
        external_message_id="70",
        text="x",
    )
    assert dispatched is False
    count = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_messages WHERE external_message_id='70'")
    ).fetchone()[0]
    assert count == 1


def test_handler_exception_does_not_crash_dispatch(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgErr")
    reg = build_registry(sa_conn)

    def _boom(_msg):
        raise RuntimeError("autocm pipeline blew up")

    reg.register_message_handler(_boom)
    # A consumer exception is swallowed so one bad message cannot kill the
    # shared listener loop; dispatch still reports True (it was dispatched).
    assert (
        reg.dispatch_message(
            platform="telegram",
            update_id=4004,
            org_id="orgErr",
            chat_id="-400",
            external_message_id="80",
            text="boom",
        )
        is True
    )


# ---------------------------------------------------------------------------
# (a) handler-registry: member-JOIN/leave dispatch (greeting flow)
# ---------------------------------------------------------------------------
def test_registered_handler_receives_member_join(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgJoin")
    reg = build_registry(sa_conn)
    events: list[MemberEvent] = []
    reg.register_member_event_handler(events.append)

    dispatched = reg.dispatch_member_event(
        platform="telegram",
        update_id=5005,
        org_id="orgJoin",
        chat_id="-500",
        event="join",
        external_user_id="9001",
        display_name="newbie",
    )
    assert dispatched is True
    assert len(events) == 1
    assert events[0].event == "join"
    assert events[0].external_user_id == "9001"
    assert events[0].org_id == "orgJoin"


def test_member_event_dedupes_redelivery(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgJoin2")
    reg = build_registry(sa_conn)
    events: list[MemberEvent] = []
    reg.register_member_event_handler(events.append)

    reg.dispatch_member_event(
        platform="telegram",
        update_id=6006,
        org_id="orgJoin2",
        chat_id="-600",
        event="join",
        external_user_id="9002",
    )
    # Redelivered join → not greeted twice.
    again = reg.dispatch_member_event(
        platform="telegram",
        update_id=6006,
        org_id="orgJoin2",
        chat_id="-600",
        event="join",
        external_user_id="9002",
    )
    assert again is False
    assert len(events) == 1


def test_member_event_leave_dispatched(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgLeave")
    reg = build_registry(sa_conn)
    events: list[MemberEvent] = []
    reg.register_member_event_handler(events.append)
    assert (
        reg.dispatch_member_event(
            platform="telegram",
            update_id=7007,
            org_id="orgLeave",
            chat_id="-700",
            event="leave",
            external_user_id="9003",
        )
        is True
    )
    assert events[0].event == "leave"


# ---------------------------------------------------------------------------
# (b) operator-chat provisioning (HITL surface)
# ---------------------------------------------------------------------------
def test_provision_then_resolve_operator_chat(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgOp")
    reg = build_registry(sa_conn)
    # Not yet provisioned.
    assert reg.get_operator_chat("orgOp") is None
    chat = reg.provision_operator_chat("orgOp", "-9001", title="orgOp ops")
    assert chat == "-9001"
    assert reg.get_operator_chat("orgOp") == "-9001"
    # The chat-id surface row exists too (FK target for autocm_drafts.source_chat_id).
    row = sa_conn.execute(
        text("SELECT id FROM relay_chats WHERE chat_id='-9001' AND platform='telegram'")
    ).fetchone()
    assert row is not None


def test_provision_is_idempotent(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgOp2")
    reg = build_registry(sa_conn)
    reg.provision_operator_chat("orgOp2", "-9100")
    reg.provision_operator_chat("orgOp2", "-9100")  # same chat again
    # Exactly one active operator binding.
    n = sa_conn.execute(
        text(
            "SELECT COUNT(*) FROM relay_chat_bindings "
            "WHERE org_id='orgOp2' AND role='operator' AND status='active'"
        )
    ).fetchone()[0]
    assert n == 1


def test_provision_repoints_to_new_operator_chat(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgOp3")
    reg = build_registry(sa_conn)
    reg.provision_operator_chat("orgOp3", "-9200")
    reg.provision_operator_chat("orgOp3", "-9201")  # different chat → re-point
    # Only the new chat is the active operator binding (partial-unique index
    # would have rejected two active operator bindings).
    assert reg.get_operator_chat("orgOp3") == "-9201"
    active = sa_conn.execute(
        text(
            "SELECT chat_id FROM relay_chat_bindings "
            "WHERE org_id='orgOp3' AND role='operator' AND status='active'"
        )
    ).fetchall()
    assert active == [("-9201",)]
    # The old one was disabled, not left dangling active.
    old = sa_conn.execute(
        text("SELECT status FROM relay_chat_bindings WHERE chat_id='-9200'")
    ).fetchone()[0]
    assert old == "disabled"


# ---------------------------------------------------------------------------
# (c) TG typing-indicator set/clear — graceful no-op on unsupported transport
# ---------------------------------------------------------------------------
class _FakeTGBot:
    def __init__(self):
        self.actions = []

    async def send_chat_action(self, chat_id, action):
        self.actions.append((chat_id, action))


def test_typing_set_sends_on_telegram() -> None:
    bot = _FakeTGBot()
    ti = TypingIndicator("telegram", bot=bot)
    assert ti.supported is True
    sent = asyncio.run(ti.set("-100"))
    assert sent is True
    assert bot.actions == [("-100", "typing")]


def test_typing_clear_is_noop_autoexpire() -> None:
    bot = _FakeTGBot()
    ti = TypingIndicator("telegram", bot=bot)
    # TG typing auto-expires (~5s) — there is no explicit stop call; clear is a
    # safe no-op (returns False = nothing sent) and never raises.
    assert asyncio.run(ti.clear("-100")) is False
    assert bot.actions == []


def test_typing_noops_gracefully_on_unsupported_transport() -> None:
    # X has no typing primitive — set/clear must no-op gracefully (no raise).
    ti = TypingIndicator("x")
    assert ti.supported is False
    assert asyncio.run(ti.set("anything")) is False
    assert asyncio.run(ti.clear("anything")) is False


def test_typing_noops_when_bot_missing_primitive() -> None:
    # A "supported" transport whose injected client lacks the primitive still
    # no-ops gracefully rather than raising AttributeError.
    ti = TypingIndicator("telegram", bot=object())
    assert asyncio.run(ti.set("-1")) is False


# ---------------------------------------------------------------------------
# (d) inline-button callback routing back to the registered consumer
# ---------------------------------------------------------------------------
def test_callback_routes_to_correct_consumer_by_prefix(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgCB")
    reg = build_registry(sa_conn)
    autocm_cbs: list[CallbackEvent] = []
    other_cbs: list[CallbackEvent] = []
    reg.register_callback_handler(autocm_cbs.append, prefix="autocm:")
    reg.register_callback_handler(other_cbs.append, prefix="relay:")

    routed = reg.dispatch_callback(
        platform="telegram",
        update_id=8008,
        callback_id="cbq-1",
        data="autocm:approve:1234",
        org_id="orgCB",
        chat_id="-9001",
    )
    assert routed is True
    assert len(autocm_cbs) == 1
    assert autocm_cbs[0].data == "autocm:approve:1234"
    assert other_cbs == []  # the relay-prefixed consumer did NOT receive it


def test_callback_dedupes_redelivery(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgCB2")
    reg = build_registry(sa_conn)
    cbs: list[CallbackEvent] = []
    reg.register_callback_handler(cbs.append, prefix="autocm:")
    reg.dispatch_callback(
        platform="telegram",
        update_id=9009,
        callback_id="cbq-2",
        data="autocm:demote:5",
    )
    again = reg.dispatch_callback(
        platform="telegram",
        update_id=9009,  # redelivered CallbackQuery
        callback_id="cbq-2",
        data="autocm:demote:5",
    )
    assert again is False
    assert len(cbs) == 1  # the [Demote] action applied exactly once


def test_callback_no_matching_consumer_returns_false(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgCB3")
    reg = build_registry(sa_conn)
    reg.register_callback_handler(lambda _e: None, prefix="autocm:")
    routed = reg.dispatch_callback(
        platform="telegram",
        update_id=10010,
        callback_id="cbq-3",
        data="unknown:thing",  # no prefix match, no default handler
    )
    assert routed is False


def test_default_callback_handler_catches_unprefixed(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgCB4")
    reg = build_registry(sa_conn)
    seen: list[CallbackEvent] = []
    reg.register_callback_handler(seen.append)  # default (prefix=None)
    routed = reg.dispatch_callback(
        platform="telegram",
        update_id=11011,
        callback_id="cbq-4",
        data="whatever",
    )
    assert routed is True
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# register_consumer convenience + Protocol
# ---------------------------------------------------------------------------
def test_register_consumer_wires_all_three_surfaces(sa_conn) -> None:
    _seed_org_client(sa_conn, "orgC")
    reg = build_registry(sa_conn)

    class _Consumer:
        def __init__(self):
            self.msgs = []
            self.events = []
            self.cbs = []

        def on_message(self, m):
            self.msgs.append(m)

        def on_member_event(self, e):
            self.events.append(e)

        def on_callback(self, c):
            self.cbs.append(c)

    consumer = _Consumer()
    assert isinstance(consumer, RelayConsumer)  # runtime_checkable Protocol
    reg.register_consumer(consumer, prefix="autocm:")
    assert reg.has_message_handler is True
    assert reg.has_member_handler is True

    reg.dispatch_message(
        platform="telegram",
        update_id=12012,
        org_id="orgC",
        chat_id="-120",
        external_message_id="120",
        text="m",
    )
    reg.dispatch_member_event(
        platform="telegram",
        update_id=12013,
        org_id="orgC",
        chat_id="-120",
        event="join",
        external_user_id="u1",
    )
    reg.dispatch_callback(
        platform="telegram",
        update_id=12014,
        callback_id="cbq",
        data="autocm:x",
    )
    assert len(consumer.msgs) == 1
    assert len(consumer.events) == 1
    assert len(consumer.cbs) == 1
