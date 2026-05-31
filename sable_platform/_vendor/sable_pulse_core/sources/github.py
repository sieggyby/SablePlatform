"""GitHubSource — project-AGNOSTIC dev-activity reader (Round 2 feature).

Works for ANY repo: give it `owner/name` strings. Pulls recent commits / PRs /
releases via the public GitHub API (optional token raises the rate limit). The
plain-language translation is a SEPARATE, scheduled step (`llm.summarize_dev_activity`)
— one summarization call per run, cached, served to everyone, so it never touches
the per-user `/review` limiter.

Gated off by default in config: narrating a project's dev work publicly needs the
project's blessing (WIP / optics). No public RobotMoney repo exists as of
2026-05-30, so for RM this stays dark until Lex/Tom point us at one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from .base import DevActivity, GitHubSource

GITHUB_API = "https://api.github.com"


class GitHubRepoSource(GitHubSource):
    def __init__(self, repos: list[str], token: str | None = None, lookback_days: int = 7, timeout: float = 20.0):
        self.repos = repos
        self.token = token
        self.lookback_days = lookback_days
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json", "User-Agent": "sable-pulse"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def recent_activity(self) -> list[DevActivity]:
        since = (datetime.now(timezone.utc) - timedelta(days=self.lookback_days)).isoformat()
        out: list[DevActivity] = []
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers()) as client:
            for repo in self.repos:
                commits = await self._get(client, f"/repos/{repo}/commits", {"since": since, "per_page": 50})
                pulls = await self._get(client, f"/repos/{repo}/pulls", {"state": "all", "sort": "updated", "direction": "desc", "per_page": 30})
                releases = await self._get(client, f"/repos/{repo}/releases", {"per_page": 10})
                out.append(DevActivity(
                    repo=repo,
                    since=since,
                    commits=[{
                        "sha": (c.get("sha") or "")[:7],
                        "msg": ((c.get("commit", {}).get("message") or "").splitlines() or [""])[0],
                        "author": (c.get("commit", {}).get("author") or {}).get("name"),
                    } for c in commits],
                    pulls=[{
                        "number": p.get("number"),
                        "title": p.get("title"),
                        "state": p.get("state"),
                        "merged": bool(p.get("merged_at")),
                    } for p in pulls],
                    releases=[{"tag": r.get("tag_name"), "name": r.get("name")} for r in releases],
                ))
        return out

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict) -> list:
        try:
            r = await client.get(f"{GITHUB_API}{path}", params=params)
            if r.status_code == 200:
                return r.json()
        except httpx.HTTPError:
            pass
        return []
