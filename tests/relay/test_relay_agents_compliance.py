"""C2.6 — AGENTS.md compliance sweep over ``sable_platform/relay``.

The MEGAPLAN C2.6 scope requires a SablePlatform AGENTS.md compliance pass over
the relay surfaces. This file encodes the three machine-checkable invariants the
chunk names plus the ``_MIGRATIONS``-derived doc/version parity:

  1. **strftime ``_at``-default on the ``.sql`` migration** (migration-053
     contract). Every record-time ``_at`` column in ``057_relay.sql`` carries the
     ISO-8601-Z ``strftime`` default; the schema.py side uses ``func.now()`` (the
     73+ -occurrence house convention) and NEVER a hand-coded ``strftime`` default
     (schema.py default values are not asserted by the parity test, so a
     hand-coded default there would only deviate from house style).
  2. **no-raw-SQL-in-handlers layering** (C2.1 §5.3 boundary). The relay handlers
     and the feed-orchestration modules embed NO raw SQL — every statement is a
     named ``relay/db.py`` helper. Raw SQL lives ONLY in the data-access layer
     (``relay/db.py`` + ``relay/socialdata.py``).
  3. **import purity.** No relay module imports ``telegram`` / ``discord`` /
     ``anthropic`` at module top EXCEPT the designated transport/escaping modules
     (``telegram_app.py`` / ``discord_app.py`` / ``escaping.py``) — so the
     dep-light data + handler layers stay importable without the heavy bot SDKs.

  4. **doc/version parity** derived from ``connection._MIGRATIONS`` — the head is
     58, ``CLI_REFERENCE.md`` states "58 migrations", and the on-disk filename /
     ``schema_version`` literals are mutually consistent at 58.
"""
from __future__ import annotations

import ast
import importlib.resources
import re
from pathlib import Path

import pytest

RELAY_PKG_DIR = (
    Path(__file__).resolve().parent.parent.parent / "sable_platform" / "relay"
)

# The ONLY relay modules permitted to import the heavy bot SDKs at module top.
_SDK_IMPORT_ALLOWED = {
    "telegram_app.py",  # python-telegram-bot Application
    "discord_app.py",  # discord.py Client
    "escaping.py",  # imports `discord` for AllowedMentions.none() / escape_*
}
_FORBIDDEN_TOP_IMPORTS = {"telegram", "discord", "anthropic"}

# The ONLY relay modules permitted to embed raw SQL (the data-access layer).
_RAW_SQL_ALLOWED = {"db.py", "socialdata.py"}


def _relay_py_files() -> list[Path]:
    return sorted(p for p in RELAY_PKG_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _read_057_sql() -> str:
    sql_path = (
        importlib.resources.files("sable_platform.db") / "migrations" / "057_relay.sql"
    )
    return sql_path.read_text(encoding="utf-8")


# ==========================================================================
# 1. strftime _at-default on the .sql migration (migration-053 contract)
# ==========================================================================
# Record-time _at columns that MUST carry the strftime default (the row's own
# creation/update/event timestamp). Nullable event-COMPLETION columns are
# excluded below — they are set on a later event, not at insert.
_STRFTIME_DEFAULT = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"

# _at columns that legitimately have NO strftime default: nullable
# event-completion timestamps (set when a later event happens) + expires_at
# (NOT NULL but caller-materialized to the quorum-window end, not "now").
_NO_DEFAULT_AT_COLUMNS = frozenset(
    {
        "last_polled_at",  # nullable: set when a poll first runs
        "last_seen_at",  # nullable: chat-binding last-seen
        "resolved_at",  # nullable: submission resolution
        "claimed_at",  # nullable: job claim time
        "dismissed_at",  # nullable: notification dismissal
        "replied_at",  # nullable: 4.6 reply follow-through
        "expires_at",  # NOT NULL but caller-materialized (window end, not now)
    }
)

# Match a column DDL line like ``created_at  TEXT NOT NULL DEFAULT (strftime(...))``.
_AT_COL_RE = re.compile(
    r"^\s*(?P<name>[a-z_]+_at)\s+TEXT\b(?P<rest>.*?)(?:,)?\s*$"
)


def _parse_at_columns(sql: str) -> dict[str, str]:
    """Return ``{column_name: full_ddl_rest}`` for every ``*_at TEXT`` column."""
    cols: dict[str, str] = {}
    for line in sql.splitlines():
        m = _AT_COL_RE.match(line)
        if m is not None:
            # A column may appear in multiple tables (e.g. created_at); keep the
            # union of constraints so a single missing default is caught.
            name = m.group("name")
            cols.setdefault(name, "")
            cols[name] += " " + m.group("rest")
    return cols


def test_057_at_columns_carry_strftime_default_or_are_known_nullable():
    """Every record-time ``_at`` column in 057_relay.sql carries the strftime
    default; the only ``_at`` columns without it are the known nullable
    event-completion columns (+ caller-materialized ``expires_at``)."""
    sql = _read_057_sql()
    at_cols = _parse_at_columns(sql)
    assert at_cols, "no *_at columns parsed from 057_relay.sql — parser drifted"

    for name, ddl in at_cols.items():
        has_default = _STRFTIME_DEFAULT in ddl
        if name in _NO_DEFAULT_AT_COLUMNS:
            # These are allowed to lack the strftime default (nullable / materialized).
            continue
        assert has_default, (
            f"_at column {name!r} in 057_relay.sql must carry the strftime ISO-8601-Z "
            f"default {_STRFTIME_DEFAULT!r} (migration-053 contract) or be listed as a "
            f"known nullable event-completion column"
        )


def test_057_strftime_default_uses_iso_z_form_no_localtime():
    """The strftime default is the UTC ISO-8601-Z form — never a bare
    ``datetime('now','localtime')`` (which would drift across deployments)."""
    sql = _read_057_sql()
    assert _STRFTIME_DEFAULT in sql, "057_relay.sql lost the canonical strftime default"
    assert "localtime" not in sql, (
        "057_relay.sql must not use a localtime default — UTC ISO-8601-Z only"
    )


def test_relay_schema_uses_func_now_not_handcoded_strftime():
    """The schema.py side follows the house ``func.now()`` convention and never
    hand-codes a ``strftime`` default (the strftime default is the ``.sql`` file's
    contract; schema.py default VALUES are not parity-asserted)."""
    schema_src = (
        RELAY_PKG_DIR.parent / "db" / "schema.py"
    ).read_text(encoding="utf-8")
    # No hand-coded strftime DEFAULT on any column — a hand-coded default would be
    # expressed as ``server_default=text("... strftime ...")``. (A bare ``strftime``
    # substring also appears in a CONVENTION COMMENT, which is fine — we forbid the
    # default-expression form only.) Parse the AST and reject any server_default=
    # whose value is a text(...) call mentioning strftime.
    tree = ast.parse(schema_src)
    handcoded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.keyword) or node.arg != "server_default":
            continue
        val = node.value
        if (
            isinstance(val, ast.Call)
            and isinstance(val.func, ast.Name)
            and val.func.id == "text"
            and val.args
            and isinstance(val.args[0], ast.Constant)
            and isinstance(val.args[0].value, str)
            and "strftime" in val.args[0].value
        ):
            handcoded.append(val.args[0].value)
    assert handcoded == [], (
        "db/schema.py must use server_default=func.now(), not a hand-coded "
        f"server_default=text(strftime(...)): {handcoded}"
    )
    assert "server_default=func.now()" in schema_src, (
        "db/schema.py lost the func.now() house convention for _at defaults"
    )


# ==========================================================================
# 2. no-raw-SQL-in-handlers layering (C2.1 §5.3 boundary)
# ==========================================================================
def _module_has_raw_sql(path: Path) -> bool:
    """True iff the module embeds raw SQL — a ``text(...)`` call or a ``.execute(``.

    ``text`` is the SQLAlchemy raw-SQL constructor; ``.execute(`` is a direct
    connection statement. Either in a handler/orchestration module breaks the
    §5.3 layering boundary (those must go through named ``relay_db.*`` helpers).
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            # A bare ``text(...)`` call.
            if isinstance(fn, ast.Name) and fn.id == "text":
                return True
            # A ``<conn>.execute(...)`` call.
            if isinstance(fn, ast.Attribute) and fn.attr == "execute":
                return True
    return False


def test_handlers_and_feed_contain_no_raw_sql():
    """Relay handlers + feed-orchestration modules embed NO raw SQL — every
    statement goes through a named ``relay/db.py`` helper (C2.1 §5.3)."""
    offenders = []
    for path in _relay_py_files():
        rel = path.relative_to(RELAY_PKG_DIR)
        # Only the data-access layer (db.py / socialdata.py) may carry raw SQL.
        if path.name in _RAW_SQL_ALLOWED:
            continue
        # Anything under bot/handlers/ or feed/ (the orchestration layer) must be
        # SQL-free; the bot/ transport + txn helpers are too.
        if _module_has_raw_sql(path):
            offenders.append(str(rel))
    assert offenders == [], (
        f"raw SQL (text()/.execute()) found outside the data-access layer "
        f"(db.py/socialdata.py) — these break the §5.3 layering boundary: {offenders}"
    )


def test_raw_sql_lives_only_in_data_access_layer():
    """Sanity: raw SQL DOES live in db.py/socialdata.py (the layering is real, not
    vacuously satisfied by a relay with no SQL at all)."""
    db_path = RELAY_PKG_DIR / "db.py"
    assert _module_has_raw_sql(db_path), "relay/db.py is expected to carry the raw SQL"


# ==========================================================================
# 3. import purity — no heavy bot SDK at module top outside the app/escaping layer
# ==========================================================================
def _top_level_imports(path: Path) -> set[str]:
    """Return the set of TOP-LEVEL imported root module names in ``path``.

    Only module-scope imports count (an import nested inside a function is a lazy
    import and does not violate import purity — the module is still importable
    without the SDK installed at import time).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in tree.body:  # MODULE BODY only — not nested in functions
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", _relay_py_files(), ids=lambda p: p.name)
def test_relay_module_import_purity(path: Path):
    """No relay module imports telegram/discord/anthropic at module top except the
    designated transport (``telegram_app``/``discord_app``) + ``escaping`` modules."""
    if path.name in _SDK_IMPORT_ALLOWED:
        return
    roots = _top_level_imports(path)
    leaked = roots & _FORBIDDEN_TOP_IMPORTS
    assert not leaked, (
        f"{path.name} imports {sorted(leaked)} at module top — the heavy bot SDKs "
        f"may only be imported by {sorted(_SDK_IMPORT_ALLOWED)} (keep the data + "
        f"handler layers importable without telegram/discord/anthropic)"
    )


def test_escaping_is_the_only_handler_layer_discord_import():
    """Defense: among the handler/feed/db layer, ONLY escaping.py imports discord
    at module top — the rest stay SDK-free (so the layering claim is real)."""
    discord_importers = []
    for path in _relay_py_files():
        if "discord" in _top_level_imports(path):
            discord_importers.append(path.name)
    # discord_app.py legitimately imports discord (the Client); escaping.py imports
    # it for AllowedMentions. No OTHER module should.
    assert set(discord_importers) <= {"discord_app.py", "escaping.py"}, (
        f"unexpected module-top discord import(s): {discord_importers}"
    )


# ==========================================================================
# 4. doc/version parity derived from _MIGRATIONS (confirm head == 58)
# ==========================================================================
def test_doc_parity_migration_head_is_58():
    """``_MIGRATIONS`` head is 58, the on-disk filename matches, and the max
    version literal is consistent — the C2.6 ``test_doc_parity`` confirmation."""
    from sable_platform.db.connection import _MIGRATIONS

    assert len(_MIGRATIONS) == 58, f"expected 58 migrations, got {len(_MIGRATIONS)}"
    last_name, last_version = _MIGRATIONS[-1]
    assert last_version == 58
    assert last_name.startswith("058_"), last_name
    # The on-disk migration file for the head exists.
    head_file = (
        importlib.resources.files("sable_platform.db") / "migrations" / last_name
    )
    assert head_file.is_file(), f"head migration file {last_name} missing on disk"


def test_cli_reference_states_58_migrations():
    """``CLI_REFERENCE.md`` states '58 migrations' (the _MIGRATIONS-derived count
    asserted by the suite-wide test_doc_parity, re-checked in the relay set)."""
    from sable_platform.db.connection import _MIGRATIONS

    cli_ref = (
        Path(__file__).resolve().parent.parent.parent / "docs" / "CLI_REFERENCE.md"
    ).read_text(encoding="utf-8")
    assert f"{len(_MIGRATIONS)} migrations" in cli_ref
    assert "58 migrations" in cli_ref
