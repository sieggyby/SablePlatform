"""SableRelay — the multi-tenant X ↔ Telegram ↔ Discord bridge.

SableRelay is built as a SablePlatform module (per SableRelay/PLAN.md §5.3):
its tables (the ``relay_*`` family, migration 057) live in SablePlatform's
single ``sable.db`` and reuse the shared connection factory. This package is
the home for the relay listener, poller/publisher feed, query helpers, and the
``pydantic-settings`` config surface.

Module layout (PLAN §5.3)::

    relay/
      __init__.py   # this file
      config.py     # pydantic-settings BaseSettings (RELAY_* env vars)
      db.py         # query helpers reusing SP's connection pool (C2.1)
      schema.py     # SQLAlchemy Table() models mirroring 057_relay.sql
      bot/          # listener (C2.2+)
      feed/         # poller / publisher / sweeper (C2.4)

Logging follows SablePlatform's stdlib-``logging`` house style — relay does
NOT add ``structlog`` (see ``sable_platform/logging_config.py``).
"""
from __future__ import annotations

__all__ = ["RelaySettings", "get_relay_settings"]


def __getattr__(name: str):
    # Lazy re-export so ``import sable_platform.relay`` never forces the
    # pydantic-settings import (and never reads the environment) unless a
    # caller actually asks for the settings surface.
    if name in ("RelaySettings", "get_relay_settings"):
        from sable_platform.relay.config import RelaySettings, get_relay_settings

        return {"RelaySettings": RelaySettings, "get_relay_settings": get_relay_settings}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
