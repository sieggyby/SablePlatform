from __future__ import annotations

import pytest

from sable_platform import socialdata_balance


@pytest.fixture(autouse=True)
def _relay_socialdata_balance_allows(monkeypatch):
    monkeypatch.setattr(socialdata_balance, "get_balance_usd", lambda *a, **k: 100.0)
