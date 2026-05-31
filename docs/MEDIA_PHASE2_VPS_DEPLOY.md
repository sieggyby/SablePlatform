# Media Phase 2 — VPS go-live note (paint-by-numbers)

Makes the clip-assist feature live on the hosted site. The Worker is already deployed
(`https://sable-media-proxy.siegby.workers.dev`) and the 8 clips are in R2 with `media_url`
in the vault. What's left is: ship the updated code to the VPS + set two env vars so the
live `/clip-suggest` endpoint can **sign** URLs.

VPS: `ssh root@178.156.204.125`. Prod DB is Postgres (`SABLE_DATABASE_URL`).

---

## 0. Commit & push the code first (PREREQUISITE)
All the Phase-1/2 code is currently **uncommitted in the local working trees** — the VPS
`git pull` steps below pull nothing until these are committed + pushed:
- **Sable_Slopper** — clip-suggest endpoint, `media_url` wiring, R2 config, `workspace/card/` tools, tests.
- **SablePlatform** — `sable_platform/media/` (signer + registry), migration 055, schema/alembic/migrate_pg.
- **SableWeb** — `/ops/clip-assist` page/component/route, schemas, client.
- *(The `sable-media-proxy` Worker is already deployed via `wrangler` — no git needed, but commit it for the record.)*
Review the diffs (`git -C <repo> status`), then commit + push each to its branch/remote. (Say the word and I'll do the commits.)

## 1. Get the shared signing secret (local)
The VPS `sable serve` must sign with the **same** secret the Worker verifies with — the one
already in `SableTracking/.env`:
```bash
grep '^MEDIA_SIGN_SECRET=' ~/Projects/SableTracking/.env     # copy the value
```

## 2. Update code on the VPS
```bash
ssh root@178.156.204.125
cd /opt/sable && git pull            # Slopper: clip-suggest endpoint + media_url wiring
cd /opt/sable/platform && git pull   # SablePlatform: sable_platform/media/ (signer) + migration 055
# apply any pending migrations to Postgres (055 media_assets; 056 reply tables if not already):
cd /opt/sable/platform && /opt/sable/venv/bin/alembic upgrade head
```
(No new Python deps needed for the *endpoint* — signing is pure HMAC; `boto3`/the `[r2]`
extra is only for uploads, which run from your laptop, not the VPS.)

## 3. Add the two env vars to the serve EnvironmentFile
`sable-serve.service` reads `/opt/sable/.env`. Append (use the secret from step 1):
```bash
cat >> /opt/sable/.env <<'EOF'
MEDIA_SIGN_SECRET=<paste the value from step 1 — MUST match the Worker>
MEDIA_PROXY_BASE_URL=https://sable-media-proxy.siegby.workers.dev
EOF
chmod 600 /opt/sable/.env
```

## 4. Restart serve
```bash
systemctl restart sable-serve
journalctl -u sable-serve -n 30 --no-pager     # confirm clean start
```

## 5. Redeploy SableWeb (ships /ops/clip-assist)
No new env vars — it calls the endpoint via the existing `SLOPPER_URL`/`SLOPPER_TOKEN`.
```bash
ssh root@178.156.204.125 'cd /opt/sable-web && git pull && docker compose build --no-cache && docker compose down && docker compose up -d'
```

## 6. Verify
- API: `curl -s -X POST https://api.sable.tools/api/v1/write/clip-suggest -H "Authorization: Bearer $SLOPPER_TOKEN" -H "Content-Type: application/json" -d '{"org":"tig","tweet_text":"isnt this just gonna get monopolized?"}'` → JSON with `suggestions[].media_url` containing `?exp=&sig=`.
- UI: open `https://sable.tools/ops/clip-assist`, paste a tweet URL → clips should **play inline**.

## Notes
- **Custom domain (optional):** to use `media.sable.tools` instead of the workers.dev URL, add it as a custom domain on the Worker (dashboard; needs a Zone-scoped token), then change `MEDIA_PROXY_BASE_URL` in `/opt/sable/.env` (+ `SableTracking/.env`) and restart. The signed URLs are minted at read time, so no re-backfill is needed.
- **Rollback:** `git checkout <prev>` in `/opt/sable` + `systemctl restart sable-serve`; `docker compose` redeploy for SableWeb. The Worker + R2 objects are untouched by app rollbacks.
- **SableTracking** stays on `MEDIA_BACKEND=drive` — its R2 cutover (PR-X4) is separate and not required for the clip path.
