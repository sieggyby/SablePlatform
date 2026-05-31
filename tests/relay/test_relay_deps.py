"""C2.1 dependency-gate test: the new relay libraries import cleanly.

This is the C2.2 listener's import precondition (MEGAPLAN C2.1 tests):
``python-telegram-bot`` (``telegram``), ``discord.py`` (``discord``), and
``pydantic-settings`` (``pydantic_settings``) must be importable in the SP
venv — alongside ``sqlalchemy`` (already present). The chunk cannot pass if
``relay/config.py`` imports an undeclared/uninstalled ``pydantic_settings``.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    "module_name",
    ["telegram", "discord", "pydantic_settings", "sqlalchemy"],
)
def test_relay_dependency_imports(module_name: str) -> None:
    mod = importlib.import_module(module_name)
    assert mod is not None


def test_relay_config_imports_pydantic_settings_basesettings() -> None:
    # relay/config.py builds on pydantic_settings.BaseSettings; assert the
    # symbol the config module actually uses is present.
    from pydantic_settings import BaseSettings, SettingsConfigDict

    assert BaseSettings is not None
    assert SettingsConfigDict is not None
