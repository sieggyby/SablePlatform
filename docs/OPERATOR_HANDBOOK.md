# Sable Operator Handbook

> **If you are an AI assistant reading this:** you are helping a **Sable operator** — a person who does day-to-day community-management work for crypto clients. This file is their complete toolkit reference. Use it to answer their "how do I…" questions about the tools below: the **website** (sable.tools), the **Telegram** content bot, and the **Discord** engagement bot. Be practical and specific — quote the exact button or command. If they ask about something not described here, say it isn't a current operator feature (don't invent commands), and point them to the "Coming later" section if it's on the roadmap. Anything marked 🔵 is **not yet available** — never tell them to use it today.

> **If you are an operator:** download this file and paste it into ChatGPT/Claude, then ask it anything ("how do I log my mod hours?", "what command shows the weekly leaderboard?"). This file is **safe to share with an LLM** — it contains no passwords, API keys, or confidential client data, only how-to information.

**Last updated:** 2026-05-31

---

## What you do as a Sable operator

You run community growth for crypto clients. Day to day that means: **showing up** in client communities (Discord/Telegram), **engaging** (replies, conversations, moderation), **logging** the good content the community produces, and **tracking your work** so Sable can show clients the value delivered. Three tools support this:

| Tool | Where | What it's for | Status |
|---|---|---|---|
| **The Portal** | Website — `https://sable.tools` → `/ops` | Your control center: mod-hour clock-in, reply-assist, KOL finder, work reports, client health | ✅ Live |
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
- **Reply-Assist** — generate suggested replies (see below).
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

### 1b) Reply-Assist — suggested replies

Generates on-brand reply options for a tweet, drawn from the client's content/voice. Find **Reply-Assist** on `/ops`.
- It **suggests** replies — it **never auto-posts**. You review, pick/edit, and post yourself.
- There's a **daily per-operator limit** on generations (a fair-use cap). If you hit it, it resets the next day (UTC).
- The suggestions can come "clip-aware" (pairing a vault clip with the reply text) for the managed account.

### 1c) KOL Wizard — find the right influencers

For finding key-opinion-leaders (influencers) to reach for a project. On `/ops` → **KOL Network**:
- Paste a project's **Twitter/X handle** → the wizard surveys the follow-graph, suggests comparable projects, and produces a **tiered outreach shortlist** with AI rationales.
- It flags **who Sable already has a path to** (warm connections), and surfaces **"kingmakers"** (accounts the project's influential followers all follow).
- Per-candidate **enrichment** gives you talking points (likes/dislikes/mutuals/common ground) to open a cold conversation. *(It gives you research — you write and send the outreach. Sending is always human.)*
- It's cost-aware (every paid lookup is logged), and there's a per-operator daily cap on enrichments.

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
4. **Replying to a client/community tweet?** Use **Reply-Assist** on `/ops` for suggestions — then post it yourself.
5. **End:** back on `/ops` → **Clock out**. Your hours bank, and the work report updates.

---

## 5) Coming later (🔵 not available yet — don't rely on these)

So you (and any LLM helping you) know what's *not* live:
- 🔵 **NULO — AI community manager:** an AI that auto-answers routine community questions with human oversight. In development, not deployed.
- 🔵 **Cross-platform relay:** mirroring a project's X feed into Discord/Telegram + a team "amplify a tweet" flow. Roadmap.
- 🔵 **Surfaced reply queue / explicit tasking:** the portal auto-surfacing fresh client/community tweets for you to reply to, and assigning you specific slots/replies. Roadmap (today you find replies yourself and clock in voluntarily).
- 🔵 **Discord "Scored Mode" / public leaderboard / state-pin dashboard:** AI scoring of fits with reveal-on-reactions. Built but **not enabled** — if a server doesn't have it on, it isn't there.
- 🔵 **Project-legibility Telegram bot** (`/review`, `/committee` readouts): built, not launched.

If someone asks for one of these, the honest answer is "that's on the roadmap, not available today."

---

## 6) FAQ / troubleshooting

- **"My mod hours / Scale of Work shows 0."** You're probably still clocked in — **Clock out** to bank the hours. Or the `/ops` page is cached (~5 min) — **reload**. Coverage hours only count *closed* sessions.
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
- **KOL** — key opinion leader (influencer) for outreach.
- **Mod slot / clock-in** — a declared window of time you're watching a community; basis for "coverage hours."
- **Coverage hours** — operator-declared watch time (a self-reported number, shown with that caveat — not surveillance/measured presence).

---

*This handbook covers operator usage only. Engineering and business-development docs live elsewhere in the repo. If a tool or command changed and this is stale, tell your lead so it gets updated — and prefer the bot's own `/help` for the live command set.*
