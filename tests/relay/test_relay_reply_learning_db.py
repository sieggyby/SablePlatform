"""Migration 063 — reply-learning CRUD tests (sable_platform.relay.db).

Exercises the P3/learning helpers against the in-memory ``sa_conn`` schema:
  * the relay_tweets embedding cache (get/set, NULL-means-embed-me, model swap);
  * recent_picked_skipped_examples (the §6 rolling rubric examples — picked via
    draft OR opportunity-thumbs-up, skipped via dismissed/expired);
  * low_quality_suggestions (the §10.4/§6 guardrail proposals — thumbs-down or
    high tell_score);
  * quality_dashboard_aggregates (the §8 P3 dashboard — tell buckets, pick-rate
    by source, suggestion thumbs) — ALL org-scoped, NO cost column.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(conn, *, org_id="orgA"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, '{}')"),
        {"o": org_id},
    )


def _seed_tweet(conn, x_id: str, *, handle="someone", text_body="hello") -> int:
    with immediate_txn(conn):
        return relay_db.upsert_relay_tweet(
            conn, x_id=x_id, x_author_handle=handle, text_body=text_body
        )


def _seed_suggestion(
    conn,
    sid: str,
    *,
    org_id="orgA",
    operator_handle="@op",
    source_tweet_id="1",
    opportunity_id=None,
    tell_score=None,
):
    conn.execute(
        text(
            "INSERT INTO reply_suggestions "
            "(id, operator_handle, org_id, source_tweet_id, opportunity_id, tell_score) "
            "VALUES (:id, :h, :org, :tid, :oid, :ts)"
        ),
        {
            "id": sid,
            "h": operator_handle,
            "org": org_id,
            "tid": source_tweet_id,
            "oid": opportunity_id,
            "ts": tell_score,
        },
    )


# ==========================================================================
# Embedding cache (get / set / model swap)
# ==========================================================================
def test_embedding_cache_set_then_get(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    _seed_tweet(sa_conn, "emb1")
    # No embedding yet -> get returns None (the ranker must embed it).
    assert relay_db.get_tweet_embedding(sa_conn, "emb1") is None
    vec = json.dumps([0.1, 0.2, 0.3])
    with immediate_txn(sa_conn):
        wrote = relay_db.set_tweet_embedding(sa_conn, "emb1", vec, "voyage-3-lite")
    assert wrote is True
    got = relay_db.get_tweet_embedding(sa_conn, "emb1")
    assert got is not None
    assert got[0] == vec
    assert got[1] == "voyage-3-lite"


def test_embedding_cache_get_unknown_tweet_is_none(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    assert relay_db.get_tweet_embedding(sa_conn, "nope") is None


def test_set_embedding_on_missing_tweet_returns_false(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        wrote = relay_db.set_tweet_embedding(sa_conn, "ghost", "[]", "m")
    assert wrote is False


def test_embedding_model_swap_overwrites(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    _seed_tweet(sa_conn, "emb2")
    with immediate_txn(sa_conn):
        relay_db.set_tweet_embedding(sa_conn, "emb2", "[1]", "model-a")
    with immediate_txn(sa_conn):
        relay_db.set_tweet_embedding(sa_conn, "emb2", "[2]", "model-b")
    got = relay_db.get_tweet_embedding(sa_conn, "emb2")
    assert got == ("[2]", "model-b")


def test_upsert_relay_tweet_preserves_embedding_columns(sa_conn):
    """A re-hydrate via upsert_relay_tweet (which does not touch embedding cols)
    must not clobber a previously-cached embedding."""
    _seed(sa_conn)
    sa_conn.commit()
    _seed_tweet(sa_conn, "emb3")
    with immediate_txn(sa_conn):
        relay_db.set_tweet_embedding(sa_conn, "emb3", "[9]", "m")
    # Re-hydrate the tweet (new text) — embedding columns are not in that writer.
    with immediate_txn(sa_conn):
        relay_db.upsert_relay_tweet(
            sa_conn, x_id="emb3", x_author_handle="a", text_body="refreshed"
        )
    assert relay_db.get_tweet_embedding(sa_conn, "emb3") == ("[9]", "m")


# ==========================================================================
# recent_picked_skipped_examples (§6 rubric)
# ==========================================================================
def test_picked_via_draft_and_thumbs_skipped_via_terminal(sa_conn):
    _seed(sa_conn, org_id="orgA")
    _seed(sa_conn, org_id="orgB")
    sa_conn.commit()
    t_draft = _seed_tweet(sa_conn, "pk-draft")
    t_thumb = _seed_tweet(sa_conn, "pk-thumb")
    t_dismiss = _seed_tweet(sa_conn, "sk-dismiss")
    t_expire = _seed_tweet(sa_conn, "sk-expire")
    t_active = _seed_tweet(sa_conn, "neutral-active")
    t_other = _seed_tweet(sa_conn, "other-org")
    with immediate_txn(sa_conn):
        o_draft = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_draft, sweep_source="mention", score=0.5)
        o_thumb = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_thumb, sweep_source="topic", score=0.5)
        o_dismiss = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_dismiss, sweep_source="topic", score=0.5)
        o_expire = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_expire, sweep_source="from_set", score=0.5)
        relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_active, sweep_source="topic", score=0.5)
        o_other = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgB", tweet_id=t_other, sweep_source="topic", score=0.5)
        # PICKED: opportunity-thumbs-up on o_thumb (suggestion_id NULL, thumb +1)
        relay_db.record_opportunity_feedback(
            sa_conn, opportunity_id=o_thumb, rater_handle="@op", rater_role="operator", thumb=1,
        )
        # SKIPPED: force terminal statuses
        sa_conn.execute(
            text("UPDATE relay_reply_opportunities SET status='dismissed' WHERE id=:i"), {"i": o_dismiss}
        )
        sa_conn.execute(
            text("UPDATE relay_reply_opportunities SET status='expired' WHERE id=:i"), {"i": o_expire}
        )
        # cross-org pick so org-scoping is observable
        relay_db.record_opportunity_feedback(
            sa_conn, opportunity_id=o_other, rater_handle="@op", rater_role="operator", thumb=1,
        )
    # PICKED via draft: a reply_suggestion linked to o_draft
    _seed_suggestion(sa_conn, "s-draft", org_id="orgA", opportunity_id=o_draft)
    sa_conn.commit()

    result = relay_db.recent_picked_skipped_examples(sa_conn, "orgA")
    picked_ids = {r["id"] for r in result["picked"]}
    skipped_ids = {r["id"] for r in result["skipped"]}
    assert picked_ids == {o_draft, o_thumb}  # draft + thumbed-up; org-scoped (no orgB)
    assert skipped_ids == {o_dismiss, o_expire}
    # rubric inputs present
    for r in result["picked"] + result["skipped"]:
        assert "tweet_text" in r and "sweep_source" in r
        assert "cost_usd" not in r and "cost" not in r


def test_picked_skipped_respects_limit(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    for i in range(5):
        tid = _seed_tweet(sa_conn, f"lim-{i}")
        with immediate_txn(sa_conn):
            oid = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=tid, sweep_source="topic", score=0.5)
            sa_conn.execute(
                text("UPDATE relay_reply_opportunities SET status='dismissed' WHERE id=:i"), {"i": oid}
            )
    result = relay_db.recent_picked_skipped_examples(sa_conn, "orgA", limit=2)
    assert len(result["skipped"]) == 2


# ==========================================================================
# low_quality_suggestions (§10.4 / §6 guardrail proposals)
# ==========================================================================
def test_low_quality_thumbs_down_or_high_tell_score(sa_conn):
    _seed(sa_conn, org_id="orgA")
    _seed(sa_conn, org_id="orgB")
    sa_conn.commit()
    t1 = _seed_tweet(sa_conn, "lq")
    with immediate_txn(sa_conn):
        oid = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t1, sweep_source="topic", score=0.5)
    # s_down: thumbs-down on the suggestion; s_high: high tell_score; s_good: clean
    _seed_suggestion(sa_conn, "s-down", org_id="orgA", tell_score=0.1)
    _seed_suggestion(sa_conn, "s-high", org_id="orgA", tell_score=0.9)
    _seed_suggestion(sa_conn, "s-good", org_id="orgA", tell_score=0.1)
    # a high-tell suggestion in a DIFFERENT org (must not leak)
    _seed_suggestion(sa_conn, "s-otherorg", org_id="orgB", tell_score=0.95)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.record_opportunity_feedback(
            sa_conn, opportunity_id=oid, suggestion_id="s-down",
            rater_handle="@op", rater_role="operator", thumb=-1,
        )
    rows = relay_db.low_quality_suggestions(sa_conn, "orgA", tell_score_threshold=0.6)
    ids = {r["id"] for r in rows}
    assert ids == {"s-down", "s-high"}  # down OR high-tell; org-scoped; clean excluded
    for r in rows:
        assert "tell_score" in r and "tell_flags_json" in r
        assert "cost_usd" not in r and "cost" not in r


def test_low_quality_no_duplicate_rows_on_multiple_downvotes(sa_conn):
    """Two thumbs-down on the same suggestion must not yield duplicate rows
    (the GROUP BY collapses them)."""
    _seed(sa_conn)
    sa_conn.commit()
    t1 = _seed_tweet(sa_conn, "lq-dup")
    with immediate_txn(sa_conn):
        oid = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t1, sweep_source="topic", score=0.5)
    _seed_suggestion(sa_conn, "s-dup", org_id="orgA", tell_score=0.1)
    sa_conn.commit()
    with immediate_txn(sa_conn):
        relay_db.record_opportunity_feedback(sa_conn, opportunity_id=oid, suggestion_id="s-dup", rater_handle="@a", rater_role="operator", thumb=-1)
        relay_db.record_opportunity_feedback(sa_conn, opportunity_id=oid, suggestion_id="s-dup", rater_handle="@b", rater_role="operator", thumb=-1)
    rows = relay_db.low_quality_suggestions(sa_conn, "orgA")
    assert [r["id"] for r in rows] == ["s-dup"]


# ==========================================================================
# quality_dashboard_aggregates (§8 P3 dashboard)
# ==========================================================================
def test_quality_dashboard_aggregates(sa_conn):
    _seed(sa_conn, org_id="orgA")
    _seed(sa_conn, org_id="orgB")
    sa_conn.commit()
    # tweets + opportunities across two sources, with picks
    t_m1 = _seed_tweet(sa_conn, "qd-m1")
    t_m2 = _seed_tweet(sa_conn, "qd-m2")
    t_t1 = _seed_tweet(sa_conn, "qd-t1")
    with immediate_txn(sa_conn):
        o_m1 = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_m1, sweep_source="mention", score=0.5)
        relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_m2, sweep_source="mention", score=0.5)
        o_t1 = relay_db.upsert_sweep_opportunity(sa_conn, org_id="orgA", tweet_id=t_t1, sweep_source="topic", score=0.5)
        # mention pick via thumbs-up; topic pick via draft
        relay_db.record_opportunity_feedback(sa_conn, opportunity_id=o_m1, rater_handle="@op", rater_role="operator", thumb=1)
    _seed_suggestion(sa_conn, "qd-s-draft", org_id="orgA", opportunity_id=o_t1, tell_score=0.1)   # low bucket
    _seed_suggestion(sa_conn, "qd-s-high", org_id="orgA", tell_score=0.85)                        # high bucket
    _seed_suggestion(sa_conn, "qd-s-null", org_id="orgA")                                         # null bucket
    sa_conn.commit()
    with immediate_txn(sa_conn):
        # suggestion thumbs: one up, one down
        relay_db.record_opportunity_feedback(sa_conn, opportunity_id=o_t1, suggestion_id="qd-s-draft", rater_handle="@op", rater_role="operator", thumb=1)
        relay_db.record_opportunity_feedback(sa_conn, opportunity_id=o_t1, suggestion_id="qd-s-high", rater_handle="@op", rater_role="operator", thumb=-1)

    agg = relay_db.quality_dashboard_aggregates(sa_conn, "orgA")

    # tell-score buckets
    assert agg["tell_score_buckets"]["low"] == 1
    assert agg["tell_score_buckets"]["high"] == 1
    assert agg["tell_score_buckets"]["null"] == 1

    # pick-rate by source
    assert agg["pick_rate_by_source"]["mention"] == {"total": 2, "picked": 1, "pick_rate": 0.5}
    assert agg["pick_rate_by_source"]["topic"] == {"total": 1, "picked": 1, "pick_rate": 1.0}

    # suggestion thumbs
    assert agg["suggestion_thumbs"] == {"up": 1, "down": 1}

    # NO cost field anywhere in the rollup
    assert "cost" not in agg and "cost_usd" not in agg


def test_quality_dashboard_empty_org(sa_conn):
    _seed(sa_conn, org_id="orgEmpty")
    sa_conn.commit()
    agg = relay_db.quality_dashboard_aggregates(sa_conn, "orgEmpty")
    assert agg["pick_rate_by_source"] == {}
    assert agg["suggestion_thumbs"] == {"up": 0, "down": 0}
    assert all(v == 0 for v in agg["tell_score_buckets"].values())


# ==========================================================================
# edited_reply_pairs (mig 088 — the edit-diff learning signal)
# ==========================================================================
def _seed_suggestion_with_variants(conn, sid, *, org_id="orgA", variants):
    conn.execute(
        text(
            "INSERT INTO reply_suggestions "
            "(id, operator_handle, org_id, source_tweet_id, source_text, variants_json) "
            "VALUES (:id, '@op', :org, '1', 'the target tweet', :vj)"
        ),
        {"id": sid, "org": org_id, "vj": json.dumps(variants)},
    )


def _seed_outcome(conn, oid, sid, *, was_edited=1, posted_text="posted!",
                  chosen_variant_idx=None, recorded_at=None):
    conn.execute(
        text(
            "INSERT INTO reply_outcomes "
            "(id, suggestion_id, posted_tweet_id, chosen_variant_idx, was_edited, "
            " posted_text, recorded_at) "
            "VALUES (:id, :sid, :ptid, :idx, :we, :pt, :ra)"
        ),
        {"id": oid, "sid": sid, "ptid": f"p{oid}", "idx": chosen_variant_idx,
         "we": was_edited, "pt": posted_text, "ra": recorded_at or _now_iso()},
    )


def test_edited_pairs_kinds_and_content(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    variants = [{"text": "draft zero"}, {"text": "draft one"}]
    _seed_suggestion_with_variants(sa_conn, "sA", variants=variants)
    _seed_suggestion_with_variants(sa_conn, "sB", variants=variants)
    # idx set -> 'edited', draft is THAT variant
    _seed_outcome(sa_conn, "o1", "sA", chosen_variant_idx=1,
                  posted_text="draft one but sharper", recorded_at="2026-07-28T02:00:00Z")
    # idx NULL -> 'replaced', draft is the FIRST variant (what we led with)
    _seed_outcome(sa_conn, "o2", "sB", chosen_variant_idx=None,
                  posted_text="something entirely their own", recorded_at="2026-07-28T01:00:00Z")
    sa_conn.commit()

    pairs = relay_db.edited_reply_pairs(sa_conn, "orgA")
    assert len(pairs) == 2
    assert pairs[0]["kind"] == "edited"
    assert pairs[0]["draft_text"] == "draft one"
    assert pairs[0]["posted_text"] == "draft one but sharper"
    assert pairs[0]["source_text"] == "the target tweet"
    assert pairs[1]["kind"] == "replaced"
    assert pairs[1]["draft_text"] == "draft zero"


def test_edited_pairs_org_scoped_and_filtered(sa_conn):
    _seed(sa_conn)
    _seed(sa_conn, org_id="orgB")
    sa_conn.commit()
    variants = [{"text": "d"}]
    _seed_suggestion_with_variants(sa_conn, "s1", variants=variants)
    _seed_suggestion_with_variants(sa_conn, "s2", variants=variants)
    _seed_suggestion_with_variants(sa_conn, "s3", variants=variants)
    _seed_suggestion_with_variants(sa_conn, "sX", org_id="orgB", variants=variants)
    _seed_outcome(sa_conn, "o1", "s1", was_edited=0)                 # unedited — excluded
    _seed_outcome(sa_conn, "o2", "s2", posted_text=None)             # no captured text — excluded
    _seed_outcome(sa_conn, "o3", "s3", posted_text="")               # empty text — excluded
    _seed_outcome(sa_conn, "oX", "sX", posted_text="other org")      # wrong org — excluded
    sa_conn.commit()

    assert relay_db.edited_reply_pairs(sa_conn, "orgA") == []
    other = relay_db.edited_reply_pairs(sa_conn, "orgB")
    assert len(other) == 1 and other[0]["posted_text"] == "other org"


def test_edited_pairs_malformed_variants_skipped(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    sa_conn.execute(
        text(
            "INSERT INTO reply_suggestions "
            "(id, operator_handle, org_id, source_tweet_id, variants_json) "
            "VALUES ('sBad', '@op', 'orgA', '1', 'not json'),"
            "       ('sEmpty', '@op', 'orgA', '1', '[]'),"
            "       ('sBlank', '@op', 'orgA', '1', :blank)"
        ),
        {"blank": json.dumps([{"text": "   "}])},
    )
    _seed_outcome(sa_conn, "o1", "sBad")
    _seed_outcome(sa_conn, "o2", "sEmpty")
    _seed_outcome(sa_conn, "o3", "sBlank")
    sa_conn.commit()
    # Bad JSON / no variants / blank draft text: skipped, never raises.
    assert relay_db.edited_reply_pairs(sa_conn, "orgA") == []


def test_edited_pairs_limit_and_order(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    variants = [{"text": "d"}]
    for i in range(4):
        _seed_suggestion_with_variants(sa_conn, f"s{i}", variants=variants)
        _seed_outcome(sa_conn, f"o{i}", f"s{i}", posted_text=f"post {i}",
                      recorded_at=f"2026-07-2{i}T00:00:00Z")
    sa_conn.commit()
    pairs = relay_db.edited_reply_pairs(sa_conn, "orgA", limit=2)
    assert [p["posted_text"] for p in pairs] == ["post 3", "post 2"]


def test_edited_pairs_invalid_idx_skipped_not_mislabeled(sa_conn):
    """A NON-NULL chosen_variant_idx that isn't a valid index means we cannot know
    which draft was edited — the row is SKIPPED, never mislabeled 'replaced'
    (which would pair the posted text against a draft the operator never chose)."""
    _seed(sa_conn)
    sa_conn.commit()
    _seed_suggestion_with_variants(sa_conn, "sOne", variants=[{"text": "only draft"}])
    _seed_suggestion_with_variants(sa_conn, "sTwo", variants=[{"text": "only draft"}])
    _seed_outcome(sa_conn, "o1", "sOne", chosen_variant_idx=3, posted_text="posted A")
    _seed_outcome(sa_conn, "o2", "sTwo", chosen_variant_idx=-1, posted_text="posted B")
    sa_conn.commit()
    assert relay_db.edited_reply_pairs(sa_conn, "orgA") == []


def test_edited_pairs_whitespace_only_posted_text_excluded(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    _seed_suggestion_with_variants(sa_conn, "sWs", variants=[{"text": "d"}])
    _seed_outcome(sa_conn, "oWs", "sWs", posted_text="   ")
    sa_conn.commit()
    assert relay_db.edited_reply_pairs(sa_conn, "orgA") == []


def test_edited_pairs_float_idx_skipped_not_truncated(sa_conn):
    """SQLite can hold a malformed float idx; int(0.5) would truncate to 0 and
    pair the wrong draft — a non-integral idx must skip the row entirely."""
    _seed(sa_conn)
    sa_conn.commit()
    _seed_suggestion_with_variants(sa_conn, "sF", variants=[{"text": "a"}, {"text": "b"}])
    _seed_outcome(sa_conn, "oF", "sF", chosen_variant_idx=0.5, posted_text="posted")
    sa_conn.commit()
    assert relay_db.edited_reply_pairs(sa_conn, "orgA") == []
