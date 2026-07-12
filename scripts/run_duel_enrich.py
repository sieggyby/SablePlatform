"""Weekly duel-pool enrichment runner (systemd: sable-duel-enrich.service).

FREE — promotes tweets ALREADY in the shared SocialData cache (relay_tweets) into the
/duel game pool. No new SocialData or model spend, so unlike the ambient producer this
may ship enabled. Per-org, config-gated: an org enrolls by setting
``orgs.config_json.duel_enrich = {"enabled": true, "terms": [...], "authors": [...],
"min_popped": 15, "lookback_days": 45, "max_add": 40}``. An org without the block is a
no-op.

Run: python -m scripts.run_duel_enrich [org ...]   (no args = every enrolled org)
"""
from __future__ import annotations

import json
import logging
import sys

from sqlalchemy import text

from sable_platform.db.connection import get_db
from sable_platform.duel_enrichment import enrich_duel_pool

logger = logging.getLogger("duel_enrich")


def _as_obj(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return None
    return v


def _enrolled(conn, only: set[str] | None):
    rows = conn.execute(text("SELECT org_id, config_json FROM orgs")).fetchall()
    for r in rows:
        org = r[0]
        if only and org not in only:
            continue
        cfg = _as_obj(r[1]) or {}
        de = _as_obj(cfg.get("duel_enrich"))
        if isinstance(de, dict) and de.get("enabled"):
            yield org, de


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    only = set(argv) or None
    with get_db() as conn:
        enrolled = list(_enrolled(conn, only))
        if not enrolled:
            logger.info("duel enrich: no enrolled orgs — nothing to do")
            return 0
        for org, de in enrolled:
            terms = tuple(_as_obj(de.get("terms")) or [])
            authors = tuple(_as_obj(de.get("authors")) or [])
            try:
                summary = enrich_duel_pool(
                    conn, org, terms=terms, authors=authors,
                    min_popped=int(de.get("min_popped", 15)),
                    lookback_days=int(de.get("lookback_days", 45)),
                    max_add=int(de.get("max_add", 40)),
                )
                logger.info("duel enrich %s: %s", org, json.dumps(summary))
            except Exception:  # noqa: BLE001 — one org's failure never blocks the rest
                logger.exception("duel enrich FAILED for %s", org)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
