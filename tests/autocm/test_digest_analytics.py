"""C3.7 — digest analytics (DIGEST §3 member-analytics + ratios + voice-drift).

Fixture-driven: seed N relay_messages (INCLUDING filter-skipped no-draft messages)
+ N drafts/reviews with KNOWN categories/decisions, then assert each analytics
signal equals the HAND-COMPUTED expected value (not just "runs"):

  * Volume + member-activity counts INCLUDE filter-skipped messages (corpus, not
    autocm_drafts);
  * auto/HITL/clean ratios (reuse C3.5a gather_review_stats);
  * FAQ-frequency clustering;
  * sentiment via the deterministic seam (the fake — KeywordSentimentScorer);
  * cultist_candidates() / topic_clusters() over the relay_messages corpus;
  * voice-drift heavy-edit clusters over autocm_reviews.

Everything offline — NO telegram / anthropic / network.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from sable_platform.autocm.digest.analytics import (
    KeywordSentimentScorer,
    VOICE_DRIFT_THRESHOLD,
    autonomy_ratios,
    category_clean_rates,
    community_health_delta,
    cultist_candidates,
    frequent_questions,
    score_sentiment,
    topic_clusters,
    voice_drift,
    volume,
    week_bounds,
)


# the deterministic digest week anchor (a Monday 00:00 UTC). All fixtures land
# inside [WEEK, WEEK+7d); the prior-week leg lands in [WEEK-7d, WEEK).
WEEK = datetime(2026, 5, 18, 0, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
def _seed_org_client(conn, org_id="orgRM"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled) VALUES (:o, 1)"), {"o": org_id}
    )
    conn.execute(
        text(
            "INSERT INTO autocm_clients (org_id, display_name, autonomy_state, enabled) "
            "VALUES (:o, 'RobotMoney', 'hitl', 1)"
        ),
        {"o": org_id},
    )
    conn.commit()
    client_id = int(
        conn.execute(text("SELECT id FROM autocm_clients WHERE org_id = :o"), {"o": org_id}).fetchone()[0]
    )
    return org_id, client_id


def _seed_chat(conn, org_id, chat_id="-100"):
    conn.execute(
        text(
            "INSERT INTO relay_chats (org_id, platform, chat_id, title) "
            "VALUES (:o, 'telegram', :cid, 'main')"
        ),
        {"o": org_id, "cid": chat_id},
    )
    conn.commit()
    return int(
        conn.execute(text("SELECT id FROM relay_chats WHERE chat_id = :cid"), {"cid": chat_id}).fetchone()[0]
    )


def _seed_member(conn, name):
    conn.execute(text("INSERT INTO relay_members (display_name) VALUES (:d)"), {"d": name})
    return int(conn.execute(text("SELECT id FROM relay_members ORDER BY id DESC LIMIT 1")).fetchone()[0])


_MSG_SEQ = {"n": 0}


def _seed_message(conn, org_id, chat_row_id, *, member_id, text_body, received_at):
    _MSG_SEQ["n"] += 1
    ext_mid = f"m{_MSG_SEQ['n']}"
    conn.execute(
        text(
            "INSERT INTO relay_messages "
            "(org_id, chat_id, member_id, platform, external_message_id, text, received_at) "
            "VALUES (:o, :chat, :mid, 'telegram', :emid, :txt, :ra)"
        ),
        {"o": org_id, "chat": chat_row_id, "mid": member_id, "emid": ext_mid, "txt": text_body, "ra": received_at},
    )
    conn.commit()


def _seed_draft(conn, client_id, *, category, status, created_at):
    conn.execute(
        text(
            "INSERT INTO autocm_drafts (client_id, category, status, register, draft_text, created_at) "
            "VALUES (:c, :cat, :st, 'calm', 'draft', :ca)"
        ),
        {"c": client_id, "cat": category, "st": status, "ca": created_at},
    )
    conn.commit()
    return int(conn.execute(text("SELECT id FROM autocm_drafts ORDER BY id DESC LIMIT 1")).fetchone()[0])


def _seed_review(conn, client_id, draft_id, *, decision, edit_diff_size, is_clean, reviewed_at):
    conn.execute(
        text(
            "INSERT INTO autocm_reviews "
            "(draft_id, client_id, decision, edit_diff_size, is_clean_approval, reviewed_at) "
            "VALUES (:d, :c, :dec, :eds, :cl, :ra)"
        ),
        {"d": draft_id, "c": client_id, "dec": decision, "eds": edit_diff_size, "cl": is_clean, "ra": reviewed_at},
    )
    conn.commit()


# ---------------------------------------------------------------------------
# week_bounds
# ---------------------------------------------------------------------------
def test_week_bounds_is_7_days_exclusive_end():
    start, end = week_bounds(WEEK)
    assert start == "2026-05-18T00:00:00Z"
    assert end == "2026-05-25T00:00:00Z"


# ---------------------------------------------------------------------------
# Sentiment seam — deterministic fake (the offline default)
# ---------------------------------------------------------------------------
def test_keyword_sentiment_is_deterministic_and_signed():
    scorer = KeywordSentimentScorer()
    # 3 positive hits, 1 negative → (3-1)/4 = 0.5
    assert scorer.score(["love this, awesome, great work", "this is a scam"]) == 0.5
    # neutral / no lexicon hits → 0.0
    assert scorer.score(["the quick brown fox"]) == 0.0
    # all negative → -1.0
    assert scorer.score(["rug scam dump"]) == -1.0


# ---------------------------------------------------------------------------
# Volume — INCLUDES filter-skipped (no-draft) messages
# ---------------------------------------------------------------------------
def test_volume_counts_full_corpus_including_filter_skipped(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    chat = _seed_chat(sa_conn, org_id)
    alice = _seed_member(sa_conn, "alice")
    bob = _seed_member(sa_conn, "bob")

    # 5 in-window messages from 2 members. NONE of these have draft rows (all
    # filter-skipped) — they MUST still count for volume.
    for i in range(3):
        _seed_message(sa_conn, org_id, chat, member_id=alice, text_body=f"gm {i}", received_at=_iso(datetime(2026, 5, 19, 9, i, tzinfo=timezone.utc)))
    for i in range(2):
        _seed_message(sa_conn, org_id, chat, member_id=bob, text_body=f"hello {i}", received_at=_iso(datetime(2026, 5, 20, 9, i, tzinfo=timezone.utc)))
    # 1 prior-week message (alice already existed last week → not "new" this week).
    _seed_message(sa_conn, org_id, chat, member_id=alice, text_body="last week", received_at=_iso(datetime(2026, 5, 12, 9, 0, tzinfo=timezone.utc)))

    v = volume(sa_conn, client_id, WEEK, org_id=org_id)
    assert v.messages == 5  # filter-skipped INCLUDED
    assert v.distinct_members == 2
    # alice's first-ever message was prior week → not new; bob's first is in-window → new.
    assert v.new_members == 1
    assert v.prev_messages == 1
    # 5 vs 1 prior → +400%
    assert v.wow_pct == 4.0


# ---------------------------------------------------------------------------
# Autonomy ratios — reuse gather_review_stats; hand-computed
# ---------------------------------------------------------------------------
def test_autonomy_ratios_hand_computed(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    ca = _iso(datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc))
    ra = ca
    # 10 drafts: 6 auto_sent, 3 approved (HITL), 1 escalated.
    for _ in range(6):
        _seed_draft(sa_conn, client_id, category="mechanics", status="auto_sent", created_at=ca)
    hitl_ids = [_seed_draft(sa_conn, client_id, category="mechanics", status="approved", created_at=ca) for _ in range(3)]
    _seed_draft(sa_conn, client_id, category="threat", status="escalated", created_at=ca)
    # reviews on the 3 HITL drafts: 2 clean approvals, 1 heavy-edit (rejection).
    _seed_review(sa_conn, client_id, hitl_ids[0], decision="approve", edit_diff_size=0.0, is_clean=1, reviewed_at=ra)
    _seed_review(sa_conn, client_id, hitl_ids[1], decision="approve", edit_diff_size=0.0, is_clean=1, reviewed_at=ra)
    _seed_review(sa_conn, client_id, hitl_ids[2], decision="edit", edit_diff_size=0.5, is_clean=0, reviewed_at=ra)

    r = autonomy_ratios(sa_conn, client_id, WEEK)
    assert r.total_drafts == 10
    assert r.auto_handled == 6
    assert r.hitl_handled == 3
    assert r.escalated == 1
    assert r.auto_pct == 0.6
    assert r.hitl_pct == 0.3
    assert r.escalated_pct == 0.1
    assert r.reviewed == 3
    assert r.clean_approvals == 2
    assert r.clean_approval_rate == round(2 / 3, 4)


def test_autonomy_ratios_window_excludes_other_weeks(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    in_window = _iso(datetime(2026, 5, 19, tzinfo=timezone.utc))
    out_window = _iso(datetime(2026, 5, 11, tzinfo=timezone.utc))
    _seed_draft(sa_conn, client_id, category="mechanics", status="auto_sent", created_at=in_window)
    _seed_draft(sa_conn, client_id, category="mechanics", status="auto_sent", created_at=out_window)
    r = autonomy_ratios(sa_conn, client_id, WEEK)
    assert r.total_drafts == 1


def test_category_clean_rates_reuses_gather_review_stats(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    ra = _iso(datetime(2026, 5, 19, tzinfo=timezone.utc))
    ids = [_seed_draft(sa_conn, client_id, category="mechanics", status="approved", created_at=ra) for _ in range(4)]
    # 3 clean / 4 reviewed → 0.75
    for i, did in enumerate(ids):
        clean = 1 if i < 3 else 0
        _seed_review(sa_conn, client_id, did, decision="approve" if clean else "reject", edit_diff_size=0.0, is_clean=clean, reviewed_at=ra)
    rates = category_clean_rates(sa_conn, client_id, ["mechanics"])
    assert rates["mechanics"] == 0.75


# ---------------------------------------------------------------------------
# FAQ-frequency — clustered over the corpus (incl. filter-skipped)
# ---------------------------------------------------------------------------
def test_frequent_questions_clusters_and_ranks(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    chat = _seed_chat(sa_conn, org_id)
    m = _seed_member(sa_conn, "asker")
    base = datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)
    # "how does the agent decide" asked 3x (varying punctuation/case → same cluster).
    for i in range(3):
        _seed_message(sa_conn, org_id, chat, member_id=m, text_body="How does the agent decide?" if i == 0 else "how does the agent decide", received_at=_iso(base.replace(minute=i)))
    # "what is the contract address" asked 2x.
    for i in range(2):
        _seed_message(sa_conn, org_id, chat, member_id=m, text_body="what is the contract address?", received_at=_iso(base.replace(minute=10 + i)))
    # a non-question — must NOT enter clustering.
    _seed_message(sa_conn, org_id, chat, member_id=m, text_body="gm everyone", received_at=_iso(base.replace(minute=20)))

    fq = frequent_questions(sa_conn, client_id, WEEK, org_id=org_id)
    assert fq[0][1] == 3  # most-asked first
    assert fq[1][1] == 2
    assert len(fq) == 2  # the non-question is excluded


# ---------------------------------------------------------------------------
# Sentiment over the corpus via the seam
# ---------------------------------------------------------------------------
def test_score_sentiment_over_corpus(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    chat = _seed_chat(sa_conn, org_id)
    m = _seed_member(sa_conn, "fan")
    base = datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)
    _seed_message(sa_conn, org_id, chat, member_id=m, text_body="love this project, awesome", received_at=_iso(base))
    _seed_message(sa_conn, org_id, chat, member_id=m, text_body="this is a scam", received_at=_iso(base.replace(minute=1)))
    # 2 positive (love, awesome) + 1 negative (scam) → (2-1)/3
    s = score_sentiment(sa_conn, client_id, WEEK, KeywordSentimentScorer(), org_id=org_id)
    assert s == round(1 / 3, 4)


def test_score_sentiment_empty_week_is_neutral(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    assert score_sentiment(sa_conn, client_id, WEEK, KeywordSentimentScorer(), org_id=org_id) == 0.0


# ---------------------------------------------------------------------------
# Community-health delta (leg C)
# ---------------------------------------------------------------------------
def test_community_health_delta_positive_week(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    chat = _seed_chat(sa_conn, org_id)
    m = _seed_member(sa_conn, "newjoiner")
    # this week: positive sentiment + a new member.
    _seed_message(sa_conn, org_id, chat, member_id=m, text_body="love it, awesome, great", received_at=_iso(datetime(2026, 5, 19, tzinfo=timezone.utc)))
    health = community_health_delta(sa_conn, client_id, WEEK, KeywordSentimentScorer(), org_id=org_id)
    # this week positive, prior week empty (0.0) → delta > 0.
    assert health.score > 0.0
    assert health.prev_score == 0.0
    assert health.delta == round(health.score - health.prev_score, 4)


# ---------------------------------------------------------------------------
# Voice-drift — heavy edits clustered by category × register
# ---------------------------------------------------------------------------
def test_voice_drift_clusters_heavy_edits_only(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    ra = _iso(datetime(2026, 5, 19, tzinfo=timezone.utc))
    # 2 heavy edits in mechanics/calm (> 0.30), 1 light edit (<= 0.30, NOT drift).
    d1 = _seed_draft(sa_conn, client_id, category="mechanics", status="approved", created_at=ra)
    d2 = _seed_draft(sa_conn, client_id, category="mechanics", status="approved", created_at=ra)
    d3 = _seed_draft(sa_conn, client_id, category="mechanics", status="approved", created_at=ra)
    _seed_review(sa_conn, client_id, d1, decision="edit", edit_diff_size=0.5, is_clean=0, reviewed_at=ra)
    _seed_review(sa_conn, client_id, d2, decision="edit", edit_diff_size=0.4, is_clean=0, reviewed_at=ra)
    _seed_review(sa_conn, client_id, d3, decision="edit", edit_diff_size=0.30, is_clean=1, reviewed_at=ra)  # boundary: NOT heavy

    clusters = voice_drift(sa_conn, client_id, WEEK)
    assert len(clusters) == 1
    assert clusters[0].category == "mechanics"
    assert clusters[0].register == "calm"
    assert clusters[0].count == 2  # only the > 0.30 edits


def test_voice_drift_threshold_is_30pct():
    assert VOICE_DRIFT_THRESHOLD == 0.30


# ---------------------------------------------------------------------------
# Cultist candidates — corpus-wide substantive-question signal
# ---------------------------------------------------------------------------
def test_cultist_candidates_over_corpus(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    chat = _seed_chat(sa_conn, org_id)
    rohan = _seed_member(sa_conn, "rohan")
    lurker = _seed_member(sa_conn, "lurker")
    base = datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)
    # rohan: 3 substantive questions (>= min 2) → candidate.
    for i in range(3):
        _seed_message(sa_conn, org_id, chat, member_id=rohan, text_body=f"how does mechanic {i} work?", received_at=_iso(base.replace(minute=i)))
    # lurker: 1 question (< min 2) → NOT a candidate.
    _seed_message(sa_conn, org_id, chat, member_id=lurker, text_body="what is this?", received_at=_iso(base.replace(minute=30)))

    cands = cultist_candidates(sa_conn, client_id, WEEK, org_id=org_id)
    assert len(cands) == 1
    assert cands[0].handle == "rohan"
    assert cands[0].question_count == 3


# ---------------------------------------------------------------------------
# Topic clusters — subsquad pollination (members per shared topic)
# ---------------------------------------------------------------------------
def test_topic_clusters_surface_shared_topic_pairs(sa_conn):
    org_id, client_id = _seed_org_client(sa_conn)
    chat = _seed_chat(sa_conn, org_id)
    rohan = _seed_member(sa_conn, "rohan")
    adi = _seed_member(sa_conn, "adi")
    solo = _seed_member(sa_conn, "solo")
    base = datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)
    # rohan + adi both mention "reasoning logs" → a 2-member topic cluster.
    _seed_message(sa_conn, org_id, chat, member_id=rohan, text_body="where are the reasoning logs", received_at=_iso(base))
    _seed_message(sa_conn, org_id, chat, member_id=adi, text_body="reasoning logs would be great", received_at=_iso(base.replace(minute=1)))
    # solo mentions a UNIQUE topic alone → not a cluster.
    _seed_message(sa_conn, org_id, chat, member_id=solo, text_body="staking rewards question", received_at=_iso(base.replace(minute=2)))

    clusters = topic_clusters(sa_conn, client_id, WEEK, org_id=org_id)
    topics = {c.topic: c for c in clusters}
    assert "reasoning" in topics and topics["reasoning"].member_count == 2
    assert "logs" in topics and topics["logs"].member_count == 2
    # solo's unique tokens never reach 2 members.
    assert "staking" not in topics
