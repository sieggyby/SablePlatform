"""C2.7 command-registry path (the additive slash-command routing C3.5c rides).

The registry gained a command-registration + dispatch path mirroring the callback
prefix routing: ``register_command_handler`` (per-verb or catch-all) +
``dispatch_command`` (parse → dedupe in one immediate_txn → route the CommandEvent
to the matching consumer OUTSIDE the txn). These tests pin that surface; the
end-to-end operator-command behavior is covered in tests/autocm.
"""
from __future__ import annotations

import pytest

from sable_platform.relay.bot.registry import (
    CommandEvent,
    RelayHandlerRegistry,
    parse_command,
)


# ---------------------------------------------------------------------------
# parse_command — the verb/args/argstr split.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/demote greeting", ("demote", ["greeting"], "greeting")),
        ("/kb-add price the price is fixed", ("kb-add", ["price", "the", "price", "is", "fixed"], "price the price is fixed")),
        ("/pause-client", ("pause-client", [], "")),
        ("/demote@nulo_bot greeting", ("demote", ["greeting"], "greeting")),
        ("  /incident-mode on  ", ("incident-mode", ["on"], "on")),
    ],
)
def test_parse_command_ok(raw, expected):
    assert parse_command(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "hello", "no slash here", "/", "/  "])
def test_parse_command_non_command(raw):
    assert parse_command(raw) is None


# ---------------------------------------------------------------------------
# dispatch_command — routing + dedupe + non-command fall-through.
# ---------------------------------------------------------------------------
def test_catch_all_command_handler_receives_command_event(sa_conn):
    reg = RelayHandlerRegistry(sa_conn)
    seen = []
    reg.register_command_handler(lambda e: seen.append(e))
    assert reg.has_command_handler is True

    routed = reg.dispatch_command(
        platform="telegram", update_id="u-1", text="/demote greeting",
        org_id="orgRM", chat_id="c", external_user_id="ext-1",
    )
    assert routed is True
    assert len(seen) == 1
    evt = seen[0]
    assert isinstance(evt, CommandEvent)
    assert evt.command == "demote"
    assert evt.args == ("greeting",)
    assert evt.argstr == "greeting"
    assert evt.external_user_id == "ext-1"


def test_per_verb_handler_beats_catch_all(sa_conn):
    reg = RelayHandlerRegistry(sa_conn)
    catch_all = []
    per_verb = []
    reg.register_command_handler(lambda e: catch_all.append(e))
    reg.register_command_handler(lambda e: per_verb.append(e), command="pause-client")

    reg.dispatch_command(platform="telegram", update_id="a", text="/pause-client", org_id="o")
    reg.dispatch_command(platform="telegram", update_id="b", text="/demote x", org_id="o")
    assert [e.command for e in per_verb] == ["pause-client"]
    assert [e.command for e in catch_all] == ["demote"]


def test_dispatch_command_dedupes_same_update_id(sa_conn):
    reg = RelayHandlerRegistry(sa_conn)
    seen = []
    reg.register_command_handler(lambda e: seen.append(e))
    first = reg.dispatch_command(platform="telegram", update_id="dup", text="/demote x", org_id="o")
    second = reg.dispatch_command(platform="telegram", update_id="dup", text="/demote x", org_id="o")
    assert first is True and second is False
    assert len(seen) == 1  # redelivery not re-dispatched


def test_dispatch_command_returns_false_for_non_command(sa_conn):
    reg = RelayHandlerRegistry(sa_conn)
    reg.register_command_handler(lambda e: None)
    assert (
        reg.dispatch_command(platform="telegram", update_id="x", text="hello there", org_id="o")
        is False
    )


def test_dispatch_command_returns_false_when_no_handler(sa_conn):
    reg = RelayHandlerRegistry(sa_conn)
    assert reg.has_command_handler is False
    assert (
        reg.dispatch_command(platform="telegram", update_id="x", text="/demote x", org_id="o")
        is False
    )


def test_dispatch_command_rejects_unknown_platform(sa_conn):
    reg = RelayHandlerRegistry(sa_conn)
    reg.register_command_handler(lambda e: None)
    with pytest.raises(ValueError):
        reg.dispatch_command(platform="myspace", update_id="x", text="/demote x")


def test_command_handler_exception_is_swallowed(sa_conn):
    """A raising command handler must not crash the shared listener loop."""
    reg = RelayHandlerRegistry(sa_conn)

    def boom(_evt):
        raise RuntimeError("handler blew up")

    reg.register_command_handler(boom)
    # routed True (dedupe + dispatch happened), exception swallowed.
    assert (
        reg.dispatch_command(platform="telegram", update_id="x", text="/demote x", org_id="o")
        is True
    )
