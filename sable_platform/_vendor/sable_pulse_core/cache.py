"""Last-good JSON file cache with TTL.

Never raises on read — a corrupt/missing file returns None. Stale entries are
returned flagged so callers can serve them when a live fetch fails (e.g. the
US-geoblocked committee page). Atomic write via temp-file replace.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CacheEntry:
    value: Any
    stored_at: float
    stale: bool


class JsonCache:
    def __init__(self, path: str | Path, ttl_seconds: int = 900, clock=time.time):
        self.path = Path(path)
        self.ttl = ttl_seconds
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self.path)

    def get(self, key: str) -> CacheEntry | None:
        rec = self._read().get(key)
        if not rec:
            return None
        age = self._clock() - rec["stored_at"]
        return CacheEntry(rec["value"], rec["stored_at"], age > self.ttl)

    def set(self, key: str, value: Any) -> None:
        data = self._read()
        data[key] = {"value": value, "stored_at": self._clock()}
        self._write(data)
