"""SableRelay configuration surface (pydantic-settings).

Loads the ``RELAY_*`` settings from SablePlatform's existing ``.env`` (dev) and
the process environment (prod / VPS), per SableRelay/PLAN.md §7 ("Config:
pydantic-settings + existing SablePlatform .env"). This is the settings *home*
for the listener (C2.2) and the Discord OAuth flow (PLAN §14.5).

Per MEGAPLAN C2.1, ``RELAY_DISCORD_CLIENT_ID`` / ``RELAY_DISCORD_CLIENT_SECRET``
are included here so the Discord OAuth flow has a settings home (NOT deferred).

All fields are Optional so importing the module — and instantiating the
settings — never raises when an env var is absent (the C2.2 listener decides at
*runtime* whether the tokens it needs are present, rather than failing at
import time). Field names map to env vars case-insensitively via the
``RELAY_`` prefix is NOT used as a pydantic prefix — instead each field is
explicitly aliased to its documented ``RELAY_*`` env var so the public env
contract in MEGAPLAN/PLAN is the literal source of truth.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RelaySettings(BaseSettings):
    """Typed view over the ``RELAY_*`` environment / ``.env`` configuration.

    Every field is optional: a missing token yields ``None`` rather than a
    validation error, so ``RelaySettings()`` is safe to construct in any
    context (tests, CLI, import). The listener validates required tokens at
    startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram listener credentials.
    tg_bot_token: str | None = Field(default=None, alias="RELAY_TG_BOT_TOKEN")
    tg_bot_username: str | None = Field(default=None, alias="RELAY_TG_BOT_USERNAME")
    admin_tg_chat_id: str | None = Field(default=None, alias="RELAY_ADMIN_TG_CHAT_ID")

    # Discord listener + OAuth credentials (PLAN §14.5).
    discord_bot_token: str | None = Field(default=None, alias="RELAY_DISCORD_BOT_TOKEN")
    discord_client_id: str | None = Field(default=None, alias="RELAY_DISCORD_CLIENT_ID")
    discord_client_secret: str | None = Field(
        default=None, alias="RELAY_DISCORD_CLIENT_SECRET"
    )


def get_relay_settings(**overrides: object) -> RelaySettings:
    """Build a :class:`RelaySettings`, optionally overriding fields by env-var name.

    ``overrides`` accept the documented ``RELAY_*`` aliases (e.g.
    ``RELAY_TG_BOT_TOKEN="..."``) because ``RelaySettings`` is
    ``populate_by_name``-via-alias; this is the seam tests use to inject values
    without mutating ``os.environ``.
    """
    return RelaySettings(**overrides)  # type: ignore[arg-type]
