"""SableRelay feed — poller / publisher / sweeper (MEGAPLAN C2.4).

The background process (``relay-poller``, PLAN §7) that runs SEPARATELY from the
listener (``relay-bot``). Three cooperating modules over the ``relay_*`` outbox
tables (migration 057), all reusing the C2.1 ``relay/db.py`` helpers, the
``relay/bot/txn.immediate_txn`` primitive (no external API call inside a
``BEGIN IMMEDIATE``), and the C1.2 ``relay/socialdata.SocialDataClient``:

  * :mod:`~sable_platform.relay.feed.poller` — Flow A per-enabled-client
    timeline poll (``since_id`` cursor), gated by the PROACTIVE per-org daily
    cost cap (``check_daily_socialdata_budget``); PLUS Flow D 4.6 reply
    follow-through tracking (budget-capped per opportunity).
  * :mod:`~sable_platform.relay.feed.publisher` — the §3.1 publish-exactly-once
    state machine (claim → external send OUTSIDE txn → record publication ON
    CONFLICT DO NOTHING → done; retry/dead with backoff).
  * :mod:`~sable_platform.relay.feed.sweeper` — submission expiry, the §15.5
    retention GC windows, stuck-claim reset (>5min), and §3.2 reconciliation
    (best-effort orphan external-message find before recycling a claim).
  * :mod:`~sable_platform.relay.feed.canonical` — §15.1 URL canonicalization +
    tweet-hydration-rejection (accept only x.com/twitter.com ``/<user>/status/<id>``;
    reject everything else with no submission; hydrate via C1.2; deleted /
    private / suspended / not-found → reject).

Guarantee (PLAN §3.1, the C2.4 exit criterion): **DB-exactly-once + external
effectively-once with reconciliation.**
"""
from __future__ import annotations

__all__ = [
    "poller",
    "publisher",
    "sweeper",
    "canonical",
]
