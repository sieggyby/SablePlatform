"""Operator work-tracking schema — mod_slot_sessions + operator_work_events (migration 059)

Mirrors SQLite migration 059_work_tracking.sql for Postgres parity (SablePlatform
dual-migration rule). Two tables backing SW-TASKING Phase 1 (the operator
"scale of work delivered" report):

  1. mod_slot_sessions — operator-declared moderating-slot "clock-in" windows
     (self-reported coverage; the ``note`` column is ops-only).
  2. operator_work_events — a generic operator work-event log (Phase 1 writes
     only non-reply ``mod_action`` events; the ``reply_delivered`` type is
     reserved/unused so replies stay sourced from reply_outcomes, mig 056).

TEXT primary keys (app-generated uuid hex), so neither table needs a
SEQUENCE_TABLES entry. _at columns are TEXT; the SQLite migration carries a
strftime ISO-8601-Z default, while the helpers (sable_platform/db/work_tracking.py)
always BIND explicit _iso_z timestamps rather than relying on the column default
(the post-053 timestamp-contract convention, so SQLite ``...T...Z`` and Postgres
forms stay lexically comparable). org_id FK -> orgs.org_id (TEXT).

Revision ID: d6e7f8e9a059
Revises: c5d6e7f8e058
Create Date: 2026-05-31 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "d6e7f8e9a059"
down_revision = "c5d6e7f8e058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mod_slot_sessions",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("operator_handle", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.Text(), nullable=True),
        sa.Column("chats_watched_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.org_id"]),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_mod_slot_sessions_org", "mod_slot_sessions", ["org_id", "started_at"])
    op.create_index("ix_mod_slot_sessions_operator", "mod_slot_sessions", ["operator_handle", "ended_at"])

    op.create_table(
        "operator_work_events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("operator_handle", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("ref_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.org_id"]),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_operator_work_events_org", "operator_work_events", ["org_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_operator_work_events_org", table_name="operator_work_events")
    op.drop_table("operator_work_events")
    op.drop_index("ix_mod_slot_sessions_operator", table_name="mod_slot_sessions")
    op.drop_index("ix_mod_slot_sessions_org", table_name="mod_slot_sessions")
    op.drop_table("mod_slot_sessions")
