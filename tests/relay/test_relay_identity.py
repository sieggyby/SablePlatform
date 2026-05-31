"""C2.3c tests — PII / identity-integrity: ``/forget-me`` + ``/link-x``.

No real Telegram/Discord; DB work runs against the in-memory ``sa_conn`` schema.
Each command returns a small result object the listener uses to reply OUTSIDE the
txn.

Coverage (per MEGAPLAN C2.3c exit):
  * ``/forget-me`` deletes the caller's preferences + identity rows but leaves the
    ``relay_members`` row (anonymized, display_name=NULL) so ``member_id`` audit
    references survive; an audit row keyed by member_id is written and does NOT
    re-leak the deleted handle/external id.
  * ``/link-x`` adds ``platform='x'`` to an existing member; an idempotent re-link
    of the same X id to the same member is a no-op.
  * ``/link-x`` REJECTS a collision (the X id already links a DIFFERENT member)
    with the §8 message and writes nothing.
  * the admin-only merge re-points the X id to the intended member and resolves
    the collision (a subsequent ``/link-x`` succeeds); the merge is role-gated.

``/link-x`` has NO live consumer until C2.4 reply-tracking — these tests assert
ONLY the collision-rejection + admin-merge invariants in isolation (no
reply-tracking assertion).
"""
from __future__ import annotations

import json

from sqlalchemy import text

from sable_platform.relay import db as relay_db
from sable_platform.relay.bot.handlers import identity


def _seed(conn, *, org_id="orgA"):
    conn.execute(text("INSERT INTO orgs (org_id, display_name) VALUES (:o, :o)"), {"o": org_id})
    conn.execute(
        text("INSERT INTO relay_clients (org_id, enabled, config) VALUES (:o, 1, '{}')"),
        {"o": org_id},
    )


def _grant_admin(conn, org_id, tg_user_id, handle="boss"):
    mid = relay_db.auto_create_member_identity(conn, "telegram", str(tg_user_id), handle=handle)
    conn.execute(
        text("INSERT INTO relay_member_roles (member_id, org_id, role) VALUES (:m, :o, 'admin')"),
        {"m": mid, "o": org_id},
    )
    return mid


def _audit_rows(conn, action):
    return conn.execute(
        text(
            "SELECT actor, entity_id, detail_json FROM audit_log "
            "WHERE action = :a AND source = 'relay'"
        ),
        {"a": action},
    ).fetchall()


# ==========================================================================
# /forget-me — §15.5 PII deletion (preferences + identities removed, audit kept)
# ==========================================================================
def test_forget_me_deletes_preferences_and_identities_keeps_anonymized_member(sa_conn):
    _seed(sa_conn)
    # The member exists with an identity + a preference, and is referenced by an
    # audit row (a durable member_id audit ref that MUST survive).
    mid = relay_db.auto_create_member_identity(sa_conn, "telegram", "20", handle="alice")
    relay_db.upsert_member_preference(sa_conn, mid, "orgA", replies_optin=True)
    relay_db.write_relay_audit(
        sa_conn, actor="alice", action="relay.amplify", org_id="orgA", entity_id=str(mid)
    )
    sa_conn.commit()

    res = identity.forget_me(sa_conn, platform="telegram", external_user_id="20")
    assert res.code == identity.FORGET_OK
    assert res.member_id == mid
    assert res.preferences_deleted == 1
    assert res.identities_deleted == 1

    # PII surface gone: no identity, no preference.
    assert relay_db.resolve_member_id(sa_conn, "telegram", "20") is None
    assert (
        sa_conn.execute(
            text("SELECT COUNT(*) FROM relay_member_preferences WHERE member_id = :m"),
            {"m": mid},
        ).fetchone()[0]
        == 0
    )
    assert (
        sa_conn.execute(
            text("SELECT COUNT(*) FROM relay_member_identities WHERE member_id = :m"),
            {"m": mid},
        ).fetchone()[0]
        == 0
    )

    # The member ROW is retained (id is the audit anchor) and anonymized.
    row = sa_conn.execute(
        text("SELECT id, display_name FROM relay_members WHERE id = :m"), {"m": mid}
    ).fetchone()
    assert row is not None
    assert row[1] is None  # display_name anonymized to NULL

    # The pre-existing audit ref still points at member_id (anonymized audit ref).
    pre = sa_conn.execute(
        text("SELECT entity_id FROM audit_log WHERE action = 'relay.amplify'")
    ).fetchone()
    assert pre[0] == str(mid)


def test_forget_me_writes_anonymized_audit_row(sa_conn):
    _seed(sa_conn)
    mid = relay_db.auto_create_member_identity(sa_conn, "telegram", "20", handle="alice")
    sa_conn.commit()

    identity.forget_me(sa_conn, platform="telegram", external_user_id="20")

    rows = _audit_rows(sa_conn, "relay.forget_me")
    assert len(rows) == 1
    actor, entity_id, detail_json = rows[0]
    # The audit row references member_id ONLY — it must NOT re-leak the deleted
    # handle ("alice") or the external user id ("20").
    assert actor == f"member:{mid}"
    assert entity_id == str(mid)
    detail = json.loads(detail_json)
    assert detail["member_id"] == mid
    assert detail["anonymized"] is True
    assert "alice" not in detail_json
    assert "alice" not in actor


def test_forget_me_nothing_to_forget(sa_conn):
    _seed(sa_conn)
    sa_conn.commit()
    # A caller with no identity (never interacted) → no-op, nothing written.
    res = identity.forget_me(sa_conn, platform="telegram", external_user_id="999")
    assert res.code == identity.FORGET_NOTHING
    assert res.member_id is None
    assert _audit_rows(sa_conn, "relay.forget_me") == []


def test_forget_me_deletes_all_orgs_preferences(sa_conn):
    """A member's preferences across MULTIPLE orgs are all removed."""
    _seed(sa_conn, org_id="orgA")
    _seed(sa_conn, org_id="orgB")
    mid = relay_db.auto_create_member_identity(sa_conn, "telegram", "20", handle="alice")
    relay_db.upsert_member_preference(sa_conn, mid, "orgA", replies_optin=True)
    relay_db.upsert_member_preference(sa_conn, mid, "orgB", replies_optin=True)
    sa_conn.commit()

    res = identity.forget_me(sa_conn, platform="telegram", external_user_id="20")
    assert res.code == identity.FORGET_OK
    assert res.preferences_deleted == 2
    assert (
        sa_conn.execute(
            text("SELECT COUNT(*) FROM relay_member_preferences WHERE member_id = :m"),
            {"m": mid},
        ).fetchone()[0]
        == 0
    )


# ==========================================================================
# /link-x — §8 identity link (add platform='x' to an existing member)
# ==========================================================================
def test_link_x_adds_identity_to_existing_member(sa_conn):
    _seed(sa_conn)
    mid = relay_db.auto_create_member_identity(sa_conn, "telegram", "20", handle="alice")
    sa_conn.commit()

    res = identity.link_x(
        sa_conn,
        platform="telegram",
        external_user_id="20",
        x_user_id="x_777",
        x_handle="alice_x",
        handle="alice",
    )
    assert res.code == identity.LINK_OK
    assert res.member_id == mid
    # The X identity now resolves to the same member.
    assert relay_db.get_member_x_user_id(sa_conn, mid) == "x_777"
    x_ident = relay_db.get_x_identity(sa_conn, "x_777")
    assert x_ident["member_id"] == mid
    assert x_ident["handle"] == "alice_x"


def test_link_x_idempotent_relink_same_member(sa_conn):
    _seed(sa_conn)
    mid = relay_db.auto_create_member_identity(sa_conn, "telegram", "20", handle="alice")
    sa_conn.commit()

    identity.link_x(sa_conn, platform="telegram", external_user_id="20", x_user_id="x_777")
    res2 = identity.link_x(
        sa_conn, platform="telegram", external_user_id="20", x_user_id="x_777", x_handle="alice_x"
    )
    assert res2.code == identity.LINK_ALREADY
    assert res2.member_id == mid
    # Still exactly one X identity row for this X id (no duplicate / PK violation).
    n = sa_conn.execute(
        text("SELECT COUNT(*) FROM relay_member_identities WHERE platform='x' AND external_user_id='x_777'")
    ).fetchone()[0]
    assert n == 1


def test_link_x_no_telegram_identity(sa_conn):
    """Linking is 'add X to an EXISTING member' — a caller with no identity is rejected."""
    _seed(sa_conn)
    sa_conn.commit()
    res = identity.link_x(
        sa_conn, platform="telegram", external_user_id="404", x_user_id="x_777"
    )
    assert res.code == identity.LINK_NO_MEMBER
    # Nothing linked.
    assert relay_db.get_x_identity(sa_conn, "x_777") is None


def test_link_x_rejects_collision(sa_conn):
    """The X id already links to a DIFFERENT member → §8 rejection, no write."""
    _seed(sa_conn)
    # member A already has the X identity x_777 linked.
    mid_a = relay_db.auto_create_member_identity(sa_conn, "telegram", "10", handle="anna")
    relay_db.link_x_identity(sa_conn, mid_a, "x_777", handle="anna_x")
    # member B (the caller) tries to claim the same X id.
    mid_b = relay_db.auto_create_member_identity(sa_conn, "telegram", "20", handle="bob")
    sa_conn.commit()
    assert mid_a != mid_b

    res = identity.link_x(
        sa_conn,
        platform="telegram",
        external_user_id="20",
        x_user_id="x_777",
        x_handle="bob_x",
    )
    assert res.code == identity.LINK_COLLISION
    assert res.member_id == mid_b
    assert res.existing_member_id == mid_a
    # The exact §8 rejection message.
    assert res.message == (
        "X account @bob_x is already linked to a different SableRelay member; "
        "ask an admin to merge."
    )
    # The X id is STILL linked to member A (the collision write is rejected).
    assert relay_db.get_x_identity(sa_conn, "x_777")["member_id"] == mid_a
    # No spurious link_x audit row was written.
    assert _audit_rows(sa_conn, "relay.link_x") == []


def test_link_x_writes_audit_on_success(sa_conn):
    _seed(sa_conn)
    relay_db.auto_create_member_identity(sa_conn, "telegram", "20", handle="alice")
    sa_conn.commit()
    identity.link_x(
        sa_conn, platform="telegram", external_user_id="20", x_user_id="x_777", handle="alice"
    )
    rows = _audit_rows(sa_conn, "relay.link_x")
    assert len(rows) == 1


# ==========================================================================
# admin-only merge — §8 collision resolution (no v1 self-serve UI)
# ==========================================================================
def test_admin_merge_resolves_collision(sa_conn):
    """The admin merge re-points the X id; the intended member can then link."""
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1, handle="boss")
    # X id x_777 is mistakenly linked to member A.
    mid_a = relay_db.auto_create_member_identity(sa_conn, "telegram", "10", handle="anna")
    relay_db.link_x_identity(sa_conn, mid_a, "x_777", handle="anna_x")
    # The INTENDED owner is member B.
    mid_b = relay_db.auto_create_member_identity(sa_conn, "telegram", "20", handle="bob")
    sa_conn.commit()

    # Before merge: B cannot link (collision).
    pre = identity.link_x(
        sa_conn, platform="telegram", external_user_id="20", x_user_id="x_777"
    )
    assert pre.code == identity.LINK_COLLISION

    # Admin merges x_777 onto member B.
    merge = identity.admin_merge_x_identity(
        sa_conn,
        org_id="orgA",
        platform="telegram",
        admin_external_user_id="1",
        admin_handle="boss",
        x_user_id="x_777",
        target_member_id=mid_b,
        x_handle="bob_x",
    )
    assert merge.code == identity.MERGE_OK
    assert merge.from_member_id == mid_a
    assert merge.to_member_id == mid_b

    # The X id now links to member B (collision resolved).
    assert relay_db.get_x_identity(sa_conn, "x_777")["member_id"] == mid_b
    assert relay_db.get_member_x_user_id(sa_conn, mid_b) == "x_777"
    # member A no longer has the X identity.
    assert relay_db.get_member_x_user_id(sa_conn, mid_a) is None
    # An audit row was written.
    assert len(_audit_rows(sa_conn, "relay.merge_x_identity")) == 1


def test_admin_merge_role_gated(sa_conn):
    """A non-admin caller cannot merge — no side effect."""
    _seed(sa_conn)
    mid_a = relay_db.auto_create_member_identity(sa_conn, "telegram", "10", handle="anna")
    relay_db.link_x_identity(sa_conn, mid_a, "x_777", handle="anna_x")
    mid_b = relay_db.auto_create_member_identity(sa_conn, "telegram", "20", handle="bob")
    sa_conn.commit()

    res = identity.admin_merge_x_identity(
        sa_conn,
        org_id="orgA",
        platform="telegram",
        admin_external_user_id="999",  # not an admin
        x_user_id="x_777",
        target_member_id=mid_b,
    )
    assert res.code == identity.MERGE_NOT_AUTHORIZED
    # The X id is untouched (still member A).
    assert relay_db.get_x_identity(sa_conn, "x_777")["member_id"] == mid_a
    assert _audit_rows(sa_conn, "relay.merge_x_identity") == []


def test_admin_merge_unknown_target(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1, handle="boss")
    mid_a = relay_db.auto_create_member_identity(sa_conn, "telegram", "10", handle="anna")
    relay_db.link_x_identity(sa_conn, mid_a, "x_777", handle="anna_x")
    sa_conn.commit()

    res = identity.admin_merge_x_identity(
        sa_conn,
        org_id="orgA",
        platform="telegram",
        admin_external_user_id="1",
        admin_handle="boss",
        x_user_id="x_777",
        target_member_id=999999,  # no such member
    )
    assert res.code == identity.MERGE_UNKNOWN_TARGET
    assert relay_db.get_x_identity(sa_conn, "x_777")["member_id"] == mid_a


def test_admin_merge_x_not_linked(sa_conn):
    _seed(sa_conn)
    _grant_admin(sa_conn, "orgA", 1, handle="boss")
    mid_b = relay_db.auto_create_member_identity(sa_conn, "telegram", "20", handle="bob")
    sa_conn.commit()

    res = identity.admin_merge_x_identity(
        sa_conn,
        org_id="orgA",
        platform="telegram",
        admin_external_user_id="1",
        admin_handle="boss",
        x_user_id="x_unlinked",
        target_member_id=mid_b,
    )
    assert res.code == identity.MERGE_NOT_LINKED
