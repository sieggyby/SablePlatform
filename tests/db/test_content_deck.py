"""Migration 076 — Content Deck candidate-substrate CRUD tests.

Two layers:
  * Behavioral (``sa_conn``, the schema.py-metadata path) — exercises the accessors in
    ``sable_platform.db.content_deck``, incl. the audit's load-bearing behaviors: app-level
    dedup, per-operator dismiss/snooze, FAIL-CLOSED IDOR (unknown/wrong-org candidate +
    wrong-org pair_loser writes nothing), expire pending-ONLY, per-accessor org-scoping
    (two-org guards), and the CHECK enums.
  * SQL-path (raw sqlite3 + ``ensure_schema``, the PROD path) — directly exercises
    ``076_content_deck.sql``'s six CHECK constraints + the ``ON DELETE CASCADE`` / no-FK
    survive behavior, so a typo'd enum or a dropped CASCADE in the .sql can't pass green
    while only schema.py is correct (impl-audit HIGH: the behavioral suite builds from
    schema.py metadata, not the .sql).
"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sable_platform.db import content_deck as cd
from sable_platform.db.connection import _MIGRATIONS, ensure_schema
from sable_platform.relay.bot.txn import immediate_txn


def _seed(conn, *orgs):
    for o in orgs or ("orgA",):
        conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": o})


def _mk(conn, *, org="orgA", kind="tweet", payload='{"text":"x"}', source="seed",
        score=None, dedupe_key=None, expires_at=None, now=None):
    return cd.upsert_candidate(
        conn, org_id=org, kind=kind, payload_json=payload, source=source,
        score=score, dedupe_key=dedupe_key, expires_at=expires_at, now=now,
    )


# === Behavioral (schema.py path) ============================================

def test_upsert_get_and_org(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn, kind="meme", payload='{"template_id":"drake"}', score=0.7)
    assert cid > 0
    row = cd.get_candidate(sa_conn, cid)
    assert row["kind"] == "meme" and row["status"] == "pending" and row["org_id"] == "orgA"
    assert cd.get_candidate_org(sa_conn, cid) == "orgA"
    assert cd.get_candidate_org(sa_conn, 999999) is None  # fail-closed primitive


def test_set_candidate_media_stamps_ref_and_status(sa_conn):
    _seed(sa_conn, "orgA"); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn, kind="meme", payload='{"template_id":"drake"}')
    assert cd.get_candidate(sa_conn, cid)["media_content_id"] is None
    with immediate_txn(sa_conn):
        changed = cd.set_candidate_media(sa_conn, candidate_id=cid, org_id="orgA",
                                         media_content_id="sable-orgA/m.png", status="kept")
    assert changed is True
    row = cd.get_candidate(sa_conn, cid)
    assert row["media_content_id"] == "sable-orgA/m.png" and row["status"] == "kept"


def test_set_candidate_media_is_org_scoped(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn, org="orgA", kind="meme")
    with immediate_txn(sa_conn):
        changed = cd.set_candidate_media(sa_conn, candidate_id=cid, org_id="orgB",
                                         media_content_id="x", status="kept")
    assert changed is False  # wrong-org id is a no-op (the org wall)
    row = cd.get_candidate(sa_conn, cid)
    assert row["media_content_id"] is None and row["status"] == "pending"  # untouched


def test_claim_pending_candidate_single_flight(sa_conn):
    _seed(sa_conn, "orgA"); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn, kind="meme")
    # first claim WINS (pending -> kept)
    with immediate_txn(sa_conn):
        assert cd.claim_pending_candidate(sa_conn, candidate_id=cid, org_id="orgA") is True
    assert cd.get_candidate(sa_conn, cid)["status"] == "kept"
    # second claim LOSES (no longer pending)
    with immediate_txn(sa_conn):
        assert cd.claim_pending_candidate(sa_conn, candidate_id=cid, org_id="orgA") is False


def test_claim_pending_candidate_is_org_scoped(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn, org="orgA", kind="meme")
    with immediate_txn(sa_conn):
        assert cd.claim_pending_candidate(sa_conn, candidate_id=cid, org_id="orgB") is False
    assert cd.get_candidate(sa_conn, cid)["status"] == "pending"  # wrong-org no-op


def test_set_candidate_status_expected_status_guard(sa_conn):
    _seed(sa_conn, "orgA"); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn, kind="meme")
        cd.set_candidate_status(sa_conn, candidate_id=cid, org_id="orgA", status="kept")
    # guarded revert undoes ONLY when currently 'kept' (the keep-render revert)
    with immediate_txn(sa_conn):
        assert cd.set_candidate_status(sa_conn, candidate_id=cid, org_id="orgA",
                                       status="pending", expected_status="kept") is True
    assert cd.get_candidate(sa_conn, cid)["status"] == "pending"
    # a guarded flip when NOT in the expected status is a no-op (can't clobber a concurrent move)
    with immediate_txn(sa_conn):
        assert cd.set_candidate_status(sa_conn, candidate_id=cid, org_id="orgA",
                                       status="pending", expected_status="kept") is False
    assert cd.get_candidate(sa_conn, cid)["status"] == "pending"


# --- app-level dedup --------------------------------------------------------
def test_dedup_pending_returns_same_id(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        a = _mk(sa_conn, dedupe_key="k1")
        b = _mk(sa_conn, dedupe_key="k1")  # exact re-emit while pending -> same row
    assert a == b
    assert sa_conn.execute(text("SELECT count(*) FROM content_candidates")).fetchone()[0] == 1


def test_dedup_is_pending_scoped(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        a = _mk(sa_conn, dedupe_key="k1")
        cd.set_candidate_status(sa_conn, candidate_id=a, org_id="orgA", status="kept")
        b = _mk(sa_conn, dedupe_key="k1")  # the pending one is gone -> a fresh insert
    assert b != a
    assert sa_conn.execute(text("SELECT count(*) FROM content_candidates")).fetchone()[0] == 2


def test_dedup_is_org_scoped(sa_conn):
    """A dedupe_key collision across orgs must NOT hand orgA orgB's candidate id."""
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        b = _mk(sa_conn, org="orgB", dedupe_key="shared")
        a = _mk(sa_conn, org="orgA", dedupe_key="shared")
    assert a != b
    assert cd.get_candidate(sa_conn, a)["org_id"] == "orgA"


# --- deck feed: dismiss / snooze / org isolation ----------------------------
def test_list_excludes_dismissed_and_unexpired_snooze(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        keep = _mk(sa_conn, score=0.5)
        dismissed = _mk(sa_conn, score=0.6)
        snoozed = _mk(sa_conn, score=0.7)
        snooze_expired = _mk(sa_conn, score=0.8)
        cd.set_operator_candidate_state(sa_conn, candidate_id=dismissed, org_id="orgA",
                                        operator_handle="op1", state="dismissed")
        cd.set_operator_candidate_state(sa_conn, candidate_id=snoozed, org_id="orgA",
                                        operator_handle="op1", state="snoozed",
                                        snooze_until="2999-01-01T00:00:00Z")
        cd.set_operator_candidate_state(sa_conn, candidate_id=snooze_expired, org_id="orgA",
                                        operator_handle="op1", state="snoozed",
                                        snooze_until="2000-01-01T00:00:00Z")
    ids = {r["id"] for r in cd.list_deck_candidates(sa_conn, "orgA", "op1")}
    assert keep in ids and snooze_expired in ids       # visible
    assert dismissed not in ids and snoozed not in ids  # filtered
    assert len(cd.list_deck_candidates(sa_conn, "orgA", "op2")) == 4  # state is per-operator


def test_list_orders_null_score_last_then_desc(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        lo = _mk(sa_conn, score=0.5)
        hi = _mk(sa_conn, score=0.9)
        none = _mk(sa_conn, score=None)
    order = [r["id"] for r in cd.list_deck_candidates(sa_conn, "orgA", "op1")]
    assert order == [hi, lo, none]


def test_list_is_org_scoped(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        a = _mk(sa_conn, org="orgA")
        _mk(sa_conn, org="orgB")
    ids = {r["id"] for r in cd.list_deck_candidates(sa_conn, "orgA", "op1")}
    assert ids == {a}  # orgB's pending candidate never appears in orgA's feed


def test_set_operator_state_rejects_cross_org_candidate(sa_conn):
    """The dismiss/snooze writer is FAIL-CLOSED like its siblings (no cross-org existence oracle)."""
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        b = _mk(sa_conn, org="orgB")
        with pytest.raises(ValueError):
            cd.set_operator_candidate_state(sa_conn, candidate_id=b, org_id="orgA",
                                            operator_handle="op1", state="dismissed")


# --- decisions + FAIL-CLOSED IDOR -------------------------------------------
def test_record_decision_happy_path(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn)
        did = cd.record_deck_decision(
            sa_conn, candidate_id=cid, org_id="orgA", actor="operator_arf",
            actor_kind="operator", decision="keep", surface="web",
        )
    assert did > 0


def test_decision_rejects_unknown_candidate(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        with pytest.raises(ValueError):
            cd.record_deck_decision(
                sa_conn, candidate_id=424242, org_id="orgA", actor="op",
                actor_kind="operator", decision="keep", surface="web",
            )


def test_decision_rejects_wrong_org_candidate(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn, org="orgB")
        with pytest.raises(ValueError):
            cd.record_deck_decision(
                sa_conn, candidate_id=cid, org_id="orgA", actor="op",
                actor_kind="operator", decision="keep", surface="web",
            )


def test_decision_rejects_cross_org_pair_loser_and_writes_nothing(sa_conn):
    """Codex r1: a cross-org pair_loser_id poisons org-scoped Elo -> reject, no row."""
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        winner = _mk(sa_conn, org="orgA")
        loser_other_org = _mk(sa_conn, org="orgB")
        with pytest.raises(ValueError):
            cd.record_deck_decision(
                sa_conn, candidate_id=winner, org_id="orgA", actor="op",
                actor_kind="community", decision="keep", surface="discord",
                pair_loser_id=loser_other_org,
            )
    assert sa_conn.execute(text("SELECT count(*) FROM content_deck_decisions")).fetchone()[0] == 0


def test_valid_pairwise_decision_writes(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        winner = _mk(sa_conn)
        loser = _mk(sa_conn)
        did = cd.record_deck_decision(
            sa_conn, candidate_id=winner, org_id="orgA", actor="discord:user:1",
            actor_kind="community", decision="keep", surface="discord", pair_loser_id=loser,
        )
    row = sa_conn.execute(text("SELECT pair_loser_id FROM content_deck_decisions WHERE id=:i"),
                          {"i": did}).fetchone()
    assert row[0] == loser


def test_count_pending_candidates_by_kind_and_org(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        _mk(sa_conn, kind="meme")
        _mk(sa_conn, kind="meme")
        _mk(sa_conn, kind="tweet")
        _mk(sa_conn, org="orgB", kind="meme")            # other org — never counted
        kept = _mk(sa_conn, kind="meme")
        cd.set_candidate_status(sa_conn, candidate_id=kept, org_id="orgA", status="kept")
    sa_conn.commit()
    assert cd.count_pending_candidates(sa_conn, "orgA", kind="meme") == 2   # kept excluded
    assert cd.count_pending_candidates(sa_conn, "orgA", kind="tweet") == 1
    assert cd.count_pending_candidates(sa_conn, "orgA", kind="clip") == 0
    assert cd.count_pending_candidates(sa_conn, "orgA") == 3                # all kinds
    assert cd.count_pending_candidates(sa_conn, "orgB", kind="meme") == 1


# --- expire (pending-only, org-scoped) --------------------------------------
def test_expire_due_is_pending_only_and_org_scoped(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    past, future = "2000-01-01T00:00:00Z", "2999-01-01T00:00:00Z"
    with immediate_txn(sa_conn):
        due = _mk(sa_conn, expires_at=past)                       # pending + past -> expires
        not_due = _mk(sa_conn, expires_at=future)                 # pending + future -> stays
        scheduled = _mk(sa_conn, expires_at=past)                 # scheduled + past -> MUST NOT expire
        cd.set_candidate_status(sa_conn, candidate_id=scheduled, org_id="orgA", status="scheduled")
        other_org = _mk(sa_conn, org="orgB", expires_at=past)     # orgB pending + past
    with immediate_txn(sa_conn):
        n = cd.expire_due_candidates(sa_conn, org_id="orgA", now="2026-06-22T00:00:00Z")
    assert n == 1
    assert cd.get_candidate(sa_conn, due)["status"] == "expired"
    assert cd.get_candidate(sa_conn, not_due)["status"] == "pending"
    assert cd.get_candidate(sa_conn, scheduled)["status"] == "scheduled"   # round-3 guarantee
    assert cd.get_candidate(sa_conn, other_org)["status"] == "pending"     # org wall on the sweep


def test_set_status_is_org_scoped(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn, org="orgA")
        changed = cd.set_candidate_status(sa_conn, candidate_id=cid, org_id="orgB", status="kept")
    assert changed is False
    assert cd.get_candidate(sa_conn, cid)["status"] == "pending"


# --- CHECK enums via the accessors (schema.py path) -------------------------
@pytest.mark.parametrize("kind", ["bogus", "", "Tweet"])
def test_bad_kind_rejected(sa_conn, kind):
    _seed(sa_conn); sa_conn.commit()
    with pytest.raises(IntegrityError):
        with immediate_txn(sa_conn):
            _mk(sa_conn, kind=kind)


def test_bad_status_rejected(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn)
    with pytest.raises(IntegrityError):
        with immediate_txn(sa_conn):
            cd.set_candidate_status(sa_conn, candidate_id=cid, org_id="orgA", status="archived")


def test_bad_decision_enum_rejected(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn)
    with pytest.raises(IntegrityError):
        with immediate_txn(sa_conn):
            cd.record_deck_decision(
                sa_conn, candidate_id=cid, org_id="orgA", actor="op",
                actor_kind="operator", decision="LOVE", surface="web",
            )


def test_bad_actor_kind_rejected(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn)
    with pytest.raises(IntegrityError):
        with immediate_txn(sa_conn):
            cd.record_deck_decision(
                sa_conn, candidate_id=cid, org_id="orgA", actor="op",
                actor_kind="bot", decision="keep", surface="web",
            )


def test_bad_surface_rejected(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn)
    with pytest.raises(IntegrityError):
        with immediate_txn(sa_conn):
            cd.record_deck_decision(
                sa_conn, candidate_id=cid, org_id="orgA", actor="op",
                actor_kind="operator", decision="keep", surface="telegram",
            )


def test_bad_operator_state_rejected(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn)
    with pytest.raises(IntegrityError):
        with immediate_txn(sa_conn):
            cd.set_operator_candidate_state(sa_conn, candidate_id=cid, org_id="orgA",
                                            operator_handle="op", state="muted")


# --- FK lifecycle: CASCADE state, SURVIVE decisions (schema.py path) ---------
def test_operator_state_cascades_decisions_survive_on_candidate_delete(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn)
        cd.set_operator_candidate_state(sa_conn, candidate_id=cid, org_id="orgA",
                                        operator_handle="op", state="dismissed")
        cd.record_deck_decision(
            sa_conn, candidate_id=cid, org_id="orgA", actor="op",
            actor_kind="operator", decision="keep", surface="web",
        )
    with immediate_txn(sa_conn):
        sa_conn.execute(text("DELETE FROM content_candidates WHERE id = :i"), {"i": cid})
    assert sa_conn.execute(
        text("SELECT count(*) FROM content_deck_operator_state WHERE candidate_id=:i"), {"i": cid}
    ).fetchone()[0] == 0   # operator_state cascaded away
    assert sa_conn.execute(
        text("SELECT count(*) FROM content_deck_decisions WHERE candidate_id=:i"), {"i": cid}
    ).fetchone()[0] == 1   # the no-FK learning-join SURVIVES (Elo/keep signal preserved)


# === SQL-path (raw sqlite3 + ensure_schema: the PROD .sql, not schema.py) ====
# Closes the impl-audit HIGH: directly exercises 076_content_deck.sql's CHECK enums + the
# ON DELETE CASCADE, so a typo'd enum / dropped CASCADE in the .sql can't pass green.

def _sql_conn():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys=ON")
    ensure_schema(raw)
    raw.execute("INSERT INTO orgs (org_id, display_name) VALUES ('orgA','orgA')")
    return raw


def _new_candidate(raw, *, kind="tweet"):
    raw.execute(
        "INSERT INTO content_candidates (org_id,kind,payload_json,source) VALUES ('orgA',?,'{}','seed')",
        (kind,),
    )
    return raw.execute("SELECT last_insert_rowid()").fetchone()[0]


@pytest.mark.parametrize(
    "sql,params",
    [
        ("INSERT INTO content_candidates (org_id,kind,payload_json,source) VALUES ('orgA','bogus','{}','s')", ()),
        ("UPDATE content_candidates SET status='archived' WHERE id=:cid", "cand"),
        ("INSERT INTO content_deck_decisions (candidate_id,org_id,actor,actor_kind,decision,surface) "
         "VALUES (:cid,'orgA','op','bot','keep','web')", "cand"),
        ("INSERT INTO content_deck_decisions (candidate_id,org_id,actor,actor_kind,decision,surface) "
         "VALUES (:cid,'orgA','op','operator','LOVE','web')", "cand"),
        ("INSERT INTO content_deck_decisions (candidate_id,org_id,actor,actor_kind,decision,surface) "
         "VALUES (:cid,'orgA','op','operator','keep','telegram')", "cand"),
        ("INSERT INTO content_deck_operator_state (candidate_id,operator_handle,state) "
         "VALUES (:cid,'op','muted')", "cand"),
    ],
    ids=["kind", "status", "actor_kind", "decision", "surface", "op_state"],
)
def test_sql_path_check_constraints_reject(sql, params):
    """Every one of the SIX CHECK enums in 076.sql rejects a bad value (prod .sql path)."""
    raw = _sql_conn()
    cid = _new_candidate(raw)
    bind = {"cid": cid} if params == "cand" else {}
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute(sql.replace(":cid", str(cid)) if bind else sql)
    raw.close()


def test_sql_path_cascade_and_survive():
    """076.sql: deleting a candidate CASCADEs operator_state but the no-FK decisions survive."""
    raw = _sql_conn()
    cid = _new_candidate(raw)
    raw.execute("INSERT INTO content_deck_operator_state (candidate_id,operator_handle,state) "
                "VALUES (?,?, 'dismissed')", (cid, "op"))
    raw.execute("INSERT INTO content_deck_decisions (candidate_id,org_id,actor,actor_kind,decision,surface) "
                "VALUES (?,?,?,?,?,?)", (cid, "orgA", "op", "operator", "keep", "web"))
    raw.execute("DELETE FROM content_candidates WHERE id=?", (cid,))
    assert raw.execute("SELECT count(*) FROM content_deck_operator_state WHERE candidate_id=?",
                       (cid,)).fetchone()[0] == 0   # cascaded
    assert raw.execute("SELECT count(*) FROM content_deck_decisions WHERE candidate_id=?",
                       (cid,)).fetchone()[0] == 1   # survives (no FK)
    raw.close()


def test_sql_path_reaches_head_version():
    raw = _sql_conn()
    # Derive the head from the registry so this can't go stale on the next migration
    # (mirrors tests/cli/test_init.py's LATEST_SCHEMA_VERSION).
    assert raw.execute("SELECT version FROM schema_version").fetchone()[0] == _MIGRATIONS[-1][1]
    raw.close()


# === list_deck_decisions (keep-rate readout) ================================
def _decide(conn, cid, *, org="orgA", decision="keep", surface="web", now=None):
    return cd.record_deck_decision(
        conn, candidate_id=cid, org_id=org, actor="op", actor_kind="operator",
        decision=decision, surface=surface, now=now,
    )


def test_list_deck_decisions_joins_payload_kind_score(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn, kind="meme", payload='{"template_id":"drake"}', score=7.0)
        _decide(sa_conn, cid, decision="keep")
    rows = cd.list_deck_decisions(sa_conn, "orgA")
    assert len(rows) == 1
    r = rows[0]
    assert r["decision"] == "keep" and r["kind"] == "meme"
    assert r["payload_json"] == '{"template_id":"drake"}' and r["score"] == 7.0
    assert r["candidate_id"] == cid


def test_list_deck_decisions_is_org_scoped(sa_conn):
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        a = _mk(sa_conn, org="orgA", kind="meme")
        b = _mk(sa_conn, org="orgB", kind="meme")
        _decide(sa_conn, a, org="orgA")
        _decide(sa_conn, b, org="orgB")
    rows = cd.list_deck_decisions(sa_conn, "orgA")
    assert len(rows) == 1 and rows[0]["candidate_id"] == a


def test_list_deck_decisions_kind_filter(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        m = _mk(sa_conn, kind="meme")
        t = _mk(sa_conn, kind="tweet")
        _decide(sa_conn, m); _decide(sa_conn, t)
    rows = cd.list_deck_decisions(sa_conn, "orgA", kind="meme")
    assert [r["candidate_id"] for r in rows] == [m]


def test_list_deck_decisions_since_filter_and_order(sa_conn):
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        cid = _mk(sa_conn, kind="meme")
        _decide(sa_conn, cid, decision="reject", now="2026-06-01T00:00:00Z")
        _decide(sa_conn, cid, decision="keep", now="2026-06-20T00:00:00Z")
    # since drops the June-1 row; newest-first puts the June-20 keep first
    rows = cd.list_deck_decisions(sa_conn, "orgA", since="2026-06-10T00:00:00Z")
    assert [r["decision"] for r in rows] == ["keep"]
    allrows = cd.list_deck_decisions(sa_conn, "orgA")
    assert [r["decision"] for r in allrows] == ["keep", "reject"]  # DESC by created_at


def test_list_deck_decisions_surfaces_pair_loser_id(sa_conn):
    # A duel win is decision='keep' + a non-null pair_loser_id; the reader must be able to tell
    # it apart from an operator single-card keep (else community duels inflate keep-rate).
    _seed(sa_conn); sa_conn.commit()
    with immediate_txn(sa_conn):
        winner = _mk(sa_conn, kind="meme")
        loser = _mk(sa_conn, kind="meme")
        cd.record_deck_decision(
            sa_conn, candidate_id=winner, org_id="orgA", actor="discord:u:1",
            actor_kind="community", decision="keep", surface="discord", pair_loser_id=loser,
        )
    row = next(r for r in cd.list_deck_decisions(sa_conn, "orgA") if r["candidate_id"] == winner)
    assert row["pair_loser_id"] == loser  # duel is distinguishable from an operator swipe


def test_list_deck_decisions_org_pins_both_sides(sa_conn):
    # Defense-in-depth (K-2): even a corrupt row whose d.org_id != its candidate's org_id must
    # not leak under either org. Raw-insert bypasses record_deck_decision's write-time guard.
    _seed(sa_conn, "orgA", "orgB"); sa_conn.commit()
    with immediate_txn(sa_conn):
        cand_b = _mk(sa_conn, org="orgB", kind="meme")
    sa_conn.execute(
        text("INSERT INTO content_deck_decisions "
             "(candidate_id, org_id, actor, actor_kind, decision, surface) "
             "VALUES (:c, 'orgA', 'op', 'operator', 'keep', 'web')"),
        {"c": cand_b},
    )
    sa_conn.commit()
    assert cd.list_deck_decisions(sa_conn, "orgA") == []  # d.org_id=A but c.org_id=B -> excluded
    assert cd.list_deck_decisions(sa_conn, "orgB") == []  # c.org_id=B but d.org_id=A -> excluded
