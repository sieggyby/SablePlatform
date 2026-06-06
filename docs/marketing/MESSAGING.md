# Sable — Messaging & Language Guardrails

**Audience:** Anyone who writes or says anything outbound — decks, one-pagers, cold emails, discovery calls, proposals, case-study copy.

**What this is:** The load-bearing file in the marketing kit. Sable's whole pitch rests on being the honest operator in a category full of vanity metrics and bot-spam. That credibility is an asset — and it's fragile. One oversold claim ("we'll 10x your community," "we auto-post for you") undoes it. This document defines exactly what we can say, how to say it, and the phrasings to avoid.

> **If you take one thing from this file:** we sell **measured work and measured amplification — never a guaranteed outcome.** Everything below is a corollary of that.

---

## 1. The measured-vs-interpretive taxonomy (memorize this)

Every claim about results falls into one of these buckets. The bucket determines how you're allowed to word it. This mirrors the four-type proof taxonomy in [`../PROOF_LOOP_PLAN.md`](../PROOF_LOOP_PLAN.md).

| Type | What it means | How to word it | Example |
|---|---|---|---|
| **Measured / attributable** | A real, counted number tied to a Sable action | State it plainly, as a count | "Sable-assisted replies produced 14 posted replies; 9 have matured 24-hour readings." |
| **Associated** | A Sable action was followed by a relevant movement; causality not proven | Use "after," "alongside," never "caused" / "drove" | "After the playbook push, recurring-participant share rose from X to Y." |
| **Directional** | A useful trend with confounders | Label it a trend; name the confounder | "Momentum improved week over week; market context may contribute." |
| **Self-reported / interpretive** | Operator-declared work, not independently measured | Always carry the caveat in the same sentence | "Operators *declared* 18.5 coverage hours across 3 communities." |

**The cardinal sins:**
- Never call a self-reported or directional signal "impact" or "results."
- Never present coverage-hours or communities-covered as a measured metric. They are operator-declared context. (See §3.)
- Never use raw impressions/reach as the headline proof claim. (See §4.)

---

## 2. What is MEASURED vs INTERPRETIVE in our own product

This is the part people get wrong most often, because it's *our own* "work delivered" reporting.

| Signal | Status | Why |
|---|---|---|
| **Replies delivered** | ✅ **Measured** | Captured via "Mark posted" reply-outcome capture, sourced from `reply_outcomes`. A real count. |
| **Assisted-vs-organic split** | ✅ **Measured** | The §10 layer separates Sable-assisted replies from a client's own. |
| **Content items logged** | ✅ **Measured** | Every contribution is logged + deduped in SableTracking. |
| **Matured engagement on a posted reply** | ✅ **Measured** (fixed-age) | Read at a fixed age (e.g. 24h) — a hard engagement number, not an impression estimate. |
| **Coverage hours** | 🟡 **Self-reported** | Operator-declared watch time from clocked mod slots. NOT measured presence. NOT surveillance. |
| **Communities covered** | 🟡 **Self-reported** | Operator-declared list of watched chats. |
| **Reach / impressions** | ⛔ **Do not headline** | Unreliable retroactively (cumulative, non-strict). Never a guarantee. (See §4.) |

**Approved framing for the "Work Delivered" card:**
> "Replies delivered is a measured count. Coverage hours and communities covered are operator-declared — they describe the scope of the watch, not a measured metric."

**Never:** present the work-tracking card as proof of "how much time operators were monitored," or as a productivity-surveillance dashboard. It is a *scale-of-work* record, declared by operators, sanitized for the client.

---

## 3. Coverage hours are not surveillance — and not a hard metric

Two failure modes, both forbidden:

1. **Overclaiming precision.** Coverage hours come from operators clocking mod slots. They are *self-reported*. Do not imply we measure operator presence minute-by-minute.
2. **Creepy framing.** Never describe work-tracking as "monitoring our operators" or "operator surveillance." It is operators *recording the work they did* so the client can see the scale of the engagement.

| ✅ APPROVED | ⛔ AVOID |
|---|---|
| "Operators logged 18.5 coverage hours across 3 communities last week (operator-declared)." | "We monitored 18.5 hours of operator activity." |
| "A record of the scale of work delivered." | "Operator surveillance / productivity tracking dashboard." |
| "Replies delivered (measured) + coverage hours (declared)." | "18.5 measured hours of community coverage." |

---

## 4. No growth percentages, no reach guarantees

Reach and impressions on X are **unreliable retroactively** — counts are cumulative and non-strict, so a "+183% impressions" story rarely reproduces. We do not build claims on them, and we never guarantee them.

| ✅ APPROVED | ⛔ AVOID |
|---|---|
| "We deliver measured amplification — counted, posted replies and fixed-age engagement on them." | "We'll grow your community 30% / 10x your engagement." |
| "Fixed-age (24h) engagement on assisted replies is a hard number we report." | "Guaranteed N impressions / N new followers per month." |
| "Reach is directional context, not a number we promise." | "+183% impressions in week 3." |
| "We concentrate force on the right conversations; we don't promise the algorithm's response." | "We'll make you go viral." |

**The phrase to reach for:** *"measured amplification, never a guarantee."*

---

## 5. Replies are suggested and human-sent — we never auto-post

This is both a safety boundary and a credibility asset. The reply-opportunity feed surfaces targets and drafts replies; **an operator always reviews and sends.** The bot does not post on anyone's behalf in v1. (OAuth auto-posting is explicitly roadmap.)

| ✅ APPROVED | ⛔ AVOID |
|---|---|
| "We surface the right reply targets and draft the reply; a human operator sends it." | "Our bot auto-replies to the right tweets for you." |
| "Suggested replies, reviewed and posted by a person." | "Fully automated reply posting." |
| "The drafts read as the operator, not an LLM — and a person still hits send." | "AI auto-engages your community 24/7." |

If a prospect asks for fully-automated posting: it's on the roadmap (relay OAuth send), and you say exactly that — "roadmap, out of v1" — never that we do it today.

---

## 6. NULO (auto-CM) is in development, not live

NULO / SableAutoCM is built end-to-end and ships **dormant** — no client is live on it. RobotMoney is onboarded as a *paused* tenant pending a voice-viability pass and sign-off. Pitch it as **coming**, never as a running service.

| ✅ APPROVED | ⛔ AVOID |
|---|---|
| "Coming next: an AI community manager ('NULO') that handles tier-1 questions with human oversight — built, in development, not yet live." | "We run an AI community manager in your Telegram today." |
| "It starts 100% human-reviewed and earns autonomy category-by-category." | "Our AI autonomously manages your community." |
| "In development; we'll pilot it once voice quality clears our bar." | "NULO is live with [any client]." |

Two NULO facts that are *good* to say (they signal rigor, not capability we lack): it never invents high-stakes facts (contract addresses/official links are slot-filled from a registry, never LLM-generated), and it *flags* bad actors to operators — it never bans/mutes on its own.

---

## 7. Status discipline — match the label

Every capability in [`../SUITE_CAPABILITIES.md`](../SUITE_CAPABILITIES.md) carries a status label. Your wording must match it:

- ✅ **LIVE** → speak in present tense, as a thing we do.
- ✅ **DEPLOYED (dormant)** (e.g. Trending-Story Autopilot) → "available, operator-initiated" — never "always-on" or "autonomous."
- 🟡 **IN-TRIAL** (e.g. weekly check-ins) → "we're piloting this with [client]."
- 🟡 **IN-DEVELOPMENT** (e.g. NULO) → "coming," "in development."
- 🔵 **ROADMAP** (e.g. relay auto-posting) → "on the roadmap," never present tense.

When unsure of a status, do not guess — check the capability map. Under-claim by default.

---

## 8. Don't invent metrics or clients

- Do **not** put a number in a deck or email that isn't a real measured count from a real run. No illustrative "e.g. 250 replies/week" that reads as a claim.
- Do **not** name a client we don't have or imply a result a client didn't get. Our live deployments are real (e.g. TIG for tracking/check-ins, SolStitch for the Discord engagement bot) — describe what those deployments actually are, not invented outcomes.
- Social proof is "here's the real work running for a real client," not a fabricated win.

---

## 9. Quick approved-vs-avoid cheat sheet

| ✅ SAY | ⛔ DON'T SAY |
|---|---|
| "Measured amplification." | "Guaranteed growth." |
| "Suggested, human-sent replies." | "Auto-posting / auto-replies." |
| "Replies delivered (measured); coverage hours (declared)." | "18.5 measured hours; +30% engagement." |
| "Reach is directional, not promised." | "We'll get you N impressions." |
| "NULO is coming / in development." | "Our AI runs your community." |
| "Trending autopilot is available, operator-initiated." | "Always-on autonomous trend engine." |
| "An AI health diagnostic that grades the community on real data." | "We score your community perfectly / definitively." |
| "A record of the scale of work delivered." | "Operator surveillance dashboard." |

---

*This file governs everything else in [`marketing/`](.). If a deck slide or one-pager conflicts with a rule here, this file wins. Source of truth for capability status: [`../SUITE_CAPABILITIES.md`](../SUITE_CAPABILITIES.md). Proof taxonomy: [`../PROOF_LOOP_PLAN.md`](../PROOF_LOOP_PLAN.md).*
