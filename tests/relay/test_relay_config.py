"""C2.1 config-surface tests for sable_platform.relay.config.

Covers the RELAY_* settings schema incl. RELAY_DISCORD_CLIENT_ID /
RELAY_DISCORD_CLIENT_SECRET (PLAN §14.5 — present, not deferred), default-None
behaviour (safe to construct with nothing set), and env-var resolution.
"""
from __future__ import annotations

from sable_platform.relay.config import RelaySettings, get_relay_settings


def test_settings_construct_with_no_env_is_all_none(monkeypatch) -> None:
    # Clear any RELAY_* that might leak from the host env / a real .env.
    for var in (
        "RELAY_TG_BOT_TOKEN",
        "RELAY_TG_BOT_USERNAME",
        "RELAY_ADMIN_TG_CHAT_ID",
        "RELAY_DISCORD_BOT_TOKEN",
        "RELAY_DISCORD_CLIENT_ID",
        "RELAY_DISCORD_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    # Disable .env file reading so the test is hermetic.
    s = RelaySettings(_env_file=None)
    assert s.tg_bot_token is None
    assert s.tg_bot_username is None
    assert s.admin_tg_chat_id is None
    assert s.discord_bot_token is None
    assert s.discord_client_id is None
    assert s.discord_client_secret is None


def test_all_six_relay_env_vars_present_in_schema() -> None:
    # The six MEGAPLAN C2.1-enumerated env vars must each map to a field.
    expected_aliases = {
        "RELAY_TG_BOT_TOKEN",
        "RELAY_TG_BOT_USERNAME",
        "RELAY_ADMIN_TG_CHAT_ID",
        "RELAY_DISCORD_BOT_TOKEN",
        "RELAY_DISCORD_CLIENT_ID",
        "RELAY_DISCORD_CLIENT_SECRET",
    }
    aliases = {
        f.alias for f in RelaySettings.model_fields.values() if f.alias is not None
    }
    assert expected_aliases <= aliases


def test_discord_oauth_settings_have_a_home() -> None:
    # PLAN §14.5: the Discord OAuth flow needs CLIENT_ID + CLIENT_SECRET fields.
    s = get_relay_settings(
        RELAY_DISCORD_CLIENT_ID="123456789",
        RELAY_DISCORD_CLIENT_SECRET="shh-secret",
    )
    assert s.discord_client_id == "123456789"
    assert s.discord_client_secret == "shh-secret"


def test_settings_read_from_process_env(monkeypatch) -> None:
    monkeypatch.setenv("RELAY_TG_BOT_TOKEN", "tg-token-xyz")
    monkeypatch.setenv("RELAY_DISCORD_BOT_TOKEN", "discord-token-abc")
    monkeypatch.setenv("RELAY_ADMIN_TG_CHAT_ID", "-100999")
    s = RelaySettings(_env_file=None)
    assert s.tg_bot_token == "tg-token-xyz"
    assert s.discord_bot_token == "discord-token-abc"
    assert s.admin_tg_chat_id == "-100999"


def test_overrides_use_documented_env_var_names() -> None:
    s = get_relay_settings(
        RELAY_TG_BOT_TOKEN="t",
        RELAY_TG_BOT_USERNAME="@sable_relay_bot",
    )
    assert s.tg_bot_token == "t"
    assert s.tg_bot_username == "@sable_relay_bot"
