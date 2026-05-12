# SocialData.tools API — Best Practices

Cross-tool reference for all Sable projects that call the SocialData API. Prioritizes real-world lessons from production usage over theoretical recommendations.

**Projects using SocialData:**
- **Cult Grader** — deep community diagnostics (timeline, mentions, ticker, replies, team handles, mutuals)
- **Lead Identifier** — enrichment pass on top-500 shortlisted projects (profile, engagement, mentions)
- **Slopper** — pulse tracking, watchlist scanning, trend detection, reply suggestions
- **SableKOL** — bulk follow-graph extraction + per-candidate enrichment (profile + 20-tweet timeline read fed to Grok for interpretation; see `SableKOL/docs/ENRICHMENT.md`)

---

## 1. Pricing Model

SocialData charges **$0.0002 per item returned** ($0.20 per 1,000 items). In practice, a single paginated API call returning ~20 tweets costs ~$0.004. Our codebase uses the simplifying constant `$0.002 per API call` which is conservative but accounts for mixed endpoint usage.

**Failed requests are not charged.** Only successfully returned data counts. HTTP 4xx/5xx responses cost nothing.

**402 = balance exhausted.** No retry will help — the account needs funds. Treat 402 as immediately fatal for the current collection phase.

---

## 2. Endpoints We Use

| Endpoint | Path | Cost Model | Used By |
|----------|------|-----------|---------|
| User profile | `GET /twitter/user/{handle}` | 1 call | All |
| User timeline | `GET /twitter/user/{user_id}/tweets` | ~5 calls per 100 tweets | Cult Grader, Slopper, SableKOL |
| Search | `GET /twitter/search` | ~5 calls per 100 results | Cult Grader, Lead Identifier, Slopper |
| Single tweet | `GET /twitter/tweets/{tweet_id}` | 1 call | Slopper |
| Following list | `GET /twitter/user/{user_id}/following` | 1 call (capped) | Cult Grader |
| Followers list | `GET /twitter/followers/list` | $0.0002 per follower returned | SableKOL bulk-fetch |
| Friends list | `GET /twitter/friends/list` | $0.0002 per followed user returned | SableKOL bulk-fetch |

All endpoints use `Authorization: Bearer {api_key}` and return JSON.

**Gotcha: timeline endpoint takes numeric id, not screen name.**
`/twitter/user/<screen_name>/tweets` returns HTTP 404 even for active
accounts. Only `/twitter/user/<numeric_id>/tweets` works. Always chain
a profile fetch first to resolve `id_str`. Discovered live in SableKOL
2026-05-10 mid-deploy; cost ~$0.50 to diagnose. See
`SableKOL/sable_kol/socialdata_live.py::fetch_live_signal` for the
canonical chained-fetch pattern.

---

## 3. Pagination

SocialData uses **cursor-based pagination**. Each response includes a `next_cursor` field; pass it as a query parameter to get the next page.

### Rules

1. **Pages return ~20 items.** Budget API calls accordingly: 100 tweets = ~5 calls.
2. **Stop when `next_cursor` is absent or null.** Not when the page is empty — some endpoints return empty pages with a valid cursor.
3. **Cursor cycling is real.** When an account has fewer tweets than requested, the API can loop back to the beginning and return duplicates indefinitely. **You must detect this.**

### Cursor Cycling Detection (Critical)

This burned real money before we caught it. Small accounts with <100 tweets would consume the entire cost ceiling returning the same tweets over and over.

**Detection pattern:** Track seen IDs per page. If >80% of a page contains already-seen IDs, break pagination immediately.

```python
page_ids = {tweet["id_str"] for tweet in page}
new_ids = page_ids - seen_ids
if len(page_ids) > 0 and len(new_ids) / len(page_ids) < 0.2:
    break  # Cursor is cycling
seen_ids |= new_ids
```

**This applies to both `/user/{id}/tweets` and `/twitter/search`.** Implement it everywhere you paginate.

---

## 4. Rate Limiting

SocialData returns **HTTP 429** when rate-limited. The documented limit is ~120 requests/minute, but this varies.

### Backoff Strategy

Exponential backoff with jitter. Our production constants:

```
Attempt 1: wait ~1s  (0.5-1.5s with jitter)
Attempt 2: wait ~4s  (2-6s)
Attempt 3: wait ~16s (8-24s)
Attempt 4: wait ~64s (32-96s)
Attempt 5: raise RateLimitError
```

**Jitter is mandatory for concurrent requests.** Without it, parallel fetches that hit 429 simultaneously will all retry at the same instant and immediately hit 429 again.

```python
actual_delay = nominal_delay * (0.5 + random.random())
```

### Conservative Throughput

Lead Identifier caps at **2 RPS** for steady-state enrichment. Cult Grader uses **2 concurrent windows** for windowed search. Both are well under the limit and avoid triggering 429s in normal operation.

**Recommendation:** Stay at 2 RPS or below. The cost of a 429 retry cascade (wasted wall-clock time, potential partial data) outweighs the speed gain from pushing throughput.

---

## 5. Search Operators

SocialData supports the full Twitter search operator syntax:

| Operator | Example | Notes |
|----------|---------|-------|
| Mention | `@handle` | Most reliable for community detection |
| Exclude author | `-from:handle` | Essential: exclude project's own tweets from mention search |
| Cashtag | `$TIG` | Case-insensitive; captures ticker discussion |
| Conversation | `conversation_id:12345` | Fetches full reply thread for a tweet |
| Date window | `since:2025-01-01 until:2025-02-01` | Used for windowed collection |
| Language | `lang:en` | Unreliable — not recommended for filtering |
| Engagement | `min_retweets:5` | Available but unused in our tools |
| Logical OR | `@handle OR "project name"` | Lead Identifier uses this for broader mention capture |

### Search Query Patterns by Tool

**Cult Grader mentions:** `@{handle} -from:{handle}` — all mentions excluding self
**Cult Grader ticker:** `${ticker}` — cashtag search
**Cult Grader replies:** `conversation_id:{tweet_id}` — per-conversation thread expansion
**Lead Identifier mentions:** `@{handle} OR "{project_name}"` — broader capture via OR
**Slopper deep scan:** Raw keyword queries (`"crypto"`, `"defi"`, etc.)

---

## 6. Windowed Search (Spike-Resilient Collection)

### The Problem

For historical collection (onboard mode, deep dives), a single paginated search across months of data hits two failure modes:

1. **Cursor exhaustion from spikes.** A viral tweet week generates thousands of results that bury months of quieter-but-valuable data under the result cap.
2. **No resume on failure.** If collection fails at tweet 800 of 2000, you lose everything and start over.

### The Solution

Split the date range into fixed-size windows (default: 5 days) and run a separate paginated search per window using `since:/until:` operators.

```
Window 1: since:2025-03-20 until:2025-03-25
Window 2: since:2025-03-15 until:2025-03-20
Window 3: since:2025-03-10 until:2025-03-15
...
```

**Benefits:**
- Spike periods consume only their window's quota, not the global cap
- Each window is independently checkpointable (see Section 7)
- Parallelizable: 2 windows concurrently with semaphore control
- Global dedup via `seen_ids` set across all windows

**Window size of 5 days** balances granularity (spike isolation) against overhead (more API calls for window boundaries). Don't go below 3 days — the overhead isn't worth it.

---

## 7. Checkpoint/Resume

Any collection that spans multiple API calls should support checkpoint/resume. We learned this the hard way: a billing error at call 200 of 250 means $0.40 wasted if you can't resume.

### Pattern

```json
{
  "query": "$TIG",
  "since_date": "2024-01-01",
  "until_date": "2026-04-01",
  "window_days": 5,
  "completed_windows": [["2026-03-25", "2026-04-01"], ...],
  "tweets": [...]
}
```

### Rules

1. **Validate params on resume.** If the query, dates, or window size changed, discard the checkpoint and start fresh. Stale checkpoints cause silent data corruption.
2. **Atomic writes.** Write to `.tmp`, then rename. A process kill during checkpoint write must not corrupt the file.
3. **Checkpoint before re-raising errors.** If a billing error (402) or cost ceiling hit occurs mid-batch, write the checkpoint for successfully-completed windows FIRST, then re-raise. Otherwise those windows get re-fetched on resume, wasting money.
4. **Delete checkpoint on clean completion.** A lingering checkpoint file causes confusion on the next run.

---

## 8. Cost Control Architecture

### Three Layers (implement all three)

| Layer | Scope | Behavior | Example |
|-------|-------|----------|---------|
| **Per-call ceiling** | Single collection run | Hard stop mid-collection; return partial data | Cult Grader: `--cost-ceiling 30` |
| **Per-run soft cap** | Full pipeline run | Warn before starting; allow override | Lead Identifier: `soft_cost_cap_per_run: 5.0` |
| **Monthly hard cap** | Cross-run cumulative | Abort or warn before any API calls | Lead Identifier: `monthly_cost_cap: 75.0` |

### Cost Tracking

Every API call must be counted. Track by phase so you can audit where spend goes:

```
user_info:       $0.002  (1 call)
timeline:        $0.064  (32 calls)
mentions:        $0.856  (428 calls)
replies:         $0.060  (30 calls)
ticker_enrich:   $0.538  (269 calls)
```

**Log phase costs in run metadata.** When a run costs more than expected, the phase breakdown tells you exactly where the overrun happened.

### Budget Estimation

Before committing to API spend, estimate cost:
- **Cult Grader onboard:** `(mention_windows * calls_per_window) + (reply_candidates * 1) + (team_handles * 2) + ticker_calls`
- **Lead Identifier Pass 2:** `top_n * 3 calls * $0.004 = top_n * $0.012`
- **Slopper scan:** `n_accounts * 1 + (10 if deep_mode) * $0.002`

Show the estimate to the operator before proceeding.

---

## 9. Caching

### When to Cache

| Data Type | TTL | Rationale |
|-----------|-----|-----------|
| User profile (followers, bio) | 7 days | Changes slowly; weekly refresh is fine |
| Tweet engagement metrics | 1-10 min | Only for rapid iteration; stale fast |
| Full collection (raw_twitter.json) | Until next run | Immutable per-run artifact |
| Team handle timelines | Reuse across runs if unchanged | Avoid re-fetching stable data |

### Cache-Aware Wrappers (Critical Pattern)

**Any new call-site invoking SocialData MUST go through a cache-aware wrapper.** Direct API calls bypass the cache and silently burn budget on data you already have.

Lead Identifier's pattern:
```python
async def enrich_socialdata_cached(pid, handle, ...):
    """Check if ALL sub-enrichers are cached. Returns None if fully cached."""
    if all(cache.has(pid, key) for key in SUB_ENRICHERS):
        return None  # Caller rehydrates from cache
    result = await enrich_socialdata(handle, ...)
    cache.set(pid, result)
    return result
```

### Granular Sub-Caching

Split a multi-call enrichment into separate cache entries (profile, tweets, mentions). This lets you invalidate one without re-fetching all three — useful when a handle is corrected.

---

## 10. Deduplication

### Within a Single Paginated Search

Track `seen_ids: set[str]` and skip duplicates as you paginate. SocialData can return the same tweet across pages, especially near window boundaries.

### Across Collections

The same tweet can appear in both `mentions` and `ticker_mentions` (e.g., a tweet containing both `@tigfoundation` and `$TIG`). If downstream consumers use sets keyed on author_handle or tweet ID, this is harmless. If they count, you'll double-count.

**Rule:** Deduplicate by tweet ID before any counting operation. Cult Grader's `TwitterData.deduplicate_collections()` does this as a post-save guard.

### Cross-Run Deduplication

For incremental collection (Slopper pulse scanning), use `since_id` cursors — only fetch tweets newer than the last-seen ID. This is the most cost-effective dedup: you never fetch the same data twice.

---

## 11. Error Handling

| HTTP Code | Meaning | Action |
|-----------|---------|--------|
| 200 | Success | Process normally |
| 402 | Balance exhausted | **Fatal.** Stop all collection immediately. No retry. Save partial results. |
| 404 | Handle/tweet not found | Log and skip. Don't charge cost. Common for deleted accounts. |
| 429 | Rate limited | Backoff with jitter (see Section 4). Max 4 retries. |
| 5xx | Server error | If data already collected: treat as end-of-pagination (return partial). If no data yet: raise. |

### 5xx Mid-Pagination (Important)

If you've already collected 300 tweets and get a 503 on page 16, **return the 300 tweets** rather than raising and losing everything. This is a deliberate Cult Grader design decision that has saved real data multiple times.

```python
try:
    page = await fetch_next_page(cursor)
except ServerError:
    if collected_tweets:
        break  # Return what we have
    raise  # Nothing to save; propagate error
```

---

## 12. Patterns for Lead Identifier Specifically

Lead Identifier's SocialData usage is fundamentally different from Cult Grader's: it's **wide and shallow** (500 projects, 3 calls each) rather than **narrow and deep** (1 project, hundreds of calls).

### Key Optimizations

1. **Pass 1 (free) before Pass 2 (paid).** Enrich all ~1,000 projects with free sources (GitHub stars, Discord member counts, Telegram). Only the top 500 get SocialData enrichment. This cuts paid API spend by 50%+.

2. **Sample-based, not exhaustive.** 100 tweets + 100 mentions per project is enough for stable engagement metrics. Don't paginate further — the marginal signal from tweet 101-200 doesn't justify the cost.

3. **KOL scoring from existing payload.** Mention tweets include the author's `followers_count`. Use it for KOL classification (>10K followers) without making a separate profile lookup. Zero marginal cost.

4. **Wrong-account guard.** DefiLlama/CoinGecko Twitter handle data is sometimes stale. If the resolved profile has <100 followers, it's probably wrong. Bail early and save 2 API calls.

5. **OR queries for broader capture.** `@handle OR "project name"` in a single search call catches mentions that use the project name but not the handle. One call, broader signal.

6. **Handle correction → cache invalidation.** When a BD team member corrects a stale handle, invalidate all three sub-cache entries so the next run re-fetches with the correct handle.

---

## 13. Patterns for Slopper Specifically

Slopper's usage is **incremental and recurring** — daily scans of a watchlist, not one-off deep dives.

### Key Optimizations

1. **`since_id` cursors.** Only fetch tweets newer than the last-seen ID per account. This is the single biggest cost saver for daily/weekly cadences.

2. **Lookback window.** Default 48-hour lookback prevents fetching ancient tweets even without a `since_id` cursor (e.g., first scan of a new account).

3. **Deep mode is capped.** Keyword searches ("outsider detection") limited to 3 queries max per scan. Unbounded keyword search would blow budget fast.

4. **File-based short-TTL cache.** 10-minute cache on user tweet lists prevents duplicate fetches during rapid development iteration. Not a substitute for `since_id` cursors in production.

5. **Cost estimation before execution.** `scanner.estimate_cost()` shows projected spend before making any API calls. If it exceeds `max_cost_per_run` ($1.00 default), abort.

---

## 14. Data Parsing Gotchas

### Datetime Formats

SocialData returns dates in multiple formats depending on the endpoint and tweet age:

```
"2026-03-28T23:51:06Z"           # ISO 8601 with Z
"2026-03-28T23:51:06+00:00"      # ISO 8601 with offset
"2026-03-28T23:51:06"            # ISO 8601 naive (treat as UTC)
"Sat Mar 28 23:51:06 +0000 2026" # Legacy Twitter v1.1 format
```

**Always normalize to timezone-aware UTC.** If `tzinfo` is None, assume UTC. Lead Identifier handles 5 date format variants in `_parse_sd_profile()`.

### Follower Count = 0 Is Valid

A `followers_count` of 0 is a real value (new/empty account), not a missing value. Use explicit `is None` checks, never truthiness checks:

```python
# WRONG — treats 0 as missing
followers = raw.get("followers_count") or default

# RIGHT — preserves 0
followers = raw.get("followers_count")
if followers is None:
    followers = default
```

This bit us in ghost account filtering where `followers_count=0` accounts were passing through.

### Tweet ID Types

SocialData returns tweet IDs as both `id` (int) and `id_str` (string). **Always use the string form** (`id_str`) or cast to string immediately. JavaScript-origin clients can lose precision on large integers.

### Nested User Data

Author profile data can appear in multiple places in the response:
```
raw["user"]["followers_count"]
raw["user"]["legacy"]["followers_count"]
raw["author"]["followers_count"]
```

Check all paths with fallback. The structure varies by endpoint and tweet age.

---

## 15. Anti-Patterns to Avoid

### 1. Unbounded Pagination Without Cycling Detection
**Cost:** $10-50 wasted per incident. Small accounts loop indefinitely.

### 2. Full Pipeline Re-run When You Only Need One Phase
**Cost:** $2-10 wasted. Use `--from-stage` in Cult Grader or cache-aware wrappers in Lead Identifier.

### 3. Mention-Rate Estimation in Windowed Mode
**Cost:** $0.004 wasted per project. When you're already using windowed search, the rate estimation call is pointless — you're going to fetch everything anyway.

### 4. Re-fetching Team Handle Timelines Every Run
**Cost:** $0.02 per handle per run, wasted. Check if prior run data exists and is fresh enough. Cult Grader reuses team timeline data across runs when unchanged.

### 5. Exhaustive Collection When Sampling Suffices
**Cost:** 5-50x overspend. Lead Identifier needs 100 tweets for engagement stats, not 1000. Cult Grader's reply threads use marginal-yield stop conditions, not fixed caps.

### 6. Direct API Calls Bypassing Cache Wrapper
**Cost:** Silent phantom spend on already-cached data. Every call-site must go through a cache-aware path.

### 7. No Cost Ceiling on Long-Running Collections
**Cost:** Unbounded. Always set a ceiling, even a generous one. Cult Grader's onboard mode defaults to `--cost-ceiling 50`.

---

## 16. Cost Reference Table

Typical per-run costs across tools:

| Tool | Mode | Typical Cost | Max Cost | Primary Driver |
|------|------|-------------|----------|---------------|
| Cult Grader | Standard weekly | $0.15-0.40 | $1.00 | Mentions + replies |
| Cult Grader | Onboard (deep) | $1.00-5.00 | $50 (ceiling) | Windowed mentions over months |
| Cult Grader | Roster only | $0.02-0.05 | $0.10 | Team timeline fetch |
| Lead Identifier | Pass 2 (500 projects) | $4-6 | $10 | 3 calls × 500 projects |
| Lead Identifier | Monthly total | $15-30 | $75 (ceiling) | Multiple runs + corrections |
| Slopper | Pulse scan (20 accounts) | $0.04-0.06 | $1.00 (ceiling) | Per-account timeline fetch |
| Slopper | Deep scan | $0.06-0.10 | $1.00 (ceiling) | + keyword searches |

**Total monthly SocialData spend across all tools:** ~$50-120 at current client volume.

---

## 17. Checklist for New SocialData Integration

Before shipping any new code that calls SocialData:

- [ ] **Cursor cycling detection** on all paginated endpoints
- [ ] **Cost ceiling or cap** — never unbounded collection
- [ ] **Cache-aware wrapper** — check cache before API call
- [ ] **402 handling** — fatal, save partial, stop immediately
- [ ] **429 handling** — exponential backoff with jitter
- [ ] **5xx mid-pagination** — return partial data, don't lose progress
- [ ] **Phase cost tracking** — log which phase each call belongs to
- [ ] **Dedup by tweet ID** — within pagination and across collections
- [ ] **Datetime normalization** — handle all 4+ formats, assume UTC for naive
- [ ] **`followers_count=0` preserved** — explicit None checks, not truthiness
- [ ] **Checkpoint/resume** for any collection >50 API calls
- [ ] **Cost estimate shown** before committing to spend
- [ ] **Run the math:** calls × $0.002, show the operator

---

*Last updated: 2026-04-02. Based on production experience across Cult Grader (v1, 700+ test suite), Lead Identifier (v1, Pass 2 enrichment), and Slopper (pulse/meta scanning). Prioritizes real-world lessons over theoretical recommendations.*
