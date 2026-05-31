# SableRelay (`sable_platform.relay`)

The multi-tenant **X ↔ Telegram ↔ Discord** bridge, built as an in-process SablePlatform module (not a separate repo). Its tables (the `relay_*` family, **migration 057**) live in the shared `sable.db` and reuse SP's connection factory. The canonical product spec is `SableRelay/PLAN.md`; this doc describes what actually exists in-tree.

> **Status (2026-05-31): partially built — substrate only.** The schema and the listener/dispatch layer are real and tested (16 test files under `tests/relay/`). The relay's own *product behavior* — feed mirroring, the amplify/quorum flow, reply-opportunity DMs — is **not built**. There is no running relay service and no CLI command. It exists primarily because **SableAutoCM sits on it** (see [AUTOCM.md](AUTOCM.md)).

## Why it lives inside SablePlatform

Per `SableRelay/PLAN.md` §5.3 and §13, Relay is deliberately **not** a standalone service — it's an SP module so it can share the single `sable.db`, the connection pool, the audit/cost ledgers, and the member-identity store with AutoCM and the rest of the suite. It follows SP's stdlib-`logging` house style (no `structlog`).

## What's built vs pending

| Area | Module(s) | Status |
|---|---|---|
| Config surface | `config.py` (`RelaySettings`, `RELAY_*` env via pydantic-settings) | ✅ Built |
| Schema | `schema.py` (SQLAlchemy `Table()` mirroring 057) + migration `057_relay.sql` | ✅ Built (17 tables) |
| Query helpers | `db.py` — binding lifecycle, operator-chat provisioning, inbound persistence | ✅ Built |
| Listener / dispatch | `bot/` — `txn` (`BEGIN IMMEDIATE`), `dedupe`, `escaping` (ping-prevention), `binding` (migrate/kick lifecycle), `loop`, `telegram_app`, `discord_app`, `registry` (handler-registration API AutoCM consumes) | ✅ Built + tested |
| Feed (mirror/publish) | `feed/` — poller / publisher / sweeper (PLAN §5.3) | ❌ **Not built** (dir absent) |
| Amplify / quorum / flag-reply | `bot/handlers/` | ❌ **Not built** |
| X / SocialData ingestion | `socialdata.py` | ❌ **Not built** |
| CLI | `cli/relay_cmds.py`, `relay` command | ❌ **Not built** |

**Net:** the part AutoCM needs (transport, exactly-once dedupe, member identity, operator-chat provisioning, in-process handler registry) is done; the publish-exactly-once **outbox** that AutoCM's publisher and the feed publishers depend on is spec-only.

## Schema — the `relay_*` tables (migration 057)

`relay_clients`, `relay_chats`, `relay_chat_bindings`, `relay_members`, `relay_member_identities`, `relay_member_roles`, `relay_member_preferences`, `relay_tweets`, `relay_messages`, `relay_submissions`, `relay_submission_reactions`, `relay_publication_jobs`, `relay_publications`, `relay_reply_opportunities`, `relay_reply_opportunity_targets`, `relay_reply_notifications`, `relay_processed_updates`.

`relay_processed_updates` is the exactly-once dedupe gate (survives restarts). `relay_chat_bindings` carries the per-client chat lifecycle (incl. the `role='operator'` HITL chat AutoCM posts to).

## How other code consumes Relay

The handler-registration API in `bot/registry.py` (`RelayHandlerRegistry`) is the seam:
- `register_message_handler(fn)` — AutoCM registers its engage-check → classifier → … pipeline; the relay invokes it **after** dedupe + `relay_messages` persistence and **outside** any `BEGIN IMMEDIATE`. Handler exceptions are isolated (logged + swallowed) so a bad message can't crash the shared loop.
- `register_member_event_handler` / `dispatch_member_event` — JOIN/leave (greeting flow).
- `register_callback_handler(fn, prefix=...)` / `dispatch_callback` — inline-button callbacks (deduped, so a redelivered `CallbackQuery` doesn't double-apply).
- `provision_operator_chat(org_id, chat_id)` / `get_operator_chat(org_id)` — idempotent per-client operator-chat provisioning.

See `SableAutoCM/docs/SABLE_RELAY_INTEGRATION.md` for the consumer-side contract.

## v1 safety boundary (from PLAN.md)

In v1, **replies are suggested, never auto-posted** — the reply-opportunity flow DMs opted-in members a one-tap X Web-Intent compose deeplink (which cannot pre-attach media); the bot does not post on anyone's behalf. OAuth auto-posting is Phase 7 / explicitly out of v1 scope.

## Extending

- Schema changes require the **dual migration** (SQL file in `_MIGRATIONS` + Alembic revision) — see CLAUDE.md § Dual-migration requirement.
- Build order per PLAN: feed/ outbox (the exactly-once publisher) is the prerequisite for both the mirror flow and AutoCM's live publish step.

## Pointers

- Product spec: `SableRelay/PLAN.md`
- Consumer contract: `SableAutoCM/docs/SABLE_RELAY_INTEGRATION.md`
- Integration shape: [CROSS_REPO_INTEGRATION.md](CROSS_REPO_INTEGRATION.md) § Integration Patterns (pattern 4, in-process subsystems)
