# Sable Product Architecture

**Status:** canonical reference, 2026-06-06. Locked.
**Owner:** Sieggy.
**Audience:** anyone (Sable team, agent sessions, future contributors) trying to answer "where does this go" or "is this a new bot."

## 0. Why this doc exists

We have several bot ideas in flight — sable-pulse, NULO/AutoCM, sable-audit, sable-roles, pairwise — and more coming. Without a canonical answer to **"how many bots is this actually"**, the same fork gets re-derived every conversation. This doc is the answer. It supersedes any earlier framing that talked about "the autocm bot" / "the pulse bot" / "sable-roles" as independent products.

The answer: **neither one nor N — three tiers.**

---

## 1. The three tiers

```
┌─────────────────────────────────────────────────────────────────────┐
│  PLATFORM   one codebase, all reuse                                 │
│  sable_platform · sable_pulse.core (vendored) · sable_platform.relay│
│  DB · audit · autonomy state · safety · KB · LLM seam · sanitizer   │
│  rate limit · embeddings · persona engine · HITL surface · cost     │
└─────────────────────────────────────────────────────────────────────┘
                            ↑ composes
              ┌─────────────┴─────────────┐
              │                           │
┌─────────────────────────┐   ┌─────────────────────────┐
│  PRODUCT · sable-audit  │   │  PRODUCT · the Suite    │
│  Sable-branded.         │   │  Client-branded.        │
│  Single bot identity.   │   │  One bot identity       │
│  Self-invite Discord.   │   │  per client per platform│
│  TOP-OF-FUNNEL wedge.   │   │  Feature-configurable.  │
└─────────────────────────┘   └─────────────────────────┘
              ↓                           ↓
        @sable_audit_bot           @robotmoney_bot
        (one identity,             @solstitch_bot
         many servers)             …one identity per tenant
                                   feature modules per tenant config
```

**Platform.** Shared runtime. All cross-product reuse lives here. Components compose; products consume them.

**Products.** What clients buy or invite. Two and only two: `sable-audit` (Sable-branded discovery wedge) and `the Suite` (per-client client-branded operational bot). A new "bot" idea is almost always a *feature module of the Suite*, not a new product.

**Instances.** Per-tenant deployments. Each client = one Suite bot identity per platform (TG, Discord, eventually X). One process per tenant — **no in-process multi-tenancy in v1** (load-bearing constraint, see §6).

The Suite's internal codename is "the Suite" pending a real product name. End users never see "Suite" — they see their own client's brand.

---

## 2. Platform — what lives here

The platform is sable-internal. It's the only place code reuse is honest about itself. Components:

| Component | Home | Notes |
|---|---|---|
| DB (Postgres) | `sable_platform/db` | One Postgres, multi-product tables prefixed. |
| Audit log | `sable_platform/db/audit.py` + `audit_log` table | Cross-tool joinable. |
| Cost ledger | `sable_platform/db/cost_events.py` | Per-org budgets, weekly/daily caps. |
| LLM seam | `sable_platform/llm/` (Anthropic adapter) | **Prompt caching mandatory** (`cache_control: ephemeral`). |
| Embeddings | `sable_platform/embeddings` (provider switch) | Read-through cache on `relay_tweets.embedding_json`. |
| Safety / refusal / engagement classifier | `sable_platform/_vendor/sable_pulse_core/` | Vendored, drift-CI-gated. |
| Rate limit | `sable_pulse.core/ratelimit.py` (vendored) | Single-process in-memory; replicas would each grant their own quota. |
| Persona engine | `sable_platform/personas/` + per-tenant YAML banks | **Persona edits = YAML only**, never Python. |
| Sanitizer pipeline | Donor: `~/Projects/sable-pulse/sable_pulse/core/sanitizer/`. Vendored consumer: `sable_platform/_vendor/sable_pulse_core/sanitizer/` (via `scripts/sync_vendor.py`). Bot-layer (runtime-specific) wrapper: `sable_pulse/sanitizer/`. | See `~/Projects/sable-pulse/docs/SANITIZER_DESIGN.md`. **Platform-level, not a feature module** — any client-branded output passes through. Donor-`core/`-and-vendor-sync model (matches the safety bank pattern). Per D19/R2-03 the secret-scanner subprocess wrapper lives at bot-layer (`sable_pulse/sanitizer/secret_scan.py`), not in vendored core. |
| Autonomy state machine | `sable_platform/autocm/gate/autonomy.py` (current); pure-constants extract → `sable_platform/_shared/autonomy.py` (planned) | Per `SANITIZER_DESIGN_AUDIT_R1.md` F-002, pure thresholds + verdict logic extract; persistence reimplements per consumer. |
| HITL surface | `sable_platform/autocm/gate/review_queue.py` (current canonical) | Inline-button approval (not reaction — see F-006). 15-min stale-expire. |
| Cross-platform IO | `sable_platform/relay/` (SableRelay) | TG, Discord, X. Exactly-once outbox, reconciliation, retention GC. |
| Checkin | `sable_platform/checkin/` | Existing cross-tool pattern. |

**What is NOT platform:** persona text (data, not code), per-tenant policy, brand assets, product-specific CLI commands.

The platform is governed by `SablePlatform/AGENTS.md` and `SablePlatform/CLAUDE.md`. **`_vendor/sable_pulse_core/` is GENERATED-NOT-EDITED** — one-way sync from `~/Projects/sable-pulse/sable_pulse/core/` via `scripts/sync_vendor.py`. Drift gate at `tests/autocm/test_vendor_drift.py`.

---

## 3. Product · sable-audit

**Single bot identity. Sable-branded. Self-invite to Discord servers. Stays distinct from the Suite.**

The rationale for keeping sable-audit separate: its whole business model is being recognizably Sable's. Someone invites `@sable_audit_bot` to a server they own → free $0 metadata audit → DM upsell with partial grade → consent-gated deep audit + leaderboard. *That's* the wedge. Merging it into the client-branded Suite kills the wedge.

Repo: `~/Projects/sable-audit/`. Spec: `~/Projects/sable-audit/PLAN.md`. Top-of-funnel for the low-effort client tier; each weak audit finding maps to a recommendation and (where one exists) a Suite SKU.

**sable-audit consumes platform components**: DB, audit log, the vendored deterministic engine (engagement, classifier), Cult Grader's Stage-4d pure-Python metrics (importable, NOT the Cult-Grader-graded LLM stages — those are walled off per the documented invariant).

**sable-audit is NOT a feature module of the Suite.** It is its own product line. A future "wedge for Telegram" would join the *audit family* (Sable-branded discovery), not the Suite.

---

## 4. Product · the Suite

**Per-client. Client-branded. One bot identity per client per platform. Feature-configurable.**

The Suite is the operational bot a client buys. End users see *the client's bot*, never Sable. Each client gets:

- One BotFather token (TG) — provisioned to `@<client>_bot`.
- One Discord application + bot user, when Discord is in scope.
- One tenant config (`config/<tenant>.yaml`) declaring which feature modules are on.
- One tenant policy (`config/<tenant>.policy.yaml`) for sanitizer + pairwise + anything else policy-bearing.
- One per-tenant data directory.
- One bot process per tenant per platform.

**Sale shape:** features turn on in tiers. A client buying the "Community" tier might get `cm + engage`; "Community Pro" adds `pulse + pairwise`. Same codebase, different config.

### 4.1 Feature modules

| Module | Heritage | Surface | Commercial framing |
|---|---|---|---|
| `pulse` | sable-pulse | `/committee`, `/review`, `/devlog` (sanitized) | Project-legibility. Light, deterministic, characterful. |
| `cm` | NULO / AutoCM | classify → draft → gate → publish; weekly digest; incident mode; escalation | Full community management. High-touch. NULO is the persona that speaks when CM is active. |
| `engage` | sable-roles | fitcheck, burn-me, roast, role rotation, influenza-style rotational signal slots | Discord-side gamified primitives. Currently Discord-first; TG variants TBD. |
| `pairwise` | new — see §4.2 | `arena`, `chime`, `dm-private` | Dual-value: gamified community engagement + Sable preference-data harvest (disclosed). |

### 4.2 pairwise — the new module

Takes pairs of similar items from Sable's corpus (tweets / images / clips), surfaces them in client TG/Discord communities, harvests preference votes. The community gets a gamified engagement game with a leaderboard; Sable gets preference data to bootstrap quality models. Three modes:

- **`arena`** — dedicated channel, continuous posting, anyone votes.
- **`chime`** — main channel, conditional (default: >3hr quiet, max 1/day). NULO posts do NOT count as "real posts" for the trigger (per-tenant overridable) so a NULO-only-active community still gets nudged.
- **`dm-private`** — operator or paid-tester DM only, on-demand or push.

**Composes platform pieces:** Slopper's corpus (`sable.db` + vault) as data source via Slopper's `sable serve` HTTP read API, embeddings for similarity, SableRelay for IO, SP DB for vote/score tables, sanitizer for content-filter (corpus is Sable's but it lands in the client's channel — same machinery, different rules).

**Leaderboard scoring v1** = `effort` (decayed vote count) + `calibration` (early-vote agreement with eventual consensus, TrueSkill-lite). v2 enrichment: pair difficulty weighting, content-type diversity, streaks. Tastemakers beat spam-voters from day one.

**Cross-platform IO asymmetry to plan for:** Discord reactions reach bots natively → use as the vote surface. Telegram reactions need bot-admin + `allowed_updates=["message_reaction"]` and are unreliable → use **inline keyboards** instead. Plan for this from day one. See `SANITIZER_DESIGN_AUDIT_R1.md` F-006 for the same pattern in HITL.

**Data-flow disclosure is contractual, not technical.** Preference data flowing back to Sable goes in the client agreement. Can be a tier-discount lever ("opt in to preference sharing → tier discount"). Not a technical decision; flag for the commercial side.

### 4.3 Future modules

Anything new that fits the operational, per-client, client-branded shape joins the Suite as a module. The question to ask first: **does it have a Sable brand or a client brand?** If Sable, it joins the audit family. If client, it's a Suite module.

---

## 5. Repo → architecture mapping

The destination, not where we are. Most repos shift in *framing*, few in code location.

| Repo | What it is today | What it is in the architecture | Trajectory |
|---|---|---|---|
| `~/Projects/SablePlatform/` | Platform runtime + DB + relay + autocm + audit | **Platform.** Canonical. | Continues. |
| `~/Projects/sable-pulse/` | Deterministic TG bot, RM-first tenant, `/review` `/committee` `/devlog` | **Suite TG runtime** (the bot process the Suite runs on Telegram). The deterministic `core/` is vendored INTO the platform. Repo name historical — to be reframed in its own README. | Rename or repurpose: it stops being "a product" and starts being "the Suite's TG entrypoint." Defer the rename; clarify the framing in `sable-pulse/AGENTS.md`. |
| `~/Projects/SableAutoCM/` | AutoCM module design, NULO persona, voice docs, calibration | **Spec/design home for the `cm` feature module + NULO persona work.** Code lives in `sable_platform.autocm`. | Continues as design + persona docs. |
| `~/Projects/sable-roles/` | SolStitch Discord bot — fitcheck, burn-me, roast, influenza | **Suite Discord runtime.** Folds in per (a) decision 2026-06-06. SolStitch becomes the first Discord tenant; the modules become `engage` feature modules. | Phase 1.x: rename/absorb into the Suite's Discord runtime. Codebase isn't yet Suite-ready — treat as a migration, not a blocker. |
| `~/Projects/sable-audit/` | Self-invite Discord audit + leaderboard, spec'd 2026-06-06 | **Product · sable-audit.** Stays distinct. | Continues as its own product. |
| `~/Projects/Sable_Slopper/` | Sable's internal ops CLI (content production, reply gen, weekly cycle) | **Sable internal tooling.** Not a Suite feature; not a discovery product. Lives in its own lane. | Continues. |
| `~/Projects/SableRelay/` (design) | Cross-platform TG/Discord/X relay substrate | **Platform component.** Code lives in `sable_platform.relay/`. | Spec doc stays; code is in SP. |
| `~/Projects/Sable_Cult_Grader/` | Community health prospecting tool, pure-Python metrics | **Platform-adjacent.** Stage-4d metrics importable; LLM stages walled off. | Continues; gets consumed by sable-audit + the Suite's `engage` module via importable functions. |
| `~/Projects/SableKOL/` | KOL matcher, follow-graph, outreach | **Sable internal tooling.** Operator-facing, not a bot. | Continues. |
| `~/Projects/SableWeb/` | Web surface (sable.tools, /ops/reply-assist, etc.) | **Web surface.** Not a bot. | Continues. |
| `~/Projects/SableTracking/` | Tracking infra | **Platform-adjacent data layer.** | Continues. |
| Misc Sable_* repos | various | Internal tools, web surfaces, or platform-adjacent. | Continues. |

**Naming honesty:** "the Suite" is a working name. The end-user-facing brand is *the client's brand* (each client's bot has the client's name). The internal product line needs a real name eventually — leave that to Sieggy to decide.

---

## 6. Tenant model (v1)

**One bot identity = one process = one tenant per platform.** No in-process multi-tenancy. This is a load-bearing constraint, deliberately chosen.

- Per-tenant config: `config/<tenant>.yaml` — functional (which features, which channels, which repos for `pulse.devlog`, etc.)
- Per-tenant policy: `config/<tenant>.policy.yaml` — content/output policy (sanitizer rules, codename maps, never-mention lists, leaderboard reset cadence, etc.). **Separate from functional config on purpose** — touched by client comms/legal, not ops.
- Per-tenant data path: `data/<tenant>/` (audit jsonl, last-good caches, leaderboard state).
- Per-tenant BotFather token (TG) / Discord application (Discord), held in env, never YAML.

**Why single-tenant per process:**
- The platform's RateLimiter is in-memory and single-process.
- The audit + HITL surfaces are per-tenant; cross-tenant bleed would be catastrophic.
- One container per tenant is operationally fine at current scale (≤20 clients), and trivial to orchestrate with systemd or compose.
- A real multi-tenant seam is deferrable to when scale demands it; doing it speculatively introduces cross-tenant attack surface for no current benefit.

**Per-tenant config schema** is shared across all feature modules. A tenant config declares which modules are on; each module reads its own subsection. Example skeleton:

```yaml
# config/robotmoney.yaml
tenant: robotmoney
platform: telegram
features:
  pulse:    { committee: on, review: on, devlog: off }   # devlog gated on Lex
  cm:       { enabled: false }                            # NULO not yet greenlit
  engage:   { enabled: false }
  pairwise: { enabled: false }

# Platform-level
rate_limits: { global_per_10min: 3, per_account_per_10min: 1, per_account_per_day: 3 }
whitelist:   [<operator tg ids>]
```

Per-tenant policy lives next to it:

```yaml
# config/robotmoney.policy.yaml
sanitizer:    { …per SANITIZER_DESIGN.md… }
pairwise:     { eligible_corpus_tags: […], … }
```

---

## 7. Worked example — RobotMoney as a tenant

- **Bot identity:** `@robotmoney_<tbd>_bot` provisioned via @BotFather. Token in env.
- **Features (v1):** `pulse.committee = on`, `pulse.review = on`, `pulse.devlog = off (gated on Lex)`, `cm = off`, `engage = off`, `pairwise = off`.
- **Process:** one bot process, `PULSE_PROJECT=robotmoney`, runs on Hetzner via systemd.
- **Persona:** Athena / Woon / Robot Money personas for `/review`. NULO persona unused (cm off). The bot identifies as a bot (bio + `_bot` suffix).
- **Sanitizer:** active for any future client-branded output (devlog when it lands). RM's `policy.yaml` declares omit-list, codename map (none currently), tense=past_present, security_fix=never-auto.
- **Audit:** `data/robotmoney/audit.jsonl`; v1 local jsonl, hash-chained.
- **HITL surface:** RM operator chat ID in env (`PULSE_AUDIT_DM`). Approvers (Sieggy + Arf) by TG user id.
- **Cost / rate limits:** per RM's `config.yaml`.
- **Data flow:** RM's `/review` reads Dexscreener + cached committee page; `/devlog` reads RM repos when Lex enables.

When `cm` later turns on for RM, NULO's persona activates, the classifier + KB + gate + digest become live, and the same bot handles community management — without provisioning a second bot.

---

## 8. Decision rule — when someone proposes a new "bot"

Run the proposal through this checklist before agreeing to a new bot identity:

1. **Discovery wedge or operational?**
   *Discovery* → joins the audit family (Sable-branded, single identity).
   *Operational* → Suite feature module (client-branded, per-tenant).

2. **Per-client or universal?**
   *Universal* (one identity for many servers) → audit family.
   *Per-client* (each tenant gets it under their brand) → Suite module.

3. **What platform components does it consume?**
   List them. Anything not already in the platform = new platform work, costed.

4. **Does it need NEW platform infrastructure?**
   Specify what. New components add to the platform once, not per-feature.

5. **New persona?**
   Add to persona engine YAML banks. Don't bolt a new bot for a new voice. Personas compose under bot identities.

6. **Does it have a Sable brand?**
   Yes → audit family.
   No → Suite module.

If the answer to 1+2+6 doesn't land cleanly in either product, the proposal probably needs reshaping before architecting.

---

## 9. Trajectory / open items

- **Phase 0 (now):** This doc lands. Other repo AGENTS.mds add a one-line pointer.
- **Phase 1:** Sanitizer R1 audit fixes applied (15 unilateral fixes + 3 architectural commitments locked in `SANITIZER_DESIGN.md`). R2 audit runs against revised spec.
- **Phase 1.x:** `sable-roles` folds into the Suite's Discord runtime. Not a blocker — a migration. Rename/absorb when SolStitch's `engage` features stabilize.
- **Phase 2:** Per-tenant config schema unified across feature modules (declared once, consumed by all modules).
- **Phase 3:** Suite TG runtime (current sable-pulse repo) reframed in its own README — stops being "a product," starts being "the Suite's TG entrypoint."
- **Phase 4:** Feature-module pluggability — adding a new module is a code addition, not a runtime change.
- **Open:** Suite needs a real product name. Currently a placeholder ("the Suite").
- **Open:** Audit family may grow beyond `sable-audit` (e.g. a TG wedge). When it does, this doc updates to reflect.

---

## 10. What this doc supersedes

- Any earlier framing that talked about "the autocm bot," "the pulse bot," "sable-roles" as independent products. They are feature modules now.
- The conversation thread 2026-06-06 (RobotMoney engagement) where "we have several bots in flight" was the framing problem. This is the answer.
- Implicit assumptions in individual repo PLAN.md / AGENTS.md files where "this bot vs that bot" appears — those are now "feature module vs feature module under the Suite," with the exception of sable-audit which stays its own product.

Individual repo AGENTS.mds remain authoritative for that repo's own conventions (code style, dependency policy, testing, etc.). This doc is authoritative for *cross-repo product structure.*

---

## References

- `~/Projects/sable-pulse/docs/SANITIZER_DESIGN.md` — platform-level sanitizer spec.
- `~/Projects/sable-pulse/docs/SANITIZER_DESIGN_AUDIT_R1.md` — R1 adversarial audit findings.
- `~/Projects/sable-pulse/AGENTS.md` — sable-pulse repo conventions.
- `~/Projects/SablePlatform/AGENTS.md` — platform conventions.
- `~/Projects/sable-audit/PLAN.md` — sable-audit spec.
- `~/Projects/SableAutoCM/{DESIGN,PLAN}.md` — AutoCM/NULO spec and persona work.
- `~/Projects/RobotMoney/community_strategy/committee_bot_and_lex_pitch.md` — design history for the RM engagement that seeded sable-pulse.
