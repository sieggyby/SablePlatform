# Sable — Service-Line One-Pagers

**Audience:** A prospect who asked about one specific thing, or a BD person sending a focused follow-up. Each section below is a standalone leave-behind for one service line.

**Rules of the road:** Every claim is status-labeled. We only present LIVE things as live. Language is governed by [`MESSAGING.md`](MESSAGING.md) — read it before editing. Each one-pager states **what's measured vs. what's interpretive**, on purpose: that distinction is the trust-builder, not fine print.

> **Status legend:** ✅ LIVE · ✅ DEPLOYED (dormant, operator-initiated) · 🟡 IN-TRIAL · 🟡 IN-DEVELOPMENT · 🔵 ROADMAP.

---

## 1. Content Engine

**Status:** ✅ **LIVE** · `Sable_Slopper` (internal operator engine) + output surfaced in `SableWeb`

**Who it's for:** A project whose accounts are quiet, off-voice, or posting on instinct instead of evidence.

**The problem:** You need a steady stream of on-brand short-form content, but you don't have a content team — and even if you did, they'd be guessing at what works.

**What we deliver:**
- Short-form video clipping from any YouTube URL or file — auto-transcribe, pick the best moments, captions + overlay — for TikTok / Reels / Shorts.
- Memes, scene compositing, face-swap, and character-explainer videos with TTS.
- Account voice management, tweet/thread drafting, and hook scoring.
- An **automated weekly cycle**: track performance → scan live niche trends → advise what to make (and stop making) → generate a posting calendar aligned to inventory.

**Measured vs. interpretive:**
- *Measured:* content items produced and logged; performance pulled live for posted content (format lift, attribution).
- *Interpretive:* trend forecasts and "what to make next" recommendations are data-informed advice, not guarantees.

**The boundary:** this is an **internal** engine. The client gets the *output* (content on their accounts) and results through the portal — never the raw pipeline.

---

## 2. Reply Amplification — the Reply-Opportunity Feed

**Status:** ✅ **LIVE** · `SableRelay` feed + `Sable_Slopper` reply-assist, on `SableWeb /ops/reply-assist`

**Who it's for:** A project whose founders/team can't be everywhere, and whose community needs to show up in the *right* conversations — without sounding like bots.

**The problem:** The conversations that matter move fast, and finding them manually is a full-time job. The "easy" fix — reply bots — reads as spam and hurts the brand.

**What we deliver:**
- A standing, **auto-sourced feed** of which tweets to reply to now (mention lane live in production; topic-keyword and VIP-author lanes built, enabled as SocialData budget allows).
- **Per-operator ranking, learning thumbs, dismiss/snooze**, and an `already-replied` depress signal so the team never doubles up.
- One-click **draft**, passed through the **§10 humanizer** so replies read as the operator, not an LLM (advisory AI-tell lint; assisted-vs-organic measurement).
- **Reply campaigns / flash-mob** (✅ LIVE) for coordinated team pushes with a performance panel.
- **Trending-Story Autopilot** (✅ DEPLOYED, operator-initiated) to ride breaking narratives.

**Measured vs. interpretive:**
- *Measured:* replies delivered (captured on post) and fixed-age (e.g. 24h) engagement on them.
- *Interpretive / not promised:* reach and impressions — directional context only, never a guarantee.

**The boundary:** **replies are suggested and human-sent.** Sable never auto-posts on your behalf (OAuth auto-posting is 🔵 roadmap). A person always reviews and sends — which is exactly why the output doesn't read as botted.

---

## 3. Community Intelligence — health grading & tracking

**Status:** ✅ **LIVE** · `Sable_Cult_Grader` (diagnostic) + `SableTracking` (intake)

**Who it's for:** Any project — including one you haven't signed. The diagnostic runs from a Twitter handle alone.

**The problem:** "How healthy is our community, really?" usually gets answered with vanity metrics. Founders can't see fragility — that one or two voices carry the whole room — until it breaks.

**What we deliver:**
- An **A–F community-health report** (Twitter + Discord), each result with a confidence band tied to data completeness.
- A **structural-fragility score** (single-point-of-failure risk) and a follower → mentioner → recurring → quality-reply → **"cultist"** conversion funnel.
- A weekly **Community Health Index (0–100)** tracked over time, plus **member-level decay tracking** (who's fading, and how) once there's history.
- Full **contribution tracking** via `SableTracking`: every link/screenshot/video an operator drops is classified, deduped, logged, and rolled into a weekly Smart Brief.

**Measured vs. interpretive:**
- *Measured:* the underlying activity, contribution counts, and funnel transitions — computed on real collected data.
- *Interpretive:* the A–F grades and the health index are scored *models* (hence the confidence bands) — strong signal, not an absolute verdict. A cold audit is a snapshot; longitudinal value builds over weekly runs.

**The wedge:** run it on a prospect before they sign. The gaps it surfaces are the pitch.

---

## 4. Scale-of-Work Proof

**Status:** ✅ **LIVE** · `SablePlatform` work-tracking + reply-outcome capture, on `SableWeb /ops` → sanitized client card

**Who it's for:** Any client who (rightly) wants to know what they're paying for — and any prospect tired of agencies that can't prove their work.

**The problem:** Community work is invisible. "We were active this week" isn't a deliverable. Most agencies fill the gap with impression numbers nobody trusts.

**What we deliver:**
- **"Mark posted"** reply-outcome capture, so every assisted reply an operator posts becomes a **measured count**.
- **Mod-slot clock-in** and free-form **"Log Work"** for the work that isn't a reply or a clip.
- An internal **"Scale of Work Delivered"** report and a sanitized client-facing **"Work Delivered"** card — totals only, never operator names, raw logs, or costs.

**Measured vs. interpretive — read this carefully:**
- *Measured:* **replies delivered** (a real, captured count) and content items logged.
- *Self-reported / interpretive:* **coverage hours and communities covered** — operator-declared. They describe the *scope of the watch*, not a measured metric, and we always say so.

**What this is not:** it is *not* operator surveillance, and must never be pitched as productivity-monitoring. It's a record of the scale of work delivered, declared by operators, sanitized for the client. (See [`MESSAGING.md`](MESSAGING.md) §3.)

---

## 5. NULO — Autonomous Community Manager *(in development)*

**Status:** 🟡 **IN-DEVELOPMENT** · `SableAutoCM` — built end-to-end, ships **dormant**, not yet live for any client

**Who it's for:** A project drowning in repetitive tier-1 Telegram questions ("where do I buy," "what's the contract," "wen") that burn the team's time.

**The problem:** The same questions, all day, every day — and the off-the-shelf answer (a generic chatbot) hallucinates contract addresses and tanks trust the first time it's wrong.

**What we're building:**
- A persona-engineered AI CM inside your Telegram that answers tier-1 questions in a tuned, on-brand voice (a calm default register and a reactive one for charged moments).
- **Earned autonomy:** starts **100% human-reviewed**; a category flips to autonomous only after it clears a strict approval-rate gate, and auto-demotes if quality regresses.
- **Tiered hallucination prevention:** high-stakes facts (contract addresses, official links) are **slot-filled from a registry, never LLM-generated**.
- **Flags, doesn't moderate** — surfaces scams/impersonation/spam to a private operator channel; never bans, mutes, or kicks.
- A weekly digest headlining *time saved* and *community-health delta*.

**Measured vs. interpretive:** N/A yet — nothing is live, so there are no client metrics to report. Do not imply otherwise.

**Honest status:** the full pipeline is built and tested (including the live publish step), and one tenant is onboarded **paused**, pending a voice-viability pass and operator sign-off. Present this as **coming**, never as a running service. (See [`MESSAGING.md`](MESSAGING.md) §6.)

---

*Built from [`../SUITE_CAPABILITIES.md`](../SUITE_CAPABILITIES.md). Status-checked against that map; language governed by [`MESSAGING.md`](MESSAGING.md). When a capability changes status, update its one-pager here.*
