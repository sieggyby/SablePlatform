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

**Bottom line up front:** The seven headline tools are live and sellable today (community diagnostic, prospecting, content tracking, KOL matching, content production, Discord engagement, and the client/operator console), plus the SablePlatform backbone — and a second wave of reply-amplification and proof-of-work capabilities has now shipped on top of it: the **Reply-Opportunity Feed**, **Scale-of-Work-Delivered** reporting, the **Media Rec Center**, **reply campaigns**, the **Trending-Story Autopilot** (deployed, dormant until swept), and the **§10 anti-AI-"tell" humanizer** — all ✅ LIVE. A **third wave shipped 2026-06-25 → 2026-07-02 and is ✅ LIVE: the Content Deck** — a swipe-to-publish content pipeline (meme/tweet/thread producers, nightly ambient restock, keep→schedule→operator hand-off) with a preference-learning layer tuned by operator and community duels (see outcome 12) — plus the **Tweet Assist compose workspace** (topic engine + tweetbank). One capability is in client trial (weekly check-ins). NULO auto-CM has moved from "stubbed" to a **built-but-dormant** product in development (🟡 IN-DEVELOPMENT — present as coming, not running), and the **sable-audit** discovery bot is built and adversarially QA'd (its public report page is already live) but not yet deployed. The remaining roadmap is narrower: cross-platform **relay auto-posting** (OAuth send), the project-legibility bot, and a client-comms surface. The roadmap items are the most exciting *story* but are not yet running — do not let them get pitched as shipped.

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
- Media auto-archived — every screenshot/video uploaded to the shared R2 media layer and linked to its record, served through the signed `media.sable.tools` proxy (the Drive → R2 cutover is done)
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
- **Operator reply-assist** — *suggested* (never auto-posted) replies, with a persistent per-operator daily generation quota *(✅ LIVE, mig 056)*. This is now fed by the **Reply-Opportunity Feed** and the **§10 humanizer** (see outcomes 8 and 11 below) — drafts read as the operator, not an LLM, and a human always sends them. A **reply length/energy slider** lets the operator dial verbosity to fit the moment
- **Tweet Assist compose workspace** *(✅ LIVE, migs 071/072/074)* — reply + compose in one surface: compose-as-persona, an AI **topic-suggestion engine with a feedback loop** (the topics operators pick steer next week's batch), and a per-client **tweetbank** of approved evergreen tweets (human-fed plus AI-suggested with human approval)
- **The Content Deck** *(✅ LIVE — see outcome 12)* — Slopper is the producer side: meme/tweet/thread generation, nightly ambient restock, and hard-negatives steering

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
- **`/duel` + `/tasteboard`** *(live on SolStitch since 2026-07-02)* — a community pairwise content game: members vote head-to-head on content candidates, and their votes feed the Content Deck's preference layer (quarantined from operator signal until validated — see outcome 12)

**Merged and shipped DISABLED (in the deployed image, not enabled in any guild):**
- **Scored Mode** — grades posted fits on a rubric and reveals scores once a post earns enough reactions (QA-approved, merged, ships off by default — enabling it is a per-guild operator decision)
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
- Content pipeline + **Content Deck**, content-quality and media-health dashboards, action queue, reply-assist / Tweet Assist, KOL network/wizard, per-human cost attribution (`/ops/reply-cost`)
- Morning brief, ops alerts, source freshness

**Public, no-login surfaces (lead-gen):**
- **Case-study microsites** (`/proof/*`, `/synq`) — animated client success stories you can send straight to a prospect
- A self-serve **prospect intake form** (`/intake`) that drops inbound leads into the operator pipeline
- A public **community-audit report page** (`/audit/[guild]`) — the web half of the sable-audit wedge, live ahead of the bot itself

> **BD takeaway:** Clients get a clean, professional health portal scoped to *only their data*. They **never** see fit scores, verdicts, costs, or pipeline data — that wall is enforced in code (separate data-assembly paths, verdict sanitization, fail-closed guards). Safe to demo.

---

### 8. Put the right reply targets in front of operators — automatically
**`SableRelay` reply-opportunity feed + `Sable_Slopper` reply-assist** · ✅ **LIVE** · *on SableWeb `/ops/reply-assist`*

A standing, auto-sourced queue of *which tweets to reply to right now*, surfaced to operators with one-click drafting. This is distinct from the still-roadmap SableRelay DM/quorum coordination flow — the **feed itself is shipped**.

- **Auto-sourced reply targets** via a sweep that stamps opportunities into the feed (the **mention lane is live in prod**; topic-keyword and VIP-author lanes are built but gated on SocialData budget)
- **`Sweep-now`** — an operator-triggered reply sweep, governed by a per-operator daily budget
- **Per-operator re-rank**, **learning thumbs** (👍/👎 that train the ranker), and **dismiss / snooze** so each operator's feed stays personal and uncluttered
- One-click **"Draft reply"** straight into reply-assist; an `already-replied` depress signal keeps the team off conversations someone already handled — with **no per-candidate paid lookup**
- v1 safety boundary holds: **replies are suggested, a human always sends them** — the bot never auto-posts on anyone's behalf

> **BD takeaway:** This is the engine behind "measured amplification." Operators don't hunt for tweets — the feed hands them the right targets, they draft and send, and every send becomes a measured proof item (outcome 9). Pitch it as *reply amplification*, never as a guarantee of reach.

---

### 9. Prove the scale of the work we deliver
**`SablePlatform` work-tracking + reply-outcome capture** · ✅ **LIVE** · *deployed; written/read from SableWeb `/ops`*

A first-class record of operator work — clock-in coverage, logged actions, and posted replies — that rolls up into both an internal report and a sanitized client-facing card. A key BD differentiator: it turns "trust us, we're active" into a defensible packet.

- **Mod-slot clock-in** + a free-form **"Log Work"** entry for the work that isn't a reply or a clip
- **"Mark posted"** reply-outcome capture — when an operator posts an assisted reply, it's recorded, which is what makes *replies-delivered* a **real, measured count** (sourced from `reply_outcomes`); a scheduled **auto-detector** now catches posted replies even when nobody clicks Mark-posted (mig 069 stamps operator-vs-auto provenance)
- An internal **"Scale of Work Delivered"** report (per-operator breakdown) and a **sanitized client-facing "Work Delivered" card** that shows totals only — never operator names, raw logs, or costs (the client/operator wall is enforced in code)

> **BD takeaway:** This is proof-of-work, and the honesty discipline is load-bearing. **Replies-delivered is *measured*.** **Coverage-hours and communities-covered are *self-reported / operator-declared* — interpretive context, not hard metrics.** Always present them with that caveat. This is *not* operator surveillance and must never be pitched that way. See [`marketing/MESSAGING.md`](marketing/MESSAGING.md) for the exact approved wording.

---

### 10. Surface the right media for every reply — no LLM, instantly
**`SablePlatform` Media Rec Center** · ✅ **LIVE** · *mig 066, on SableWeb `/ops`*

A ranked media-library matcher that suggests the right image/clip to attach to a reply, plus a health surface for the media library itself.

- **No-LLM ranked matching** — deterministic, fast, and cheap; recommends the best media asset from the client's library for the reply being drafted
- A **`/ops/media-health`** surface to keep the media library curated and the recommendations sharp
- Plugs straight into reply-assist so a drafted reply arrives with the right visual already suggested

> **BD takeaway:** Media is the single biggest performance lever on crypto-Twitter — this makes "attach a relevant image" the default, not an afterthought, without spending an LLM call per reply.

---

### 11. Coordinate team reply pushes and ride breaking stories
**`SablePlatform` reply campaigns + Trending-Story Autopilot + §10 humanizer** · *mixed status — read each label*

Three capabilities that make reply amplification coordinated, timely, and indistinguishable from organic.

- **Reply campaigns / flash-mob** *(✅ LIVE, mig 061)* — coordinated team reply pushes against a shared objective, with a **performance panel** that tracks assignments, posted count, post rate, and matured engagement (with an explicit caveat when no outcomes have matured yet)
- **Trending-Story Autopilot** *(✅ DEPLOYED, mig 064 — dormant until manually swept)* — auto-detects breaking story topics and stands up monitoring, so the team can pile onto a live narrative; **deployed but inert until an operator triggers a sweep**, so pitch it as "available, operator-initiated," not "always-on autonomous"
- **§10 anti-AI-"tell" humanizer** *(✅ LIVE)* — drafts read as the operator, not an LLM; ships with an **advisory tell-score lint** (flags AI-tell phrasing for the operator, never auto-rewrites) and **assisted-vs-organic reply measurement** so we can honestly separate Sable-assisted replies from a client's own

> **BD takeaway:** Campaigns + the autopilot are how we concentrate force on the moments that matter; the humanizer is why the output doesn't read as botted. None of it changes the v1 rule — **a human always sends the reply.**

---

### 12. Keep the content pipeline stocked — and learning the client's taste
**The Content Deck** (`Sable_Slopper` producers + `SablePlatform` migs 076–080 + SableWeb `/ops/content-deck`) · ✅ **LIVE** · *shipped 2026-06-25 → 2026-07-02*

A swipe-to-publish pipeline that keeps a standing deck of ready-to-post content stocked — and gets sharper with every decision made on it.

- Generated **content candidates land in a swipe deck**; operators keep / reject / skip. Kept items flow **keep → schedule → release → operator hand-off** — composed and handed to a human to post, **never auto-posted**
- **Producers live today:** memes (remixing the client's own corpus, per-operator weekly dollar budget), single tweets, and threads — generated on demand from the console
- **Ambient generation** — a nightly producer restocks the deck automatically within a per-org daily dollar cap *(live for TIG; per-org opt-in)*
- **A preference-learning layer** — pairwise A/B duels + a content-quality Elo tune generation toward what actually gets kept, with a `/ops/content-quality` dashboard, a weekly content digest, preference-ranked deck ordering, and a **hard-negatives loop** so rejected patterns stop reappearing
- **The community joins the taste loop** — the sable-roles `/duel` game (outcome 6) lets community members vote head-to-head; their votes are quarantined from operator signal until validated
- Posted deck content is **auto-detected and outcome-snapshotted** with the same fixed-age discipline as replies

> **BD takeaway:** this turns the content factory into an always-stocked, self-improving pipeline instead of a per-request service — and the client's own community helps tune it. The boundary stands: a human posts everything.

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
- **Shared media layer** (✅ LIVE) — R2 storage + an HMAC-signed, expiring-URL proxy (the `sable-media-proxy` Cloudflare Worker, live at the branded **`media.sable.tools`** domain), with content-addressed dedup and per-client bucket isolation. Powers Slopper clips, SableTracking media (the Drive → R2 cutover is done), and Content Deck media
- **Shared SocialData cache** (✅ LIVE, mig 082) — one cross-system tweet cache shared by the reply stack and the community diagnostic, so the same paid data is never bought twice
- **Adapters** — clean integration so each specialized tool stays independent

> **BD takeaway:** When you pitch "an integrated growth operation," *this* is what makes it integrated. The data flows between tools automatically.

---

## In development & on the roadmap (do **not** pitch as available)

These are the most exciting parts of the story and the most likely to be oversold. The reply-opportunity feed, campaigns, trending autopilot, and the work-tracking/proof layer that *used* to live in this section have now shipped — see outcomes 8–11 above. What remains here is genuinely **not yet running for a client.**

### Autonomous AI community manager — "NULO"
**`SableAutoCM`** · 🟡 **IN-DEVELOPMENT** *(built end-to-end, ships dormant — no client live yet)*

The vision: Sable runs a persona-engineered AI community manager inside a client's Telegram (v1), with invisible human-in-the-loop oversight.

- Autonomously answers tier-1 community questions in a tuned, on-brand voice — but only after each category earns autonomy through a human-approval-rate gate (it starts 100% human-reviewed)
- A per-client knowledge base with **tiered hallucination prevention** — high-stakes facts (contract addresses, official links) are slot-filled from a registry, never LLM-generated
- Silently escalates ambiguous or sensitive messages to a Sable operator (and major incidents to the founder + on-call)
- A weekly digest headlining **"time saved"** and **"community-health delta"**
- Expands its autonomy category-by-category as approval rates prove out

*Status: the full pipeline is built and tested — classifier, KB, drafter, safety/autonomy gate, escalation, weekly digest, adversarial harness, **and the live publish step** (it enqueues to the now-built SableRelay outbox; it is no longer stubbed). RobotMoney is onboarded as a **dormant** tenant (paused), pending a voice-viability spike pass and operator sign-off on real outputs. For BD: present NULO as **coming / in development**, never as a running service — but the old "it's only a stub" framing is out of date.*

### Self-serve community audit — the discovery wedge
**`sable-audit`** · 🟡 **BUILT, NOT DEPLOYED** *(the public report page is already ✅ LIVE on the portal)*

A Sable-branded, self-invite Discord bot: a server owner invites it → free $0 metadata audit → consent-gated deep audit with findings, security checks, and a contributor leaderboard — each weak finding maps to a Sable service. The bot is built and adversarially QA'd (backing tables live, migs 067/070); what remains is registering the Discord app and deploying it resident. Pitch as **coming** ("a free self-serve audit") — the operator-run diagnostic (outcome 2) is the live equivalent today.

### Cross-platform relay — auto-posting
**`SableRelay`** · ✅ **substrate + feed BUILT** · 🔵 **OAuth auto-posting ROADMAP**

The multi-tenant X ↔ Telegram ↔ Discord substrate. Most of it is **built and tested** (substrate, the publish-exactly-once feed, SocialData ingestion, the operator flows including the reply-opportunity feed above, and the `relay` CLI). The reason it's still in this section is the part a BD person is most tempted to oversell:

- ✅ **Built:** transport/dedupe substrate, the publish-exactly-once outbox, the **Reply-Opportunity Feed** (outcome 8, live), SocialData ingestion, operator amplify/quorum/flag-reply flows, the CLI
- 🔵 **Roadmap:** **OAuth auto-posting** — direct send on a member's behalf (Phase 7, explicitly out of v1) and the higher-tier coordination UX

*Status: the substrate and feed are real and tested; the v1 safety boundary stands — **replies are suggested, never auto-posted.** Auto-mirror cross-posting and the peer-review quorum cross-post are built on the outbox but not yet a running client service. Do not pitch "we auto-post for you" — that is the one piece still on the roadmap.*

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
| Reply-Opportunity Feed (auto-sourced targets + sweep) | SableRelay + SableWeb (mig 062) | ✅ LIVE (mention lane in prod; topic/VIP lanes budget-gated) |
| §10 anti-AI-"tell" humanizer + assisted-vs-organic measurement | Sable_Slopper (mig 063) | ✅ LIVE |
| Media Rec Center (no-LLM media matching + media-health) | SablePlatform (mig 066) | ✅ LIVE |
| Reply campaigns / flash-mob | SablePlatform (mig 061) | ✅ LIVE |
| Trending-Story Autopilot | SablePlatform (mig 064) | ✅ DEPLOYED (dormant until swept) |
| Content Deck (swipe → schedule → hand-off; meme/tweet/thread producers) | Sable_Slopper + SablePlatform (migs 076–079) + SableWeb | ✅ LIVE |
| Ambient deck generation (nightly restock, budget-capped) | Sable_Slopper | ✅ LIVE (TIG; per-org opt-in) |
| Content-preference Elo + pairwise duels + content-quality dashboard | SablePlatform (mig 080) + SableWeb | ✅ LIVE |
| Community `/duel` content game | sable-roles | ✅ LIVE (SolStitch) |
| Tweet Assist compose + topic engine + tweetbank | Sable_Slopper + SablePlatform (migs 071/072/074) | ✅ LIVE |
| Scale-of-Work-Delivered + reply-outcome capture | SablePlatform (mig 059) + SableWeb | ✅ LIVE (replies *measured*; coverage hours/communities *self-reported*) |
| Discord engagement games | sable-roles | ✅ LIVE |
| Fitcheck Scored Mode | sable-roles | 🟡 BUILT (merged, ships disabled — not enabled in any guild) |
| Client portal + operator console | SableWeb | ✅ LIVE |
| Workflow engine + alerts | SablePlatform | ✅ LIVE |
| Alert-Triage API | SablePlatform | ✅ LIVE |
| Shared media storage + delivery | SablePlatform + sable-media-proxy | ✅ LIVE (clips + Tracking + deck media; `media.sable.tools`) |
| Shared SocialData cache (cross-tool, anti-double-spend) | SablePlatform (mig 082) | ✅ LIVE |
| Self-serve community-audit bot | sable-audit (migs 067/070) | 🟡 BUILT (report page LIVE; bot not deployed) |
| Weekly client check-ins | SablePlatform (checkin) | 🟡 IN-TRIAL (TIG) |
| Autonomous AI community manager (NULO) | SableAutoCM (mig 058) | 🟡 IN-DEVELOPMENT (built end-to-end, ships dormant) |
| Relay substrate + publish-exactly-once feed | SableRelay (mig 057/062/064/065) | ✅ BUILT (tested) |
| Relay OAuth auto-posting (direct send) | SableRelay | 🔵 ROADMAP (Phase 7, out of v1) |
| Project-legibility bot | sable-pulse | 🔵 ROADMAP (built, not launched) |
| Dedicated client-comms surface | Sable_Client_Comms | 🔵 ROADMAP (stub) |

---

## The one-paragraph version (for a deck or a cold email)

> Sable is a managed community-growth operation for crypto projects. We find under-served, well-funded projects, run an AI diagnostic that grades their community's health and pinpoints exactly where it's fragile, then run the fix: a content factory that produces and optimizes short-form video, memes, tweets, and threads — with a standing content deck that restocks itself nightly and learns the team's taste from every keep/reject — a reply-amplification engine that puts the right reply targets in front of operators and drafts human-sent replies that read as the operator (never a bot), KOL outreach planning with warm-intro routing, in-Discord engagement games, and full contribution tracking. We then *prove* it — every assisted reply an operator posts is a measured proof item, rolled into a sanitized "work delivered" record for the client. All of it is visible to the client through a clean health portal and to our operators through a single console, with automated alerts when something needs attention. Coming next: an autonomous AI community manager ("NULO") that handles tier-1 engagement with human oversight — built and in development, not yet live.

---

*Maintained for BD. Refreshed 2026-07-05 (Content Deck wave, Tweet Assist compose, media domain, Tracking→R2, shared cache, sable-audit status). When a roadmap item ships, move its row to LIVE and update the relevant section. Outbound sales/marketing collateral built from this map (pitch deck, one-pagers, messaging + language guardrails) lives in [`marketing/`](marketing/). Engineering detail for each tool lives in that repo's own `README.md` / `CLAUDE.md`; the backbone's technical docs are in this `docs/` directory (see [CROSS_REPO_INTEGRATION.md](CROSS_REPO_INTEGRATION.md), [ARCHITECTURE.md](ARCHITECTURE.md)).*
