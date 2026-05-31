# The Sable Suite — Capability Map

**Audience:** Business development, partnerships, and anyone who needs to explain what Sable can actually *do* for a crypto client — without reading code.

**What this is:** A plain-language inventory of every capability in the Sable tool suite, organized by the outcome it delivers, with an honest status label on each so you always know what we can promise *today* versus what's on the roadmap.

> **One-line pitch:** Sable runs managed community growth for crypto projects, backed by a proprietary tooling suite that finds the right clients, diagnoses their community's health, manufactures and measures content at scale, surfaces the right people to reach, and gives both the operator and the client a single console to see it all.

---

## How to read the status labels

| Label | Meaning | Can BD promise it? |
|---|---|---|
| ✅ **LIVE** | In production use today — includes internal operator/analyst tools (CLI/console), not only hosted services | Yes — sell it |
| 🟡 **IN-TRIAL** | Built and running, scoped to one client / proving out | Yes, framed as "we're piloting this with [client]" |
| 🔵 **ROADMAP** | Specified or scaffolded, not yet running | No — pitch as "coming," never as available |

**Bottom line up front:** Seven tools are live and sellable today (community diagnostic, prospecting, content tracking, KOL matching, content production, Discord engagement, and the client/operator console), plus the SablePlatform backbone; one capability is in client trial (weekly check-ins); and four are roadmap (NULO auto-CM, cross-platform relay, the project-legibility bot, and a client-comms surface). The roadmap items are the most exciting *story* but are not yet running — do not let them get pitched as shipped.

---

## What we can do for a client — by outcome

### 1. Find the right clients before they know they need us
**`Sable_Community_Lead_Identifier`** · ✅ **LIVE** · *internal BD tool*

An automated top-of-funnel prospecting pipeline. It scans for recently-funded crypto projects whose communities are weak — Sable's ideal-client profile — and hands BD a ranked, tiered shortlist.

- Ranked **Tier 1 / 2 / 3** shortlist of fundraising projects, scored on budget, credibility, **community gap**, TGE proximity, and thesis fit
- "Community gap" is the headline signal: well-funded projects with thin communities = the addressable market
- Free first-pass enrichment (GitHub activity, Discord/Telegram/Reddit size, token/genesis status, hiring signals) **before** any paid spend
- Optional paid Twitter deep-dive (SocialData) on the **top ~500** by first-pass score — engagement rate and mention/KOL quality
- Per-lead **BD pitch bullets**, contact enrichment, and a shareable prospect-facing one-pager
- Inbound detection: scans job boards for projects hiring community managers — and auto-promotes those finds into the next ranked run
- Manual BD overrides — **exclude, boost, penalize, or annotate** any project so BD judgment stays in the loop
- **Closed feedback loop** — real BD outcomes (who signed, who passed) flow back in and recalibrate the scoring weights, so the shortlist sharpens over time

> **BD takeaway:** This is your prospect engine. It tells you *who* to call and *why they're a fit* — with the pitch half-written. (Rankings are a strong heuristic; the scoring weights are still being calibrated against real BD outcomes.)

---

### 2. Diagnose how healthy a community really is
**`Sable_Cult_Grader`** · ✅ **LIVE** · *the most heavily-tested tool in the suite (1,680+ tests, full CI, Docker)*

An AI diagnostic that grades a project's community (Twitter + Discord) on real data and produces a report card. This is the analytical heart of the pitch.

- An **A–F community health report** (Twitter + Discord grades, including activity and culture dimensions) on real collected data, with a **confidence band** (low/medium/high) on each result tied to data completeness
- A **structural fragility score** — flags when a community depends on one or two voices (single-point-of-failure risk)
- A weekly **Community Health Index (0–100)** tracked over time, with trend reports once there's history
- **Member-level decay tracking** — who is fading, and how (gradual fade, sudden drop, sentiment-first, interaction-first); builds up over several weeks of runs (needs ≥3 weekly snapshots before it produces signal)
- A conversion funnel: follower → mentioner → recurring → quality reply → **"cultist"**
- AI-generated **Discord engagement playbooks**, with an outcome-tracking loop that measures whether the playbook's target metrics improved on the next run
- Cross-project intelligence — compare a prospect against the whole corpus, find **bridge nodes** (accounts active across multiple projects)

> **BD takeaway:** The first-pass health audit is the wedge — it runs on any prospect from just a Twitter handle, no onboarding, at low per-run cost (repo target under ~$0.60/run; re-renders are ~$0.07 or free). Run it, share the findings, and the gaps sell the engagement. Note: a cold audit is a *snapshot* — the longitudinal value (health-index trends, member decay, playbook outcomes) builds over repeated weekly runs.

---

### 3. Capture and measure every piece of community content
**`SableTracking`** · ✅ **LIVE** · *client TIG is the live deployment*

A Telegram + Discord bot where operators drop links, screenshots, and videos; the bot classifies, dedupes, and logs every contribution into a per-client database.

- **One-tap content logging** — paste a link or forward a post, the bot does the rest
- AI enrichment: summarizes, classifies format/intent, scores culture signals
- Three-layer **dedup** so the same content isn't double-counted
- Media auto-archived — every screenshot/video uploaded and linked to its record (Google Drive today; the shared R2 media layer is wired but not yet flipped on)
- Human **review queue** for ambiguous items, plus a deep operator command set
- **Weekly Smart Brief** (`/brief`) — a 6-section analytical report (what worked, contributor movement, participation tiers, retention curves) auto-generated each Monday and exported to the client's Drive as a Google Doc
- **Outcome attribution** (`/outcome`) — tag a piece of content with the result it drove (follower gain, DM, partnership lead, event), turning "we posted" into "this produced a lead"
- **Live engagement enrichment** — for any X link, pulls live tweet stats and auto-runs a deeper "why it worked" pass on high performers; promotes top contributors and "cultist candidates" into the shared graph
- Feeds the rest of the suite — contributors and content batch-sync (twice weekly) into the shared platform database

> **BD takeaway:** This is how we *prove* the work. Every contribution is logged, so the weekly client report is backed by real numbers, not vibes.

---

### 4. Reach the right influencers — with warm intros
**`SableKOL`** · ✅ **LIVE** · *operator tool in the web console*

A KOL (key-opinion-leader) discovery and matching engine. Ranks crypto-Twitter influencers by fit for a project and flags where Sable already has a relationship.

- A ranked, **fit-scored KOL shortlist** for any project or Twitter handle, with AI rationales
- A **"Sable already has a path here"** flag — surfaces candidates already present in a Sable client's community graph, plus operator-confirmed intro paths
- **Any-project wizard:** paste a handle, get a follow-graph survey, comparable-project suggestions, and a tiered outreach plan
- Per-candidate enrichment: real tweets read against an operator's persona → likes/dislikes/mutuals/common-ground for **cold-intro notes**
- Follow-graph clustering for audience overlap — and surfaces the **"kingmakers"** every influential follower has in common, so outreach hits the upstream nodes that move a whole audience
- A daily-refreshed **interactive network map** of the project's KOL graph, exportable as the outreach plan in Markdown / CSV / PDF
- Built on a standing bank of **~16,600 active crypto-Twitter KOLs**, each archetype- and sector-classified; every AI-suggested handle is **ground-truth-verified against live X data** so the list never contains hallucinated influencers
- Strict cost discipline — every paid call is logged

> **BD takeaway:** We don't just list influencers — we tell you which ones a Sable operator already has a path to, and hand them researched talking points to open the conversation.

---

### 5. Manufacture and optimize content at scale
**`Sable_Slopper`** · ✅ **LIVE** · *internal operator toolkit*

Sable's content production and account-strategy engine — Claude, ffmpeg, yt-dlp, and social APIs in one pipeline.

- **Short-form video clipping** from any YouTube URL or file — auto-transcribe, pick the best moments, captions + overlay — for TikTok / Reels / Shorts
- Meme generation, scene compositing, face-swap, and character-explainer videos with TTS
- **Performance analytics** ("pulse"): format lift, content attribution, trend scanning, and AI recommendations on what to make vs. stop making
- AI-generated **posting calendars** aligned to inventory and live niche trends
- Account voice management, tweet/thread drafting, hook scoring, full account audits
- Community intelligence: lexicon extraction, narrative velocity, pre-churn silence signals, churn-intervention playbooks
- **Automated weekly cycle** (`sable weekly`) — track performance → scan trends → advise → generate calendar → sync vault, on a timer; this is the "at scale" engine
- **Operator reply-assist** — *suggested* (never auto-posted) replies, with a persistent per-operator daily generation quota *(✅ LIVE, mig 056)*

> **BD takeaway:** This is the content factory — an **internal** engine. The *operator* gets the data-driven view of what's working; the *client* gets the output: high-volume short-form content on their accounts, with results surfaced through the SableWeb client portal (never the raw pipeline). Slopper is internal operator tooling by design — it is never exposed to clients directly.

---

### 6. Run engagement games inside the client's Discord
**`sable-roles`** · ✅ **LIVE** · *running in the SolStitch Discord since 2026-05-13*

Sable's dedicated Discord bot for community-role automation and engagement mechanics. One bot process serves every client server (multi-tenant), and every action writes into the shared platform DB — so in-server activity flows into the client's health view.

**Live in production today:**
- **"Fitcheck"** engagement game: streak tracking (`/streak`), image-only channel enforcement, auto-threads, and reaction scoring
- **"Burn-me"** and **"roast"** mechanics — opt-in AI roasts plus a peer-roast token economy (`/burn-me`, "Roast this fit")
- **"Airlock"** new-member verification — invite-source-aware admit / hold / triage
- **`/relax-mode`** — mods pause image-only enforcement with one toggle for off-theme moments
- A passive **vibe-personalization** layer (opt-in, budget-capped) so roasts land personally, plus a **full audit trail** on every enforcement action

**Built but not yet merged/enabled (branch code, not in production):**
- **Scored Mode** — grades posted fits on a rubric and reveals scores once a post earns enough reactions (ships off by default; QA-approved on a branch, not merged)
- A public **`/leaderboard`** and an ops **"state pin"** config dashboard

> **BD takeaway:** Daily, sticky, in-server engagement — not just analytics from the outside. *Caveat:* the fitcheck/scoring mechanics are tuned to SolStitch's fashion community; reuse for another client is a re-tune, not a config flip.

---

### 7. Give the client (and the operator) a single console
**`SableWeb`** · ✅ **LIVE** · *the human face of the whole suite*

The Sable Portal — a production web app with a strict wall between what clients see and what operators see.

**The client sees (`/client`, primarily read-only, Google login, scoped to their org only):**
- Community health and grade history
- Recommended actions — and clients can mark them adopted/skipped, a closed feedback loop (not just a read-only view)
- Content performance and engagement trends
- Deeper strategic views: counterfactual "what if you stopped?" value modeling, engagement-quality decomposition, member-lifecycle funnel, and a one-click **QBR export**

**The operator sees (`/ops`, everything):**
- Prospect fit scores and Sable verdicts, prospect/client triage
- Cost dashboard, audit trail
- Entity intelligence + force-directed relationship graph, playbook effectiveness
- Content pipeline, action queue, reply-assist, KOL network/wizard
- Morning brief, ops alerts, source freshness

**Public, no-login surfaces (lead-gen):**
- **Case-study microsites** (`/proof/*`, `/synq`) — animated client success stories you can send straight to a prospect
- A self-serve **prospect intake form** (`/intake`) that drops inbound leads into the operator pipeline

> **BD takeaway:** Clients get a clean, professional health portal scoped to *only their data*. They **never** see fit scores, verdicts, costs, or pipeline data — that wall is enforced in code (separate data-assembly paths, verdict sanitization, fail-closed guards). Safe to demo.

---

## The operator's command center

Behind the client-facing tools, an operator drives the whole suite from two surfaces:

- **`SableWeb /ops`** — the web console above (✅ LIVE)
- **`SablePlatform` CLI + alert system** — the backbone (✅ LIVE):
  - Deterministic **workflow engine** — runs multi-step cross-tool jobs (e.g. "discover lead → auto-trigger diagnostic → score") with retry/resume
  - **12 automated alert checks** — tracking gone stale, sentiment shifts, member decay, score changes, workflow failures, and more, delivered to Telegram / Discord with dedup + cooldown
  - **Weekly client check-in generator** 🟡 **IN-TRIAL** — auto-drafts a client-facing weekly update with week-over-week deltas, ready for an operator to forward *(piloting with TIG)*
  - **Alert-Triage API** (✅ LIVE) — token-authed endpoint so an agent or dashboard can review and resolve alerts
  - **Cost & budget tracking** — every paid API call logged, weekly spend caps per client

---

## What ties it all together

**`SablePlatform`** is the backbone — not a client-facing product, but the reason the suite is a *suite* and not seven disconnected scripts.

- **Shared database** — one canonical store every tool reads and writes, so a contributor tracked in SableTracking shows up in the client's health report and the operator's relationship graph
- **Canonical data contracts** — tools agree on what a "lead," "entity," or "metric" means
- **Workflow engine + alerts** — cross-tool automation and monitoring (above)
- **Shared media layer** (✅ LIVE for Slopper clips) — R2 storage + an HMAC-signed, expiring-URL proxy (the `sable-media-proxy` Cloudflare Worker, live at `sable-media-proxy.siegby.workers.dev`), with content-addressed dedup and per-client bucket isolation. Powering Slopper's clip-assist today; the SableTracking cutover (Drive → R2) and the branded `media.sable.tools` domain are still pending
- **Adapters** — clean integration so each specialized tool stays independent

> **BD takeaway:** When you pitch "an integrated growth operation," *this* is what makes it integrated. The data flows between tools automatically.

---

## On the roadmap (do **not** pitch as available)

These are the most exciting parts of the story and the most likely to be oversold. They are **specified or scaffolded, not running.**

### Autonomous AI community manager — "NULO"
**`SableAutoCM`** · 🔵 **ROADMAP**

The vision: Sable runs a persona-engineered AI community manager inside a client's Telegram (v1), with invisible human-in-the-loop oversight.

- Autonomously answers tier-1 community questions in a tuned, on-brand voice — but only after each category earns autonomy through a human-approval-rate gate (it starts 100% human-reviewed)
- A per-client knowledge base with **tiered hallucination prevention** — high-stakes facts (contract addresses, official links) are slot-filled from a registry, never LLM-generated
- Silently escalates ambiguous or sensitive messages to a Sable operator (and major incidents to the founder + on-call)
- A weekly digest headlining **"time saved"** and **"community-health delta"**
- Expands its autonomy category-by-category as approval rates prove out

*Status: under active development in-platform — the classifier, drafter, knowledge base, safety/autonomy gate, and escalation are implemented with tests; the live publish step, weekly digest, and adversarial harness aren't wired yet, and no client is deployed. Gated on a voice-viability spike, client sign-off on real outputs, and the SableRelay substrate landing first.*

### Cross-platform relay
**`SableRelay`** · 🔵 **ROADMAP**

The substrate AutoCM sits on: a multi-tenant bot bridging X, Telegram, and Discord.

- Auto-mirror a project's X feed into Discord and Telegram
- Team members submit a tweet from Telegram → after a peer-review quorum it cross-posts everywhere
- Reply-opportunity coordination: flag a tweet, DM opted-in team members a one-tap compose link *(replies are suggested, never auto-posted in v1)*

*Status: detailed spec; the schema (migration 057) and the listener/dispatch substrate are built, but the feed, publishing, and quorum logic aren't — no running service yet.*

### Project-legibility bot
**`sable-pulse`** · 🔵 **ROADMAP** *(built, not launched)*

A deterministic Telegram bot that makes a project's universe legible to newcomers — committee-call readouts (`/committee`), token reviews in the project's own personas' voices (`/review`), and newcomer explainers (`/who`, `/regime`). A "what the devs are working on" GitHub digest (`/devlog`) is planned for Round 2 (the source module exists; the command isn't wired yet). It also carries a built, tested **deterministic community-manager surface** — zero-LLM auto-answers for greetings, glossary, and fixed-fact lookups with silent operator escalation — which is the engine the roadmap NULO / SableAutoCM is vendored from. MVP built and tested; awaiting a bot token and persona sign-off (RobotMoney is the first intended tenant).

### Client-comms surface
**`Sable_Client_Comms`** · 🔵 **ROADMAP** *(stub)*

Placeholder for a future dedicated client-communications surface. Currently a no-op; the live check-in logic lives in SablePlatform's check-in module for now.

---

## Availability at a glance

| Capability | Tool | Status |
|---|---|---|
| Prospect discovery & ranking | Sable_Community_Lead_Identifier | ✅ LIVE |
| Community health diagnostic | Sable_Cult_Grader | ✅ LIVE |
| Content intake & contributor tracking | SableTracking | ✅ LIVE |
| KOL matching + warm-intro flags | SableKOL | ✅ LIVE |
| Content production & optimization | Sable_Slopper | ✅ LIVE |
| Operator reply-assist | Sable_Slopper (mig 056) | ✅ LIVE |
| Discord engagement games | sable-roles | ✅ LIVE |
| Fitcheck Scored Mode | sable-roles | 🔵 ROADMAP (built on branch, not merged) |
| Client portal + operator console | SableWeb | ✅ LIVE |
| Workflow engine + alerts | SablePlatform | ✅ LIVE |
| Alert-Triage API | SablePlatform | ✅ LIVE |
| Shared media storage + delivery | SablePlatform + sable-media-proxy | ✅ LIVE (Slopper clips; Tracking cutover pending) |
| Weekly client check-ins | SablePlatform (checkin) | 🟡 IN-TRIAL (TIG) |
| Autonomous AI community manager (NULO) | SableAutoCM | 🔵 ROADMAP |
| Cross-platform X/TG/Discord relay | SableRelay | 🔵 ROADMAP |
| Project-legibility bot | sable-pulse | 🔵 ROADMAP (built, not launched) |
| Dedicated client-comms surface | Sable_Client_Comms | 🔵 ROADMAP (stub) |

---

## The one-paragraph version (for a deck or a cold email)

> Sable is a managed community-growth operation for crypto projects. We find under-served, well-funded projects, run an AI diagnostic that grades their community's health and pinpoints exactly where it's fragile, then run the fix: a content factory that produces and optimizes short-form video and posts, KOL outreach planning with warm-intro routing, in-Discord engagement games, and full contribution tracking — all visible to the client through a clean health portal and to our operators through a single console, with automated alerts when something needs attention. Coming next: an autonomous AI community manager that handles tier-1 engagement around the clock with human oversight.

---

*Maintained for BD. When a roadmap item ships, move its row to LIVE and update the relevant section. Outbound sales/marketing collateral built from this map (pitch deck, one-pagers, messaging + language guardrails) lives in [`marketing/`](marketing/). Engineering detail for each tool lives in that repo's own `README.md` / `CLAUDE.md`; the backbone's technical docs are in this `docs/` directory (see [CROSS_REPO_INTEGRATION.md](CROSS_REPO_INTEGRATION.md), [ARCHITECTURE.md](ARCHITECTURE.md)).*
