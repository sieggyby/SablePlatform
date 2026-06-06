# SableRelay (`sable_platform.relay`)

The multi-tenant **X ↔ Telegram ↔ Discord** bridge, built as an in-process SablePlatform module (not a separate repo). Its tables (the `relay_*` family) live in the shared `sable.db` and reuse SP's connection factory. The canonical product spec is `SableRelay/PLAN.md`; this doc describes what actually exists in-tree.

> **Status (2026-06-06): substantially built — substrate + feed + operator flows live.** The schema, listener/dispatch layer, SocialData ingestion, the publish-exactly-once feed (poller / publisher / sweeper), the operator flows (amplify / quorum / flag-reply / preferences / admin / forget-me / link-x), and the `relay` CLI are all real and tested (34 test files under `tests/relay/`). The remaining roadmap is OAuth auto-posting (Phase 7, explicitly out of v1) and the higher-tier coordination UX. SableAutoCM sits on the relay outbox (see [AUTOCM.md](AUTOCM.md)); the **Reply-Opportunity Feed** (the operator-facing surface in SableWeb) reads the relay opportunity/sweep tables — see [CROSS_REPO_INTEGRATION.md](CROSS_REPO_INTEGRATION.md).

## Why it lives inside SablePlatform

Per `SableRelay/PLAN.md` §5.3 and §13, Relay is deliberately **not** a standalone service — it's an SP module so it can share the single `sable.db`, the connection pool, the audit/cost ledgers, and the member-identity store with AutoCM and the rest of the suite. It follows SP's stdlib-`logging` house style (no `structlog`).

## What's built vs pending

| Area | Module(s) | Status |
|---|---|---|
| Config surface | `config.py` (`RelaySettings`, `RELAY_*` env via pydantic-settings) | ✅ Built |
| Schema | `schema.py` (SQLAlchemy `Table()` mirroring the `relay_*` family) + migrations 057/062/064/065 | ✅ Built |
| Query helpers | `db.py` — binding lifecycle, operator-chat provisioning, inbound persistence, outbox enqueue, opportunity/sweep CRUD | ✅ Built |
| Listener / dispatch | `bot/` — `txn` (`BEGIN IMMEDIATE`), `dedupe`, `escaping` (ping-prevention), `binding` (migrate/kick lifecycle), `loop`, `telegram_app`, `discord_app`, `registry` (handler-registration API AutoCM consumes) | ✅ Built + tested |
| Feed (mirror/publish) | `feed/` — `poller`, `publisher` (the §3.1 publish-exactly-once outbox state machine), `sweeper` (expiry/GC/stuck-claim/reconciliation), `canonical` | ✅ Built + tested |
| Operator flows | amplify / quorum / flag-reply / preferences / admin / forget-me / link-x | ✅ Built |
| X / SocialData ingestion | `socialdata.py` — caching, 402/429-aware client + per-org budget controls | ✅ Built |
| CLI | `cli/relay_cmds.py` — `relay bind-chat / register-operator / enable / disable` (alias `pause-org`) `/ status / pending` | ✅ Built |
| Reply-opportunity feed + sweep | opportunity stamping + sweep state machine (mention/topic/VIP lanes) — read by SableWeb `/ops/reply-assist` | ✅ Built (mention lane live in prod; topic/VIP lanes gated on SocialData budget) |
| OAuth auto-posting | direct send on a member's behalf | 🔵 Roadmap (Phase 7, out of v1) |

**Net:** the transport, exactly-once dedupe, member identity, operator-chat provisioning, in-process handler registry, the publish-exactly-once **outbox** (which AutoCM's publisher and the feed publishers enqueue to), SocialData ingestion, and the operator flows are done. v1's safety boundary still holds — **replies are suggested, never auto-posted** (see below).

## Schema — the `relay_*` family

**Migration 057 (substrate, 17 tables):** `relay_clients`, `relay_chats`, `relay_chat_bindings`, `relay_members`, `relay_member_identities`, `relay_member_roles`, `relay_member_preferences`, `relay_tweets`, `relay_messages`, `relay_submissions`, `relay_submission_reactions`, `relay_publication_jobs`, `relay_publications`, `relay_reply_opportunities`, `relay_reply_opportunity_targets`, `relay_reply_notifications`, `relay_processed_updates`.

- `relay_processed_updates` is the exactly-once dedupe gate (survives restarts).
- `relay_publication_jobs` is the **publish-exactly-once outbox** (the `relay_publication_jobs_dedupe` partial-unique index collapses double-enqueues); AutoCM's publisher and the feed publisher both write here.
- `relay_chat_bindings` carries the per-client chat lifecycle (incl. the `role='operator'` HITL chat AutoCM posts to).

**Reply-Opportunity Feed + sweep (migration 062):** `relay_reply_opportunities` (the feed surface) plus `relay_opportunity_operator_state` (per-operator dismiss/snooze/handled), `relay_opportunity_feedback` (the learning thumbs; made nullable in mig 068), `relay_sweep_config` + `relay_sweep_cursor` (per-org sweep parameters + resume state), and `relay_operator_heartbeat` (the live-operator gate the sweep keys on).

**Reply-learning (migration 063):** tell-score + embedding-cache columns/tables backing the §10 humanizer and the P3 ranker.

**Trending-Story Autopilot (migration 064):** `relay_trending_stories` — auto-detected story topics + auto-monitor state.

**Relay-quality corpus (migration 065):** `relay_quality_accounts`, `relay_quality_tweets`, `relay_tweet_snapshots` — the VIP/quality-author corpus + engagement snapshots feeding the VIP sweep lane.

## How other code consumes Relay

The handler-registration API in `bot/registry.py` (`RelayHandlerRegistry`) is the seam:
- `register_message_handler(fn)` — AutoCM registers its engage-check → classifier → … pipeline; the relay invokes it **after** dedupe + `relay_messages` persistence and **outside** any `BEGIN IMMEDIATE`. Handler exceptions are isolated (logged + swallowed) so a bad message can't crash the shared loop.
- `register_member_event_handler` / `dispatch_member_event` — JOIN/leave (greeting flow).
- `register_callback_handler(fn, prefix=...)` / `dispatch_callback` — inline-button callbacks (deduped, so a redelivered `CallbackQuery` doesn't double-apply).
- `provision_operator_chat(org_id, chat_id)` / `get_operator_chat(org_id)` — idempotent per-client operator-chat provisioning.
- `enqueue_publication_job(...)` — the outbox enqueue AutoCM and the feed publisher call.

The **Reply-Opportunity Feed** is consumed by SableWeb (not via the registry): SableWeb reads `relay_reply_opportunities` (+ per-operator state) directly from the shared `sable.db` and writes heartbeat/dismiss/snooze/thumbs back. See `SableAutoCM/docs/SABLE_RELAY_INTEGRATION.md` for the AutoCM consumer contract and `SableRelay/REPLY_OPPORTUNITY_FEED_PLAN.md` for the feed.

## v1 safety boundary (from PLAN.md)

In v1, **replies are suggested, never auto-posted** — the reply-opportunity flow surfaces targets to operators (and DMs opted-in members a one-tap X Web-Intent compose deeplink, which cannot pre-attach media); the bot does not post on anyone's behalf. OAuth auto-posting is Phase 7 / explicitly out of v1 scope.

## Extending

- Schema changes require the **dual migration** (SQL file in `_MIGRATIONS` + Alembic revision) — see CLAUDE.md § Dual-migration requirement. The relay family currently spans migrations 057, 062, 063, 064, 065.
- The `feed/` outbox (the exactly-once publisher) is the prerequisite for both the mirror flow and AutoCM's live publish step — both are built and enqueue to `relay_publication_jobs`.

## Pointers

- Product spec: `SableRelay/PLAN.md`
- Reply-Opportunity Feed: `SableRelay/REPLY_OPPORTUNITY_FEED_PLAN.md`
- Consumer contract: `SableAutoCM/docs/SABLE_RELAY_INTEGRATION.md`
- Integration shape: [CROSS_REPO_INTEGRATION.md](CROSS_REPO_INTEGRATION.md) § Integration Patterns (pattern 4, in-process subsystems)
