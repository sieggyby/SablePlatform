"""Slot-fill constants registry (DESIGN §4 ``kb/constants``) — D-1 reuse.

This is the AutoCM-native bridge over the VENDORED ``sable_pulse_core.slotfill``
engine (the D-1 reuse wired in C3.1): the irreducible, NEVER-LLM-generated facts
(contract addresses, audit URLs, official handles) answered with zero LLM.

The per-client constants live in the 058 ``autocm_kb_constants`` table
(``PRIMARY KEY (client_id, key)``). C3.2a populates that table and the full
glossary leg; C3.1 ships the bridge: load a client's constants into a
``SlotFillKB`` and expose the deterministic ``match_slotfill`` / ``constant``
lookups. The slot-fill ROUTING patterns (which question → which key) are the
vendored engine's; AutoCM supplies only the per-tenant VALUES.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

# D-1 reuse: the vendored deterministic slot-fill engine (NOT the sibling repo).
from sable_platform._vendor.sable_pulse_core import SlotFillKB


def _load_client_constants(conn: Connection, client_id: int) -> dict[str, str]:
    """Load ``autocm_kb_constants`` (key→value) for a client into a flat dict."""
    rows = conn.execute(
        text(
            "SELECT key, value FROM autocm_kb_constants "
            "WHERE client_id = :client_id ORDER BY key"
        ),
        {"client_id": client_id},
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def build_slotfill_kb(
    conn: Connection, client_id: int, *, glossary: Optional[dict[str, str]] = None
) -> SlotFillKB:
    """Build a vendored :class:`SlotFillKB` from a client's ``autocm_kb_constants`` rows.

    The glossary leg is populated by C3.2a (from ``autocm_kb_chunks`` definitional
    content); C3.1 wires the constants leg and accepts an optional glossary so the
    bridge is complete and testable now.
    """
    constants = _load_client_constants(conn, client_id)
    return SlotFillKB(constants=constants, glossary=glossary or {})


@dataclass
class ConstantsKB:
    """Thin per-client facade over the vendored ``SlotFillKB`` (D-1 reuse).

    The AutoCM pipeline (C3.2a+) holds a ``ConstantsKB`` per client and asks it the
    deterministic, zero-LLM questions; the actual routing/matching is delegated to
    the vendored engine so the deterministic-reply contract cannot diverge.
    """

    client_id: int
    kb: SlotFillKB

    @classmethod
    def load(
        cls, conn: Connection, client_id: int, *, glossary: Optional[dict[str, str]] = None
    ) -> "ConstantsKB":
        return cls(client_id=client_id, kb=build_slotfill_kb(conn, client_id, glossary=glossary))

    def constant(self, key: str) -> Optional[str]:
        """Literal lookup of an irreducible fact (delegates to vendored engine)."""
        return self.kb.constant(key)

    def match_slotfill(self, text: str) -> Optional[Tuple[str, str]]:
        """Map free text → (key, value) via the vendored slot-fill router, or None."""
        return self.kb.match_slotfill(text)

    def match_glossary(self, text: str) -> Optional[Tuple[str, str]]:
        """Map free text → (term, definition) via the vendored glossary, or None."""
        return self.kb.match_glossary(text)


__all__ = ["ConstantsKB", "build_slotfill_kb", "SlotFillKB"]
