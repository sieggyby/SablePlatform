"""``/sweep-config`` — manage the per-client reply-opportunity sweep config (C2.3b + mig 062).

SableRelay/REPLY_OPPORTUNITY_FEED_PLAN.md §2 #2 / §3.4: the per-client curated
query set (mention handles + topic queries + from-set + operator handles +
enabled + expiry_hours) is managed by an ADMIN via a permission-gated TG bot
command — there is NO Twitter signal account. The daily cost cap is deliberately
NOT settable here: it lives in ``relay_clients.config.polling.daily_cost_cap_usd``
(the single cap source, resolved by ``get_daily_cost_cap``).

This mirrors the :mod:`~sable_platform.relay.bot.handlers.admin` pattern exactly:

  * a pure, directly-testable command function (:func:`set_sweep_config`) that
    runs ALL of its DB side effects inside ONE ``immediate_txn`` (the §3.1 / C2.2
    audit invariant), embeds NO raw SQL (every statement is a named
    ``relay/db.py`` helper — the C2.1 §5.3 layering boundary), and writes the
    audit row via :func:`relay_db.write_relay_audit` (the txn-safe insert; the
    ``db/audit.py`` ``log_audit`` commits and would break the single-txn
    contract);
  * authorization ALWAYS role-gated via ``relay_member_roles`` (§8): the caller
    must hold ``admin`` for the org;
  * a frozen result dataclass driving the OUTSIDE-the-txn ack reply;
  * a thin :func:`register` that wires the command onto the C2.7 registry's
    command path (verb ``"sweep-config"``), resolving the caller's
    ``relay_members`` id from the platform identity before delegating.

"Lexicon-seeded where natural": when a brand-new config is created and no
``mention_handles`` are supplied, the org's own resolved X handle
(``COALESCE(relay_clients.x_handle_override, orgs.twitter_handle)``) is seeded as
the default mention target — the one mention every client always wants.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.registry import CommandEvent, RelayHandlerRegistry
from sable_platform.relay.bot.txn import immediate_txn

logger = logging.getLogger(__name__)

# The verb the registry routes to this handler (no leading '/').
SWEEP_CONFIG_COMMAND = "sweep-config"

# Machine-stable outcome codes (asserted by tests / used for the listener reply).
SWEEP_CONFIG_OK = "sweep_config_set"
SWEEP_CONFIG_NOT_AUTHORIZED = "not_authorized"  # caller is not an admin for the org
SWEEP_CONFIG_UNKNOWN_CLIENT = "unknown_client"  # no relay_clients row for the org
SWEEP_CONFIG_NO_FIELDS = "no_fields"            # nothing to set
SWEEP_CONFIG_BAD_VALUE = "bad_value"            # malformed enabled / expiry_hours

# The settable fields (the cost cap is intentionally NOT one of them).
SETTABLE_LIST_FIELDS = ("mention_handles", "topic_queries", "from_set", "operator_handles")


@dataclass(frozen=True)
class SweepConfigResult:
    """Outcome of ``/sweep-config`` (drives the OUTSIDE-the-txn admin reply).

    ``code`` is one of the ``SWEEP_CONFIG_*`` constants. ``fields_set`` lists the
    config fields that were written (so the ack can echo them). ``created`` is
    True when this call minted the config row (vs. updated an existing one).
    """

    code: str
    org_id: str | None = None
    fields_set: tuple = ()
    created: bool = False


def set_sweep_config(
    conn: Connection,
    *,
    org_id: str,
    platform: str,
    admin_external_user_id: str,
    admin_handle: str | None = None,
    mention_handles: list[str] | None = None,
    topic_queries: list[str] | None = None,
    from_set: list[str] | None = None,
    operator_handles: list[str] | None = None,
    enabled: bool | None = None,
    expiry_hours: int | None = None,
) -> SweepConfigResult:
    """Admin-set the per-client sweep config (the §3.4 curated query set).

    Only the fields passed (non-``None``) are written; the rest keep their
    current value (or, on first creation, the column default). The daily cost cap
    is NOT settable here (one cap source — ``relay_clients.config.polling``). The
    role-gate + the config write + the audit row all run inside ONE
    ``immediate_txn``. Returns a :class:`SweepConfigResult`.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"unknown relay platform {platform!r}")

    # Validate the scalar fields BEFORE opening the txn so a bad value never
    # writes a partial config.
    if expiry_hours is not None and (not isinstance(expiry_hours, int) or expiry_hours <= 0):
        return SweepConfigResult(code=SWEEP_CONFIG_BAD_VALUE, org_id=org_id)

    field_updates: dict[str, str | int] = {}
    fields_set: list[str] = []
    if mention_handles is not None:
        field_updates["mention_handles"] = json.dumps(mention_handles)
        fields_set.append("mention_handles")
    if topic_queries is not None:
        field_updates["topic_queries"] = json.dumps(topic_queries)
        fields_set.append("topic_queries")
    if from_set is not None:
        field_updates["from_set"] = json.dumps(from_set)
        fields_set.append("from_set")
    if operator_handles is not None:
        field_updates["operator_handles"] = json.dumps(operator_handles)
        fields_set.append("operator_handles")
    if enabled is not None:
        field_updates["enabled"] = 1 if enabled else 0
        fields_set.append("enabled")
    if expiry_hours is not None:
        field_updates["expiry_hours"] = int(expiry_hours)
        fields_set.append("expiry_hours")

    if not fields_set:
        return SweepConfigResult(code=SWEEP_CONFIG_NO_FIELDS, org_id=org_id)

    with immediate_txn(conn):
        # Client existence is checked FIRST: an org with no relay_clients row can
        # have no admin grant either (relay_member_roles.org_id FKs relay_clients),
        # so the unknown-client error must precede the admin-gate to be reachable.
        if not relay_db.relay_client_exists(conn, org_id):
            return SweepConfigResult(code=SWEEP_CONFIG_UNKNOWN_CLIENT, org_id=org_id)

        admin_id = relay_db.auto_create_member_identity(
            conn, platform, str(admin_external_user_id), handle=admin_handle
        )
        if not relay_db.member_has_role(conn, admin_id, org_id, "admin"):
            return SweepConfigResult(code=SWEEP_CONFIG_NOT_AUTHORIZED, org_id=org_id)

        created = relay_db.get_sweep_config(conn, org_id) is None

        # Lexicon-seed: on first creation with no explicit mention_handles, seed
        # the org's own resolved X handle as the default mention target.
        if created and "mention_handles" not in field_updates:
            own_handle = relay_db.resolve_x_handle(conn, org_id)
            if own_handle:
                field_updates["mention_handles"] = json.dumps([own_handle])
                fields_set.append("mention_handles(seeded)")

        relay_db.upsert_sweep_config(
            conn,
            org_id=org_id,
            mention_handles=field_updates.get("mention_handles"),
            topic_queries=field_updates.get("topic_queries"),
            from_set=field_updates.get("from_set"),
            operator_handles=field_updates.get("operator_handles"),
            enabled=field_updates.get("enabled"),
            expiry_hours=field_updates.get("expiry_hours"),
        )
        relay_db.write_relay_audit(
            conn,
            actor=admin_handle or str(admin_external_user_id),
            action="relay.sweep_config",
            org_id=org_id,
            entity_id=org_id,
            detail={
                "fields_set": fields_set,
                "created": created,
                "set_by_member_id": admin_id,
            },
        )
        return SweepConfigResult(
            code=SWEEP_CONFIG_OK,
            org_id=org_id,
            fields_set=tuple(fields_set),
            created=created,
        )


def _parse_argstr(argstr: str) -> dict:
    """Parse ``key=value`` tokens from a ``/sweep-config`` arg tail.

    Supported keys: ``mention_handles`` / ``topic_queries`` / ``from_set`` /
    ``operator_handles`` (comma-separated lists), ``enabled`` (on/off/true/false/
    1/0), ``expiry_hours`` (positive int). Unknown keys are ignored. Returns a
    kwargs dict suitable for :func:`set_sweep_config` (``expiry_hours`` may be the
    sentinel string ``"__bad__"`` so the caller surfaces SWEEP_CONFIG_BAD_VALUE).
    """
    out: dict = {}
    for tok in argstr.split():
        if "=" not in tok:
            continue
        key, _, value = tok.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key in SETTABLE_LIST_FIELDS:
            out[key] = [v for v in (s.strip() for s in value.split(",")) if v]
        elif key == "enabled":
            out["enabled"] = value.lower() in ("1", "true", "on", "yes")
        elif key == "expiry_hours":
            try:
                out["expiry_hours"] = int(value)
            except ValueError:
                out["expiry_hours"] = "__bad__"  # surfaced as SWEEP_CONFIG_BAD_VALUE
    return out


def register(registry: RelayHandlerRegistry) -> None:
    """Wire ``/sweep-config`` onto the C2.7 command registry (verb-scoped).

    Registers a per-verb consumer (so it never claims the whole command surface).
    The consumer resolves the caller's ``relay_members`` id from the platform
    identity, parses the ``key=value`` arg tail, and delegates to
    :func:`set_sweep_config` (which owns the admin-gate + the immediate_txn).
    """

    def _consumer(evt: CommandEvent) -> None:
        if evt.org_id is None or evt.external_user_id is None:
            logger.debug("relay sweep-config: missing org_id/external_user_id; ignored")
            return
        parsed = _parse_argstr(evt.argstr)
        if parsed.get("expiry_hours") == "__bad__":
            parsed["expiry_hours"] = -1  # forces SWEEP_CONFIG_BAD_VALUE
        set_sweep_config(
            registry._conn,  # the listener's single long-lived SP-pool Connection
            org_id=evt.org_id,
            platform=evt.platform,
            admin_external_user_id=evt.external_user_id,
            **parsed,
        )

    registry.register_command_handler(_consumer, command=SWEEP_CONFIG_COMMAND)


__all__ = [
    "SweepConfigResult",
    "set_sweep_config",
    "register",
    "SWEEP_CONFIG_COMMAND",
    "SWEEP_CONFIG_OK",
    "SWEEP_CONFIG_NOT_AUTHORIZED",
    "SWEEP_CONFIG_UNKNOWN_CLIENT",
    "SWEEP_CONFIG_NO_FIELDS",
    "SWEEP_CONFIG_BAD_VALUE",
]
