"""The relay entrypoints must hand relay code a RAW SQLAlchemy connection.

``get_db()`` returns a ``CompatConnection``, the sqlite3-compatible wrapper. The relay
write boundary needs SQLAlchemy transaction methods that wrapper does not carry:
``immediate_txn`` calls ``conn.in_transaction()`` and ``conn.exec_driver_sql()``, and its
PostgreSQL branch calls ``conn.execution_options()``. Both entrypoints opened the
connection with ``get_db()``, so the first message would have raised
``AttributeError: 'CompatConnection' object has no attribute 'in_transaction'``.

Neither script is deployed: no ``relay`` systemd unit exists on the production host, and
the poller's transport seams raise ``NotImplementedError`` before any database work. The
defect was latent, and it would have fired on the first message after someone set a bot
token.

Two rules are pinned here, and the second was learned the hard way.

**Drive ``main()``, not the helper.** A break test proved a test calling the connection
helper directly passes against the very defect it was written to catch, because reverting
``main()`` to ``get_db()`` leaves the helper untouched. The probe below runs at the
hand-off point, on the live connection, because ``main()`` closes it in a ``finally``.

**Prove the database is USABLE, not just that the object has the right type.** The first
fix used ``get_engine(...).connect()`` directly, which skips the SQLite ``mkdir`` and
``ensure_schema`` that ``get_db`` performs. A type-only assertion passed anyway, while a
fresh database would have failed on the first query with ``no such table``. The probe
therefore reads a real relay table. ``get_raw_db`` exists to keep the two in step.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import create_engine

import scripts.run_relay_bot as run_relay_bot
import scripts.run_relay_poller as run_relay_poller
from sable_platform.db.compat_conn import CompatConnection
from sable_platform.relay.bot.txn import immediate_txn

ENTRYPOINTS = [run_relay_bot, run_relay_poller]
IDS = ["run_relay_bot", "run_relay_poller"]
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"

# Every SQLAlchemy method the relay write path calls on the connection it is given.
# ``bot/txn.py`` uses all three; ``feed/publisher.py`` and ``bot/dedupe.py`` use the first two.
RELAY_METHODS = ("in_transaction", "exec_driver_sql", "execution_options")

# The first table ``poll_all_enabled`` reads, and the one ``immediate_txn`` writes through.
RELAY_TABLES = ("relay_clients", "relay_processed_updates")


class _Stop(Exception):
    """Breaks out of the poller's ``main()`` once the probe has run."""


@pytest.fixture()
def fresh_sqlite_target(tmp_path, monkeypatch):
    """A database file that does NOT exist yet, under a directory that does not either.

    Both halves matter. A pre-created file would hide a missing ``ensure_schema`` and a
    pre-existing directory would hide a missing ``mkdir``.

    This goes through ``SABLE_DB_PATH`` rather than ``SABLE_DATABASE_URL`` because that is
    the branch where ``get_db`` does the ``mkdir``. Setting an explicit ``sqlite://`` URL
    skips it in ``get_db`` too, so requiring the directory there would hold ``get_raw_db``
    to a contract its twin does not meet.
    """
    target = tmp_path / "nested" / "relay.db"
    assert not target.parent.exists()
    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SABLE_DB_PATH", str(target))
    return target


def _probe_at_handoff(module, monkeypatch, probe):
    """Run *probe* on the connection ``main()`` hands to relay code, and return its result.

    The probe runs at the hand-off point rather than afterwards, because ``main()`` closes
    the connection in a ``finally`` and a closed SQLAlchemy connection raises
    ``ResourceClosedError`` on every method that matters here.
    """
    results = []

    if module is run_relay_bot:
        # ``main()`` calls ``_build_listeners(conn, settings)`` and returns 1 when both
        # transports come back None, so this needs no exception to stop it.
        def _build_listeners(conn, settings):
            results.append(probe(conn))
            return (None, None, None)

        monkeypatch.setattr(module, "_build_listeners", _build_listeners)
        assert module.main() == 1
    else:
        # The poller's first use of the connection is ``_build_socialdata_client(conn)``.
        def _build_socialdata_client(conn):
            results.append(probe(conn))
            raise _Stop

        monkeypatch.setattr(module, "_build_socialdata_client", _build_socialdata_client)
        with pytest.raises(_Stop):
            module.main()

    assert len(results) == 1, f"{module.__name__}: main() did not reach the hand-off point"
    return results[0]


@pytest.mark.parametrize("module", ENTRYPOINTS, ids=IDS)
def test_main_hands_relay_a_connection_that_survives_immediate_txn(
    module, fresh_sqlite_target, monkeypatch
):
    """Validate against the BUG. The old wiring raised AttributeError on this line."""

    def probe(conn):
        with immediate_txn(conn):
            pass
        return conn.in_transaction()

    assert _probe_at_handoff(module, monkeypatch, probe) is False


@pytest.mark.parametrize("module", ENTRYPOINTS, ids=IDS)
def test_main_hands_relay_a_database_it_can_actually_query(
    module, fresh_sqlite_target, monkeypatch
):
    """The SECOND defect: a raw connection with no ``ensure_schema`` behind it.

    Reading a real relay table is what separates "the object has the right methods" from
    "the database is usable". Without the setup this fails with ``no such table``.
    """
    counts = _probe_at_handoff(
        module,
        monkeypatch,
        lambda conn: {
            t: conn.exec_driver_sql(f"SELECT COUNT(*) FROM {t}").scalar() for t in RELAY_TABLES
        },
    )
    assert counts == dict.fromkeys(RELAY_TABLES, 0)


@pytest.mark.parametrize("module", ENTRYPOINTS, ids=IDS)
def test_main_creates_the_database_file_and_its_parent(
    module, fresh_sqlite_target, monkeypatch
):
    """``get_db`` did the ``mkdir``. A bare ``get_engine(...).connect()`` does not."""
    _probe_at_handoff(module, monkeypatch, lambda conn: None)
    assert fresh_sqlite_target.exists()


@pytest.mark.parametrize("module", ENTRYPOINTS, ids=IDS)
def test_main_hands_relay_every_method_it_calls(module, fresh_sqlite_target, monkeypatch):
    missing = _probe_at_handoff(
        module, monkeypatch, lambda conn: [m for m in RELAY_METHODS if not hasattr(conn, m)]
    )
    assert not missing, f"{module.__name__} hands relay a connection lacking {missing}"


@pytest.mark.parametrize("module", ENTRYPOINTS, ids=IDS)
def test_main_does_not_hand_relay_the_compat_wrapper(module, fresh_sqlite_target, monkeypatch):
    """Name the wrong type outright, so a failure says what actually went wrong."""
    assert (
        _probe_at_handoff(module, monkeypatch, lambda conn: type(conn).__name__)
        != CompatConnection.__name__
    )


_CONNECTION_MODULE = "sable_platform.db.connection"


def _dotted(node: ast.expr) -> str | None:
    """Render ``a.b.c`` from an attribute chain, or None if it is not a plain chain."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _get_db_calls(path: Path) -> list[str]:
    """Calls to the ``get_db`` factory in one file, by two routes and no others.

    Route 1 is a name imported as ``get_db``, including an ``as`` alias. Route 2 is
    ``.get_db()`` on a name bound to the connection module, or on its full dotted path.

    THE ATTRIBUTE ROUTE IS DELIBERATELY NARROW. An earlier version flagged ``.get_db()``
    on ANY object, so ``settings.get_db()`` in either entrypoint would have failed the
    guard. A gate named that. Wrong in that direction costs more than the hole it closed,
    because it fails work that is correct.

    IT DOES NOT CATCH ``getattr(module, "get_db")()``, nor a module rebound through a
    second variable. That is the point where a static check stops paying. The probe tests
    above drive ``main()`` and cover the executed path regardless of import form.
    """
    tree = ast.parse(path.read_text())
    func_names: set[str] = set()
    module_names: set[str] = {_CONNECTION_MODULE}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "get_db":
                    func_names.add(alias.asname or alias.name)
                elif f"{node.module or ''}.{alias.name}" == _CONNECTION_MODULE:
                    module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _CONNECTION_MODULE:
                    module_names.add(alias.asname or alias.name)

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in func_names:
            found.append(f"{func.id}() at line {node.lineno}")
        elif isinstance(func, ast.Attribute) and func.attr == "get_db":
            owner = _dotted(func.value)
            if owner in module_names:
                found.append(f"{owner}.get_db() at line {node.lineno}")
    return found


@pytest.mark.parametrize("name", ["run_relay_bot.py", "run_relay_poller.py"], ids=IDS)
def test_the_entrypoint_never_calls_get_db(name):
    """The static guard, which earns its place only by catching UNDRIVEN paths.

    The probe tests above drive ``main()`` and read the live connection, so they are the
    real check. They only cover the path the test drives. A ``get_db()`` call behind a flag
    or an error branch would not be reached. This one reads the whole file.

    IT WAS A SUBSTRING CHECK AND IT COULD NOT FAIL. It asserted ``"import get_db\\n"`` and
    ``"= get_db()"`` were absent. A gate named the bypass: write
    ``from sable_platform.db import connection as db_connection`` and then
    ``conn = db_connection.get_db()``, and both assertions still pass while the defect is
    fully present. Substring checks on source were the pattern behind three of the
    cannot-fail tests found on this branch. This one parses instead.
    """
    calls = _get_db_calls(SCRIPTS_DIR / name)
    assert calls == [], f"{name} calls the wrapper factory: {calls}"


def test_the_get_db_detector_actually_detects(tmp_path):
    """KNOWN ANSWER: run the guard on files whose answer I already know.

    A guard is worth its line count only if it fires on the thing it names AND stays quiet
    on everything else. The substring version it replaces missed every aliased form. The
    first AST version then failed in the other direction, flagging ``.get_db()`` on any
    object at all, so ``settings.get_db()`` would have failed a correct file. A gate found
    that, and it is why ``must_not_fire`` exists. Both directions are checked here, because
    a guard tested in one direction only has no evidence about the other.
    """
    should_fire = {
        "plain import": "from sable_platform.db.connection import get_db\nconn = get_db()\n",
        "aliased function": (
            "from sable_platform.db.connection import get_db as _g\nconn = _g()\n"
        ),
        "module alias": (
            "from sable_platform.db import connection as c\nconn = c.get_db()\n"
        ),
        "dotted alias": (
            "import sable_platform.db.connection as c\nconn = c.get_db()\n"
        ),
        "unaliased module": (
            "from sable_platform.db import connection\nconn = connection.get_db()\n"
        ),
        "full dotted path": (
            "import sable_platform.db.connection\n"
            "conn = sable_platform.db.connection.get_db()\n"
        ),
    }
    for label, source in should_fire.items():
        probe = tmp_path / "probe.py"
        probe.write_text(source)
        assert _get_db_calls(probe), f"detector missed the {label} form"

    must_not_fire = {
        "the correct call": (
            "from sable_platform.db.connection import get_raw_db\nconn = get_raw_db()\n"
        ),
        "somebody else's method": "conn = settings.get_db()\n",
        "a DI container": "import container\nconn = container.get_db()\n",
    }
    for label, source in must_not_fire.items():
        probe = tmp_path / "clean.py"
        probe.write_text(source)
        assert _get_db_calls(probe) == [], f"detector wrongly fires on {label}: {source!r}"


def test_a_compat_connection_would_still_fail():
    """POWER CHECK: prove the wrapper really lacks the method.

    Without this the tests above could pass on a connection type that never had a
    problem, and none of them would tell you so.
    """
    engine = create_engine("sqlite://")
    with engine.connect() as raw:
        compat = CompatConnection(raw)
        assert not any(hasattr(compat, name) for name in RELAY_METHODS)
        with pytest.raises(AttributeError, match="in_transaction"):
            with immediate_txn(compat):
                pass
