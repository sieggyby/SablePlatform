# SableRelay + SableAutoCM — Operations Runbook

> **Scope (MEGAPLAN C4.3).** Boot, monitor, halt, roll back, and reconcile the
> three runtime roles that carry SableRelay (the substrate) and SableAutoCM (the
> product): **`relay-bot`**, **`relay-poller`**, and the **`autocm-batch`** worker.
> Deployed alongside the existing SablePlatform services on the Hetzner VPS.
>
> **Source of truth for behavior:** the committed modules under
> `sable_platform/relay/` and `sable_platform/autocm/`. This runbook is the *ops*
> layer over them — it does not re-implement any logic.

---

## 1. The three runtime roles (MEGAPLAN §2 topology)

| Role | Unit / script | What it does | RateLimiter? |
|---|---|---|---|
| **relay-bot** | `sable-platform-relay-bot.service` → `scripts/run_relay_bot.py` | Long-running TG/Discord **listener**. ALSO hosts the **in-process AutoCM online handlers** (filter → classify → draft → gate → HITL post). | **YES — SOLE owner of the in-memory core `RateLimiter`.** Pin to **one replica**. |
| **relay-poller** | `sable-platform-relay-poller.service` → `scripts/run_relay_poller.py` | X-timeline **poller** (Flow A) + **sweeper** (expiry/recon/GC) + publish **outbox drain**, on a loop. | **NO.** Spend via per-org SocialData daily cap + `cost_events`. Scalable. |
| **autocm-batch** | `sable-platform-autocm-batch.service` + `.timer` → `scripts/run_autocm_batch.py` | Scheduled batch jobs: `autocm_kb_refresh`, `autocm_autonomy_sweep`, `autocm_weekly_digest`, `autocm_adversarial_sweep`, per enabled AutoCM client. | **NO.** Spend via SP `check_budget()` / `cost_events`. Scalable. |

The AutoCM **online** path is NOT a separate daemon — it co-runs inside
`relay-bot` via the relay listener handler-registry (C2.7). Only the poller and
the batch jobs are separate worker units.

Compose alternative to the systemd units: `deploy/docker-compose.relay.yaml`
(run with `docker compose -f docker-compose.yaml -f deploy/docker-compose.relay.yaml up -d`).

---

## 2. Secrets — env only, NEVER inline (MEGAPLAN §5)

**No secret value ever appears in a unit file, in source, or in a config/YAML
row.** Every credential is read from the process environment:

- **systemd:** `EnvironmentFile=/opt/sable/.env` (chmod 600, owned by `sable`, gitignored).
- **compose:** `${VAR}` interpolation from the host env / `--env-file` (the `${VAR:-}` default is the empty string, never a token).
- **manifest:** `sable_platform/autocm/manifest.py` enforces **secrets-as-references** — a credential field must be `env:NAME` or `secret://path`; an inline token is rejected with `ManifestSecretError`.

| Variable | Used by | Purpose |
|---|---|---|
| `SABLE_DATABASE_URL` | all | Shared SP Postgres URL on the VPS (single DB; Relay folds into it — there is no separate Relay DB). |
| `SABLE_OPERATOR_ID` | all | Audit actor / workflow operator id. Set per unit (`relay-bot` / `relay-poller` / `autocm-batch`). |
| `RELAY_TG_BOT_TOKEN` | relay-bot, relay-poller | Telegram bot token (listen + send transport). |
| `RELAY_DISCORD_BOT_TOKEN` | relay-bot | Discord bot token (transport off if unset). |
| `RELAY_DISCORD_CLIENT_ID` / `RELAY_DISCORD_CLIENT_SECRET` | relay-bot | Discord OAuth (PLAN §14.5). |
| `SOCIALDATA_API_KEY` | relay-poller | SocialData HTTP transport (Flow A poll). |
| `ANTHROPIC_API_KEY` | relay-bot, autocm-batch | AutoCM LLM (drafter / classifier / adversarial). Resolved lazily; only the Anthropic provider reads it. |
| `RELAY_POLLER_INTERVAL_SECONDS` | relay-poller | Poll loop interval (default 60). Not a secret. |

> **`.env` hygiene:** rotate `ANTHROPIC_API_KEY` and `RELAY_*` tokens out-of-band;
> never commit `.env`; verify `git check-ignore /opt/sable/.env` returns the path.

---

## 3. Boot

```sh
# (one time) drop the unit files in place
sudo cp deploy/sable-platform-relay-bot.service      /etc/systemd/system/
sudo cp deploy/sable-platform-relay-poller.service   /etc/systemd/system/
sudo cp deploy/sable-platform-autocm-batch.service   /etc/systemd/system/
sudo cp deploy/sable-platform-autocm-batch.timer     /etc/systemd/system/
sudo systemctl daemon-reload

# schema is already at head 058 — confirm before starting
sudo -u sable /opt/sable/venv/bin/sable-platform db-health --json

# start the long-running listeners + enable the batch timer
sudo systemctl enable --now sable-platform-relay-bot.service
sudo systemctl enable --now sable-platform-relay-poller.service
sudo systemctl enable --now sable-platform-autocm-batch.timer
```

`relay-bot` / `relay-poller` are `Type=simple` with `Restart=on-failure`.
`autocm-batch` is a `Type=oneshot` driven by the `.timer` (daily 04:00 + 20min
after boot).

> A transport whose token is absent is simply not run (the listener decides at
> runtime, never at import time). A `relay-bot` with NEITHER `RELAY_TG_BOT_TOKEN`
> nor `RELAY_DISCORD_BOT_TOKEN` exits non-zero — that is intentional, not a crash.

### relay-poller — transport seams (pre-go-live wiring)

The relay feed product behavior (mirroring/publish) is the final wiring step
before go-live. `scripts/run_relay_poller.py` runs the committed
poll → sweep → drain loop, but the **production transports** are operator-provided
seams that currently **fail loudly** rather than silently no-op a poller that
publishes nothing:

- `_build_socialdata_client()` — the `SocialDataClient` whose `http_get` is the
  SocialData HTTP transport keyed by `SOCIALDATA_API_KEY`.
- `_build_sender()` — the TG/Discord `Sender` send transport.

Wire both before enabling `relay-poller` in prod. `relay-bot` has no such seam —
its listener path is fully wired.

---

## 4. Monitor

```sh
systemctl status sable-platform-relay-bot.service sable-platform-relay-poller.service
journalctl -u sable-platform-relay-bot.service -f          # structured JSON logs
systemctl list-timers sable-platform-autocm-batch.timer    # next batch run
journalctl -u sable-platform-autocm-batch.service --since today

# per-client relay health (poll cursor, last error, in-flight jobs)
sudo -u sable SABLE_OPERATOR_ID=ops /opt/sable/venv/bin/sable-platform relay status <ORG_ID>
sudo -u sable SABLE_OPERATOR_ID=ops /opt/sable/venv/bin/sable-platform relay pending <ORG_ID>
```

Health checks: each unit / container runs `sable-platform db-health --json`
(shared DB reachability). Existing SP alerts (e.g. `workflow_failures`,
`stuck_runs`) surface a wedged `autocm-batch` workflow run.

---

## 5. Kill-switches & scaling invariants

There are **three distinct halt modes at different blast radii** (MEGAPLAN C4.3).
Pick the narrowest that fixes the problem.

### 5a. `/pause-client` — AutoCM publishing halt (narrowest)
Stops **all** AutoCM publishing for one client (autonomous auto-send **and**
HITL-approved replies **and** the incident proactive poster). Sets
`autocm_clients.autonomy_state='paused'`, read by every publishing path via
`is_publishing_paused`. Does **not** touch the relay substrate.

- **Engage:** operator slash-command `/pause-client [id]` in the relay chat
  (mod-gated). Audit verb `client_publishing_paused`.
- **Revert:** `/resume-client [id]` → restores `autonomy_state` (safe default
  `hitl` — NOT straight back to `auto`). Audit verb `client_publishing_resumed`.

### 5b. relay `disable` / `pause-org` — substrate publishing halt (wider)
Stops mirror/quorum publishing **at the substrate**, even when AutoCM is
uninvolved (C2.5). In ONE transaction: `relay_clients.enabled=0` (stops the
poller admitting the org) **and** marks every pending/retry/claimed
`relay_publication_jobs` row `state='dead'` (stops the publisher mid-flight).

```sh
SABLE_OPERATOR_ID=ops sable-platform relay disable <ORG_ID>     # or: relay pause-org <ORG_ID>
SABLE_OPERATOR_ID=ops sable-platform relay enable  <ORG_ID>     # re-admit to the poller loop
```
Audit verb `relay.disable` / `relay.enable`. `pause-org` is the operator-friendly
alias of `disable` (same callback).

### 5c. SAFETY §6 — 48h pure-HITL freeze (the "embarrassing post" response)
**Freezes all autonomous auto-send for a client but KEEPS drafting + HITL
review** — the correct response to an embarrassing post despite the guards, NOT a
full pause. Sets a future `freeze_until` on **every** `autocm_category_state`
row (C3.8a). Auto-restores after ≥48h (a category must re-pass the autonomy gate
**after** the freeze elapses — it never silently re-arms). Incident-mode cannot
clear an active freeze; only `freeze_until` elapsing or an explicit operator
clear restores autonomy. While frozen, drafts are produced and routed to HITL
(`frozen_to_hitl`), never auto-sent.

| Mode | Blast radius | Drafting | HITL | Auto-send | Restore |
|---|---|---|---|---|---|
| `/pause-client` (5a) | one AutoCM client | **stopped** | stopped | stopped | `/resume-client` |
| relay `disable` (5b) | one org's substrate | n/a | n/a | substrate publish dead | `relay enable` |
| 48h freeze (5c) | one client's auto-send | continues | continues | **frozen** | auto after ≥48h / operator clear |

### 5d. Scaling invariants (HARD — verify before any scale-out)
The in-memory core `RateLimiter` is **single-PROCESS** state ("a second replica
would let each process grant its own quota"). Two checks, both required:

1. **(a) cross-unit:** no rate-limited LLM/cost path runs in BOTH `relay-bot`
   and a worker unit. The worker units use `check_budget()`/`cost_events`, never
   the in-memory limiter — keep it that way.
2. **(b) intra-unit:** `relay-bot` is pinned to **exactly ONE replica**
   (systemd: a plain unit, no `@`-instances; compose: `deploy.replicas: 1`).

If **either** a rate-limited path appears in a second unit **or** `relay-bot`
count > 1, the §8-deferred **shared-store limiter is a hard prerequisite** before
that change ships — the per-process counter cannot guarantee cost control across
processes. `relay-poller` and `autocm-batch` may be scaled freely (no limiter).

---

## 6. NULO autonomy rollout — paused → silent → revealed

NULO ships **dormant** (RobotMoney seeded by `scripts/seed_robotmoney.py` with
`relay_clients.enabled=0`, `autocm_clients.enabled=0`,
`autonomy_state='paused'`). The rollout has two interlocking layers — advance
them deliberately, one client at a time.

### Layer A — deployment/disclosure phases (per-client, operator-driven)

| Phase | Meaning | How to enter | How to revert |
|---|---|---|---|
| **paused** | Bot present but posts NOTHING (the seeded default). | seeded state; `/pause-client` | — |
| **silent** | Bot drafts + posts under HITL, **without** advertising it is an AI agent (no bot-bio/pinned AI-disclosure line yet). Operators watch voice + safety on a live channel. | enable the tenant (`relay enable <ORG>` + flip `autocm_clients.enabled=1`), keep every category at `state='hitl'`. | `/pause-client`, or relay `disable`. |
| **revealed** | Same posting, now with the **AI-disclosure live** (C4.1 per-client TG disclosure decision: bot-bio / pinned-message line, Lex-signed-off). This is the public-honesty milestone, not a capability change. | land the C4.1 disclosure decision, publish the bio/pinned line. | pull the disclosure + `/pause-client` (note: un-revealing is a trust event — prefer pausing). |

> **Gate before `revealed`:** the C4.1 per-client TG AI-disclosure decision MUST
> be recorded with Lex sign-off (or an explicit accepted-risk entry). Do not go
> `revealed` on the unowned SAFETY §8 item. FTC posture + EU AI Act Art. 50.

### Layer B — earned per-`(client, category)` autonomy state machine

Orthogonal to A. Even when `revealed`, a category posts via HITL until it
**earns** auto-send. DB `autocm_category_state.state`: `hitl → auto` (the
manifest/sweep also models a `partial` step). A category flips to `auto` only
after the DESIGN §7 gate: **≥50 samples + ≥90% clean-approval + zero safety
violations + operator sign-off**. It **auto-demotes** on rolling-7d
clean-approval `< 0.85` (the `autocm_autonomy_sweep` batch job).

```sh
# advance a category to autonomous (runs the flip-criteria gate; flips iff it passes)
/promote <category>          # in-chat, mod-gated; audit autonomy_promoted

# revert a category to human-reviewed (always allowed, no gate)
/demote <category>           # in-chat, mod-gated; audit autonomy_demoted_operator

# inspect a category's merged state / threshold / sample count / clean-approval rate
/category-state [category]
```

> **The hard autonomy gate (MEGAPLAN R-4 / C4.2):** AutoCM does NOT go autonomous
> on LLM drafts until the C4.2 voice spike passes (`pass_rate ≥ 0.75`, no register
> `< 0.60`) **AND** Lex signs off. Until then, keep every category at `hitl`
> regardless of disclosure phase.

**Recommended advance order:** `paused` → `silent` (HITL only, watch a week) →
land C4.1 disclosure → `revealed` → per-category `/promote` as each earns it.
**Revert** is always: `/demote` the category (fast) → `/pause-client` (client) →
relay `disable` (substrate) → 48h freeze (post-incident).

---

## 7. Escalation handling

AutoCM routes high-stakes traffic out of the autonomous path (C3.8a):

- **Tier-3 dual-route:** founder + Sable on-call, both notified; `<2 min`
  untouched → `PushNotification` to on-call (C3.8a). Manual trigger: `/punt <ref>`.
- **Threat / whale-inbound / founder-voice-needed:** escalated, not auto-answered.
- **conflict_detected / moderation_flag:** Arf-only routing + auto-silence.
- **Incident mode** (`/incident-mode on|off`, C3.8b): war-room register +
  proactive timed poster + tier-1 suppression. Note: turning incident-mode on
  does **not** clear an active SAFETY §6 freeze (the freeze is the stronger guard).

Operator playbook on an escalation page:
1. Acknowledge in the relay chat (clears the `<2 min` on-call timer).
2. Decide: answer in-voice (HITL `[Edit]`/approve), `/punt` to founder, or
   `/silence <user>` if it is an abuse vector.
3. If the bot already posted something embarrassing → **48h freeze** (5c), then
   `/kb-add` / `/kb-stale` to correct the grounding that produced it.

HITL queue hygiene: drafts auto-expire after 15 min if untouched (C3.5b) — they
are NOT auto-sent; an expired draft means a human missed the window.

---

## 8. Rollback

> **C4.3 exit:** rollback is **rehearsed, not merely documented** — do a dry-run
> on a test client (e.g. a Sable-internal test org) and confirm services come
> back healthy. If a live dry-run is infeasible in the deploy window, label this
> section **"rollback documented (doc-deliverable)"** rather than passing it off
> as a tested behavior gate.

**Image rollback (code regression):**
```sh
# systemd: point the venv/checkout back at the prior tag and restart
sudo -u sable git -C /opt/sable/platform checkout <PRIOR_TAG>
sudo -u sable /opt/sable/venv/bin/pip install -e /opt/sable/platform
sudo systemctl restart sable-platform-relay-bot.service sable-platform-relay-poller.service
sudo -u sable /opt/sable/venv/bin/sable-platform db-health --json   # verify healthy

# compose: redeploy the prior image tag
docker compose -f docker-compose.yaml -f deploy/docker-compose.relay.yaml \
  up -d --no-deps relay-bot relay-poller autocm-batch
```

**Migration note (this deploy is at head 058):** these four chunks add NO
migration. A rollback that needs to step the schema BACK is out of scope here —
the safe rollback is **image-only** at a fixed schema head. If a future deploy
adds 059+, rehearse the Alembic `downgrade` on the test client before prod.

**Dry-run rehearsal (the behavior gate):**
1. On a test org, `/pause-client` (stop publishing).
2. Roll the test deploy back one image/tag (steps above).
3. `sable-platform db-health --json` green; `systemctl status` active; `relay
   status <TEST_ORG>` shows no new errors.
4. `/resume-client` and confirm a HITL draft flows again.

---

## 9. Reconciliation (exactly-once publish)

The publisher is **DB-exactly-once**: claim → external send OUTSIDE the txn →
record publication `ON CONFLICT DO NOTHING` → done; retry/dead with backoff
(R-7 accepts a rare duplicate window). The **sweeper** (`run_sweep`, run every
poller tick) handles drift:

- **Stuck-claim reset:** a claim held > 5 min is recycled.
- **Reconciliation:** before recycling, a best-effort orphan external-message
  find (did the send actually land?) avoids a duplicate; if none found, recycle
  to `retry`.
- **Retention GC:** the §15.5 windows (e.g. 30d job GC by `created_at`).

If you suspect a duplicate or a wedged job:
```sh
SABLE_OPERATOR_ID=ops sable-platform relay status <ORG_ID>     # inflight_jobs, last_error
# a stuck poller tick is self-healing on the next loop; for a hard stop use 5b (disable).
```

---

## 10. Hosting

- **Primary:** Hetzner VPS, alongside the existing SablePlatform services
  (`sable-platform` health-server, `alerts` timer). Same `sable` system user,
  same `/opt/sable/{platform,venv,.env}` layout, same shared Postgres
  (`SABLE_DATABASE_URL`). Relay folds into the single DB — no separate Relay DB.
- **DB:** Postgres on the VPS (migrated 2026-04-09). SQLite remains the
  dev/test target; prod uses `SABLE_DATABASE_URL`.
- **Media / R2:** the shared media layer (`sable_platform/media/`, `R2Store`)
  uploads to Cloudflare R2 and serves via the signed-URL Worker proxy; any
  relay/autocm media reuses that bucket + proxy (no re-host). R2 creds are env
  refs (`secret://…` / `env:…`) per the manifest secrets-as-references rule.
- **Railway:** SableTracking-style worker bots can run on Railway; the relay
  units here target the Hetzner box (cross-region PG needs the keepalive /
  statement_timeout / idle_in_tx_timeout hardening on the libpq URL).
- **Second-client deploy is zero-code (C3.10 / C4.3):** onboard a new tenant by
  seeding rows + a deployment manifest (the `seed_robotmoney.py` shape) — no code
  change, no new unit. The units iterate enabled clients (`autocm_clients.enabled=1`
  / `relay_clients.enabled=1`) automatically.

---

## 11. Quick reference

| I want to… | Do this |
|---|---|
| Start everything | `systemctl enable --now sable-platform-relay-bot relay-poller; systemctl enable --now sable-platform-autocm-batch.timer` |
| Stop ALL AutoCM posting for one client | `/pause-client` (revert: `/resume-client`) |
| Stop ALL substrate publishing for an org | `sable-platform relay disable <ORG>` (revert: `relay enable`) |
| Respond to an embarrassing post | 48h SAFETY §6 freeze (auto-restores) |
| Make a category autonomous | `/promote <cat>` (revert: `/demote <cat>`) |
| Go public about the AI | land C4.1 disclosure → `revealed` phase |
| Check client health | `sable-platform relay status <ORG>` |
| Roll back code | checkout prior tag + reinstall + restart (image-only at head 058) |
| Run a batch job now | `sudo -u sable ... python3 scripts/run_autocm_batch.py [WORKFLOW]` |
