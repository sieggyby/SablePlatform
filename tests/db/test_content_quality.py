"""Migration 080 — content-preference Elo rollup tests (sable_platform.db.content_quality).

Exercises the duel-fold applier against the in-memory schema:
  * apply_pending_content_events — forward-only pairwise Elo from the duel log, DUAL grain
    (candidate = live tie-break; feature = durable, like-to-like over kind/template/format);
  * swipe rows (NULL pair_loser_id) are cursored forward but NOT folded;
  * idempotent (second call folds nothing);
  * UNFOLDABLE duels (mig 083): a community_tweet on either side, or a GC'd candidate
    (kind unknowable), skips BOTH grains — cursored forward, never folded;
  * get_content_quality — the {elo, pick_rate, n_offered, n_chosen} rollup, subject_kind-filtered,
    NO cost column.
All org-scoped.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from sable_platform.db import content_quality as cq

_BASE = 1500.0


def _seed_org(conn, org_id="orgA"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.commit()


def _cand(conn, cid, org, kind, payload):
    conn.execute(
        text(
            "INSERT INTO content_candidates (id, org_id, kind, status, target_handle, payload_json, source) "
            "VALUES (:id, :org, :kind, 'pending', '@x', :pl, 'seed')"
        ),
        {"id": cid, "org": org, "kind": kind, "pl": json.dumps(payload)},
    )


def _duel(conn, org, winner, loser, did):
    conn.execute(
        text(
            "INSERT INTO content_deck_decisions (id, candidate_id, org_id, actor, actor_kind, decision, surface, pair_loser_id) "
            "VALUES (:id, :w, :org, 'op1', 'operator', 'keep', 'web', :l)"
        ),
        {"id": did, "w": winner, "org": org, "l": loser},
    )


def _swipe(conn, org, cand, did, decision="keep"):
    conn.execute(
        text(
            "INSERT INTO content_deck_decisions (id, candidate_id, org_id, actor, actor_kind, decision, surface, pair_loser_id) "
            "VALUES (:id, :c, :org, 'op1', 'operator', :d, 'web', NULL)"
        ),
        {"id": did, "c": cand, "org": org, "d": decision},
    )


def test_apply_pending_folds_duels_dual_grain(sa_conn):
    _seed_org(sa_conn)
    _cand(sa_conn, 1, "orgA", "meme", {"template_id": "drake", "format": "two-panel"})
    _cand(sa_conn, 2, "orgA", "tweet", {"text": "x"})
    _cand(sa_conn, 3, "orgA", "meme", {"template_id": "two_buttons", "format": "two-panel"})
    _duel(sa_conn, "orgA", 1, 2, 1)  # meme/drake beats tweet
    _duel(sa_conn, "orgA", 1, 3, 2)  # meme/drake beats meme/two_buttons
    sa_conn.commit()

    folded = cq.apply_pending_content_events(sa_conn, "orgA")
    assert folded == 2

    cand = cq.get_content_quality(sa_conn, "orgA", "candidate")
    feat = cq.get_content_quality(sa_conn, "orgA", "feature")

    # candidate grain: #1 won twice (above base), #2/#3 lost (below base); counts right.
    assert cand["1"]["elo"] > _BASE and cand["1"]["n_offered"] == 2 and cand["1"]["n_chosen"] == 2
    assert cand["2"]["elo"] < _BASE and cand["2"]["n_chosen"] == 0
    assert cand["3"]["elo"] < _BASE and cand["3"]["n_chosen"] == 0
    assert cand["1"]["pick_rate"] == 1.0

    # feature grain, LIKE-TO-LIKE: kind:meme beat kind:tweet (duel 1); template:drake beat
    # template:two_buttons (duel 2). kind:meme-vs-meme (duel 2) and format two-panel-vs-two-panel are
    # SAME-value → no update (not present or unchanged).
    assert feat["kind:meme"]["elo"] > _BASE
    assert feat["kind:tweet"]["elo"] < _BASE
    assert feat["template:drake"]["elo"] > _BASE
    assert feat["template:two_buttons"]["elo"] < _BASE
    assert "format:two-panel" not in feat  # only same-value comparisons → never folded


def test_cross_kind_duel_folds_kind_only_not_format(sa_conn):
    """``template``/``format`` are KIND-SPECIFIC vocabularies, and the deck duels ACROSS kinds. A
    meme (format:two-panel) vs a tweet that now ALSO carries a format (format:hot_take) must fold the
    ``kind`` arm ONLY — never compare a meme template/format to a text format (would mix two
    incompatible vocabularies in one namespace)."""
    _seed_org(sa_conn)
    _cand(sa_conn, 1, "orgA", "meme", {"template_id": "drake", "format": "two-panel"})
    _cand(sa_conn, 2, "orgA", "tweet", {"text": "x", "format": "hot_take"})
    _duel(sa_conn, "orgA", 1, 2, 1)  # meme beats tweet — CROSS-KIND
    sa_conn.commit()
    assert cq.apply_pending_content_events(sa_conn, "orgA") == 1
    feat = cq.get_content_quality(sa_conn, "orgA", "feature")
    assert feat["kind:meme"]["elo"] > _BASE and feat["kind:tweet"]["elo"] < _BASE  # kind folds
    # format/template do NOT cross kinds:
    assert "format:two-panel" not in feat and "format:hot_take" not in feat
    assert "template:drake" not in feat


def test_same_kind_text_duel_folds_format(sa_conn):
    """Two TWEETS with different format buckets: the ``format`` arm folds WITHIN the kind, so the
    text format: Elo populates (the gap this change closes). ``kind`` is same-value → not folded."""
    _seed_org(sa_conn)
    _cand(sa_conn, 1, "orgA", "tweet", {"text": "a", "format": "hot_take"})
    _cand(sa_conn, 2, "orgA", "tweet", {"text": "b", "format": "listicle"})
    _duel(sa_conn, "orgA", 1, 2, 1)  # hot_take beats listicle — SAME-KIND
    sa_conn.commit()
    assert cq.apply_pending_content_events(sa_conn, "orgA") == 1
    feat = cq.get_content_quality(sa_conn, "orgA", "feature")
    assert feat["format:hot_take"]["elo"] > _BASE
    assert feat["format:listicle"]["elo"] < _BASE
    assert "kind:tweet" not in feat  # same kind on both sides → no kind fold


def test_idempotent_second_apply_folds_nothing(sa_conn):
    _seed_org(sa_conn)
    _cand(sa_conn, 1, "orgA", "meme", {"template_id": "drake"})
    _cand(sa_conn, 2, "orgA", "tweet", {"text": "x"})
    _duel(sa_conn, "orgA", 1, 2, 1)
    sa_conn.commit()
    assert cq.apply_pending_content_events(sa_conn, "orgA") == 1
    elo_after_first = cq.get_content_quality(sa_conn, "orgA", "candidate")["1"]["elo"]
    # second call: nothing left unapplied → folds 0, Elo unchanged (no double-count).
    assert cq.apply_pending_content_events(sa_conn, "orgA") == 0
    assert cq.get_content_quality(sa_conn, "orgA", "candidate")["1"]["elo"] == elo_after_first


def test_swipe_rows_not_folded_but_cursored(sa_conn):
    _seed_org(sa_conn)
    _cand(sa_conn, 1, "orgA", "meme", {"template_id": "drake"})
    _swipe(sa_conn, "orgA", 1, 1, "keep")  # NULL pair_loser_id → never folds into the Elo
    sa_conn.commit()
    assert cq.apply_pending_content_events(sa_conn, "orgA") == 0  # no duel → nothing folded
    assert cq.get_content_quality(sa_conn, "orgA", "candidate") == {}  # no Elo rows written
    # the swipe row is marked applied=1 (cursored) so it isn't re-scanned forever.
    row = sa_conn.execute(text("SELECT applied FROM content_deck_decisions WHERE id = 1")).fetchone()
    assert row[0] == 1


def test_gc_candidate_duel_is_unfoldable(sa_conn):
    """mig 083: a duel whose candidate is GC'd/deleted before the fold (kind unknowable — e.g. a
    community-tweet rollback DELETE) skips BOTH grains rather than guessing, but still cursors
    applied=1 forward. (Pre-083 this folded candidate grain on bare ids — rows for a dead candidate
    that no consumer could ever read.)"""
    _seed_org(sa_conn)
    _cand(sa_conn, 1, "orgA", "meme", {"template_id": "drake"})
    # loser #99 has NO content_candidates row (GC'd; the no-FK decisions log survives the purge).
    _duel(sa_conn, "orgA", 1, 99, 1)
    sa_conn.commit()
    assert cq.apply_pending_content_events(sa_conn, "orgA") == 0  # unfoldable → nothing folded
    assert cq.get_content_quality(sa_conn, "orgA", "candidate") == {}
    assert cq.get_content_quality(sa_conn, "orgA", "feature") == {}
    row = sa_conn.execute(text("SELECT applied FROM content_deck_decisions WHERE id = 1")).fetchone()
    assert row[0] == 1  # cursored forward — never re-scanned


def _community_duel(conn, org, winner, loser, did):
    conn.execute(
        text(
            "INSERT INTO content_deck_decisions (id, candidate_id, org_id, actor, actor_kind, decision, surface, pair_loser_id) "
            "VALUES (:id, :w, :org, 'discord:user:42', 'community', 'keep', 'discord', :l)"
        ),
        {"id": did, "w": winner, "org": org, "l": loser},
    )


def test_community_tweet_duel_never_folds_either_grain(sa_conn):
    """mig 083: the /duel prediction game over REAL community tweets writes decision rows but must
    never touch content_quality — no candidate-grain rows, no feature-grain rows, no 'community:'-
    prefixed rows. Ground truth lives in the payload; votes live in the decisions log."""
    _seed_org(sa_conn)
    _cand(sa_conn, 1, "orgA", "community_tweet", {"text": "gm", "author_handle": "gabbyvorbeck"})
    _cand(sa_conn, 2, "orgA", "community_tweet", {"text": "wagmi", "author_handle": "synapz_org"})
    _community_duel(sa_conn, "orgA", 1, 2, 1)
    sa_conn.commit()
    assert cq.apply_pending_content_events(sa_conn, "orgA") == 0
    assert cq.get_content_quality(sa_conn, "orgA") == {}  # all grains empty — prefixed or not
    row = sa_conn.execute(text("SELECT applied FROM content_deck_decisions WHERE id = 1")).fetchone()
    assert row[0] == 1


def test_community_tweet_on_either_side_skips_but_normal_duels_still_fold(sa_conn):
    """A mixed batch: a community_tweet-vs-tweet duel (either-side rule) skips; a normal
    meme-vs-tweet duel in the SAME catch-up still folds."""
    _seed_org(sa_conn)
    _cand(sa_conn, 1, "orgA", "community_tweet", {"text": "gm", "author_handle": "gabbyvorbeck"})
    _cand(sa_conn, 2, "orgA", "tweet", {"text": "x"})
    _cand(sa_conn, 3, "orgA", "meme", {"template_id": "drake"})
    _cand(sa_conn, 4, "orgA", "tweet", {"text": "y"})
    _community_duel(sa_conn, "orgA", 1, 2, 1)  # community_tweet beats tweet → UNFOLDABLE
    _duel(sa_conn, "orgA", 3, 4, 2)            # meme beats tweet → folds normally
    sa_conn.commit()
    assert cq.apply_pending_content_events(sa_conn, "orgA") == 1
    cand = cq.get_content_quality(sa_conn, "orgA", "candidate")
    assert set(cand) == {"3", "4"}  # the community pair contributed NO candidate rows
    feat = cq.get_content_quality(sa_conn, "orgA", "feature")
    assert feat["kind:meme"]["elo"] > _BASE and feat["kind:tweet"]["elo"] < _BASE
    assert not any("community_tweet" in k for k in feat)
    rows = sa_conn.execute(
        text("SELECT id, applied FROM content_deck_decisions ORDER BY id")
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [(1, 1), (2, 1)]  # both cursored


def test_get_content_quality_org_scoped_no_cost(sa_conn):
    _seed_org(sa_conn, "orgA")
    _seed_org(sa_conn, "orgB")
    _cand(sa_conn, 1, "orgA", "meme", {"template_id": "drake"})
    _cand(sa_conn, 2, "orgA", "tweet", {"text": "x"})
    _cand(sa_conn, 3, "orgB", "meme", {"template_id": "drake"})
    _cand(sa_conn, 4, "orgB", "tweet", {"text": "y"})
    _duel(sa_conn, "orgA", 1, 2, 1)
    _duel(sa_conn, "orgB", 3, 4, 2)
    sa_conn.commit()
    cq.apply_pending_content_events(sa_conn, "orgA")
    a = cq.get_content_quality(sa_conn, "orgA")  # all grains, org A only
    assert "1" in a and "3" not in a  # org B's candidate excluded
    for v in a.values():
        for k in v:
            assert "cost" not in k.lower() and "usd" not in k.lower()


def _community_duel(conn, org, winner, loser, did, actor="discord:user:42"):
    conn.execute(
        text(
            "INSERT INTO content_deck_decisions (id, candidate_id, org_id, actor, actor_kind, decision, surface, pair_loser_id) "
            "VALUES (:id, :w, :org, :a, 'community', 'keep', 'discord', :l)"
        ),
        {"id": did, "w": winner, "org": org, "l": loser, "a": actor},
    )


def test_community_duels_fold_into_quarantined_prefix(sa_conn):
    """Phase-5 QUARANTINE: a community duel folds into 'community:'-prefixed rows at BOTH
    grains, so operator-Elo consumers (ranking, the meme/text loops — all keyed on
    unprefixed keys) can never read community taste."""
    _seed_org(sa_conn)
    _cand(sa_conn, 1, "orgA", "meme", {"template_id": "drake", "format": "Drake"})
    _cand(sa_conn, 2, "orgA", "tweet", {"text": "x"})
    _community_duel(sa_conn, "orgA", 1, 2, 1)
    sa_conn.commit()

    assert cq.apply_pending_content_events(sa_conn, "orgA") == 1
    cand = cq.get_content_quality(sa_conn, "orgA", "candidate")
    feat = cq.get_content_quality(sa_conn, "orgA", "feature")

    # community rows exist ONLY under the prefix…
    assert cand["community:1"]["elo"] > _BASE and cand["community:2"]["elo"] < _BASE
    assert feat["community:kind:meme"]["elo"] > _BASE
    assert feat["community:kind:tweet"]["elo"] < _BASE
    # …and the operator keyspace is untouched.
    assert "1" not in cand and "2" not in cand
    assert "kind:meme" not in feat and "kind:tweet" not in feat


def test_mixed_operator_and_community_batch_folds_separately(sa_conn):
    _seed_org(sa_conn)
    _cand(sa_conn, 1, "orgA", "meme", {"template_id": "drake"})
    _cand(sa_conn, 2, "orgA", "tweet", {"text": "x"})
    _duel(sa_conn, "orgA", 1, 2, 1)                 # operator: meme beats tweet
    _community_duel(sa_conn, "orgA", 2, 1, 2)       # community: tweet beats meme (opposite!)
    sa_conn.commit()

    assert cq.apply_pending_content_events(sa_conn, "orgA") == 2
    feat = cq.get_content_quality(sa_conn, "orgA", "feature")
    # the two signals live in separate keyspaces and may DISAGREE without interfering
    assert feat["kind:meme"]["elo"] > _BASE            # operators liked the meme
    assert feat["community:kind:meme"]["elo"] < _BASE  # the community didn't
