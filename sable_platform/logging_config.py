"""Structured logging configuration for SablePlatform.

Provides a JSON formatter for production and a human-readable default for dev.
Call configure_logging() from the CLI entry point.
"""
from __future__ import annotations

import json
import logging
import time


class StructuredFormatter(logging.Formatter):
    """JSON-line log formatter for production environments."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Include extra fields from record (org_id, run_id, step_name, adapter, etc.)
        for key in ("org_id", "run_id", "step_name", "adapter", "migration"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(json_mode: bool = False, level: int = logging.INFO) -> None:
    """Set up root logging with either structured JSON or human-readable format."""
    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers to avoid duplicates on re-configure
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler()
    if json_mode:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
