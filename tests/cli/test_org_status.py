"""``org set-status``, and the case collision that put both ``tig`` and ``TIG`` in prod.

Two defects are pinned here.

**There was no way to deactivate an org.** ``weekly run --all`` selects
``WHERE status='active'`` (``cli/workflow_cmds.py``). ``org create --status`` was the only
writer of that column, and only at creation time; ``org reject`` and ``org graduate`` stamp
``prospect_scores`` and never touch it. So ``sable-weekly`` ran six orgs that should have
left the set, including a client removed on 2026-08-23 and four test fixtures, and failed on
their dead X handles every run.

**``org create`` compared ids exactly.** ``orgs.org_id`` is the primary key, nothing
normalises its case, and 40 tables carry a foreign key to it. An exact-match existence check
therefore accepted ``TIG`` alongside ``tig`` and partitioned one client's data across all 40.

The test that matters most here is
``test_deactivating_removes_an_org_from_the_all_sweep``. It runs the CLI and then issues the
EXACT query the weekly runner issues, rather than asserting on the command's own output. A
command that prints "inactive" while the sweep still picks the org up would pass a
message-only test and fix nothing.
"""
from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner

from sable_platform.cli.org_cmds import org_create, org_set_status
from sable_platform.db.connection import ensure_schema
from sable_platform.db.orgs import (
    find_org_id_ignoring_case,
    set_org_status,
)
from tests.conftest import make_test_conn

# The literal query in cli/workflow_cmds.py that `weekly run --all` uses to pick orgs.
# Copied verbatim on purpose: this file's central claim is about THAT sweep, so the test
# must ask the same question the runner asks.
ACTIVE_SWEEP = "SELECT org_id FROM orgs WHERE status='active'"


@pytest.fixture
def file_db(tmp_path, monkeypatch):
    """A real file-backed database, because the CLI opens its own connection."""
    path = str(tmp_path / "orgs.db")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    ensure_schema(con)
    con.commit()
    con.close()
    monkeypatch.setenv("SABLE_DB_PATH", path)
    monkeypatch.delenv("SABLE_DATABASE_URL", raising=False)
    monkeypatch.setenv("SABLE_OPERATOR_ID", "test_operator")
    return path


def _sweep(path: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute(ACTIVE_SWEEP)}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# The behaviour the whole change exists for
# ---------------------------------------------------------------------------


def test_deactivating_removes_an_org_from_the_all_sweep(file_db):
    """THE test. Assert on the sweep, never on the command's own output.

    A command that prints "inactive" while the org stays in the sweep would satisfy any
    message-only assertion and fix nothing. So this asks the runner's own question, before
    and after, and requires the answer to change.
    """
    assert CliRunner().invoke(org_create, ["tig", "--name", "TIG"]).exit_code == 0
    assert "tig" in _sweep(file_db), "precondition: a new org starts active"

    result = CliRunner().invoke(org_set_status, ["tig", "inactive"])
    assert result.exit_code == 0, result.output
    assert "active -> inactive" in result.output

    assert "tig" not in _sweep(file_db), "the org is still in the weekly --all sweep"


def test_reactivating_puts_it_back(file_db):
    """The reverse direction, so 'inactive' is a state and not a one-way door.

    Deactivation was chosen over deletion for the four production test fixtures precisely
    because it is reversible. That claim needs a test.
    """
    CliRunner().invoke(org_create, ["fixture", "--name", "Fixture"])
    CliRunner().invoke(org_set_status, ["fixture", "inactive"])
    assert "fixture" not in _sweep(file_db)

    result = CliRunner().invoke(org_set_status, ["fixture", "active"])
    assert result.exit_code == 0, result.output
    assert "fixture" in _sweep(file_db)


# ---------------------------------------------------------------------------
# A typo must not read as success
# ---------------------------------------------------------------------------


def test_a_missing_org_fails_instead_of_silently_updating_nothing(file_db):
    """An UPDATE matching no row is a silent no-op that a rowcount reports as 0.

    Reporting success for a typo'd org_id is the failure this command exists to avoid, so
    the miss is detected with a SELECT before the UPDATE runs.
    """
    result = CliRunner().invoke(org_set_status, ["nosuchorg", "inactive"])
    assert result.exit_code == 1
    assert "No such org" in result.output


def test_a_case_mismatch_suggests_the_stored_id(file_db):
    """org_id is case-sensitive, so 'TIG' against a stored 'tig' is the likely typo."""
    CliRunner().invoke(org_create, ["tig", "--name", "TIG"])

    result = CliRunner().invoke(org_set_status, ["TIG", "inactive"])
    assert result.exit_code == 1
    assert "No such org" in result.output
    assert "Did you mean 'tig'" in result.output
    assert "tig" in _sweep(file_db), "the near-miss must not have changed anything"


def test_setting_the_status_it_already_has_says_so(file_db):
    """Idempotent, and it reports 'nothing changed' rather than a false state change."""
    CliRunner().invoke(org_create, ["myorg", "--name", "My Org"])

    result = CliRunner().invoke(org_set_status, ["myorg", "active"])
    assert result.exit_code == 0, result.output
    assert "already active" in result.output
    assert "->" not in result.output


def test_the_change_is_audited(file_db):
    """Every other org mutation in org_cmds.py writes an audit row. So does this one."""
    CliRunner().invoke(org_create, ["myorg", "--name", "My Org"])
    CliRunner().invoke(org_set_status, ["myorg", "inactive"])

    con = sqlite3.connect(file_db)
    try:
        rows = con.execute(
            "SELECT actor, org_id, detail_json FROM audit_log WHERE action='org_status_changed'"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 1, rows
    actor, org_id, detail = rows[0]
    assert actor == "test_operator", "SABLE_OPERATOR_ID must reach the audit row"
    assert org_id == "myorg"
    assert '"from": "active"' in detail and '"to": "inactive"' in detail


def test_an_unchanged_status_writes_no_audit_row(file_db):
    """A no-op must not manufacture history. Otherwise the log stops meaning anything."""
    CliRunner().invoke(org_create, ["myorg", "--name", "My Org"])
    CliRunner().invoke(org_set_status, ["myorg", "active"])

    con = sqlite3.connect(file_db)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='org_status_changed'"
        ).fetchone()[0]
    finally:
        con.close()
    assert n == 0


# ---------------------------------------------------------------------------
# The db layer, directly
# ---------------------------------------------------------------------------


def test_set_org_status_returns_the_previous_status():
    """It returns the PREVIOUS status, not a row count, so three cases stay distinguishable.

    A rowcount collapses "no such org" and "already that status" into 0 on some drivers.
    The caller needs to tell them apart, because one is an error and one is a no-op.
    """
    conn = make_test_conn()
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES ('a', 'A', 'active')"
    )
    conn.commit()

    assert set_org_status(conn, "a", "inactive") == "active"
    assert set_org_status(conn, "a", "inactive") == "inactive"
    assert set_org_status(conn, "nope", "inactive") is None


def test_set_org_status_rejects_an_unknown_status_and_writes_nothing():
    """The column is plain Text with no CHECK, so an unrecognised value would be STORED.

    It would then be invisible to every ``status='active'`` sweep and to every listing
    looking for ``'inactive'``: a row that exists in neither set.
    """
    conn = make_test_conn()
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status) VALUES ('a', 'A', 'active')"
    )
    conn.commit()

    with pytest.raises(ValueError, match="status must be one of"):
        set_org_status(conn, "a", "archived")

    row = conn.execute("SELECT status FROM orgs WHERE org_id='a'").fetchone()
    assert row[0] == "active", "a rejected status must not have been written"


def test_set_org_status_bumps_updated_at():
    conn = make_test_conn()
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status, updated_at) "
        "VALUES ('a', 'A', 'active', '2020-01-01T00:00:00Z')"
    )
    conn.commit()

    set_org_status(conn, "a", "inactive")
    row = conn.execute("SELECT updated_at FROM orgs WHERE org_id='a'").fetchone()
    assert row[0] > "2020-01-01T00:00:00Z"


def test_find_org_id_ignoring_case_both_directions():
    """KNOWN ANSWER, both ways. A matcher that fires on everything is worthless.

    The negative cases are the point: 'tig2' and 'ti' share a prefix with 'tig' and must NOT
    match, or ``org create`` would start refusing legitimate ids.
    """
    conn = make_test_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('tig', 'TIG')")
    conn.commit()

    for probe in ("tig", "TIG", "Tig", "tIG"):
        assert find_org_id_ignoring_case(conn, probe) == "tig", f"missed {probe!r}"

    for probe in ("tig2", "ti", "atig", "robotmoney", ""):
        assert find_org_id_ignoring_case(conn, probe) is None, f"wrongly matched {probe!r}"


# ---------------------------------------------------------------------------
# org create: the collision, and the legitimate ids it must still accept
# ---------------------------------------------------------------------------


def test_org_create_refuses_an_id_differing_only_in_case(file_db):
    """The measured production failure: both 'tig' and 'TIG' exist, splitting 40 FK tables."""
    assert CliRunner().invoke(org_create, ["tig", "--name", "TIG"]).exit_code == 0

    result = CliRunner().invoke(org_create, ["TIG", "--name", "TIG again"])
    assert result.exit_code == 1
    assert "differs from it only in case" in result.output
    assert "Use 'tig'" in result.output

    con = sqlite3.connect(file_db)
    try:
        n = con.execute("SELECT COUNT(*) FROM orgs").fetchone()[0]
    finally:
        con.close()
    assert n == 1, "the second row must not have been created"


def test_org_create_still_reports_an_exact_duplicate_the_old_way(file_db):
    """The exact-match message predates this change and other callers read it. Keep it."""
    CliRunner().invoke(org_create, ["myorg", "--name", "My Org"])

    result = CliRunner().invoke(org_create, ["myorg", "--name", "My Org"])
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert "differs from it only in case" not in result.output


def test_org_create_still_accepts_a_genuinely_different_id(file_db):
    """THE OTHER DIRECTION. A collision check that over-fires blocks correct work.

    'tig2' and 'stig' both contain 'tig'. Neither is a case variant of it, and refusing
    either would be worse than the hole this check closes.
    """
    CliRunner().invoke(org_create, ["tig", "--name", "TIG"])

    # 'ti' is the case that matters: a substring-style matcher would see 'tig' CONTAINING
    # 'ti' and refuse a legitimate short id. The other three are not substrings of 'tig',
    # so they cannot detect that failure mode.
    for org_id in ("tig2", "stig", "robotmoney", "ti"):
        result = CliRunner().invoke(org_create, [org_id, "--name", org_id])
        assert result.exit_code == 0, f"wrongly refused {org_id!r}: {result.output}"


# ---------------------------------------------------------------------------
# The status change and its audit row are ONE transaction
# ---------------------------------------------------------------------------


def test_a_failed_audit_rolls_back_the_status_change(file_db, monkeypatch):
    """A gate found this. Committing the status and auditing afterwards is TWO writes.

    ``set_org_status`` used to commit before the CLI wrote the audit row, so a failure
    between them left the org deactivated with no record of who did it. That is the worst
    shape for an audit trail: the change is real and the history says it never happened.
    """
    CliRunner().invoke(org_create, ["tig", "--name", "TIG"])

    def _explode(*a, **k):
        raise RuntimeError("audit_log is unavailable")

    monkeypatch.setattr("sable_platform.db.audit.log_audit", _explode)

    result = CliRunner().invoke(org_set_status, ["tig", "inactive"])
    assert result.exit_code != 0

    assert "tig" in _sweep(file_db), (
        "the status change survived a failed audit, so the two are not one transaction"
    )


def test_a_no_op_writes_nothing_at_all(file_db):
    """Setting the status it already has must not bump updated_at either.

    A timestamp moved by a change that did not happen makes the column lie about when the
    row last moved.
    """
    CliRunner().invoke(org_create, ["myorg", "--name", "My Org"])

    con = sqlite3.connect(file_db)
    try:
        before = con.execute("SELECT updated_at FROM orgs WHERE org_id='myorg'").fetchone()[0]
    finally:
        con.close()

    CliRunner().invoke(org_set_status, ["myorg", "active"])

    con = sqlite3.connect(file_db)
    try:
        after = con.execute("SELECT updated_at FROM orgs WHERE org_id='myorg'").fetchone()[0]
    finally:
        con.close()
    assert after == before


# ---------------------------------------------------------------------------
# Production already holds BOTH tig and TIG
# ---------------------------------------------------------------------------


def test_an_exact_match_wins_when_both_case_variants_exist():
    """A gate found this, and it only bites on the database we actually have.

    With both rows present, a bare ``LOWER(org_id) = LOWER(:org_id)`` returns whichever the
    planner reaches first. Every test that seeds only ONE row passes regardless.
    """
    conn = make_test_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('tig', 'lower')")
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('TIG', 'upper')")
    conn.commit()

    assert find_org_id_ignoring_case(conn, "tig") == "tig"
    assert find_org_id_ignoring_case(conn, "TIG") == "TIG"

    # A probe matching NEITHER exactly must still be deterministic across runs.
    assert find_org_id_ignoring_case(conn, "TiG") == "TIG"
    assert find_org_id_ignoring_case(conn, "TiG") == "TIG"


def test_org_create_still_reports_a_plain_duplicate_on_the_split_prod_data(file_db):
    """The behaviour regression the exact-match rule prevents, on prod's actual state."""
    CliRunner().invoke(org_create, ["tig", "--name", "lower"])
    con = sqlite3.connect(file_db)
    try:
        con.execute("INSERT INTO orgs (org_id, display_name) VALUES ('TIG', 'upper')")
        con.commit()
    finally:
        con.close()

    result = CliRunner().invoke(org_create, ["tig", "--name", "again"])
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert "differs from it only in case" not in result.output, (
        "an exact duplicate was reported as a case collision"
    )


def test_set_org_status_writes_nothing_for_a_no_op_even_when_it_would_commit():
    """The no-op must be enforced in the DB layer, not only by the CLI's early return.

    A sweep caught this. The CLI-level no-op test could not fail, because the CLI passes
    commit=False and returns before committing, so an UPDATE issued anyway is discarded
    either way. This calls the function directly with commit=True, where the UPDATE would
    actually land.
    """
    conn = make_test_conn()
    conn.execute(
        "INSERT INTO orgs (org_id, display_name, status, updated_at) "
        "VALUES ('a', 'A', 'active', '2020-01-01T00:00:00Z')"
    )
    conn.commit()

    assert set_org_status(conn, "a", "active", commit=True) == "active"

    row = conn.execute("SELECT updated_at FROM orgs WHERE org_id='a'").fetchone()
    assert row[0] == "2020-01-01T00:00:00Z", "a no-op bumped updated_at"


# ---------------------------------------------------------------------------
# An EXISTING split must be surfaced, not hidden by the exact-match preference
# ---------------------------------------------------------------------------


def _add_variant(path: str, org_id: str) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(
            "INSERT INTO orgs (org_id, display_name) VALUES (?, ?)", (org_id, org_id)
        )
        con.commit()
    finally:
        con.close()


def test_an_existing_split_is_reported_even_on_an_exact_duplicate(file_db):
    """A gate found this. Preferring an exact match answers the wrong question alone.

    It answers "which row did you mean". It cannot answer "is this client already split",
    and with both rows stored the operator was told nothing about the 40 FK tables
    partitioned between them.
    """
    CliRunner().invoke(org_create, ["tig", "--name", "lower"])
    _add_variant(file_db, "TIG")

    result = CliRunner().invoke(org_create, ["tig", "--name", "again"])
    assert result.exit_code == 1
    assert "already exists" in result.output, "the plain-duplicate message was lost"
    assert "differ only in case: 'TIG', 'tig'" in result.output
    assert "40 tables reference it" in result.output


def test_the_split_warning_stays_quiet_when_there_is_no_split(file_db):
    """THE OTHER DIRECTION. A warning that always fires tells you nothing."""
    CliRunner().invoke(org_create, ["myorg", "--name", "My Org"])

    result = CliRunner().invoke(org_create, ["myorg", "--name", "again"])
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert "differ only in case" not in result.output


def test_set_status_reports_the_split_on_a_near_miss(file_db):
    """The other command that meets this data. Both must say the same thing about it."""
    CliRunner().invoke(org_create, ["tig", "--name", "lower"])
    _add_variant(file_db, "TIG")

    result = CliRunner().invoke(org_set_status, ["TiG", "inactive"])
    assert result.exit_code == 1
    assert "No such org" in result.output
    assert "differ only in case" in result.output


def test_list_org_id_case_variants_both_directions():
    """KNOWN ANSWER. It must find every variant, and invent none."""
    from sable_platform.db.orgs import list_org_id_case_variants, split_warning

    conn = make_test_conn()
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('tig', 'a')")
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('TIG', 'b')")
    conn.execute("INSERT INTO orgs (org_id, display_name) VALUES ('tig2', 'c')")
    conn.commit()

    assert list_org_id_case_variants(conn, "tig") == ["TIG", "tig"]
    assert list_org_id_case_variants(conn, "TIG") == ["TIG", "tig"]
    assert list_org_id_case_variants(conn, "tig2") == ["tig2"], "a near name is not a variant"
    assert list_org_id_case_variants(conn, "nothing") == []

    assert split_warning(["TIG", "tig"]) is not None
    assert split_warning(["tig"]) is None, "one row is not a split"
    assert split_warning([]) is None
