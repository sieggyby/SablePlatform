"""C2.7 exit criterion: the documented API surface, asserted by signatures.

The C2.7 exit/audit bullet requires that the handler-registry / provisioning /
typing / callback API surface be **asserted by an interface/import test** — this
is the contract every C3 chunk (C3.1 message handler, C3.5b review queue, C3.6
publisher callbacks) builds against. Downstream *consumability* is verified when
those chunks build; THIS test pins the signatures so the surface cannot silently
drift out from under them.

It imports through the package re-export (``sable_platform.relay.bot``) — the
path AutoCM uses — so the lazy ``__getattr__`` wiring is also covered.
"""
from __future__ import annotations

import inspect

import sable_platform.relay.bot as relay_bot
from sable_platform.relay import db as relay_db


# ---------------------------------------------------------------------------
# Package-level re-export surface (the import path AutoCM uses)
# ---------------------------------------------------------------------------
def test_package_reexports_c27_surface() -> None:
    for name in (
        "RelayHandlerRegistry",
        "RelayConsumer",
        "InboundMessage",
        "MemberEvent",
        "CallbackEvent",
        "TypingIndicator",
        "build_registry",
    ):
        assert name in relay_bot.__all__, f"{name} missing from relay.bot.__all__"
        assert getattr(relay_bot, name) is not None, f"{name} not importable"


# ---------------------------------------------------------------------------
# Registry registration + dispatch + provisioning signatures
# ---------------------------------------------------------------------------
def test_registry_method_signatures() -> None:
    Registry = relay_bot.RelayHandlerRegistry
    methods = {
        "register_message_handler": ["self", "handler"],
        "register_member_event_handler": ["self", "handler"],
        "register_callback_handler": ["self", "handler"],
        "register_consumer": ["self", "consumer"],
        "dispatch_message": ["self"],
        "dispatch_member_event": ["self"],
        "dispatch_callback": ["self"],
        "get_operator_chat": ["self", "org_id"],
        "provision_operator_chat": ["self", "org_id", "chat_id"],
    }
    for name, required in methods.items():
        assert hasattr(Registry, name), f"RelayHandlerRegistry missing {name}"
        params = list(inspect.signature(getattr(Registry, name)).parameters)
        for r in required:
            assert r in params, f"{name} missing param {r}; has {params}"


def test_dispatch_message_keyword_contract() -> None:
    # The dispatch contract every C3.1 caller relies on (keyword args).
    params = inspect.signature(
        relay_bot.RelayHandlerRegistry.dispatch_message
    ).parameters
    for kw in (
        "platform",
        "update_id",
        "org_id",
        "chat_id",
        "external_message_id",
        "external_user_id",
        "member_id",
        "text",
    ):
        assert kw in params, f"dispatch_message missing kw {kw}"


def test_dispatch_callback_keyword_contract() -> None:
    params = inspect.signature(
        relay_bot.RelayHandlerRegistry.dispatch_callback
    ).parameters
    for kw in ("platform", "update_id", "callback_id", "data"):
        assert kw in params, f"dispatch_callback missing kw {kw}"


def test_dispatch_member_event_keyword_contract() -> None:
    params = inspect.signature(
        relay_bot.RelayHandlerRegistry.dispatch_member_event
    ).parameters
    for kw in ("platform", "update_id", "org_id", "chat_id", "event", "external_user_id"):
        assert kw in params, f"dispatch_member_event missing kw {kw}"


# ---------------------------------------------------------------------------
# Event dataclass field contracts (the FK-target fields C3.0 references)
# ---------------------------------------------------------------------------
def test_inbound_message_fields() -> None:
    fields = set(relay_bot.InboundMessage.__dataclass_fields__)
    # message_row_id → autocm_drafts.source_message_id; chat_row_id →
    # autocm_drafts.source_chat_id (C1.1 FK reconciliation).
    for f in (
        "org_id",
        "platform",
        "chat_id",
        "chat_row_id",
        "external_message_id",
        "external_user_id",
        "member_id",
        "text",
        "message_row_id",
    ):
        assert f in fields, f"InboundMessage missing field {f}"


def test_member_event_fields() -> None:
    fields = set(relay_bot.MemberEvent.__dataclass_fields__)
    for f in ("org_id", "platform", "chat_id", "event", "external_user_id"):
        assert f in fields, f"MemberEvent missing field {f}"


def test_callback_event_fields() -> None:
    fields = set(relay_bot.CallbackEvent.__dataclass_fields__)
    for f in ("platform", "callback_id", "data"):
        assert f in fields, f"CallbackEvent missing field {f}"


# ---------------------------------------------------------------------------
# TypingIndicator surface
# ---------------------------------------------------------------------------
def test_typing_indicator_surface() -> None:
    TI = relay_bot.TypingIndicator
    assert hasattr(TI, "set")
    assert hasattr(TI, "clear")
    assert hasattr(TI, "supported")
    # set/clear are coroutine functions (awaitable call sites in AutoCM).
    assert inspect.iscoroutinefunction(TI.set)
    assert inspect.iscoroutinefunction(TI.clear)


# ---------------------------------------------------------------------------
# DB-side provisioning + persistence helpers (the SQL home, C2.1 layering)
# ---------------------------------------------------------------------------
def test_db_layer_exposes_provisioning_and_persistence() -> None:
    for name in (
        "get_operator_chat",
        "provision_operator_chat",
        "upsert_chat",
        "persist_inbound_message",
    ):
        assert hasattr(relay_db, name), f"relay.db missing {name}"
