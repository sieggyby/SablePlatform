# Sable — Outbound Pitch Deck

**Audience:** A prospect — a crypto project (typically well-funded, recently raised, with a thinner community than its raise deserves).

**How to use this:** Each `##` section is a slide. Speaker notes are in *italics*. This is the long-form version; cut to 6–8 slides for a cold call, keep all of it for a full deck. **Everything claimed here is ✅ LIVE unless the slide says otherwise.** Before editing, read [`MESSAGING.md`](MESSAGING.md) — the guardrails there override any slide copy.

> **Status legend:** ✅ LIVE · ✅ DEPLOYED (dormant, operator-initiated) · 🟡 IN-TRIAL · 🟡 IN-DEVELOPMENT · 🔵 ROADMAP. We only claim LIVE things as live.

---

## Slide 1 — The problem

**You raised. Your community didn't keep up.**

Well-funded crypto projects routinely have communities that are thinner, quieter, and more fragile than their raise — or their roadmap — demands. A few loud voices carry the whole server. Engagement is a vibe, not a number. And the standard "fix" is buying impressions or spinning up reply bots that everyone can smell.

*Speaker note: name their specific gap if you ran the diagnostic first — that's the wedge (slide 4).*

---

## Slide 2 — Why the usual fixes fail

- **Impression farming** — buys reach you can't trust and a community that evaporates the day you stop paying.
- **Reply bots** — read as bots, get muted, and damage the brand they're meant to grow.
- **A generic CM agency** — posts on a calendar, can't tell you *which* conversations matter, and can't prove what the work actually produced.

**The gap:** nobody is doing *measured, human-quality* community work and then *proving* it honestly.

---

## Slide 3 — What Sable is

**A managed community-growth operation, backed by a proprietary tooling suite.**

We don't sell software. We run the growth operation — and the tools are why we can do it at quality and at scale, with proof. One integrated stack, not seven disconnected scripts: a contributor we track shows up in your health report, your relationship graph, and your weekly proof packet automatically.

*Speaker note: this is a service sale. The suite is the moat, not the product.*

---

## Slide 4 — The wedge: a free-to-run health diagnostic

**We can grade your community before you sign anything.**

`Sable_Cult_Grader` · ✅ **LIVE** (our most heavily-tested tool — 1,680+ tests)

From just a Twitter handle, we produce:
- An **A–F community-health report** (Twitter + Discord), each result carrying a confidence band tied to data completeness.
- A **structural-fragility score** — flags when the community depends on one or two voices (single-point-of-failure risk).
- A follower → mentioner → recurring → quality-reply → **"cultist"** conversion funnel.

*Speaker note: run it on the prospect first. Share the findings. The gaps sell the engagement. A cold audit is a snapshot — the longitudinal value (health-index trends, member decay) builds over weekly runs.*

---

## Slide 5 — The live suite (the engine room)

**Four things run today, end to end:**

1. **Content engine** (`Sable_Slopper`, ✅ LIVE) — short-form video clipping from any YouTube URL, memes, account voice, hook scoring, and an automated weekly cycle: track performance → scan trends → advise → generate a posting calendar. Internal engine; the client sees the output, never the pipeline.
2. **Reply amplification** (Reply-Opportunity Feed + reply-assist, ✅ LIVE) — see slide 6.
3. **Community intelligence** (`Sable_Cult_Grader` + `SableTracking`, ✅ LIVE) — the diagnostic plus full contribution tracking; every link/screenshot/video an operator drops is classified, deduped, and logged.
4. **Scale-of-work proof** (✅ LIVE) — see slide 7.

Plus: **KOL matching with warm-intro routing** (`SableKOL`, ✅ LIVE, ~16,600 ground-truth-verified crypto-KOLs) and **in-Discord engagement games** (`sable-roles`, ✅ LIVE, running in a client server today).

---

## Slide 6 — Reply amplification, done right

**We put the right reply targets in front of operators — and the replies read as people, not bots.**

✅ **LIVE** on the operator console.

- A standing, **auto-sourced feed** of which tweets to reply to now (mention lane live in production; topic and VIP-author lanes built, enabled as budget allows).
- **Per-operator ranking, learning thumbs, dismiss/snooze** — each operator's feed stays sharp.
- One-click **draft**, then a **§10 humanizer** so the reply reads as the operator, not an LLM — with an advisory "AI-tell" lint and assisted-vs-organic measurement.
- **Reply campaigns / flash-mob** (✅ LIVE) for coordinated team pushes against a shared objective, with a performance panel.
- **Trending-Story Autopilot** (✅ DEPLOYED, operator-initiated) to ride a breaking narrative.

**The boundary that makes this credible:** replies are **suggested and human-sent**. We never auto-post on your behalf. That's not a limitation — it's why the output doesn't read as botted.

*Speaker note: do NOT say "auto-reply" or "auto-post." See MESSAGING.md §5.*

---

## Slide 7 — We prove the work (the differentiator)

**Most agencies say "trust us, we're active." We hand you a measured record.**

✅ **LIVE.**

- Every assisted reply an operator posts is captured ("Mark posted") and becomes a **measured count** of replies delivered.
- It rolls into a sanitized, client-facing **"Work Delivered"** card — totals only, never operator names, raw logs, or costs.

**The honesty that earns trust:**
- **Replies delivered is *measured*** — a real count.
- **Coverage hours and communities covered are *operator-declared*** — the scope of the watch, not a measured metric, and never presented as one. (This is not surveillance.)

*Speaker note: this honesty is the sale. Competitors can't show measured proof; the ones who throw out impression numbers are bluffing — and the prospect usually knows it.*

---

## Slide 8 — One console, a hard client/operator wall

**The client sees a clean health portal. We see the whole machine.**

`SableWeb` · ✅ **LIVE**

- **Client view** (read-mostly, scoped to *only their org*): health + grade history, recommended actions they can adopt/skip, content and engagement trends, the Work Delivered card, a one-click QBR export.
- **Operator view:** the full pipeline — fit scores, costs, the relationship graph, the reply feed, KOL network.
- The wall is **enforced in code** — clients never see fit scores, verdicts, costs, or pipeline data. Safe to demo.

Underneath, `SablePlatform` is the backbone: one shared database, a workflow engine, and **12 automated alert checks** so nothing important goes unnoticed.

---

## Slide 9 — What's in development (coming, not live)

**NULO — an AI community manager**

`SableAutoCM` · 🟡 **IN-DEVELOPMENT** (built end-to-end, ships dormant — not yet live for any client)

The vision: a persona-engineered AI CM inside your Telegram that answers tier-1 questions in a tuned, on-brand voice, with invisible human-in-the-loop oversight.

- Starts **100% human-reviewed**; earns autonomy category-by-category only as approval rates prove out.
- **Never invents high-stakes facts** — contract addresses and official links are slot-filled from a registry, never generated.
- **Flags, doesn't moderate** — surfaces scams/impersonation to a private operator channel; never bans or mutes on its own.

*Speaker note: pitch as coming. Do NOT say it's live or name a client on it. The rigor (no hallucinated facts, human-reviewed start) is the selling point — it signals we'll do this responsibly.*

---

## Slide 10 — Social proof (real deployments)

**This is running for real clients today.**

- **TIG** — live deployment of `SableTracking` (content intake + contributor tracking) and the weekly client check-in pilot (🟡 IN-TRIAL).
- **SolStitch** — `sable-roles`, our Discord engagement bot, running in their server (fitcheck, roasts, airlock verification).

*Speaker note: describe what these deployments actually are — real running work — not invented outcomes or growth figures. See MESSAGING.md §8.*

---

## Slide 11 — How we engage (pricing model)

**Services-phase engagement.** Today Sable is a managed service: you're buying the operation — the diagnostic, the content, the reply amplification, the proof — delivered by our operators on top of the suite. Pricing is scoped per engagement (typically a retainer aligned to coverage and content volume), with weekly spend caps per client enforced in the platform.

We don't sell seats or per-impression packages. You're buying *measured work*, not promised reach.

*Speaker note: keep this directional — agree scope and retainer in the follow-up. Don't quote numbers in the deck.*

---

## Slide 12 — The honest line (and the close)

**What we promise, precisely:**
- Measured amplification — counted, human-sent replies and fixed-age engagement on them. **Never a guarantee of reach.**
- A diagnostic that tells you exactly where your community is fragile.
- A proof packet every week so you always know what the work produced.

**What we don't do:** auto-post, buy impressions, or promise a growth percentage. That's the point.

**CTA:** *Let us run the free health diagnostic on your community this week. You'll see exactly where it's fragile — and we'll show you the plan to fix it.*

---

*Built from [`../SUITE_CAPABILITIES.md`](../SUITE_CAPABILITIES.md). Every claim is status-checked against that map. Language governed by [`MESSAGING.md`](MESSAGING.md). When a capability changes status, update the relevant slide.*
