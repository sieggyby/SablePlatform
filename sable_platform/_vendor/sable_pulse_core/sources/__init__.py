"""Data sources behind a common interface (the `DataSource` seam).

MVP wires the public/generative implementations. Round 2 swaps a `RealFeedSource`
(Lex's committee feed + live vault state) behind the SAME interfaces — the
template / handler / Telegram layers never change.
"""
from __future__ import annotations
