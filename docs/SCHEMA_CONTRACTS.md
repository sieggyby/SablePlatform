# Schema Contracts

Canonical reference for cross-suite data contracts in sable.db. Any change to
these contracts requires updating this document and notifying downstream consumers.

---

## Entity Status Values

| Status | Meaning | Display (SableWeb) |
|--------|---------|---------------------|
| `candidate` | Prospect entity from Lead Identifier | Shown |
| `confirmed` | Live entity, fully tracked | Shown |
| `archived` | Retired, no longer tracked | Hidden |

**SableWeb filter:** `WHERE status != 'archived'`

Adding a new status value requires:
1. Update this table
2. File a SableWeb TODO for filter review

---

## Prospect Tiers

| Tier | Composite Score | Action |
|------|----------------|--------|
| `Tier 1` | >= 0.70 | pursue |
| `Tier 2` | >= 0.55 | monitor |
| `Tier 3` | < 0.55 | pass (filtered from triage) |

Thresholds defined in `sable_platform/contracts/leads.py` as `PURSUE_THRESHOLD` / `MONITOR_THRESHOLD`.

### Prospect Lifecycle

| Column | Set by | Meaning |
|--------|--------|---------|
| `graduated_at` | `sable-platform org graduate <id>` | Prospect converted to active client |
| `rejected_at` | `sable-platform org reject <id>` | Prospect rejected (bad fit, etc.) |

- Default queries (`list_prospect_scores`) exclude graduated and rejected rows.
- Pass `include_graduated=True` or `include_rejected=True` to include them.
- A prospect can technically have both `graduated_at` and `rejected_at` set (e.g., graduated then later dropped). Each operation only checks its own column's NULL state.
- Rejection reason is stored in `audit_log` (action=`prospect_rejected`), not on the row.

### prospect_scores.org_id — Prospect Project Identifier

`prospect_scores.org_id` stores the **prospect's project_id** (the external crypto community
evaluated by Lead Identifier), **not** the Sable client org_id. The column name follows SQLite
FK conventions but is semantically a prospect identifier.

- `graduate_prospect(conn, project_id)` and `reject_prospect(conn, project_id)` treat it as a project_id.
- `list_prospect_scores()` has no Sable client filter — it returns all prospects globally (single-operator assumption).
- Do **not** use `prospect_scores.org_id` to filter by Sable client. Use `orgs.org_id` for that.
- If multi-tenant support is added, a migration will be needed to add a `client_org_id` column.

---

## Prospect Dimensions (5 canonical)

| Dimension | Source Field | Transform |
|-----------|-------------|-----------|
| `community_health` | `community_gap` | `1.0 - gap` |
| `language_signal` | `conversation_gap` | `1.0 - gap` |
| `growth_trajectory` | `tge_proximity` | passthrough |
| `engagement_quality` | `engagement_gap` | `1.0 - gap` |
| `sable_fit` | `composite` | passthrough (placeholder) |

---

## Cost Model Prefixes

| Prefix | Source | Notes |
|--------|--------|-------|
| `claude-*` | Anthropic API | Used by Cult Grader, Slopper |
| `socialdata` | SocialData API | Used by SableTracking |
| `replicate` | Replicate | Used by Slopper (image/video) |
| `elevenlabs` | ElevenLabs | Used by Slopper (TTS) |

`cost.py:log_cost()` accepts any model string — no validation.

---

## Artifact Types

| artifact_type | Source | Notes |
|---------------|--------|-------|
| `pulse_report` | Slopper | Pulse snapshot report |
| `meta_report` | Slopper | Meta analysis report |
| `discord_playbook` | Cult Grader | Discord playbook |
| `twitter_strategy_brief` | Cult Grader | Twitter strategy brief |
| `lead_identifier_output` | Lead Identifier | Lead scoring output |
| `content_meme` | Slopper (planned) | Meme content artifact |
| `content_clip` | Slopper (planned) | Video clip artifact |

`content_tweet` is deferred — Slopper writes text to stdout, not a file.

---

## Outcome Types

| outcome_type | Source | metric_name Convention |
|-------------|--------|------------------------|
| (diagnostic types) | Cult Grader | varies |
| `content_performance` | Slopper (planned) | `engagement_rate_{content_type}` |

Slopper outcomes use `recorded_by='pulse_outcomes'`.

---

## Alert Severity & Status

| Severity | Usage |
|----------|-------|
| `info` | Low-priority notifications (e.g., unclaimed actions) |
| `warning` | Requires attention (e.g., stale tracking, decay) |
| `critical` | Urgent action needed (e.g., bridge decay, high-risk member) |

| Status | Meaning |
|--------|---------|
| `new` | Freshly created, blocks dedup |
| `acknowledged` | Operator has seen it, still blocks dedup |
| `resolved` | Closed, allows re-alerting on same dedup_key |

---

## Decay Risk Tiers

| Tier | Score Range | Alert |
|------|-------------|-------|
| `low` | < 0.4 | None |
| `medium` | 0.4–0.6 | None |
| `high` | 0.6–0.8 | Warning |
| `critical` | >= 0.8 | Critical (if structurally important tag) |

---

## Interaction Types

| Type | Meaning |
|------|---------|
| `reply` | Direct reply to another handle |
| `mention` | @ mention in a post |
| `co_mention` | Both handles mentioned in the same post |

---

## SableTracking Metadata Schema

See `sable_platform/contracts/tracking.py` for the canonical `TrackingMetadata` Pydantic model.
17 fields, versioned via `schema_version`. Adding a field requires bumping the version.

---

## SableKOL Tables (migrations 032-041)

Defined in SablePlatform migrations, written exclusively by SableKOL code via `sable_kol.db.upsert_candidate` and the path-specific writers. Migration files in `sable_platform/db/migrations/`, mirrored in `sable_platform/db/schema.py` (parity-tested in `tests/db/test_schema.py`).

| Table | Mig | Purpose | Key constraints |
|-------|-----|---------|-----------------|
| `kol_candidates` | 032 | One row per X handle in the bank | Partial unique index `idx_kol_candidates_handle_live` on `handle_normalized WHERE is_unresolved=0` |
| `kol_handle_resolution_conflicts` | 032 | Unresolved-duplicate triage queue | FK → `kol_candidates(candidate_id)` |
| `project_profiles_external` | 032 | Path-(ii) project profiles (non-Sable orgs) | Indexed on `handle_normalized` |
| `kol_extract_runs` | 037+039 | One row per `bulk-fetch followers / following` run | Carries `client_id` for client-scoping |
| `kol_follow_edges` | 037 | Per-run follower/followee handle pairs | PK `(run_id, follower_id, followed_id)`; indexed on `followed_id` and `followed_handle` |
| `kol_operator_relationships` | 038 | Per-(client, candidate, operator) tagging surface | Append-only; tag changes write new rows |
| `kol_create_audit` | 040 | Audit log for `/api/ops/kol-network/*` SableWeb routes | `email` is NULLABLE (anonymous failures still log); migration 042 added `review_status` |
| `kol_enrichment` | 041 | Per-(candidate, operator) Grok intel cache (KO-3 v2.5) | Composite index on `(candidate_id, operator_email, fetched_at DESC)` for latest-wins lookups |

**Hard rules** (lifted from `SableKOL/CLAUDE.md` — see there for context):

- Never bypass `sable_kol.db.upsert_candidate()` for `kol_candidates` writes. JSON encoding + partial-unique-index handling + conflict routing live there.
- Schema changes go in SP migrations (SQL + Alembic dual pattern), never in SableKOL.
- The wizard-era `kol_create_audit` table lives in SP but is written by SableWeb via `recordAudit()`, not by SableKOL Python — different writer than the rest of the KOL surface.
- `kol_enrichment.payload_json` is opaque to SP (carries the `Enrichment` Pydantic shape from `sable_kol/preflight_schemas.py`). Schema-version field lives inside the JSON, not as a column.

---

## Reply-Assist Tables (migrations 056, 060, 061, 062, 063, 066)

The operator reply-assist surface: SableWeb `/ops/reply-assist` requests a generation, Slopper (`sable_platform.db.replies`) writes it, and the "Mark posted" action records the outcome. The cross-suite contract: **SableWeb reads** the per-org/per-operator feed and outcome rows; **Slopper writes** `reply_suggestions` (the generation) and `reply_outcomes` (the post). Writers commit; `cost_usd` is internal-only and never returned to the browser.

| Table | Mig | Purpose | Key columns / FKs |
|-------|-----|---------|-------------------|
| `operator_reply_quota` | 056 | Per-(operator, UTC-day) generation quota (50/day, raisable) | PK `(operator_handle, day_utc)`; `count` INTEGER |
| `reply_suggestions` | 056 | One row per generation (the suggestion variants) | PK `id` TEXT; `org_id`→`orgs(org_id)`; `variants_json`, `model`, `cost_usd` (internal-only), `generated_at`. Indexed on `(operator_handle, source_tweet_id)` and `(org_id, generated_at)` |
| `reply_outcomes` | 056 | Actual-post mapping — measures assisted-vs-organic reply lift; **the single source of truth for "replies delivered"** (work-tracking counts from here, never mirrors) | PK `id` TEXT; `suggestion_id`→`reply_suggestions(id)`; `posted_tweet_id`, `chosen_variant_idx`, `was_edited`, `engagement_json`. Unique `(suggestion_id, posted_tweet_id)` |

**Additive columns (no new tables):**

| Column | Mig | On table | Purpose |
|--------|-----|----------|---------|
| `clip_media_kind` | 060 | `reply_suggestions` | Media kind a reply attached (`image`/`video`/`none`); NULL = text-only. Backs the prefer-image ranking + per-operator image throttle |
| `opportunity_id` (INTEGER), `source_conversation_id` (TEXT) | 062 | `reply_suggestions` | Learning join back to the feed row (`relay_reply_opportunities.id`; NULL for paste-URL generations) + the target tweet's `conversation_id` (the cheap local depress-already-replied signal) |
| `tell_score` (REAL), `tell_flags_json` (TEXT) | 063 | `reply_suggestions` | §10 anti-AI-tell weighted flag density (0..1) + the `{type,span,why}` flags blob; both NULL for pre-063 / unlinted rows |
| `media_content_id` (TEXT) | 066 | `reply_outcomes` | The media that rode along with the posted reply, so assisted-vs-organic lift can be sliced by media |

### Reply Campaigns (migration 061)

The coordinated-reply "flash mob": several operators reply to ONE target tweet toward a shared objective, ties into `reply_suggestions`/`reply_outcomes`.

| Table | Mig | Purpose | Key columns / FKs |
|-------|-----|---------|-------------------|
| `reply_campaigns` | 061 | The campaign (target tweet + objective + status) | PK `id` TEXT; `org_id`→`orgs(org_id)`; `target_tweet_id`, `objective`, `status` (default `active`), `created_by`, `won_at`, `closed_at`. Indexed `(org_id, status, created_at)` |
| `reply_campaign_assignments` | 061 | Per-operator assignment (who took which angle, what they posted) | PK `id` TEXT; `campaign_id`→`reply_campaigns(id)`; `operator_handle`, `suggestion_id`, `posted_tweet_id`, `angle`, `status` (default `assigned`) |

### Media Recommendation Center (migration 066)

Records each media slate offered for a reply + the learned per-asset quality. The events log is the source of truth; `media_quality` is the forward-only Elo rollup recomputed by `apply_pending_media_events`. **No cost column ever** (cost lives only in `cost_events`).

| Table | Mig | Purpose | Key columns / FKs |
|-------|-----|---------|-------------------|
| `media_rec_events` | 066 | One row per offered slate (source of truth) | PK `id` INTEGER; `org_id`, `operator_handle`, `tweet_ref`, `slate_json` (ordered offered `content_id`s), `chosen_content_id` (MAY be NULL — slate offered, no media attached), `applied`. Indexed `(org_id, applied)` |
| `media_quality` | 066 | Materialized Elo rollup (derived) | PK `(org_id, content_id)`; `elo` (default 1500), `n_offered`, `n_chosen` |
| `media_embeddings` | 066 | Per-asset semantic embedding cache | PK `(org_id, content_id)`; `embedding_json`, `embedding_model` (model swap invalidates) |

---

## SableRelay `relay_*` Tables (migrations 057, 062, 064, 065)

The multi-tenant X↔Telegram↔Discord bridge, built as the in-process `sable_platform.relay` module. CRUD lives in `sable_platform/relay/db.py`; SQLAlchemy mirror in `sable_platform/relay/schema.py`. `relay_clients.org_id` is a TEXT FK to `orgs(org_id)` — Relay never duplicates org identity. All other `relay_*` tables FK their `org_id` to `relay_clients(org_id)`.

### Substrate (migration 057)

| Table | Purpose | Key columns / FKs |
|-------|---------|-------------------|
| `relay_clients` | Per-org relay enablement + poll state | PK `org_id`→`orgs(org_id)`; `enabled`, `polling_interval_seconds`, `last_polled_at`, `last_seen_x_id` (broadcast-timeline poll cursor), `config` |
| `relay_chats` | One stable row per (platform, external chat id) | PK `id`; `org_id`→`relay_clients(org_id)`; `platform` (`telegram`/`discord`), `chat_id`. Unique `(platform, chat_id)`. **FK target for `autocm_drafts.source_chat_id`** |
| `relay_chat_bindings` | Operator/shared/community/broadcast role binding per chat | PK `id`; `role`, `status` (`active`/`migrated`/`kicked`/`disabled`); unique active role per `(org_id, platform, role)` |
| `relay_members` | Canonical member identity (cross-platform) | PK `id` |
| `relay_member_identities` | Platform handle → member | PK `(platform, external_user_id)`; `member_id`→`relay_members(id)` |
| `relay_member_roles` | Per-(member, org) role grant | PK `(member_id, org_id, role)`; role ∈ `sable_operator`/`client_team`/`admin` |
| `relay_member_preferences` | Per-(member, org) reply opt-in / mute | PK `(member_id, org_id)`; `replies_optin`, `mute_until` |
| `relay_tweets` | Read-through cache of fetched tweets | PK `id`; `x_id` UNIQUE; `x_author_handle`, `text`, `media_urls`, `conversation_x_id` |
| `relay_messages` | One row PER inbound message (NOT just engaged) — the corpus AutoCM digest/analytics aggregate over | PK `id`; `org_id`→`relay_clients(org_id)`, `chat_id`→`relay_chats(id)`, `member_id`→`relay_members(id)`; unique `(platform, chat_id, external_message_id)`. GC'd on a bounded window. **FK target for `autocm_drafts.source_message_id`** |
| `relay_submissions` | Operator/shared tweet-publish submission | PK `id`; `tweet_id`→`relay_tweets(id)`, `submitter_id`→`relay_members(id)`; `status` ∈ `pending`/`ready_to_publish`/`published`/`expired`/`rejected` |
| `relay_submission_reactions` | Reaction-vote on a submission | PK `(submission_id, member_id, emoji)` |
| `relay_publication_jobs` | Publish job queue | PK `id`; `state` ∈ `pending`/`claimed`/`retry`/`done`/`dead` (note: `failed` removed — `dead` is the only terminal value); dedupe over `pending`/`claimed`/`done` |
| `relay_publications` | Completed publication record | PK `id`; `tweet_id`→`relay_tweets(id)`; unique `(org_id, tweet_id, destination_platform, destination_chat_id)` |
| `relay_reply_opportunities` | Reply-opportunity surface (TG-flagged in 057; **extended into the web feed by 062**) | PK `id`; `org_id`→`relay_clients(org_id)`, `tweet_id`→`relay_tweets(id)`, `flagger_id`→`relay_members(id)`; `origin` ∈ `explicit_command`/`reaction`/`auto_mention` |
| `relay_reply_opportunity_targets` | Members targeted by an opportunity | PK `(opportunity_id, member_id)` |
| `relay_reply_notifications` | Per-member TG inbox notification (member-keyed — distinct from the web feed's handle-keyed state) | PK `id`; unique `(opportunity_id, member_id)`; `dismissed_at`, `replied_at`, `replied_tweet_id` |
| `relay_processed_updates` | Idempotency ledger for TG/Discord update IDs | PK `(platform, update_id)` |

### Reply-Opportunity Feed (migration 062)

The web reply-opportunity feed is **unified on the existing `relay_reply_opportunities` table** (extended, not a parallel table). Auto-sourced rows reuse an allowed `origin` value and carry the real source in `sweep_source`; `flagger_id` stays NOT NULL via a sentinel `__sweep__` `relay_members` row.

**Columns added to `relay_reply_opportunities` (062):** `score` (REAL), `score_reason` (TEXT), `suggested_angle` (TEXT), `status` (TEXT NOT NULL DEFAULT `active`), `expires_at` (TEXT), `sweep_source` (TEXT). Indexed `(org_id, status, score)` and on `expires_at`.

**Columns added to `relay_tweets` (062):** `engagement_json`, `lang`, `author_followers` (cheap quality signals for the heuristic pre-rank).

| Table | Mig | Purpose | Key columns / FKs |
|-------|-----|---------|-------------------|
| `relay_opportunity_operator_state` | 062 | Per-operator web-feed state (handle-keyed — personalizes the shared feed) | PK `(opportunity_id, operator_handle)`; `opportunity_id`→`relay_reply_opportunities(id)`; `state` (dismiss/snooze), `snooze_until` |
| `relay_opportunity_feedback` | 062 | The two thumbs (learning labels) | PK `id`; `opportunity_id`→`relay_reply_opportunities(id)` (**NULLABLE as of mig 068** — freeform-draft thumbs have a `suggestion_id` but no opportunity), `suggestion_id`→`reply_suggestions(id)` (NULL = thumb on the OPPORTUNITY/ranker; set = thumb on a SUGGESTION/gen quality); `rater_handle`, `rater_role`, `thumb` |
| `relay_sweep_config` | 062 | Per-client curated sweep query set (managed via TG bot) | PK `org_id`→`relay_clients(org_id)`; `mention_handles`, `topic_queries`, `from_set`, `operator_handles` (the three lanes), `enabled`, `expiry_hours`, `last_sweep_at` (hourly-due check), `sweep_requested_at` ("sweep now" enqueue marker). Daily cost cap is NOT here — it lives in `relay_clients.config.polling.daily_cost_cap_usd` |
| `relay_sweep_cursor` | 062 | Per-source `since_id` cursor (do NOT overload `relay_clients.last_seen_x_id`) | PK `(org_id, source, query_hash)`; `since_id` |
| `relay_operator_heartbeat` | 062 | Logged-in gate — SableWeb stamps on each `/ops/reply-assist` load; the sweep only runs for orgs with a recent heartbeat | PK `(org_id, operator_handle)`; `last_seen` |

**Embedding cache columns added to `relay_tweets` (063):** `embedding_json` (the P3 ranker vector blob), `embedding_model` (so a model swap invalidates the cache).

### Trending Stories (migration 064)

| Table | Mig | Purpose | Key columns / FKs |
|-------|-----|---------|-------------------|
| `relay_trending_stories` | 064 | Bursting + relevant stories detected from the scored sweep pool (Trending-Story Autopilot) | PK `id`; `org_id`→`relay_clients(org_id)`; `label`, `summary`, `relevance` + `momentum` (INTERPRETIVE — rendered behind a caveat banner), `member_tweet_ids_json`, `monitor_terms_json`, `status` (default `emerging`), `expires_at`. App-level dedup (no UNIQUE constraint), **no cost column** |

### Quality Corpus (migration 065)

Curated CT-account bank + sampled tweets + longitudinal engagement-decay snapshots — feeds the quality model that judges reply-assist drafts.

| Table | Mig | Purpose | Key columns / FKs |
|-------|-----|---------|-------------------|
| `relay_quality_accounts` | 065 | Stratified CT-account bank | PK `handle`; `band`, `kol_strength`, `archetype_json` (INTERPRETIVE — carried from `kol_candidates`), `active` |
| `relay_quality_tweets` | 065 | Sampled tweets from the bank | PK `tweet_x_id`; `author_handle`, `posted_at`, `band` |
| `relay_tweet_snapshots` | 065 | Longitudinal engagement snapshots (MEASURED at known ages) | PK `id`; `tweet_x_id`, `target_age_hours` (scheduled bucket), `age_hours` (actual), `likes`/`retweets`/`replies`/`quotes`/`bookmarks`/`views`, `author_followers` |

---

## SableAutoCM `autocm_*` Tables (migration 058)

The per-client AI community manager (persona = NULO for RobotMoney), built as the in-process `sable_platform.autocm` module reusing the vendored `_vendor/sable_pulse_core`. `autocm_clients.org_id` is a TEXT FK to `orgs(org_id)`. **Cross-suite contract: AutoCM writes `autocm_drafts` whose source FKs point at the relay substrate** — `source_message_id`→`relay_messages(id)`, `source_chat_id`→`relay_chats(id)` (the C1.1 AutoCM→Relay FK reconciliation; the reason 057 added `relay_chats`/`relay_messages`).

| Table | Mig | Purpose | Key columns / FKs |
|-------|-----|---------|-------------------|
| `autocm_personas` | 058 | Persona definitions (calm/reactive prompts + calibration) | PK `id`; `name` UNIQUE |
| `autocm_clients` | 058 | Per-org AutoCM enablement + autonomy state | PK `id`; `org_id`→`orgs(org_id)` (UNIQUE), `persona_id`→`autocm_personas(id)`; `autonomy_state` ∈ `hitl`/`partial`/`auto`/`paused`, `incident_active`, `enabled` |
| `autocm_kb_sources` | 058 | KB source registry (per client) | PK `id`; `client_id`→`autocm_clients(id)`; `source_type`, `authority_default`, `status` |
| `autocm_kb_chunks` | 058 | Chunked KB content + embedding | PK `id`; `source_id`→`autocm_kb_sources(id)`, `client_id`→`autocm_clients(id)`; `chunk_text`, `chunk_embedding` (TEXT JSON float-array — the one intentional dialect divergence; Postgres may use a vector type) |
| `autocm_kb_chunks_fts` | 058 | FTS5 external-content index over `chunk_text` (BM25 keyword leg) | Virtual table content-linked to `autocm_kb_chunks` via `content_rowid='id'` |
| `autocm_kb_constants` | 058 | Slot-fill registry of irreducibles (contract addrs, audit URLs — NEVER LLM-generated) | PK `(client_id, key)` |
| `autocm_drafts` | 058 | Every draft with source/classification/register/confidence/status | PK `id`; `client_id`→`autocm_clients(id)`, **`source_message_id`→`relay_messages(id)`**, **`source_chat_id`→`relay_chats(id)`**; `register` ∈ `calm`/`reactive`, `cited_chunk_ids`, `status` ∈ `pending`/`auto_sent`/`hitl_pending`/`approved`/`rejected`/`published`/`escalated`/`suppressed` |
| `autocm_reviews` | 058 | HITL review decisions per draft | PK `id`; `draft_id`→`autocm_drafts(id)`, `client_id`→`autocm_clients(id)`; `decision` ∈ `approve`/`edit`/`reject`/`punt_to_founder`, `is_clean_approval` |
| `autocm_category_state` | 058 | Per-(client, category) autonomy + threshold + the §6 48h HITL freeze | PK `id`; unique `(client_id, category)`; `state` ∈ `hitl`/`auto`, `confidence_threshold`, `freeze_until`/`freeze_reason`/`frozen_by` |
| `autocm_escalations` | 058 | Founder/on-call escalations | PK `id`; `client_id`→`autocm_clients(id)`, `draft_id`→`autocm_drafts(id)`, `source_message_id`→`relay_messages(id)`; `founder_status`, `oncall_status` |
| `autocm_flagged_users` | 058 | Auto-silenced users pending mod clearance | PK `id`; `client_id`→`autocm_clients(id)`, `member_id`→`relay_members(id)`; `status` ∈ `silenced`/`cleared` |
| `autocm_adversarial_runs` | 058 | Daily adversarial-regression results | PK `id`; `client_id`→`autocm_clients(id)`; `total_cases`/`passed`/`failed`, `status` |
| `autocm_digest_interactions` | 058 | Founder-digest button responses (Approve-for-KB/Recognize/Demote/Compose/Ignore/Ask) | PK `id`; `client_id`→`autocm_clients(id)`; `digest_period`, `action`, `target_ref` |
| `autocm_time_saved_baseline` | 058 | Per-client time-saved calibration (one row per client) | PK `id`; `client_id`→`autocm_clients(id)` (UNIQUE); `minutes_per_auto`, `minutes_per_hitl`, `engagement_start_at` |

---

## Work-Tracking Tables (migration 059)

Operator work-tracking (SW-TASKING Phase 1) feeding the ops "scale of work delivered" report. **SableWeb (`/ops`) is the writer/reader; SP does not invoke it.** Replies are NOT mirrored here — they are counted from `reply_outcomes` (mig 056) so there is exactly one source of truth. The `note` column is ops-only and must never reach a client surface.

| Table | Mig | Purpose | Key columns / FKs |
|-------|-----|---------|-------------------|
| `mod_slot_sessions` | 059 | Mod-slot "clock-in" sessions (coverage hours, self-reported) | PK `session_id` TEXT; `org_id`→`orgs(org_id)`; `operator_handle`, `started_at`, `ended_at` (open slots excluded from hours rollup), `chats_watched_json`, `note` (ops-only) |
| `operator_work_events` | 059 | Generic operator work-event log | PK `event_id` TEXT; `org_id`→`orgs(org_id)`; `operator_handle`, `event_type`, `occurred_at`, `ref_json` |

---

## Community-Audit `community_audit_*` Tables (migration 067)

Backs the self-invite **sable-audit** Discord bot (a thin client importing `sable_platform.db.community_audit`). SP owns the tables. **Naming contract: the `community_audit_` prefix deliberately avoids `audit_log` / `db/audit.py`** (the compliance audit LOG — a different surface). Never name a community-audit table `audit_*`. `org_id` on a guild is NULL until consent (the prospect org is created at consent, not at join).

| Table | Mig | Purpose | Key columns / FKs |
|-------|-----|---------|-------------------|
| `community_audit_guilds` | 067 | One row per joined guild (parent) | PK `guild_id` TEXT; `org_id`→`orgs(org_id)` (NULL until consent); `plan_tier`, `status`, `consent_at`, `last_audit_at`. Re-invite reuses the row |
| `community_audit_runs` | 067 | One row per audit run | PK `id`; `guild_id`→`community_audit_guilds(guild_id)`; `kind` ∈ `metadata`/`deep`, `status` ∈ `running`/`ok`/`aborted`/`partial`, `overall_grade` (NULL until grade-suppression satisfied), `category_grades_json` |
| `community_audit_findings` | 067 | Plain-language findings with a jump-link (NO verbatim snippet in free tier) | PK `id`; `run_id`→`community_audit_runs(id)`; `category`, `severity`, `type`, `title`, `message_ref` (a link, not text), `confidence` |
| `community_audit_security_checks` | 067 | Deterministic security checklist results | PK `id`; `run_id`→`community_audit_runs(id)`; `check_key`, `status` ∈ `pass`/`warn`/`fail` |
| `community_audit_settings_snapshot` | 067 | Identity & Polish snapshot (one per run) | PK `id`; `run_id`→`community_audit_runs(id)` (UNIQUE); boosts/emoji/soundboard/vanity/verification fields, `raw_json` |
| `community_audit_reaction_ledger` | 067 | Reaction-existence ledger (one row = "this reaction currently exists") | PK `(guild_id, post_id, reactor_id, emoji)`; `guild_id`→`community_audit_guilds(guild_id)`, `author_id`. **Leaderboard score is a COUNT over live rows** — REMOVE deletes the row so a reaction removal correctly decrements (never a monotonic counter) |
| `community_audit_member_scores` | 067 | Materialized contributor score (derived from ledger + thread-depth; always recomputable) | PK `(guild_id, member_id)`; `contribution_score`, `components_json`, `last_active_at` |
| `community_audit_member_activity` | 067 | Per-member per-period activity (for dormant-member reactivation list) | PK `(guild_id, member_id, period)`; `message_count` |
| `community_audit_rate_limits` | 067 | Per-guild/inviter/global rate + cost counters | PK `(scope, key, window_start)`; `scope` ∈ `guild`/`inviter`/`global`, `count`, `ai_usd` |
| `community_audit_benchmark` | 067 | Anonymized cross-server per-category score distribution ("vs median" band) | PK `(category, metric_key)`; `distribution_json`, `sample_size` |
| `community_audit_identity_links` | 067 | Twitter↔Discord identity link (paid blended leaderboard; empty in v1) | PK `(guild_id, discord_member_id)`; `twitter_handle`, `confidence`, `source` |
