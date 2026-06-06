# Sable Operator Handbook

> **If you are an AI assistant reading this:** you are helping a **Sable operator** — a person who does day-to-day community-management work for crypto clients. This file is their complete toolkit reference. Use it to answer their "how do I…" questions about the tools below: the **website** (sable.tools), the **Telegram** content bot, and the **Discord** engagement bot. Be practical and specific — quote the exact button or command. **Anything marked ✅ is LIVE — help the operator use it today.** Anything marked 🔵 is genuinely **not yet available** (true roadmap) — don't tell them to use a 🔵 item, but DO point them to it if they ask "what's coming." If they ask about something not described here at all, say it isn't a current operator feature (don't invent commands). When in doubt, prefer helping them use the live ✅ features below — the Reply-Opportunity Feed, sweep-now, mark-posted, the media center, tell-score, variant thumbs, and reply campaigns are all live.

> **If you are an operator:** download this file and paste it into ChatGPT/Claude, then ask it anything ("how do I log my mod hours?", "what command shows the weekly leaderboard?"). This file is **safe to share with an LLM** — it contains no passwords, API keys, or confidential client data, only how-to information.

**Last updated:** 2026-06-06

---

## What you do as a Sable operator

You run community growth for crypto clients. Day to day that means: **showing up** in client communities (Discord/Telegram), **engaging** (replies, conversations, moderation), **logging** the good content the community produces, and **tracking your work** so Sable can show clients the value delivered. Three tools support this:

| Tool | Where | What it's for | Status |
|---|---|---|---|
| **The Portal** | Website — `https://sable.tools` → `/ops` | Your control center: mod-hour clock-in, the reply-opportunity feed + reply-assist, log-work, KOL finder, work reports, client health | ✅ Live |
| **Content tracker** | Telegram bot (and Discord intake) | Log community content by dropping a link — the bot classifies + records it | ✅ Live (TIG, via Telegram) |
| **Engagement bot** | Discord, in client servers | Run engagement games + moderation (fitcheck streaks, roasts, member verification) | ✅ Live (SolStitch) |

> Sable assigns operators to specific clients/communities. You'll only see and act on the clients you're assigned to.

---

## 1) The Portal — `sable.tools` `/ops`

The operator web console. **Sign in with Google** (your Sable operator email — if you can't get in, you're not on the allowlist yet; ask your lead). After login, go to **`/ops`**. A dot-navigation rail on the side jumps you between sections.

### What's on the `/ops` console
- **Morning Brief** — the top-of-day "what needs attention" summary across your clients.
- **Client / Prospect Triage** — which clients need attention; prospects in the pipeline.
- **Action Queue** — recommended actions and who's on them.
- **Mod-Slot Clock-In** + **Scale of Work Delivered** — your mod hours and the work report (see below).
- **Log Work** — quick-capture card for any work that isn't a clock-in or a reply (see §1a-2 below).
- **Reply-Assist + Reply-Opportunity Feed** — your queue of reply targets, on-brand draft generation, media attach, and the mandatory "mark posted" step (see §1b below).
- **Reply Quality** (`/ops/reply-quality`) — internal dashboard: how human your drafts read, what gets picked, what to refine (see §1b below).
- **Media Health** (`/ops/media-health`) — the vault media library's health (see §1b below).
- **KOL Network / Wizard** — find and plan influencer outreach (see below).
- **Source Freshness / Ops Alerts** — what data is stale, what's firing.
- **Entity Intelligence / Relationship Graph / Content Pipeline / Cost / Audit / Playbook** — read-only intelligence on each client's community and Sable's work.

Sections that have **no data yet stay hidden** — that's normal, not a bug. The page also **caches for ~5 minutes** (it refreshes itself periodically), so after you do something, give it a moment or **reload** to see it reflected.

### 1a) Mod hours — clocking in and out ⭐ (new)

This is how Sable records the time you spend watching/moderating a community, so we can report "scale of work delivered."

**To start a shift:**
1. On `/ops`, find the **"Mod-Slot Clock-In"** card.
2. Pick the **Client** from the dropdown.
3. In **"Chats watched"**, type the channels you're covering, **comma-separated** (e.g. `#general, #alpha`). *(Optional, but if you skip it, that session counts 0 communities — fill it in to get credit for coverage.)*
4. Click **Clock in**. You'll see a "Clocked in to …" confirmation.

**To end a shift:** click **Clock out**.

**Key rules (so your hours look right):**
- ⏱️ **You must Clock out for the hours to count.** While a slot is open it shows under **"Active now"** but contributes **0 hours** — the time only banks when you close it. (This is intentional: a slot you forget to close doesn't inflate the number forever.)
- 🔁 **One open slot at a time.** Clocking in automatically closes any slot you'd left open, so you can't double-count.
- 🧾 Your sessions roll up into the **"Scale of Work Delivered"** report (replies delivered + coverage hours + communities, with a per-operator breakdown) and a sanitized **"Work Delivered"** card the client sees (which shows totals only — never your name, raw logs, or costs).
- After clocking in, **refresh** `/ops` if the Scale-of-Work report doesn't appear immediately (the ~5-min cache).

**Common confusion:** "My hours show 0." → You're still clocked in (open slot) — click **Clock out**. Or the page is cached — reload.

### 1a-2) Log Work — quick-capture for everything else ⭐ (new)

Clock-in covers watch time; mark-posted (see §1b) covers replies. **Log Work** is the quick-capture card for *any other* work you do that isn't either of those — e.g. you DM'd a partner, set up a giveaway, wrote a thread brief, cleaned up a channel.

**To log a piece of work:**
1. On `/ops`, find the **"Log Work"** card.
2. Type a short **note** (free text — what you did).
3. Pick an **effort level**: **quick / medium / deep**.
4. Optionally paste a **link** (the tweet/message/doc it relates to).
5. Submit. It's recorded against you and rolls into the **Scale of Work Delivered** report.

If you don't log it, it didn't happen as far as the work report is concerned — so log the off-platform and one-off work too, not just replies.

### 1b) Reply-Assist & the Reply-Opportunity Feed — your reply workflow ⭐ (rebuilt)

This is now a **queue-driven** flow, not "find a tweet yourself → paste → generate." Open **Reply-Assist** on `/ops` (`/ops/reply-assist`). The page is **org-locked** to the client you're working — pick the client, and everything below is scoped to it.

The normal loop is: **work the Opportunities queue → Draft reply → pick a variant (most-human first) → optionally attach media → post it on X yourself → ✓ Mark this one posted.** It **never auto-posts** — every reply is sent by a human.

#### The Opportunities panel (work from this, don't hunt)
Above the draft form is the **Opportunities** panel — an auto-sourced, per-operator, org-filtered queue of reply targets the system found for you. Each card shows:
- the **target tweet**,
- a **source badge** telling you where it came from: **mention** (someone mentioned the client), **topic** (on-theme conversation), **from-set** (a tracked author posted), or **operator-submit** (someone pasted it in),
- an interpretive **"why" + "angle"** — the system's read on why it's worth a reply and how to come at it. ⚠️ **This is AI-assessed, not a measured fact** (it's shown behind a caveat) — treat it as a suggestion, not gospel,
- **two thumbs** (good / weak) to teach the ranker which opportunities are worth surfacing,
- **Dismiss** (remove it) and **Snooze** (hide it for 24h),
- **Draft reply** — the main action: it prefills the draft form's target URL and locks the org, so you go straight to generating.

Two operators may see the same opportunities in a slightly different order — the feed personalizes per operator. That's expected.

#### Sweep-now — pull a fresh batch
If the queue looks thin or stale, click **Sweep-now**. It **enqueues** a fresh sweep for your org (it doesn't run inline — you'll see **"sweep queued"** and the feed refreshes shortly after). There's a **per-operator daily budget** on sweeps; the uncapped operators are **Arf, Ben, Sieggy, and Sparta**. If you hit the cap, it resets the next day.

#### Generating + picking a variant
Hit **Draft reply** on an opportunity (or paste a tweet URL manually if you must), then generate. You get several **on-brand variants** drawn from the client's voice and content. Key things:
- Variants are **ordered most-human-first** — the top one is the system's read on the least AI-sounding draft.
- Each variant carries a **"tell" lint** (the §10 tell-score / TellFlagGutter): an advisory highlight of any AI-sounding phrasing. ⚠️ **It's advisory — info, not a hard error.** Read it, tighten the copy if you like, but don't be afraid of a flag. Obvious AI residue is already stripped for you automatically.
- **Thumb each variant good / weak.** This feeds the ranker and the **/ops/reply-quality** dashboard — it's how the drafts get better, so use it.
- There's a **daily per-operator cap** on generations (fair use). Resets next day (UTC).

#### Media Rec Center — attach a clip or image
In the reply flow you get a **ranked carousel of vault media** (images + clips) matched to the target tweet — the **Media Rec Center**. Pick one to attach to your reply; attaching it is logged. To check the health of the underlying library (what's available, what's stale), open **`/ops/media-health`**.

#### ✓ Mark this one posted — DO NOT SKIP THIS
After you post your chosen reply on X, come back to that variant and click **"✓ Mark this one posted,"** then paste **the URL of the tweet you actually posted**. This **records the outcome so the reply counts toward your delivered-work totals.**

> **Why mark-posted matters:** the **Scale of Work Delivered** report counts *measured* replies — and a reply is only "measured" once you've marked it posted. **If you skip this step, that reply is invisible: your output is under-reported and you don't get credit for the work.** Make it a habit: post → mark posted → paste URL.

#### Reply Quality dashboard (`/ops/reply-quality`)
A small internal dashboard that answers "how human do our drafts read, what do operators pick, and what should we refine." It shows the tell-score spread (most-human first), pick-rate by source, your suggestion thumbs, and advisory style-refinement proposals. Everything on it is **interpretive** (behind a caveat) and **advisory** — nothing here auto-applies or posts anything; it's a read-and-learn surface.

### 1c) KOL Wizard — find the right influencers

For finding key-opinion-leaders (influencers) to reach for a project. On `/ops` → **KOL Network**:
- Paste a project's **Twitter/X handle** → the wizard surveys the follow-graph, suggests comparable projects, and produces a **tiered outreach shortlist** with AI rationales.
- It flags **who Sable already has a path to** (warm connections), and surfaces **"kingmakers"** (accounts the project's influential followers all follow).
- Per-candidate **enrichment** gives you talking points (likes/dislikes/mutuals/common ground) to open a cold conversation. *(It gives you research — you write and send the outreach. Sending is always human.)*
- It's cost-aware (every paid lookup is logged), and there's a per-operator daily cap on enrichments.

### 1d) Reply campaigns / flash-mob ⭐ (new)

When the team wants a **coordinated reply push** on a moment (a launch tweet, an AMA, a news beat), use the **Campaign bar** in the reply-assist flow.
- **Create or join** a campaign — it groups everyone's replies around one objective.
- **Draft-from-campaign** — generate your reply in the campaign's context (same variant/tell/media flow as a normal reply).
- **Mark won** — flag a reply that landed the result the campaign was after.
- A **Performance** panel shows how the push is doing: post-rate, mean 24h engagement (matured replies only), and adoption-rate — all interpretive, behind a caveat. (No cost is ever shown.)

This is the same post → mark-posted discipline as a normal reply — campaign replies still need to be marked posted to count.

### 1e) Relay & NULO ops (if you run them)

If you operate the **SableRelay** (cross-platform X→Discord/Telegram relay) or **NULO** (the AutoCM persona) layer for a client, those operator surfaces — the relay CLI (`relay status`/`pending`/`enable`/`disable`/`pause-org`) and the in-chat slash-commands (`/pause-client`, `/resume-client`, the 48h HITL freeze) — are documented in **`docs/OPERATIONS_RUNBOOK.md`**. That runbook is the source of truth for the relay/NULO ops layer; this handbook doesn't duplicate it.

> **NULO is currently DORMANT** — seeded but not enabled for any live client. Until a client is switched on, there's nothing running to operate. (See the "Coming later" section.)

---

## 2) Telegram — the content tracker

Sable runs a **content-tracking bot** in the client's Telegram group (live for **TIG**). Its job: capture the good content the community makes so the weekly client report is backed by real numbers.

### The core move: just drop the link
**Paste a link** (a tweet, a meme, a video, a thread) **or forward a post** into the tracking group. The bot automatically:
- pulls the content, **AI-classifies** it (format, intent, culture signals),
- **dedupes** it (so the same thing isn't counted twice),
- archives any media, and **logs a structured record**.

That's it — no command needed to log something. For ambiguous items, the bot drops it in a **review queue** with **Accept / Skip / Review** buttons for an operator to confirm.

### Operator commands
Type **`/help`** in the group for the bot's authoritative current command list. The commonly used ones:

| Command | What it does |
|---|---|
| `/brief` | The weekly analytical report (what worked, contributor movement, retention) — also auto-generated Mondays |
| `/leaderboard` | Top contributors (7- and 30-day windows) |
| `/outcome` | Tag a piece of content with the real result it drove (follower gain, DM, partnership lead, event) |
| `/pulse` | Quick health snapshot (entries, contributors, open reviews, trend) |
| `/triage` | Work the review queue (ambiguous / high-signal items) |
| `/rate` | Score a piece of content 1–10 |
| `/top` | Top performers by quality × engagement |
| `/stats` | Tracking stats for the client |

> If a command above doesn't appear when you type `/`, it may not be enabled for that client — use `/help` for the live set.

---

## 3) Discord — the engagement bot (for mods)

Sable's **engagement bot** runs in client Discord servers (live in **SolStitch**). It powers community games + new-member control. Some commands are for members; the **mod commands** are for you.

> The specific mechanics (the fitcheck game, the roast voice, the verification copy) are **tuned per community** — what's described here reflects the SolStitch deployment.

### Member-facing (good to know)
- **`/streak`** — a member sees their fit-check streak + most-reacted post.
- **`/burn-me`** — a member opts in to be playfully AI-roasted on their next post; `/set-burn-mode` (`once`/`persist`/`never`), `/stop-pls` to opt out for good.
- **"Roast this fit"** — a right-click (context-menu) action to roast a specific post (peer-roast has a light token economy); `/my-roasts`, `/peer-roast-report`.

### Mod commands (yours)
| Command | What it does |
|---|---|
| `/relax-mode on` / `off` | Pause image-only enforcement in the fitcheck channel for an off-theme moment (and resume) |
| `/admit` / `/ban` / `/kick` | Act on a held new member in the airlock/verification flow |
| `/airlock-status` | See pending/held new members |
| `/add-team-inviter` / `/list-team-inviters` | Manage which invite sources auto-admit (team invites skip the hold) |
| `/set-personalize-mode` | Toggle the opt-in personalization layer for roasts |

**How new-member verification works:** when someone joins, the bot checks how they were invited. **Team invites → auto-admitted.** Anyone else → **held**, sent a "prove you belong" prompt, and flagged to the mod/triage channel for you to **`/admit`**, **`/kick`**, or **`/ban`**.

**Every enforcement action is logged** (deletions, holds, admits, roasts) — so "did the bot delete X's message / why was Y held?" is always answerable from the record.

---

## 4) A shift, end to end

1. **Start:** open `sable.tools` → `/ops` → **Clock in** to your client, list the chats you're covering.
2. **Watch + engage** in the community (Discord/Telegram). Use **mod commands** as needed (`/relax-mode`, airlock admits, etc.).
3. **See a great community post?** Drop its link in the **Telegram tracking group** — it's logged automatically. Tag a real result later with **`/outcome`** if one lands.
4. **Reply work:** open **Reply-Assist** → work the **Opportunities** queue (or **Sweep-now** for a fresh batch). **Draft reply** on a good one → pick the **most-human** variant → optionally attach a clip/image from the **Media Rec Center** → thumb the variants → **post it on X yourself** → **✓ Mark this one posted** (paste the URL). The mark-posted step is what makes it count.
5. **Did other work?** (DMs, a giveaway, a brief, channel cleanup.) Log it on the **Log Work** card with a note + effort level.
6. **End:** back on `/ops` → **Clock out**. Your hours bank, and the work report updates.

---

## 5) Now live vs. coming later

Some things that used to be "coming later" are now **live** — use them. A few are genuinely still roadmap.

### ✅ Now live (use these today)
- ✅ **Surfaced reply queue (Reply-Opportunity Feed):** the portal auto-surfaces fresh reply targets for you in the **Opportunities** panel on `/ops/reply-assist` — you no longer have to hunt for replies. See §1b. (You still clock in voluntarily; the queue replaces the manual *finding* of replies, not the clock-in.)
- ✅ **Sweep-now:** pull a fresh batch of opportunities on demand (per-operator daily budget). See §1b.
- ✅ **Mark-posted + work tracking:** your posted replies and free-form **Log Work** now feed the Scale-of-Work report. See §1b / §1a-2.
- ✅ **Reply campaigns / flash-mob:** the Campaign bar for a coordinated reply push. See §1d.
- ✅ **SableRelay (cross-platform relay) operator layer:** the relay CLI + in-chat kill-switches are operable — but operated from the **`docs/OPERATIONS_RUNBOOK.md`** surface, not this handbook. See §1e. (It's a backend/ops layer, so a typical CM operator won't touch it.)

### 🔵 Coming later (genuinely not available yet — don't rely on these)
- 🔵 **OAuth auto-posting:** the portal posting your approved reply to X for you. Not live — **you always post the reply yourself** and then mark it posted. Roadmap.
- 🔵 **NULO going live for a client — AI community manager:** NULO (the AutoCM persona) auto-answering routine community questions with human oversight. It's **seeded but DORMANT** — not enabled for any live client yet. The *operator surface exists* (in the runbook, §1e), but there's no live client to run it for. Roadmap.
- 🔵 **Discord "Scored Mode" / public leaderboard / state-pin dashboard:** AI scoring of fits with reveal-on-reactions. Built but **not enabled** — if a server doesn't have it on, it isn't there.
- 🔵 **Project-legibility Telegram bot** (`/review`, `/committee` readouts): built, not launched.

If someone asks for a 🔵 item, the honest answer is "that's on the roadmap, not available today" — but if they ask about a ✅ item above, help them use it.

---

## 6) FAQ / troubleshooting

- **"My mod hours / Scale of Work shows 0."** You're probably still clocked in — **Clock out** to bank the hours. Or the `/ops` page is cached (~5 min) — **reload**. Coverage hours only count *closed* sessions.
- **"I posted a bunch of replies but my reply count is low."** You skipped **"✓ Mark this one posted."** A reply only counts once you mark it posted and paste the posted-tweet URL. Go back and mark them; unmarked replies are invisible to the work report. See §1b.
- **"The Opportunities queue is empty / stale."** Click **Sweep-now** to enqueue a fresh batch (you'll see "sweep queued"), then give it a moment and reload. If you're not one of the uncapped operators, you may have hit your daily sweep budget — it resets tomorrow.
- **"Does the portal post my reply for me?"** No. Auto-posting (OAuth) is roadmap (🔵). You post the reply on X yourself, then **mark it posted**.
- **"The 'why/angle' on an opportunity, or the tell-score on a draft — can I trust it?"** Both are **interpretive/advisory** (AI-assessed, shown behind a caveat), not measured facts. Use them as guidance; the tell lint is info, not an error — don't fear a flag.
- **"I clocked in but the report section isn't showing."** Reload `/ops`; it's hidden until there's data and the page caches briefly. After your first clock-in it should appear (showing you as "Active now," 0 hours until you clock out).
- **"A command isn't working in the bot."** Type **`/help`** (Telegram) for the live list, or it may not be enabled for that client. Don't assume a command exists because it's elsewhere — use what `/help` shows.
- **"I can't log into the website."** Your Google email isn't on the operator allowlist yet — ask your lead to add it.
- **"I left 'chats watched' blank."** That session still counts time, but 0 communities. You can't backfill an open slot — clock out and clock back in with the chats listed.
- **"Will the client see my name / my hours / costs?"** No. The client's "Work Delivered" card shows **totals only** — never operator identities, per-person breakdowns, raw logs, or any cost. That wall is enforced in the software.
- **"Which bot is in which server?"** The Telegram content tracker is live for **TIG**; the Discord engagement bot is live in **SolStitch**. Other clients may be onboarded over time.

---

## 7) Mini-glossary

- **Cultist / cultist candidate** — a high-quality, recurring community contributor (the people worth nurturing).
- **Fit-check** — the Discord posting game where members share "fits" (outfits/posts); streaks + reactions drive engagement.
- **Airlock** — the new-member verification gate (admit / hold / triage based on how they were invited).
- **Reply-assist** — AI-suggested replies you review and post (never auto-posted).
- **Reply-Opportunity Feed / Opportunities** — the auto-sourced, per-operator queue of reply targets at the top of `/ops/reply-assist`. You work from it instead of hunting for replies.
- **Source badge** — where an opportunity came from: **mention / topic / from-set / operator-submit**.
- **Sweep / Sweep-now** — the job that finds fresh reply opportunities for your org; **Sweep-now** enqueues one on demand (per-operator daily budget).
- **Mark-posted** — the "✓ Mark this one posted" step that records a reply's posted-tweet URL so it counts toward your delivered-work totals. Skip it and the reply is under-reported.
- **Log Work** — quick-capture card for work that isn't a clock-in or a reply (note + effort level + optional link).
- **Tell-score / tell lint** — an advisory highlight of AI-sounding phrasing on each draft variant; variants are ordered most-human-first. Info, not an error.
- **Media Rec Center** — the ranked carousel of vault images/clips matched to the target tweet, in the reply flow.
- **Reply campaign / flash-mob** — a coordinated team reply push around one moment, run from the Campaign bar.
- **NULO** — Sable's AutoCM persona (an AI community manager). Currently **dormant** — seeded, not enabled for any live client.
- **SableRelay** — the cross-platform X→Discord/Telegram relay layer; operated from `docs/OPERATIONS_RUNBOOK.md`.
- **KOL** — key opinion leader (influencer) for outreach.
- **Mod slot / clock-in** — a declared window of time you're watching a community; basis for "coverage hours."
- **Coverage hours** — operator-declared watch time (a self-reported number, shown with that caveat — not surveillance/measured presence).

---

*This handbook covers operator usage only. Engineering and business-development docs live elsewhere in the repo. If a tool or command changed and this is stale, tell your lead so it gets updated — and prefer the bot's own `/help` for the live command set.*
