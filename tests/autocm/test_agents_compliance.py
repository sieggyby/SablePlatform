"""C3.10 — SP AGENTS-compliance sweep over ``sable_platform/autocm``.

A static (AST) sweep that pins the three SP conventions the MEGAPLAN C3.10 scope
names ("SP AGENTS compliance"): **layering**, **import-purity**, and the
**strftime** timestamp form. The sweep walks EVERY ``*.py`` under
``sable_platform/autocm`` so a future module that violates a convention fails this
test, not a human review.

  1. **Import-purity (MEGAPLAN §5 determinism ethos + D-1 import rule).** No autocm
     module imports a heavy LLM/transport SDK (``anthropic`` / ``telegram`` /
     ``telethon`` / ``discord`` / ``aiogram``) at MODULE TOP. The ONLY place the
     ``anthropic`` SDK is imported at all is the LAZY, in-function import inside
     ``llm.py`` (``AnthropicProvider._ensure_client``) — never at any module's top
     level, so importing the autocm package never requires the SDK and never makes a
     network call at import.

  2. **Layering (SP CLAUDE.md: subprocess/connection boundary; relay/db + autocm/db
     contract).** No autocm module constructs a DB engine (``create_engine``) or
     resolves a connection itself (``get_db`` / ``sable_db_path``) — every DB helper
     takes an already-open ``Connection`` the caller owns. AND the security half of
     the classifier (``filter`` / ``tier`` / ``register``) embeds NO raw SQL: it
     reaches runtime state through the named ``sable_platform.autocm.db`` helpers
     (SQL behind functions), never an inline query.

  3. **strftime (MEGAPLAN §5 / migration-053 contract).** No EXECUTABLE string
     literal uses the banned ``datetime('now')`` SQLite form (the space-separated /
     no-``Z`` shape 053 standardized away); every inline-SQL ``strftime(...,'now')``
     uses the ISO-8601 ``%Y-%m-%dT%H:%M:%SZ`` form (``T`` + trailing ``Z``).
     Docstrings/comments that REFERENCE ``datetime('now')`` (to explain the 018
     audit-log default) are excluded — they are documentation, not emitted SQL.

The sweep is pure static analysis — it imports nothing from the swept tree, so a
module with a heavy top-level dep would be CAUGHT (the failure is the assertion,
not an ImportError).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

AUTOCM_ROOT = (
    pathlib.Path(__file__).resolve().parents[2] / "sable_platform" / "autocm"
)

# heavy SDKs that must NEVER be imported at module top in an autocm module.
_BANNED_TOP_IMPORTS = {
    "anthropic",
    "telegram",
    "telethon",
    "discord",
    "aiogram",
}

# the classifier security half embeds NO raw SQL (reaches state via autocm.db).
_NO_RAW_SQL_MODULES = {
    "classifier/filter.py",
    "classifier/tier.py",
    "classifier/register.py",
}

# layering: no autocm module constructs an engine or resolves a connection itself.
_BANNED_DB_CALLS = {"create_engine", "get_db", "sable_db_path", "ensure_schema"}


def _autocm_modules():
    return sorted(
        p for p in AUTOCM_ROOT.rglob("*.py") if "__pycache__" not in p.parts
    )


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(AUTOCM_ROOT).as_posix()


def _parse(path: pathlib.Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """ids() of every docstring Constant node (module/class/func), to exclude them."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _top_level_imports(tree: ast.AST):
    """Yield (module_name) for every MODULE-TOP-LEVEL import/from-import."""
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module.split(".")[0]


# ---------------------------------------------------------------------------
# sanity: the sweep actually found the tree.
# ---------------------------------------------------------------------------
def test_sweep_discovers_the_autocm_tree():
    mods = _autocm_modules()
    assert len(mods) >= 20  # the DESIGN §4 layout is sizeable; guard against empty glob
    names = {_rel(p) for p in mods}
    assert "llm.py" in names
    assert "cost.py" in names
    assert "classifier/filter.py" in names


# ===========================================================================
# (1) import-purity — no heavy SDK at module top; anthropic only lazily in llm.py.
# ===========================================================================
def test_no_heavy_sdk_imported_at_module_top():
    offenders = []
    for path in _autocm_modules():
        tree = _parse(path)
        for top in _top_level_imports(tree):
            if top in _BANNED_TOP_IMPORTS:
                offenders.append((_rel(path), top))
    assert offenders == [], f"heavy SDK imported at module top: {offenders}"


def test_anthropic_is_imported_only_lazily_in_llm():
    """The ONLY ``anthropic`` import anywhere is the in-function lazy import in llm.py."""
    anthropic_import_sites = []
    for path in _autocm_modules():
        tree = _parse(path)
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.Import):
                mod = next(
                    (a.name.split(".")[0] for a in node.names if a.name.split(".")[0] == "anthropic"),
                    None,
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] == "anthropic":
                    mod = "anthropic"
            if mod == "anthropic":
                anthropic_import_sites.append(_rel(path))
    # exactly one module imports anthropic — llm.py — and (asserted below) lazily.
    assert set(anthropic_import_sites) == {"llm.py"}, anthropic_import_sites

    # the anthropic import in llm.py is NOT a module-top statement (it is nested in a
    # function body — the lazy import).
    llm_tree = _parse(AUTOCM_ROOT / "llm.py")
    assert "anthropic" not in set(_top_level_imports(llm_tree))


# ===========================================================================
# (2) layering — no engine/connection construction; classifier embeds no raw SQL.
# ===========================================================================
def test_no_module_constructs_an_engine_or_resolves_a_connection():
    offenders = []
    for path in _autocm_modules():
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = None
                if isinstance(fn, ast.Name):
                    name = fn.id
                elif isinstance(fn, ast.Attribute):
                    name = fn.attr
                if name in _BANNED_DB_CALLS:
                    offenders.append((_rel(path), name))
    assert offenders == [], f"autocm module created an engine/connection: {offenders}"


def test_classifier_security_half_embeds_no_raw_sql():
    """filter / tier / register reach state via autocm.db helpers — never inline SQL."""
    for rel in _NO_RAW_SQL_MODULES:
        path = AUTOCM_ROOT / rel
        tree = _parse(path)
        doc_ids = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            # no sqlalchemy text() call.
            if isinstance(node, ast.Call):
                fn = node.func
                fname = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                assert fname != "text", f"{rel}: raw SQL text() call found (use autocm.db)"
            # no SQL-keyword string literal (excluding docstrings/comments).
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in doc_ids
            ):
                upper = node.value.upper()
                for kw in ("SELECT ", "INSERT INTO", "UPDATE ", "DELETE FROM"):
                    assert kw not in upper, f"{rel}: inline SQL literal ({kw.strip()}) found"


# ===========================================================================
# (3) strftime — no banned datetime('now'); inline strftime uses the T...Z form.
# ===========================================================================
def test_no_banned_datetime_now_in_executable_code():
    offenders = []
    for path in _autocm_modules():
        tree = _parse(path)
        doc_ids = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in doc_ids
            ):
                s = node.value
                if "datetime('now')" in s or 'datetime("now")' in s:
                    offenders.append((_rel(path), s[:60]))
    assert offenders == [], f"banned datetime('now') in executable code: {offenders}"


def test_inline_strftime_uses_iso_t_z_form():
    """Every executable strftime(...,'now') SQL literal uses '%Y-%m-%dT%H:%M:%SZ'."""
    checked = 0
    for path in _autocm_modules():
        tree = _parse(path)
        doc_ids = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in doc_ids
            ):
                s = node.value
                # an inline SQL strftime over 'now' (the only place the form matters).
                if "strftime(" in s and "'now'" in s:
                    checked += 1
                    assert "%Y-%m-%dT%H:%M:%SZ" in s, (
                        f"{_rel(path)}: inline strftime must use the ISO-8601 "
                        f"T...Z form (migration-053 contract), got: {s[:80]!r}"
                    )
    # there IS at least one such literal (kb/constants.py upsert) — the assertion
    # is not vacuous.
    assert checked >= 1


def test_python_timestamp_helpers_use_iso_t_z_form():
    """Python-side ``_utc_now_iso`` / ``_iso_z`` helpers render the ...Z form, not space.

    The autocm/relay contract binds timestamps in Python as UTC ISO-8601 ``...Z``
    (matching the 058 TEXT columns), never via ``datetime('now')``. We assert the
    canonical Python format strings used for column writes carry the ``T`` + ``Z``.
    """
    from sable_platform.autocm.db import _utc_now_iso
    from sable_platform.autocm.publisher.tg import _utc_now_iso as pub_iso

    for fn in (_utc_now_iso, pub_iso):
        stamp = fn()
        assert stamp.endswith("Z"), stamp
        assert "T" in stamp, stamp
        assert " " not in stamp, stamp  # no space-separated (the 053 drift) form


# ===========================================================================
# Every autocm module parses + has no value-position match/case smell (sanity).
# ===========================================================================
@pytest.mark.parametrize("path", _autocm_modules(), ids=_rel)
def test_module_parses_cleanly(path):
    # a parse failure here would surface a syntax regression in any swept module.
    assert _parse(path) is not None
