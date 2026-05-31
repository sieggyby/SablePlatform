"""AutoCM knowledge base (DESIGN §4 ``kb/``).

The KB family: ``store`` (chunk+embed+index, C3.2a), ``extractor`` /
``onchain`` (source adapters, C3.2b), ``refresher`` (freshness contracts,
C3.2c), and ``constants`` — the slot-fill registry that bridges the vendored
``sable_pulse_core.slotfill`` engine (D-1 reuse, wired in C3.1).
"""
from __future__ import annotations

from .constants import ConstantsKB, build_slotfill_kb

__all__ = ["ConstantsKB", "build_slotfill_kb"]
