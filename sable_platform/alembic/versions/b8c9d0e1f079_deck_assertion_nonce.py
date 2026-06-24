"""Single-use store for deck/produce authorization assertions -- deck_consumed_assertions (mig 079)

Mirrors SQLite migration 079_deck_assertion_nonce.sql for Postgres parity (the dual-migration
rule). HAND-WRITTEN (not --autogenerate). Codex Tier-1 replay defense: Slopper consumes the
SableWeb-signed assertion SIGNATURE exactly once (PRIMARY KEY(sig)) BEFORE any budget reserve /
state change, so a captured-but-valid assertion cannot be replayed within its TTL (with or without
tampered unsigned request fields). No FKs (sig is a self-contained HMAC hex; org is validated at the
serve layer). NO cost column. See sable/serve/deck_authz.py + Sable_Slopper/docs/MEME_ENGINE_PLAN.md.

Revision ID: b8c9d0e1f079
Revises: a7b8c9d0e078
Create Date: 2026-06-24 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f079"
down_revision = "a7b8c9d0e078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deck_consumed_assertions",
        sa.Column("sig", sa.Text(), primary_key=True, nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        # unix seconds -- stored ONLY so expired rows can be GC'd (the verifier already rejected an
        # expired/over-future assertion before the consume).
        sa.Column("exp", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "deck_consumed_assertions_by_exp", "deck_consumed_assertions", ["exp"]
    )


def downgrade() -> None:
    op.drop_table("deck_consumed_assertions")
