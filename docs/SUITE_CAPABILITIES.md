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

**Bottom line up front:** 7 of the suite's tools are live and sellable today, 1 is in client trial, and 3 are roadmap. The roadmap items (an autonomous AI community manager and a cross-platform relay) are the most exciting *story*, but they are not yet running — do not let them get pitched as shipped.

---

## What we can do for a client — by outcome

### 1. Find the right clients before they know they need us
**`Sable_Community_Lead_Identifier`** · ✅ **LIVE** · *internal BD tool*

An automated top-of-funnel prospecting pipeline. It scans for recently-funded crypto projects whose communities are weak — Sable's ideal-client profile — and hands BD a ranked, tiered shortlist.

- Ranked **Tier 1 / 2 / 3** shortlist of fundraising projects, scored on budget, credibility, **community gap**, TGE proximity, and thesis fit
- "Community gap" is the headline signal: well-funded projects with thin communities = the addressable market
- Free first-pass enrichment (GitHub activity, Discord/Telegram size, hiring signals) **before** any paid spend
- Optional paid Twitter deep-dive on the top candidates for engagement and mention quality
- Per-lead **BD pitch bullets**, contact enrichment, and one-page prospect snapshots
- Inbound detection: scans job boards for projects actively hiring community managers
- Manual pin / suppress / boost so BD judgment stays in the loop

> **BD takeaway:** This is your prospect engine. It tells you *who* to call and *why they're a fit* — with the pitch half-written.

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
- Media auto-archived (shared media layer)
- Human **review queue** for ambiguous items + a deep operator command set
- Feeds the rest of the suite — contributors and content flow into the shared platform database

> **BD takeaway:** This is how we *prove* the work. Every contribution is logged, so the weekly client report is backed by real numbers, not vibes.

---

### 4. Reach the right influencers — with warm intros
**`SableKOL`** · ✅ **LIVE** · *operator tool in the web console*

A KOL (key-opinion-leader) discovery and matching engine. Ranks crypto-Twitter influencers by fit for a project and flags where Sable already has a relationship.

- A ranked, **fit-scored KOL shortlist** for any project or Twitter handle, with AI rationales
- A **"Sable already knows this person"** flag — surfaces warm connections from existing client communities
- **Any-project wizard:** paste a handle, get a follow-graph survey, comparable-project suggestions, and a tiered outreach plan
- Per-candidate enrichment: real tweets read against an operator's persona → likes/dislikes/mutuals/common-ground for **cold-intro notes**
- Follow-graph clustering to find audience overlap
- Strict cost discipline — every paid call is logged

> **BD takeaway:** We don't just list influencers — we tell you which ones we can already get a warm intro to, and draft the opener.

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
**`sable-roles`** · ✅ **LIVE** · *running in the SolStitch Discord*

Sable's dedicated Discord bot for community-role automation and engagement mechanics.

- **"Fitcheck"** engagement game: streak tracking, image-only channel enforcement, auto-threads, reaction scoring, leaderboard
- **"Burn-me"** and **"roast"** mechanics (opt-in AI roasts, peer-roast token economy)
- **"Airlock"** new-member verification (invite-source-aware admit / hold / triage)
- **Scored Mode** — optionally grades posted fits on a rubric and reveals scores when a post earns enough reactions *(✅ LIVE, ships off by default, mod-enabled per server)*
- An ops **"state pin"** dashboard showing live bot config in a pinned message

> **BD takeaway:** Daily, sticky, in-server engagement — not just analytics from the outside. Lives where the community already is.

---

### 7. Give the client (and the operator) a single console
**`SableWeb`** · ✅ **LIVE** · *the human face of the whole suite*

The Sable Portal — a production web app with a strict wall between what clients see and what operators see.

**The client sees (`/client`, read-only, Google login, scoped to their org only):**
- Community health and grade history
- Recommended actions
- Content performance and engagement trends

**The operator sees (`/ops`, everything):**
- Prospect fit scores and Sable verdicts, prospect/client triage
- Cost dashboard, audit trail
- Entity intelligence + relationship graph, centrality, playbook effectiveness
- Content pipeline, action queue, reply-assist, KOL network/wizard
- Morning brief, ops alerts, source freshness

> **BD takeaway:** Clients get a clean, professional health portal scoped to *only their data*. They **never** see fit scores, verdicts, costs, or pipeline data — that wall is enforced in code. Safe to demo.

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
- **Shared media layer** (✅ LIVE) — one place to store and serve client media (clips, screenshots) over secure, expiring links, via the **`sable-media-proxy`** Cloudflare service
- **Adapters** — clean integration so each specialized tool stays independent

> **BD takeaway:** When you pitch "an integrated growth operation," *this* is what makes it integrated. The data flows between tools automatically.

---

## On the roadmap (do **not** pitch as available)

These are the most exciting parts of the story and the most likely to be oversold. They are **specified or scaffolded, not running.**

### Autonomous AI community manager — "NULO"
**`SableAutoCM`** · 🔵 **ROADMAP**

The vision: Sable runs a persona-engineered AI community manager inside a client's chat, with invisible human-in-the-loop oversight.

- Autonomously answers tier-1 community questions in a tuned, on-brand voice
- Silently escalates ambiguous or sensitive messages to a Sable operator (and major incidents to the founder)
- A weekly digest headlining **"time saved"** and **"community-health delta"**
- Expands its autonomy category-by-category as approval rates prove out

*Status: scaffolded in-platform; most of the reply pipeline is still stubbed. Gated on a voice-viability spike and SableRelay shipping first.*

### Cross-platform relay
**`SableRelay`** · 🔵 **ROADMAP**

The substrate AutoCM sits on: a multi-tenant bot bridging X, Telegram, and Discord.

- Auto-mirror a project's X feed into Discord and Telegram
- Team members submit a tweet from Telegram → it cross-posts everywhere
- Reply-opportunity coordination: flag a tweet, DM opted-in team members a one-tap compose link *(replies are suggested, never auto-posted in v1)*

*Status: detailed spec + partial substrate built; no standalone running service yet.*

### Project-legibility bot
**`sable-pulse`** · 🔵 **ROADMAP** *(built, not launched)*

A deterministic Telegram bot that makes a project's universe legible to newcomers — committee-call readouts, token reviews in the project's own personas' voices, and a plain-language "what the devs are working on" digest. MVP is built and tested; awaiting a bot token and go-ahead (RobotMoney is the first intended tenant).

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
| Fitcheck Scored Mode | sable-roles | ✅ LIVE (off by default) |
| Client portal + operator console | SableWeb | ✅ LIVE |
| Workflow engine + alerts | SablePlatform | ✅ LIVE |
| Alert-Triage API | SablePlatform | ✅ LIVE |
| Shared media storage + delivery | SablePlatform + sable-media-proxy | ✅ LIVE |
| Weekly client check-ins | SablePlatform (checkin) | 🟡 IN-TRIAL (TIG) |
| Autonomous AI community manager (NULO) | SableAutoCM | 🔵 ROADMAP |
| Cross-platform X/TG/Discord relay | SableRelay | 🔵 ROADMAP |
| Project-legibility bot | sable-pulse | 🔵 ROADMAP (built, not launched) |
| Dedicated client-comms surface | Sable_Client_Comms | 🔵 ROADMAP (stub) |

---

## The one-paragraph version (for a deck or a cold email)

> Sable is a managed community-growth operation for crypto projects. We find under-served, well-funded projects, run an AI diagnostic that grades their community's health and pinpoints exactly where it's fragile, then run the fix: a content factory that produces and optimizes short-form video and posts, KOL outreach with warm-intro routing, in-Discord engagement games, and full contribution tracking — all visible to the client through a clean health portal and to our operators through a single console, with automated alerts when something needs attention. Coming next: an autonomous AI community manager that handles tier-1 engagement around the clock with human oversight.

---

*Maintained for BD. When a roadmap item ships, move its row to LIVE and update the relevant section. Engineering detail for each tool lives in that repo's own `README.md` / `CLAUDE.md`; the backbone's technical docs are in this `docs/` directory (see [CROSS_REPO_INTEGRATION.md](CROSS_REPO_INTEGRATION.md), [ARCHITECTURE.md](ARCHITECTURE.md)).*
