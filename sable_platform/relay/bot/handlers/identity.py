"""PII / identity-integrity commands — ``/forget-me`` + ``/link-x`` (C2.3c).

This is the SableRelay PLAN §15.5 (PII) + §8 (identity-linking collisions / admin
merge) handler half. Both flows are member/identity operations rather than the
amplify/quorum publish path, so they live together as one auditable unit.

``/forget-me`` — PII deletion (§15.5: "deletion via ``/forget-me`` removes
preferences and identity rows but keeps audit references via member_id
(anonymized)"). The handler resolves the CALLER's own member (self-serve — a
member forgets themselves), then:

  * DELETEs the member's ``relay_member_preferences`` rows (opt-in / mute PII),
  * DELETEs the member's ``relay_member_identities`` rows (the external_user_id +
    handle PII — after this the member is no longer resolvable from any external
    id; a future interaction auto-creates a FRESH member), and
  * ANONYMIZES the ``relay_members`` row (clears ``display_name``) but KEEPS it,
    so every ``member_id`` audit reference (submissions, reactions, reply
    opportunities, audit rows) still points at a now-nameless row.

``/link-x`` — identity link (§8 / Phase 4.6+): adds ``platform='x'`` to an
existing member. The ``(platform, external_user_id)`` PK enforces X-id uniqueness
mechanically, so the handler enforces the §8 collision rule explicitly: if the X
id is already linked to a DIFFERENT ``member_id`` it REJECTS with the §8 message
("X account @handle is already linked to a different SableRelay member; ask an
admin to merge.") — there is NO v1 self-serve merge UI. The only way to resolve a
collision is :func:`admin_merge_x_identity`, an admin-only DB re-assignment.

**NOTE (the "dead-on-arrival until C2.4" caveat lives here):** ``/link-x`` has NO
live consumer in C2.3c — its only downstream consumer is C2.4's Phase-4.6
reply-tracking match against ``relay_member_identities`` where ``platform='x'``.
So this module ships the identity-linking command, but its tests assert ONLY the
collision-rejection + admin-merge invariants in isolation (no reply-tracking
assertion — that is C2.4 / C3.10).

Per the LOCKED C2.1 §5.3 layering boundary this module embeds NO raw SQL (every
statement is a named ``relay/db.py`` helper) and runs all writes inside ONE
``immediate_txn`` with NO external send inside it (the §3.1 / C2.2 invariant); it
returns a small result object the listener uses to reply OUTSIDE the txn. The
audit row is written via :func:`relay_db.write_relay_audit` (the txn-safe audit
insert; ``db/audit.py``'s ``log_audit`` commits and would break the single-txn
contract).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.engine import Connection

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.txn import immediate_txn

logger = logging.getLogger(__name__)


# Machine-stable outcome codes (asserted by tests / used for the listener reply).
FORGET_OK = "forgotten"  # preferences + identities removed, member anonymized
FORGET_NOTHING = "nothing_to_forget"  # caller has no identity (already gone)

LINK_OK = "linked"  # platform='x' identity added (newly)
LINK_ALREADY = "already_linked"  # X id already links to THIS member (idempotent)
LINK_COLLISION = "x_already_linked_elsewhere"  # X id links a DIFFERENT member (§8)
LINK_NO_MEMBER = "no_telegram_identity"  # caller has no TG identity to link onto

MERGE_OK = "merged"  # admin re-pointed the X id to the intended member
MERGE_NOT_AUTHORIZED = "merge_not_authorized"  # caller is not admin for the org
MERGE_UNKNOWN_TARGET = "merge_unknown_target"  # intended member_id does not exist
MERGE_NOT_LINKED = "merge_x_not_linked"  # the X id was not linked to anyone


# The §8 collision rejection message (handle substituted at the call site). Kept
# as a template constant so the listener and the tests share one exact string.
COLLISION_MESSAGE_TEMPLATE = (
    "X account @{handle} is already linked to a different SableRelay member; "
    "ask an admin to merge."
)


@dataclass(frozen=True)
class ForgetResult:
    """Outcome of ``/forget-me`` (drives the OUTSIDE-the-txn DM reply).

    ``code`` is one of the ``FORGET_*`` constants. ``member_id`` is the member that
    was anonymized (retained for the audit trail; ``None`` when there was nothing
    to forget). ``preferences_deleted`` / ``identities_deleted`` are the row counts
    removed (so the listener / tests can confirm the PII surface was cleared).
    """

    code: str
    member_id: int | None = None
    preferences_deleted: int = 0
    identities_deleted: int = 0


@dataclass(frozen=True)
class LinkXResult:
    """Outcome of ``/link-x`` (drives the OUTSIDE-the-txn reply).

    ``code`` is one of the ``LINK_*`` constants. ``member_id`` is the member the X
    id was (or would be) linked onto. On a collision (``LINK_COLLISION``)
    ``existing_member_id`` is the OTHER member the X id already links to and
    ``message`` is the rendered §8 rejection string.
    """

    code: str
    member_id: int | None = None
    x_user_id: str | None = None
    existing_member_id: int | None = None
    message: str | None = None


@dataclass(frozen=True)
class MergeResult:
    """Outcome of the admin-only X-identity merge (drives the OUTSIDE-the-txn reply).

    ``code`` is one of the ``MERGE_*`` constants. ``x_user_id`` is the X id that was
    re-pointed; ``from_member_id`` / ``to_member_id`` are the old/new owners.
    """

    code: str
    x_user_id: str | None = None
    from_member_id: int | None = None
    to_member_id: int | None = None


def forget_me(
    conn: Connection,
    *,
    platform: str,
    external_user_id: str,
) -> ForgetResult:
    """``/forget-me`` — delete the caller's PII, keep anonymized audit refs (§15.5).

    Resolves the caller's member from ``(platform, external_user_id)``. If they
    have no identity (never interacted, or already forgotten) returns
    ``FORGET_NOTHING`` and writes nothing. Otherwise, in ONE ``immediate_txn``:
    deletes the member's preferences + identity rows, anonymizes the
    ``relay_members`` row (display_name → NULL, id retained), and writes an audit
    row keyed by ``member_id`` ONLY (the actor is the anonymized ``member:<id>``,
    never the handle / external id — the audit reference itself must not re-leak
    the PII just deleted).
    """
    if platform not in ("telegram", "discord", "x"):
        raise ValueError(
            f"unknown relay platform {platform!r}; expected 'telegram', 'discord' or 'x'"
        )
    with immediate_txn(conn):
        member_id = relay_db.resolve_member_id(conn, platform, str(external_user_id))
        if member_id is None:
            return ForgetResult(code=FORGET_NOTHING)
        prefs = relay_db.delete_member_preferences(conn, member_id)
        idents = relay_db.delete_member_identities(conn, member_id)
        relay_db.anonymize_member(conn, member_id)
        # Audit actor is the anonymized member ref — NEVER the handle/external id
        # (re-logging the PII we just deleted would defeat §15.5). detail carries
        # only the member_id + row counts.
        relay_db.write_relay_audit(
            conn,
            actor=f"member:{member_id}",
            action="relay.forget_me",
            org_id=None,
            entity_id=str(member_id),
            detail={
                "member_id": member_id,
                "preferences_deleted": prefs,
                "identities_deleted": idents,
                "anonymized": True,
            },
        )
        return ForgetResult(
            code=FORGET_OK,
            member_id=member_id,
            preferences_deleted=prefs,
            identities_deleted=idents,
        )


def link_x(
    conn: Connection,
    *,
    platform: str,
    external_user_id: str,
    x_user_id: str,
    x_handle: str | None = None,
    handle: str | None = None,
) -> LinkXResult:
    """``/link-x`` — add ``platform='x'`` to the caller's existing member (§8).

    The caller is resolved from their TG/Discord ``(platform, external_user_id)``
    identity (the member ``/link-x`` runs against — a member must already exist;
    linking grants no role and creates no member, so a caller with no identity
    gets ``LINK_NO_MEMBER``). Then:

      * if the X id is unlinked → insert the ``platform='x'`` row → ``LINK_OK``;
      * if it already links to THIS member → ``LINK_ALREADY`` (idempotent re-link,
        handle refreshed);
      * if it links to a DIFFERENT member → ``LINK_COLLISION`` (the §8 rejection +
        rendered message + the OTHER member's id), and NOTHING is written — the
        only resolution is the admin merge (no v1 self-serve UI).

    All in ONE ``immediate_txn``; an audit row is written on a successful link.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(
            f"unknown relay platform {platform!r}; expected 'telegram' or 'discord'"
        )
    with immediate_txn(conn):
        member_id = relay_db.resolve_member_id(conn, platform, str(external_user_id))
        if member_id is None:
            # No self-creation here: linking is "add X to an EXISTING member" (§8).
            return LinkXResult(code=LINK_NO_MEMBER, x_user_id=str(x_user_id))

        existing = relay_db.get_x_identity(conn, str(x_user_id))
        if existing is not None:
            if int(existing["member_id"]) == int(member_id):
                # Idempotent re-link — refresh the display handle, no new row.
                if x_handle is not None:
                    relay_db.reassign_x_identity(
                        conn, str(x_user_id), new_member_id=member_id, handle=x_handle
                    )
                return LinkXResult(
                    code=LINK_ALREADY, member_id=member_id, x_user_id=str(x_user_id)
                )
            # §8 collision: the X id already links a DIFFERENT member. REJECT.
            # No write — the only resolution is the admin merge.
            return LinkXResult(
                code=LINK_COLLISION,
                member_id=member_id,
                x_user_id=str(x_user_id),
                existing_member_id=int(existing["member_id"]),
                message=COLLISION_MESSAGE_TEMPLATE.format(
                    handle=x_handle or existing.get("handle") or str(x_user_id)
                ),
            )

        relay_db.link_x_identity(conn, member_id, str(x_user_id), handle=x_handle)
        relay_db.write_relay_audit(
            conn,
            actor=handle or str(external_user_id),
            action="relay.link_x",
            org_id=None,
            entity_id=str(member_id),
            detail={
                "member_id": member_id,
                "x_user_id": str(x_user_id),
                "x_handle": x_handle,
            },
        )
        return LinkXResult(
            code=LINK_OK, member_id=member_id, x_user_id=str(x_user_id)
        )


def admin_merge_x_identity(
    conn: Connection,
    *,
    org_id: str,
    platform: str,
    admin_external_user_id: str,
    x_user_id: str,
    target_member_id: int,
    admin_handle: str | None = None,
    x_handle: str | None = None,
) -> MergeResult:
    """Admin-only §8 merge — re-point a linked X id to the intended member.

    The §8 collision resolution path ("Merging is an admin-only DB operation (no
    v1 self-serve UI)"). Admin-gates the CALLER (must hold ``admin`` for the org),
    verifies the intended ``target_member_id`` exists, then re-assigns the X
    identity's ``member_id`` from whatever member it currently links to →
    ``target_member_id``. Returns:

      * ``MERGE_NOT_AUTHORIZED`` if the caller is not an admin for the org;
      * ``MERGE_UNKNOWN_TARGET`` if the intended member does not exist;
      * ``MERGE_NOT_LINKED`` if the X id is not linked to anyone (nothing to merge);
      * ``MERGE_OK`` with ``from_member_id`` / ``to_member_id`` on success.

    All in ONE ``immediate_txn`` with an in-txn audit row on success.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(
            f"unknown relay platform {platform!r}; expected 'telegram' or 'discord'"
        )
    with immediate_txn(conn):
        admin_id = relay_db.auto_create_member_identity(
            conn, platform, str(admin_external_user_id), handle=admin_handle
        )
        if not relay_db.member_has_role(conn, admin_id, org_id, "admin"):
            return MergeResult(code=MERGE_NOT_AUTHORIZED, x_user_id=str(x_user_id))
        if not relay_db.member_exists(conn, int(target_member_id)):
            return MergeResult(
                code=MERGE_UNKNOWN_TARGET,
                x_user_id=str(x_user_id),
                to_member_id=int(target_member_id),
            )
        existing = relay_db.get_x_identity(conn, str(x_user_id))
        if existing is None:
            return MergeResult(code=MERGE_NOT_LINKED, x_user_id=str(x_user_id))
        from_member_id = int(existing["member_id"])
        relay_db.reassign_x_identity(
            conn, str(x_user_id), new_member_id=int(target_member_id), handle=x_handle
        )
        relay_db.write_relay_audit(
            conn,
            actor=admin_handle or str(admin_external_user_id),
            action="relay.merge_x_identity",
            org_id=org_id,
            entity_id=str(x_user_id),
            detail={
                "x_user_id": str(x_user_id),
                "from_member_id": from_member_id,
                "to_member_id": int(target_member_id),
                "merged_by_member_id": admin_id,
            },
        )
        return MergeResult(
            code=MERGE_OK,
            x_user_id=str(x_user_id),
            from_member_id=from_member_id,
            to_member_id=int(target_member_id),
        )


__all__ = [
    "ForgetResult",
    "LinkXResult",
    "MergeResult",
    "forget_me",
    "link_x",
    "admin_merge_x_identity",
    "COLLISION_MESSAGE_TEMPLATE",
    "FORGET_OK",
    "FORGET_NOTHING",
    "LINK_OK",
    "LINK_ALREADY",
    "LINK_COLLISION",
    "LINK_NO_MEMBER",
    "MERGE_OK",
    "MERGE_NOT_AUTHORIZED",
    "MERGE_UNKNOWN_TARGET",
    "MERGE_NOT_LINKED",
]
