# SableAutoCM — "NULO" (`sable_platform.autocm`)

The bimodal-NULO LLM **community manager**, built as an in-process SablePlatform product layer on top of the SableRelay substrate ([RELAY.md](RELAY.md)) and SP's foundational layer (audit / cost / member identity / workflow engine / alerts). Its tables (the `autocm_*` family, **migration 058**) live in the shared `sable.db`. The product spec is `SableAutoCM/` (README, PLAN, DESIGN, `docs/`); this doc describes the in-tree build.

> **Status (2026-05-31): under active development — not deployed.** Most of the pipeline is implemented with tests (32 test files under `tests/autocm/`); the **live publish step is stubbed** (gated on the SableRelay outbox), and **no client is running**. It is 🔵 ROADMAP for BD purposes — do not present as available. Gated on a voice-viability spike, client sign-off on real outputs, and the SableRelay outbox landing.

## The reuse rule (read this first)

AutoCM reuses sable-pulse's deterministic CM engine via the **in-tree vendored copy** `sable_platform._vendor.sable_pulse_core` — **never** the sibling `sable_pulse` repo (SP pillar-1). The vendored tree is **GENERATED, never edited in place**; it's refreshed only by re-running the donor's sync script and is **CI drift-gated** (`tests/autocm/test_vendor_drift.py`). See CLAUDE.md § Architecture Decisions for the full vendoring-deviation record. The D-1 reuse is wired live:
- `autocm.classifier.filter` ← vendored `engagement.assess`
- `autocm.gate.safety` ← vendored `safety.check_refusal`
- `autocm.kb.constants` ← vendored `slotfill.SlotFillKB`

## The pipeline

A message flows: **classifier** (engage-check → tier → category) → **KB** retrieval → register-routed **drafter** (calm vs reactive) → **gate** (safety → citation/hallucination → confidence → autonomy) → **HITL review queue** *or* autonomous **publisher**, with **escalation** for tier-3. A weekly **digest** reports time-saved + community-health delta.

## What's built vs pending

| Stage | Module(s) | Status |
|---|---|---|
| Classifier | `classifier/` (`filter`, `tier`, `register`, `categories`) — ~1,500 LOC | ✅ Implemented + tested |
| Knowledge base | `kb/` (`extractor`, `store`, `onchain`, `refresher`, `constants`) — ~2,500 LOC | ✅ Concrete impls done (the `NotImplementedError`s are ABC defaults; subclasses like `SQLiteKBStore` are implemented) |
| Drafter | `drafter/` (`compose_calm`, `compose_reactive`, `compose_shared`, `dispatch`, `persona`, `thread_context`) | ✅ Implemented + tested |
| Gate | `gate/` (`safety`, `citation_check`, `confidence`, `autonomy`, `review_queue`) — ~2,700 LOC | ✅ Implemented + tested (`review_queue.HITLReviewSurface` is an abstract seam; the TG concrete impl exists) |
| Escalation | `escalation/` (`tier3`, `incident`) — ~2,000 LOC | ✅ Implemented + tested (tier-3 dual-routes to founder **and** on-call) |
| Operator commands | `operator/commands.py` | ✅ Implemented |
| Digest | `digest/` (`weekly`, `analytics`) — ~1,800 LOC | ✅ Implemented (not wired into a live schedule — no deployment) |
| Adversarial harness | `adversarial/regression.py` | 🟡 Partial |
| **Publisher (live send)** | `publisher/tg.py` | ❌ **Stubbed** — `raise NotImplementedError`, gated on the Relay outbox |
| **Publisher (X replies)** | `publisher/x_reply.py` | ❌ Stubbed — **v2, feature-flagged off** per client |
| LLM seam | `llm.py` (`LLMProvider` adapter) | ✅ Built (seam) |
| Loaders / manifest | `loaders.py` (`ClientConfig`/`PersonaSpec`), `manifest.py` (deployment manifest) | ✅ Built |

Builtin workflows already registered: `autocm_kb_refresh`, `autocm_autonomy_sweep`, `autocm_weekly_digest`.

**Why it can't run end-to-end yet:** (1) the publisher raises `NotImplementedError` until the Relay outbox exists; (2) the Relay listener feed isn't built ([RELAY.md](RELAY.md)); (3) no client is configured/deployed.

## The product model (per spec)

- **Bimodal voice** — a **calm register** (default; Bill-Monday tone) and a **reactive register** (charged contexts; HK-47-ish). Per-client persona; "NULO" is RobotMoney's specifically (Esperanto for "zero").
- **Earned autonomy** — per-`(client, category)` state machine, states `hitl` → `auto`. Starts 100% human-reviewed; a category flips to autonomous only after ≥50 samples + ≥90% clean-approval + zero safety violations + operator sign-off. Auto-demotes on approval-rate regression.
- **Tiered hallucination prevention** — high-stakes facts (contract addresses, audit/official links) are **slot-filled from a registry, never LLM-generated** (`kb/constants` ← vendored `SlotFillKB`).
- **Flags, doesn't moderate** — surfaces impersonation/scam/spam/prompt-injection to a private operator channel; never bans/mutes/kicks.
- **Hard refusals** — price prediction, financial/legal advice refused in-persona (safety bank).
- **Productization tiers** (v1/v2/v3): white-glove (Sable-operated) → managed → self-hosted.

## Schema — the `autocm_*` tables (migration 058)

`autocm_personas`, `autocm_clients`, `autocm_kb_sources`, `autocm_kb_chunks`, `autocm_kb_constants`, `autocm_drafts`, `autocm_reviews`, `autocm_category_state`, `autocm_escalations`, `autocm_flagged_users`, `autocm_adversarial_runs`, `autocm_digest_interactions`, `autocm_time_saved_baseline`.

`autocm_drafts` FKs into Relay tables (`source_message_id` → `relay_messages.id`, `posted_publication_id` → `relay_publications.id`, `author_member_id` → `relay_members.id`).

## Extending

- Schema changes require the **dual migration** (SQL + Alembic) — CLAUDE.md § Dual-migration requirement.
- **Never** hot-fix the vendored safety bank in place — land it in the `sable-pulse` donor and re-sync, or CI drift-gate fails.

## Pointers

- Product spec: `SableAutoCM/{README,PLAN,DESIGN}.md` + `SableAutoCM/docs/` (`FEATURE_INVENTORY`, `PERSONA_ENGINEERING`, `SAFETY`, `HITL_UX`, `DIGEST`, …)
- Substrate: [RELAY.md](RELAY.md) and `SableAutoCM/docs/SABLE_RELAY_INTEGRATION.md`
- Vendoring rule: CLAUDE.md § Architecture Decisions
- Integration shape: [CROSS_REPO_INTEGRATION.md](CROSS_REPO_INTEGRATION.md) § Integration Patterns (pattern 4)
