#!/usr/bin/env python3
"""Add (or remove) the AI-bot watch queries on RobotMoney's reply-opportunity sweep.

Wires @aixbt_agent / @bankrbot / @clanker into RM's `relay_sweep_config.topic_queries`
so the sweep surfaces THEIR on-topic posts as reply opportunities in /ops/reply-assist.
An operator then generates an on-voice reply (the @RobotMoneyAgent "bot-to-bot register"
primed in the persona notes) and posts it manually — there is NO auto-post anywhere in
Sable, so this is suggest-not-autopost by construction.

WHY CURATED `from:HANDLE (keywords)` QUERIES (not bare `from_set`):
The sweep passes each query verbatim to SocialData /twitter/search, which supports the
full search grammar. `from_set` wraps handles as `from:(a OR b)` — ALL their posts, no
filter — which firehoses aixbt's dozens of daily shills. Putting a keyword-gated
`from:` query in `topic_queries` surfaces only the posts that touch RM's thesis
(treasury / savings / machine economy), which is signal, not noise, and cheaper.
The off-ticker filter (guardrails.yaml tickers.appropriate) then drops any pure
other-coin post automatically.

IDEMPOTENT: re-running converges (never double-adds). REVERSIBLE: `--remove` pulls the
bot queries back out, leaving RM's other topic queries untouched.

Usage:
    cd ~/Projects/SablePlatform
    python scripts/add_bot_watch_robotmoney.py --dry-run     # show the plan, write nothing
    python scripts/add_bot_watch_robotmoney.py               # apply (local ~/.sable/sable.db by default)
    SABLE_DATABASE_URL=postgresql://... python scripts/add_bot_watch_robotmoney.py            # PROD (the box)
    SABLE_DATABASE_URL=postgresql://... python scripts/add_bot_watch_robotmoney.py --remove   # undo on PROD
"""
from __future__ import annotations

import argparse
import datetime
import json
import os

from sqlalchemy import text
from sqlalchemy.engine import Connection

from sable_platform.db.connection import get_sa_engine

ORG_ID = "robotmoney"

# Curated bot-watch queries — full SocialData /twitter/search grammar. Each surfaces
# only the bot's posts that intersect RM's thesis, not its whole feed. Keep these
# BYTE-IDENTICAL to the notes.md line pool's targets so add/remove stays a clean set op.
BOT_WATCH_QUERIES = [
    'from:aixbt_agent (treasury OR savings OR "idle cash" OR "machine economy" '
    'OR "agent economy" OR "agent payments" OR stablecoin OR yield)',
    'from:bankrbot (treasury OR savings OR yield OR bank OR wallet OR agent '
    'OR "machine economy")',
    'from:clanker (treasury OR savings OR agent OR "machine economy")',
]


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_topic_queries(conn: Connection) -> list[str]:
    row = conn.execute(
        text("SELECT topic_queries FROM relay_sweep_config WHERE org_id = :o"),
        {"o": ORG_ID},
    ).fetchone()
    if row is None:
        raise SystemExit(
            f"No relay_sweep_config row for org_id={ORG_ID!r} on this DB. "
            "The RM sweep config lives in PROD Postgres — run this with "
            "SABLE_DATABASE_URL=postgresql://... against the box."
        )
    raw = row[0] or "[]"
    try:
        val = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"topic_queries is not valid JSON ({exc}): {raw!r}")
    if not isinstance(val, list):
        raise SystemExit(f"topic_queries is not a JSON array: {raw!r}")
    return [str(q) for q in val]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=None, help="DB URL override (else SABLE_DATABASE_URL / default).")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, write nothing.")
    ap.add_argument("--remove", action="store_true", help="remove the bot-watch queries instead of adding.")
    args = ap.parse_args()

    url = args.url or os.environ.get("SABLE_DATABASE_URL")
    engine = get_sa_engine(url)
    with engine.connect() as conn:
        current = _load_topic_queries(conn)

        if args.remove:
            new = [q for q in current if q not in BOT_WATCH_QUERIES]
            action = "REMOVE"
        else:
            new = list(current)
            for q in BOT_WATCH_QUERIES:
                if q not in new:
                    new.append(q)
            action = "ADD"

        print(f"[{action}] org={ORG_ID}  ({'DRY-RUN' if args.dry_run else 'LIVE'})")
        print("  current topic_queries:")
        for q in current:
            print(f"    - {q}")
        print("  new topic_queries:")
        for q in new:
            print(f"    - {q}")

        if new == current:
            print("  (no change — already in desired state)")
            return
        if args.dry_run:
            print("  --dry-run: no write performed.")
            return

        conn.execute(
            text(
                "UPDATE relay_sweep_config SET topic_queries = :tq, updated_at = :now "
                "WHERE org_id = :o"
            ),
            {"tq": json.dumps(new), "now": _utc_now_iso(), "o": ORG_ID},
        )
        conn.commit()
        print("  written. Next manual sweep will pick it up.")


if __name__ == "__main__":
    main()
