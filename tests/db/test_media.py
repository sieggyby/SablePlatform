"""Migration 066 — media recommendation-center CRUD tests (sable_platform.db.media).

Exercises the media-rec helpers against the in-memory ``sa_conn`` schema:
  * apply_pending_media_events — the forward-only incremental Elo update
    (chosen beats every other slate asset, counts bumped, idempotent);
  * get_media_quality — the {elo, pick_rate} rollup;
  * get/set_media_embedding — the per-asset vector cache (round-trip + swap);
  * stamp_outcome_media — reply_outcomes.media_content_id stamp.
All org-scoped; NO cost column anywhere.
"""
from __future__ import annotations

from sqlalchemy import text

from sable_platform.db import media


def _seed_org(conn, org_id="orgA"):
    conn.execute(
        text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"),
        {"o": org_id},
    )
    conn.commit()


# ==========================================================================
# apply_pending_media_events — the incremental Elo update (the focused test)
# ==========================================================================
def test_apply_pending_media_events_elo_and_counts(sa_conn):
    """A slate of 3 with one chosen: winner Elo up, the other two down, n_offered
    counted for all three, n_chosen only for the winner; second call is a no-op.
    """
    _seed_org(sa_conn)

    # One slate of 3, "b" chosen.
    eid = media.log_media_rec_event(
        sa_conn, "orgA", "@op", "tweet:1", ["a", "b", "c"], "b"
    )
    sa_conn.commit()
    assert isinstance(eid, int) and eid > 0

    applied = media.apply_pending_media_events(sa_conn, "orgA")
    assert applied == 1

    q = media.get_media_quality(sa_conn, "orgA")
    assert set(q) == {"a", "b", "c"}

    # Winner above the 1500 base, both losers below it.
    assert q["b"]["elo"] > 1500.0
    assert q["a"]["elo"] < 1500.0
    assert q["c"]["elo"] < 1500.0
    # Symmetric start (all began at 1500), so the two losers fall equally.
    assert abs(q["a"]["elo"] - q["c"]["elo"]) < 1e-9
    # Elo is zero-sum across the pairwise games: winner gain == total loser loss.
    winner_gain = q["b"]["elo"] - 1500.0
    loser_loss = (1500.0 - q["a"]["elo"]) + (1500.0 - q["c"]["elo"])
    assert abs(winner_gain - loser_loss) < 1e-6

    # Pick-rate: every asset offered once; only "b" chosen.
    assert q["a"]["pick_rate"] == 0.0
    assert q["b"]["pick_rate"] == 1.0
    assert q["c"]["pick_rate"] == 0.0

    # Raw counters back the pick_rate.
    rows = sa_conn.execute(
        text(
            "SELECT content_id, n_offered, n_chosen FROM media_quality "
            "WHERE org_id = 'orgA' ORDER BY content_id"
        )
    ).fetchall()
    counts = {r[0]: (int(r[1]), int(r[2])) for r in rows}
    assert counts == {"a": (1, 0), "b": (1, 1), "c": (1, 0)}

    # The event is now applied.
    applied_flag = sa_conn.execute(
        text("SELECT applied FROM media_rec_events WHERE id = :id"), {"id": eid}
    ).fetchone()[0]
    assert int(applied_flag) == 1

    # Idempotent — a second sweep processes nothing and changes nothing.
    elo_before = {k: v["elo"] for k, v in q.items()}
    applied2 = media.apply_pending_media_events(sa_conn, "orgA")
    assert applied2 == 0
    q2 = media.get_media_quality(sa_conn, "orgA")
    assert {k: v["elo"] for k, v in q2.items()} == elo_before


def test_apply_pending_media_events_no_chosen_only_offers(sa_conn):
    """A slate offered with nothing attached bumps n_offered but carries no Elo
    signal (everyone stays at the 1500 base), and still marks applied."""
    _seed_org(sa_conn)
    media.log_media_rec_event(sa_conn, "orgA", "@op", "tweet:2", ["x", "y"], None)
    sa_conn.commit()

    assert media.apply_pending_media_events(sa_conn, "orgA") == 1
    q = media.get_media_quality(sa_conn, "orgA")
    assert q["x"]["elo"] == 1500.0
    assert q["y"]["elo"] == 1500.0
    assert q["x"]["pick_rate"] == 0.0
    assert q["y"]["pick_rate"] == 0.0


def test_apply_pending_media_events_is_org_scoped(sa_conn):
    """One org's slates never fold into another org's quality."""
    _seed_org(sa_conn, "orgA")
    _seed_org(sa_conn, "orgB")
    media.log_media_rec_event(sa_conn, "orgA", "@op", "t", ["a", "b"], "a")
    sa_conn.commit()

    assert media.apply_pending_media_events(sa_conn, "orgB") == 0
    assert media.get_media_quality(sa_conn, "orgB") == {}
    # orgA's pending event is untouched by the orgB sweep.
    assert media.apply_pending_media_events(sa_conn, "orgA") == 1


def test_repeated_picks_accumulate(sa_conn):
    """Two slates both picking the same asset push its Elo strictly higher than
    a single pick — the aggregate signal compounds forward-only."""
    _seed_org(sa_conn)
    media.log_media_rec_event(sa_conn, "orgA", "@op", "t1", ["a", "b"], "a")
    sa_conn.commit()
    media.apply_pending_media_events(sa_conn, "orgA")
    elo_after_one = media.get_media_quality(sa_conn, "orgA")["a"]["elo"]

    media.log_media_rec_event(sa_conn, "orgA", "@op", "t2", ["a", "b"], "a")
    sa_conn.commit()
    media.apply_pending_media_events(sa_conn, "orgA")
    q = media.get_media_quality(sa_conn, "orgA")
    assert q["a"]["elo"] > elo_after_one
    assert q["a"]["pick_rate"] == 1.0  # 2 offered, 2 chosen
    assert q["b"]["pick_rate"] == 0.0  # 2 offered, 0 chosen


# ==========================================================================
# Embedding cache (round-trip, NULL-means-embed-me, model swap)
# ==========================================================================
def test_media_embedding_set_then_get_roundtrip(sa_conn):
    _seed_org(sa_conn)
    assert media.get_media_embedding(sa_conn, "orgA", "clip1") is None

    media.set_media_embedding(sa_conn, "orgA", "clip1", [0.1, 0.2, 0.3], "model-v1")
    sa_conn.commit()

    got = media.get_media_embedding(sa_conn, "orgA", "clip1")
    assert got is not None
    vector, model = got
    assert model == "model-v1"
    assert len(vector) == 3
    assert abs(vector[0] - 0.1) < 1e-9
    assert abs(vector[2] - 0.3) < 1e-9


def test_media_embedding_model_swap_overwrites(sa_conn):
    _seed_org(sa_conn)
    media.set_media_embedding(sa_conn, "orgA", "clip1", [1.0], "old")
    media.set_media_embedding(sa_conn, "orgA", "clip1", [2.0, 3.0], "new")
    sa_conn.commit()
    vector, model = media.get_media_embedding(sa_conn, "orgA", "clip1")
    assert model == "new"
    assert vector == [2.0, 3.0]
    # Exactly one row (upsert, not append).
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM media_embeddings WHERE org_id='orgA' AND content_id='clip1'")
    ).fetchone()[0]
    assert int(n) == 1


def test_media_embedding_is_org_scoped(sa_conn):
    _seed_org(sa_conn, "orgA")
    _seed_org(sa_conn, "orgB")
    media.set_media_embedding(sa_conn, "orgA", "clip1", [9.0], "m")
    sa_conn.commit()
    assert media.get_media_embedding(sa_conn, "orgB", "clip1") is None


# ==========================================================================
# stamp_outcome_media — reply_outcomes.media_content_id
# ==========================================================================
def test_stamp_outcome_media(sa_conn):
    _seed_org(sa_conn)
    sa_conn.execute(
        text(
            "INSERT INTO reply_suggestions "
            "(id, operator_handle, org_id, source_tweet_id, variants_json) "
            "VALUES ('sug1', '@op', 'orgA', '1', '[]')"
        )
    )
    sa_conn.execute(
        text(
            "INSERT INTO reply_outcomes "
            "(id, suggestion_id, posted_tweet_id) "
            "VALUES ('out1', 'sug1', '999')"
        )
    )
    sa_conn.commit()

    assert media.stamp_outcome_media(sa_conn, "sug1", "clipX") is True
    sa_conn.commit()
    row = sa_conn.execute(
        text("SELECT media_content_id FROM reply_outcomes WHERE id = 'out1'")
    ).fetchone()
    assert row[0] == "clipX"

    # A suggestion with no outcome row updates nothing.
    assert media.stamp_outcome_media(sa_conn, "nope", "clipZ") is False
