"""Admin commands — ``/register-operator`` + ``/bind-chat`` (C2.3b).

PLAN §4 / §8 admin surface (admin-chat, admin-only). Every member is provisioned
by an existing admin/operator — **no self-registration** (§8).

``/register-operator`` — grant the ``sable_operator`` (or ``admin``) role. The TG
Bot API exposes **no ``getUserByUsername``** and handles are mutable, so a bare
``@handle`` can NOT be resolved to a stable ``tg_user_id`` (§8). The command
therefore supports exactly THREE resolution modes, and **NO bare-handle
resolution** (a bare ``@handle`` with none of the three paths errors with usage
hints):

  1. **Numeric** — ``/register-operator tg_user_id=<numeric> [as=…] [org=…]``: the
     admin supplies the user id directly.
  2. **Self-claim via recent DM** — the target DMs the bot ``/whoami`` first
     (populating ``relay_member_identities``); then ``/register-operator @handle``
     resolves the handle against TG identities SEEN within the last 7 days — exactly
     one match succeeds, else it errors with the candidate list (mode-1 fallback).
  3. **Forwarded message** — the admin forwards a message from the target into the
     admin chat alongside ``/register-operator as=… org=…``; the forwarded message
     carries the original sender's ``from.id`` (passed here as
     ``forwarded_from_user_id``).

``/bind-chat <client> <role>`` — bind the CURRENT chat as
operator/shared/community/broadcast for a client (the partial unique indexes are
honored: at most one active binding per (org,platform,role) and per
(platform,chat)).

Per the LOCKED C2.1 §5.3 layering boundary this module embeds NO raw SQL (every
statement is a named ``relay/db.py`` helper) and runs all writes inside ONE
``immediate_txn`` with NO external send inside it (the §3.1 / C2.2 invariant) —
including the audit row (via :func:`relay_db.write_relay_audit`, the txn-safe
audit insert; the ``db/audit.py`` ``log_audit`` commits and would break the
single-txn contract). Authorization is ALWAYS role-gated via
``relay_member_roles`` (§8): both commands require the CALLER to hold ``admin``
for the org.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn

logger = logging.getLogger(__name__)


# Resolution modes for /register-operator (§8).
MODE_NUMERIC = "numeric"
MODE_SELF_CLAIM = "self_claim"
MODE_FORWARDED = "forwarded"

# Machine-stable outcome codes (asserted by tests / used for the listener reply).
REGISTER_OK = "registered"  # role granted (newly)
REGISTER_ALREADY = "already_registered"  # member already held the role (no-op)
REGISTER_NOT_AUTHORIZED = "not_authorized"  # caller is not an admin for the org
REGISTER_BARE_HANDLE = "bare_handle_unresolvable"  # @handle with no resolution path
REGISTER_NO_MATCH = "no_recent_identity"  # self-claim: no recent identity for handle
REGISTER_AMBIGUOUS = "ambiguous_identity"  # self-claim: 2+ recent matches
REGISTER_BAD_ARGS = "bad_args"  # missing/invalid tg_user_id / role / org

BIND_OK = "bound"
BIND_NOT_AUTHORIZED = "bind_not_authorized"
BIND_UNKNOWN_CLIENT = "unknown_client"  # no relay_clients row for the org
BIND_BAD_ROLE = "bad_role"


# Roles a /register-operator may grant (the §8 chain-of-trust set).
_GRANTABLE_ROLES = ("sable_operator", "admin")
_BINDING_ROLES = ("operator", "shared", "community", "broadcast")


@dataclass(frozen=True)
class RegisterResult:
    """Outcome of ``/register-operator`` (drives the OUTSIDE-the-txn admin reply).

    ``code`` is one of the ``REGISTER_*`` constants. ``mode`` is the resolution
    mode used (one of ``MODE_*``). ``candidates`` lists the ambiguous TG user ids
    when ``code == REGISTER_AMBIGUOUS`` so the admin can pick one for mode 1.
    """

    code: str
    mode: str | None = None
    target_member_id: int | None = None
    org_id: str | None = None
    role: str | None = None
    candidates: tuple = ()


@dataclass(frozen=True)
class BindChatResult:
    """Outcome of ``/bind-chat`` (drives the OUTSIDE-the-txn admin reply)."""

    code: str
    org_id: str | None = None
    platform: str | None = None
    chat_id: str | None = None
    role: str | None = None
    binding_id: int | None = None


def register_operator(
    conn: Connection,
    *,
    org_id: str,
    platform: str,
    admin_external_user_id: str,
    role: str = "sable_operator",
    target_tg_user_id: str | None = None,
    target_handle: str | None = None,
    forwarded_from_user_id: str | None = None,
    target_display_handle: str | None = None,
    admin_handle: str | None = None,
) -> RegisterResult:
    """Grant ``role`` to a target member via one of the THREE §8 resolution modes.

    Mode selection (in §8 order of preference):

      * ``target_tg_user_id`` set → **mode 1 (numeric)**;
      * ``forwarded_from_user_id`` set → **mode 3 (forwarded)** (carries the
        original sender's ``from.id``);
      * ``target_handle`` set (and neither of the above) → **mode 2 (self-claim)**:
        resolve the handle against recently-seen TG identities; exactly one match
        succeeds, zero → ``REGISTER_NO_MATCH``, 2+ → ``REGISTER_AMBIGUOUS``. A bare
        ``@handle`` is NOT resolved against the full identity table (no
        ``getUserByUsername``); the only handle path is this recent-self-claim one.

    With none of the three → ``REGISTER_BARE_HANDLE`` (the usage-hint error). The
    CALLER is admin-gated first; ``role`` must be in
    ``('sable_operator','admin')``. The grant + the audit row are written inside
    ONE ``immediate_txn``.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"unknown relay platform {platform!r}")
    if role not in _GRANTABLE_ROLES:
        return RegisterResult(code=REGISTER_BAD_ARGS, org_id=org_id, role=role)

    with immediate_txn(conn):
        # Admin-gate the CALLER (§8: only an admin registers operators).
        admin_id = relay_db.auto_create_member_identity(
            conn, platform, str(admin_external_user_id), handle=admin_handle
        )
        if not relay_db.member_has_role(conn, admin_id, org_id, "admin"):
            return RegisterResult(code=REGISTER_NOT_AUTHORIZED, org_id=org_id, role=role)

        # Resolve the TARGET via one of the three modes (no bare-handle path).
        mode: str | None = None
        target_member_id: int | None = None

        if target_tg_user_id is not None:
            # Mode 1 — numeric. Auto-create the identity if unseen (audit only).
            mode = MODE_NUMERIC
            target_member_id = relay_db.auto_create_member_identity(
                conn, "telegram", str(target_tg_user_id), handle=target_display_handle
            )
        elif forwarded_from_user_id is not None:
            # Mode 3 — forwarded message (original sender's from.id).
            mode = MODE_FORWARDED
            target_member_id = relay_db.auto_create_member_identity(
                conn, "telegram", str(forwarded_from_user_id), handle=target_display_handle
            )
        elif target_handle is not None:
            # Mode 2 — self-claim via recently-seen identity (NOT bare-handle).
            mode = MODE_SELF_CLAIM
            resolved_id, candidates = relay_db.resolve_recent_telegram_identity(
                conn, target_handle, within_days=7
            )
            if resolved_id is None:
                if candidates:
                    return RegisterResult(
                        code=REGISTER_AMBIGUOUS,
                        mode=mode,
                        org_id=org_id,
                        role=role,
                        candidates=tuple(candidates),
                    )
                return RegisterResult(
                    code=REGISTER_NO_MATCH, mode=mode, org_id=org_id, role=role
                )
            target_member_id = resolved_id
        else:
            # No resolution path supplied → the §8 usage-hint error.
            return RegisterResult(code=REGISTER_BARE_HANDLE, org_id=org_id, role=role)

        granted = relay_db.grant_member_role(
            conn, target_member_id, org_id, role, granted_by=admin_id
        )
        relay_db.write_relay_audit(
            conn,
            actor=admin_handle or str(admin_external_user_id),
            action="relay.register_operator",
            org_id=org_id,
            entity_id=str(target_member_id),
            detail={
                "mode": mode,
                "role": role,
                "granted": granted,
                "target_member_id": target_member_id,
                "granted_by_member_id": admin_id,
            },
        )
        return RegisterResult(
            code=REGISTER_OK if granted else REGISTER_ALREADY,
            mode=mode,
            target_member_id=target_member_id,
            org_id=org_id,
            role=role,
        )


def bind_chat(
    conn: Connection,
    *,
    org_id: str,
    platform: str,
    chat_id: str,
    role: str,
    admin_external_user_id: str,
    title: str | None = None,
    admin_handle: str | None = None,
) -> BindChatResult:
    """``/bind-chat <client> <role>`` — bind the current chat for a client (admin).

    Admin-gates the caller, validates ``role`` against the binding CHECK set
    ``('operator','shared','community','broadcast')`` and the client's existence,
    then binds the chat (re-pointing the role / displacing any other role on the
    chat per the partial unique indexes) and writes an audit row — all in ONE
    ``immediate_txn``.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"unknown relay platform {platform!r}")
    if role not in _BINDING_ROLES:
        return BindChatResult(
            code=BIND_BAD_ROLE, org_id=org_id, platform=platform, chat_id=chat_id, role=role
        )

    with immediate_txn(conn):
        admin_id = relay_db.auto_create_member_identity(
            conn, platform, str(admin_external_user_id), handle=admin_handle
        )
        # Client existence is checked FIRST: an org with no relay_clients row can
        # have no admin grant either (relay_member_roles.org_id FKs relay_clients),
        # so the unknown-client error must precede the admin-gate to be reachable.
        if not relay_db.relay_client_exists(conn, org_id):
            return BindChatResult(
                code=BIND_UNKNOWN_CLIENT,
                org_id=org_id,
                platform=platform,
                chat_id=chat_id,
                role=role,
            )
        if not relay_db.member_has_role(conn, admin_id, org_id, "admin"):
            return BindChatResult(
                code=BIND_NOT_AUTHORIZED,
                org_id=org_id,
                platform=platform,
                chat_id=chat_id,
                role=role,
            )
        binding_id = relay_db.bind_chat(
            conn,
            org_id=org_id,
            platform=platform,
            chat_id=str(chat_id),
            role=role,
            title=title,
        )
        relay_db.write_relay_audit(
            conn,
            actor=admin_handle or str(admin_external_user_id),
            action="relay.bind_chat",
            org_id=org_id,
            entity_id=str(chat_id),
            detail={
                "platform": platform,
                "chat_id": str(chat_id),
                "role": role,
                "binding_id": binding_id,
                "bound_by_member_id": admin_id,
            },
        )
        return BindChatResult(
            code=BIND_OK,
            org_id=org_id,
            platform=platform,
            chat_id=str(chat_id),
            role=role,
            binding_id=binding_id,
        )


__all__ = [
    "RegisterResult",
    "BindChatResult",
    "register_operator",
    "bind_chat",
    "MODE_NUMERIC",
    "MODE_SELF_CLAIM",
    "MODE_FORWARDED",
    "REGISTER_OK",
    "REGISTER_ALREADY",
    "REGISTER_NOT_AUTHORIZED",
    "REGISTER_BARE_HANDLE",
    "REGISTER_NO_MATCH",
    "REGISTER_AMBIGUOUS",
    "REGISTER_BAD_ARGS",
    "BIND_OK",
    "BIND_NOT_AUTHORIZED",
    "BIND_UNKNOWN_CLIENT",
    "BIND_BAD_ROLE",
]
