"""C2.2 exit/audit invariant: NO external API call inside any BEGIN IMMEDIATE.

PLAN §3.1 (line 145) is the load-bearing correctness property for the listener:
every external send (Telegram / Discord / SocialData / HTTP) happens OUTSIDE the
``BEGIN IMMEDIATE`` transaction. This test enforces it behaviorally: a sentinel
"external call" raises if invoked while the manual transaction is open, and we
assert the routing primitives never trip it.

Instrumentation works by monkeypatching the module-level :func:`immediate_txn`
that the binding helpers import, wrapping it so a tracker flag flips True on
entry (BEGIN IMMEDIATE) and False on exit (COMMIT/ROLLBACK). The real
``immediate_txn`` still runs underneath — we only observe the boundary.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from sable_platform.relay.bot import binding, discord_app, registry, telegram_app
from sable_platform.relay.bot.discord_app import DiscordListener
from sable_platform.relay.bot.registry import build_registry
from sable_platform.relay.bot.telegram_app import TelegramListener
from sable_platform.relay.bot.txn import immediate_txn


class _TxnTracker:
    """Tracks whether a BEGIN IMMEDIATE is currently open on the connection."""

    def __init__(self) -> None:
        self.in_txn = False
        self.commits = 0
        self.rollbacks = 0
        self.external_calls_during_txn = 0

    def external_send(self) -> None:
        """Stand-in for any Telegram/Discord/HTTP call.

        If this is ever invoked while a transaction is open, the §3.1 invariant
        is violated.
        """
        if self.in_txn:
            self.external_calls_during_txn += 1
            raise AssertionError(
                "external API call attempted INSIDE a BEGIN IMMEDIATE transaction"
            )


def _tracking_immediate_txn(tracker: _TxnTracker):
    """A drop-in for ``immediate_txn`` that flips the tracker around the real one."""
    from contextlib import contextmanager

    @contextmanager
    def _wrapped(conn):
        tracker.in_txn = True
        try:
            with immediate_txn(conn) as c:
                yield c
        except Exception:
            tracker.rollbacks += 1
            tracker.in_txn = False
            raise
        else:
            tracker.commits += 1
            tracker.in_txn = False

    return _wrapped


def _seed(conn, org_id, chat_id):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id})
    conn.execute(
        text(
            "INSERT INTO relay_chat_bindings (org_id, platform, chat_id, role, status) "
            "VALUES (:o, 'telegram', :c, 'operator', 'active')"
        ),
        {"o": org_id, "c": chat_id},
    )
    conn.commit()


def test_external_send_inside_txn_would_be_detected(sa_conn) -> None:
    # Meta-test: prove the tracker actually catches a violation, so the
    # green assertions below are meaningful (not vacuous).
    tracker = _TxnTracker()
    wrapped = _tracking_immediate_txn(tracker)
    with pytest.raises(AssertionError, match="INSIDE a BEGIN IMMEDIATE"):
        with wrapped(sa_conn):
            tracker.external_send()  # simulated leak — must be caught
    assert tracker.external_calls_during_txn == 1
    assert tracker.rollbacks == 1  # the leak rolled the txn back
    assert tracker.in_txn is False


def test_kick_binding_does_no_external_call_inside_txn(sa_conn, monkeypatch) -> None:
    _seed(sa_conn, "orgINV", "-700")
    tracker = _TxnTracker()
    monkeypatch.setattr(binding, "immediate_txn", _tracking_immediate_txn(tracker))
    # The binding cleanup runs entirely inside one BEGIN IMMEDIATE...
    result = binding.kick_chat_binding(sa_conn, "-700", platform="telegram")
    assert result.flipped is True
    # ...committed exactly once, with zero external calls attempted inside it.
    assert tracker.commits == 1
    assert tracker.rollbacks == 0
    assert tracker.external_calls_during_txn == 0
    # After the txn closed, the admin-notify (external) would run OUTSIDE — and
    # the flag is clear so it could not have been inside.
    assert tracker.in_txn is False
    tracker.external_send()  # the OUTSIDE-the-txn admin notify: no raise


def test_migration_does_no_external_call_inside_txn(sa_conn, monkeypatch) -> None:
    _seed(sa_conn, "orgINV2", "-800")
    tracker = _TxnTracker()
    monkeypatch.setattr(binding, "immediate_txn", _tracking_immediate_txn(tracker))
    result = binding.migrate_chat_binding(sa_conn, "-800", "-800999")
    assert result.migrated is True
    assert tracker.commits == 1
    assert tracker.external_calls_during_txn == 0
    assert tracker.in_txn is False


# ---------------------------------------------------------------------------
# Dedupe-gate dispatch primitives: lock the §3.1 invariant for the paths that
# open a BEGIN IMMEDIATE around the persistent dedupe claim, not just the
# binding helpers. A future edit that slips a send inside one of these txns
# (e.g. eagerly deferring before COMMIT) must trip the tracker.
# ---------------------------------------------------------------------------
def _reaction_update(update_id, user_id):
    user = None if user_id is None else SimpleNamespace(id=user_id)
    return SimpleNamespace(
        update_id=update_id,
        message_reaction=SimpleNamespace(user=user),
    )


def test_tg_route_reaction_does_no_external_call_inside_txn(sa_conn, monkeypatch) -> None:
    tracker = _TxnTracker()
    monkeypatch.setattr(telegram_app, "immediate_txn", _tracking_immediate_txn(tracker))
    listener = TelegramListener(sa_conn)
    # New + attributable reaction: claimed inside one BEGIN IMMEDIATE, accepted.
    assert listener.route_reaction(_reaction_update(1001, user_id=42)) is True
    assert tracker.commits == 1
    assert tracker.external_calls_during_txn == 0
    assert tracker.in_txn is False
    # The Discord defer()/followup + TG admin-notify are OUTSIDE the txn — the
    # flag is clear so a send here could not have been inside the transaction.
    tracker.external_send()  # no raise


def test_discord_route_interaction_does_no_external_call_inside_txn(sa_conn, monkeypatch) -> None:
    tracker = _TxnTracker()
    monkeypatch.setattr(discord_app, "immediate_txn", _tracking_immediate_txn(tracker))
    listener = DiscordListener(sa_conn)
    res = listener.route_interaction("1234567890123456789")
    assert res.should_defer is True
    assert tracker.commits == 1
    assert tracker.external_calls_during_txn == 0
    assert tracker.in_txn is False
    # The defer()/followup.send happens only AFTER route_interaction returns.
    tracker.external_send()  # no raise


def test_discord_route_reaction_event_does_no_external_call_inside_txn(sa_conn, monkeypatch) -> None:
    tracker = _TxnTracker()
    monkeypatch.setattr(discord_app, "immediate_txn", _tracking_immediate_txn(tracker))
    listener = DiscordListener(sa_conn)
    assert listener.route_reaction_event("evt-77") is True
    assert tracker.commits == 1
    assert tracker.external_calls_during_txn == 0
    assert tracker.in_txn is False


# ---------------------------------------------------------------------------
# SA/driver tracker consistency: immediate_txn drives the transaction THROUGH
# SQLAlchemy so conn.in_transaction() reflects reality after every block, even
# across many blocks on one long-lived (listener) connection — and a read run
# just before the txn (autobegin) does not break the next block.
# ---------------------------------------------------------------------------
def test_in_transaction_consistent_after_immediate_txn(sa_conn) -> None:
    assert sa_conn.in_transaction() is False
    with immediate_txn(sa_conn):
        sa_conn.exec_driver_sql(
            "INSERT INTO relay_processed_updates (platform, update_id) "
            "VALUES ('telegram', 'c1')"
        )
    # The driver committed AND SA's tracker reset — no permanent desync.
    assert sa_conn.in_transaction() is False
    # A second block on the SAME connection (the production reuse pattern) is
    # not blocked by a stale transaction.
    with immediate_txn(sa_conn):
        sa_conn.exec_driver_sql(
            "INSERT INTO relay_processed_updates (platform, update_id) "
            "VALUES ('telegram', 'c2')"
        )
    assert sa_conn.in_transaction() is False
    rows = sa_conn.exec_driver_sql(
        "SELECT COUNT(*) FROM relay_processed_updates"
    ).fetchone()[0]
    assert rows == 2


def test_in_transaction_consistent_after_rollback(sa_conn) -> None:
    with pytest.raises(RuntimeError):
        with immediate_txn(sa_conn):
            sa_conn.exec_driver_sql(
                "INSERT INTO relay_processed_updates (platform, update_id) "
                "VALUES ('telegram', 'r1')"
            )
            raise RuntimeError("boom inside txn")
    # Rollback through SA leaves the tracker consistent (not stuck True).
    assert sa_conn.in_transaction() is False
    # And a fresh block still works.
    with immediate_txn(sa_conn):
        assert sa_conn.exec_driver_sql(
            "SELECT COUNT(*) FROM relay_processed_updates"
        ).fetchone()[0] == 0


def test_immediate_txn_clears_stale_autobegin_from_prior_read(sa_conn) -> None:
    # A SELECT before the txn leaves a read autobegin open (SA 2.0). The next
    # immediate_txn must clear it rather than collide ("cannot start a
    # transaction within a transaction"). This is the binding._kicked_after_threshold
    # read-before-txn pattern.
    sa_conn.exec_driver_sql("SELECT 1")
    assert sa_conn.in_transaction() is True  # stale read autobegin
    with immediate_txn(sa_conn):
        sa_conn.exec_driver_sql(
            "INSERT INTO relay_processed_updates (platform, update_id) "
            "VALUES ('telegram', 's1')"
        )
    assert sa_conn.in_transaction() is False
    assert sa_conn.exec_driver_sql(
        "SELECT update_id FROM relay_processed_updates"
    ).fetchone()[0] == "s1"


# ---------------------------------------------------------------------------
# Registry dispatch primitives (C2.7): the §3.1 invariant for the ACTUAL paths
# that invoke the in-process AutoCM/LLM consumer. The registry docstring claims
# to reuse the C2.2 audit invariant verbatim — "No AutoCM/LLM work ever runs
# inside a BEGIN IMMEDIATE" — but the three dispatch_* methods (the consumer
# entry points) were unpinned: a future edit that moved the handler call inside
# the `with immediate_txn(...)` block (e.g. to read chat_row_id without
# re-fetching) would pass every other relay test while silently running the
# AutoCM pipeline inside SQLite's RESERVED write lock, serializing the shared
# listener connection. These tests fail the moment a consumer is invoked while
# the tracker reports in_txn == True.
#
# The instrumentation differs slightly from the binding/telegram_app tests: the
# tracker's flag is observed FROM INSIDE the registered handler (the registry
# IS the code under test), so external_send() is called by the handler itself.
# ---------------------------------------------------------------------------
def _seed_org_client(conn, org_id):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id})
    conn.commit()


def test_registry_dispatch_message_invokes_consumer_outside_txn(sa_conn, monkeypatch) -> None:
    _seed_org_client(sa_conn, "orgTXN1")
    tracker = _TxnTracker()
    monkeypatch.setattr(registry, "immediate_txn", _tracking_immediate_txn(tracker))
    reg = build_registry(sa_conn)
    # The handler is AutoCM's pipeline entry. If the registry ever called it
    # inside the BEGIN IMMEDIATE, tracker.in_txn would be True here and
    # external_send() (the LLM/draft/HTTP stand-in) would raise.
    reg.register_message_handler(lambda _msg: tracker.external_send())
    dispatched = reg.dispatch_message(
        platform="telegram",
        update_id=20001,
        org_id="orgTXN1",
        chat_id="-2001",
        external_message_id="m1",
        text="gm",
    )
    assert dispatched is True
    assert tracker.commits == 1
    assert tracker.external_calls_during_txn == 0
    assert tracker.in_txn is False


def test_registry_dispatch_member_event_invokes_consumer_outside_txn(sa_conn, monkeypatch) -> None:
    _seed_org_client(sa_conn, "orgTXN2")
    tracker = _TxnTracker()
    monkeypatch.setattr(registry, "immediate_txn", _tracking_immediate_txn(tracker))
    reg = build_registry(sa_conn)
    reg.register_member_event_handler(lambda _evt: tracker.external_send())
    dispatched = reg.dispatch_member_event(
        platform="telegram",
        update_id=20002,
        org_id="orgTXN2",
        chat_id="-2002",
        event="join",
        external_user_id="u-2002",
    )
    assert dispatched is True
    assert tracker.commits == 1
    assert tracker.external_calls_during_txn == 0
    assert tracker.in_txn is False


def test_registry_dispatch_callback_invokes_consumer_outside_txn(sa_conn, monkeypatch) -> None:
    _seed_org_client(sa_conn, "orgTXN3")
    tracker = _TxnTracker()
    monkeypatch.setattr(registry, "immediate_txn", _tracking_immediate_txn(tracker))
    reg = build_registry(sa_conn)
    reg.register_callback_handler(lambda _evt: tracker.external_send(), prefix="autocm:")
    routed = reg.dispatch_callback(
        platform="telegram",
        update_id=20003,
        callback_id="cbq-2003",
        data="autocm:approve:1",
        org_id="orgTXN3",
        chat_id="-2003",
    )
    assert routed is True
    assert tracker.commits == 1
    assert tracker.external_calls_during_txn == 0
    assert tracker.in_txn is False
