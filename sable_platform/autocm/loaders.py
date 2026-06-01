"""``ClientConfig`` / ``PersonaSpec`` loaders (MEGAPLAN C3.1 §5).

In-memory dataclasses loaded from the 058 ``autocm_clients`` / ``autocm_personas``
tables via relay/db-style query helpers: each function takes an already-open
SQLAlchemy ``Connection`` (the caller owns lifecycle; NO engine is created here),
exactly like ``sable_platform.relay.db`` and ``sable_platform.db.cost``.

These are the typed, read-only views the AutoCM pipeline (C3.2+) loads at boot /
per-message; the JSON config blobs (``surface_config`` / ``kb_config`` /
``config`` / ``calibration_set``) are decoded into plain dicts that the owning
modules interpret. This module owns the SQL + decode; it embeds no business logic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

# The autonomy states enforced by the 058 CHECK on autocm_clients.autonomy_state.
AUTONOMY_STATES = ("hitl", "partial", "auto", "paused")


def _decode_json(blob: Optional[str]) -> dict[str, Any]:
    """Decode a JSON-in-TEXT column into a dict; ``{}`` for NULL/empty/non-object."""
    if not blob:
        return {}
    try:
        value = json.loads(blob)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class PersonaSpec:
    """A persona definition loaded from an ``autocm_personas`` row.

    ``calm_prompt`` / ``reactive_prompt`` are the bimodal NULO system blocks the
    C3.3 drafter prompt-caches; ``calibration_set`` is the voice-calibration
    examples; ``config`` carries persona-level knobs (e.g. catchphrase cadence).
    """

    id: int
    name: str
    description: Optional[str]
    calm_prompt: Optional[str]
    reactive_prompt: Optional[str]
    calibration_set: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClientConfig:
    """A per-client config loaded from an ``autocm_clients`` row.

    ``surface_config`` / ``kb_config`` are the decoded JSON blobs. Per tension #6
    (config convergence, C3.1 §7) the AutoCM ``surface_config`` owns persona / kb /
    categories / ops but does NOT re-declare transport/surface ENABLEMENT — that is
    relay's ``relay_clients.config``. ``persona`` is the joined ``PersonaSpec``
    (None if the client has no persona bound yet).
    """

    id: int
    org_id: str
    persona_id: Optional[int]
    display_name: Optional[str]
    autonomy_state: str
    incident_active: bool
    enabled: bool
    surface_config: dict[str, Any] = field(default_factory=dict)
    kb_config: dict[str, Any] = field(default_factory=dict)
    persona: Optional[PersonaSpec] = None


def load_persona_spec(conn: Connection, persona_id: int) -> Optional[PersonaSpec]:
    """Load a :class:`PersonaSpec` by ``autocm_personas.id`` (or None if absent)."""
    row = conn.execute(
        text(
            "SELECT id, name, description, calm_prompt, reactive_prompt, "
            "       calibration_set, config "
            "FROM autocm_personas WHERE id = :id"
        ),
        {"id": persona_id},
    ).fetchone()
    if row is None:
        return None
    m = row._mapping
    return PersonaSpec(
        id=m["id"],
        name=m["name"],
        description=m["description"],
        calm_prompt=m["calm_prompt"],
        reactive_prompt=m["reactive_prompt"],
        calibration_set=_decode_json(m["calibration_set"]),
        config=_decode_json(m["config"]),
    )


def load_client_config(
    conn: Connection, org_id: str, *, with_persona: bool = True
) -> Optional[ClientConfig]:
    """Load the :class:`ClientConfig` for an org (or None if no autocm_clients row).

    When ``with_persona`` and the client has a ``persona_id``, the joined
    :class:`PersonaSpec` is attached (a second cheap query, mirroring relay/db's
    one-helper-per-read style).
    """
    row = conn.execute(
        text(
            "SELECT id, org_id, persona_id, display_name, autonomy_state, "
            "       incident_active, surface_config, kb_config, enabled "
            "FROM autocm_clients WHERE org_id = :org_id"
        ),
        {"org_id": org_id},
    ).fetchone()
    if row is None:
        return None
    m = row._mapping
    persona: Optional[PersonaSpec] = None
    if with_persona and m["persona_id"] is not None:
        persona = load_persona_spec(conn, m["persona_id"])
    return ClientConfig(
        id=m["id"],
        org_id=m["org_id"],
        persona_id=m["persona_id"],
        display_name=m["display_name"],
        autonomy_state=m["autonomy_state"],
        incident_active=bool(m["incident_active"]),
        enabled=bool(m["enabled"]),
        surface_config=_decode_json(m["surface_config"]),
        kb_config=_decode_json(m["kb_config"]),
        persona=persona,
    )
