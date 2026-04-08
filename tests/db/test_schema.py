"""Verify SQLAlchemy schema parity with legacy ensure_schema() migrations."""
from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine, inspect, text

from sable_platform.db.connection import ensure_schema
from sable_platform.db.schema import metadata


# ---------------------------------------------------------------------------
# Helpers — build both schemas in-memory and introspect via PRAGMA / inspector
# ---------------------------------------------------------------------------

def _legacy_schema() -> dict[str, list[dict]]:
    """Build schema via legacy ensure_schema().

    Returns ``{table_name: [{name, type, notnull, pk}, ...]}``.
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    tables: dict[str, list[dict]] = {}
    for (name,) in cursor.fetchall():
        cols = conn.execute(f"PRAGMA table_info({name})").fetchall()  # noqa: S608
        tables[name] = [
            {"name": r[1], "type": r[2].upper(), "notnull": bool(r[3]), "pk": bool(r[5])}
            for r in cols
        ]
    conn.close()
    return tables


def _legacy_indexes() -> set[str]:
    """Return the set of index names created by the legacy migration path."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    )
    names = {row[0] for row in cursor.fetchall()}
    conn.close()
    return names


def _sa_schema() -> dict[str, list[dict]]:
    """Build schema via SQLAlchemy metadata.create_all().

    Returns ``{table_name: [{name, type, notnull, pk}, ...]}``.
    """
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    inspector = inspect(engine)
    tables: dict[str, list[dict]] = {}
    for name in inspector.get_table_names():
        cols = inspector.get_columns(name)
        pk_cols = set(inspector.get_pk_constraint(name).get("constrained_columns", []))
        tables[name] = [
            {
                "name": c["name"],
                "type": str(c["type"]).upper(),
                "notnull": bool(c.get("nullable") is False),
                "pk": c["name"] in pk_cols,
            }
            for c in cols
        ]
    engine.dispose()
    return tables


def _sa_indexes() -> set[str]:
    """Return the set of index names created by the SA path."""
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    inspector = inspect(engine)
    names: set[str] = set()
    for table in inspector.get_table_names():
        for idx in inspector.get_indexes(table):
            names.add(idx["name"])
    engine.dispose()
    return names


# ---------------------------------------------------------------------------
# Table and column parity
# ---------------------------------------------------------------------------

def test_same_tables():
    """Both paths produce the same set of table names."""
    legacy = set(_legacy_schema().keys())
    sa = set(_sa_schema().keys())
    assert legacy == sa, f"Table mismatch — legacy only: {legacy - sa}, SA only: {sa - legacy}"


def test_same_columns_per_table():
    """Every table has identical column names in both paths."""
    legacy = _legacy_schema()
    sa = _sa_schema()
    mismatches = {}
    for table in sorted(legacy.keys() & sa.keys()):
        legacy_names = {c["name"] for c in legacy[table]}
        sa_names = {c["name"] for c in sa[table]}
        if legacy_names != sa_names:
            mismatches[table] = {
                "legacy_only": legacy_names - sa_names,
                "sa_only": sa_names - legacy_names,
            }
    assert not mismatches, f"Column mismatches: {mismatches}"


# SQLite type affinity normalization — both paths should agree after normalization.
_TYPE_MAP = {
    "": "TEXT",           # SQLite treats bare "" as TEXT affinity
    "VARCHAR": "TEXT",    # SQLAlchemy may emit VARCHAR; SQLite treats as TEXT
    "FLOAT": "REAL",      # SA Float renders as FLOAT; migrations say REAL — same affinity
}


def _normalize_type(t: str) -> str:
    return _TYPE_MAP.get(t, t)


def test_column_types_match():
    """Every column has the same SQLite type affinity in both paths."""
    legacy = _legacy_schema()
    sa = _sa_schema()
    mismatches = {}
    for table in sorted(legacy.keys() & sa.keys()):
        leg_cols = {c["name"]: _normalize_type(c["type"]) for c in legacy[table]}
        sa_cols = {c["name"]: _normalize_type(c["type"]) for c in sa[table]}
        for col in sorted(leg_cols.keys() & sa_cols.keys()):
            if leg_cols[col] != sa_cols[col]:
                mismatches[f"{table}.{col}"] = f"legacy={leg_cols[col]}, sa={sa_cols[col]}"
    assert not mismatches, f"Type mismatches: {mismatches}"


def test_nullability_matches():
    """Every non-PK column has the same NOT NULL flag in both paths.

    Primary keys are excluded because SQLite PRAGMA table_info reports PKs as
    ``notnull=0`` even though they are implicitly NOT NULL.
    """
    legacy = _legacy_schema()
    sa = _sa_schema()
    mismatches = {}
    for table in sorted(legacy.keys() & sa.keys()):
        leg_cols = {c["name"]: c for c in legacy[table]}
        sa_cols = {c["name"]: c for c in sa[table]}
        for col in sorted(leg_cols.keys() & sa_cols.keys()):
            # Skip PKs — SQLite PRAGMA quirk reports them as notnull=False.
            if leg_cols[col]["pk"] or sa_cols[col]["pk"]:
                continue
            if leg_cols[col]["notnull"] != sa_cols[col]["notnull"]:
                mismatches[f"{table}.{col}"] = (
                    f"legacy_notnull={leg_cols[col]['notnull']}, "
                    f"sa_notnull={sa_cols[col]['notnull']}"
                )
    assert not mismatches, f"Nullability mismatches: {mismatches}"


# ---------------------------------------------------------------------------
# Index parity
# ---------------------------------------------------------------------------

def test_legacy_indexes_present_in_sa():
    """Both paths produce the same set of named indexes."""
    legacy = _legacy_indexes()
    sa = _sa_indexes()
    # SA may generate auto-indexes for UniqueConstraints; filter to named indexes from migrations.
    # Only check that all legacy indexes exist in SA (SA may have extras from UniqueConstraint).
    missing = legacy - sa
    assert not missing, f"Indexes missing from SA path: {missing}"


# ---------------------------------------------------------------------------
# Basic sanity
# ---------------------------------------------------------------------------

def test_sa_schema_creates_cleanly():
    """metadata.create_all() succeeds on a fresh in-memory SQLite engine."""
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    with engine.connect() as conn:
        tables = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ).fetchall()
    assert len(tables) >= 36
    engine.dispose()


def test_sa_schema_is_idempotent():
    """Calling create_all() twice does not raise."""
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    metadata.create_all(engine)  # must not raise
    engine.dispose()
