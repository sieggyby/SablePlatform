"""Per-operator attribution on the cost ledger -- cost_events.operator_id (mig 081)

Mirrors SQLite migration 081_cost_operator_attribution.sql for Postgres parity (the
dual-migration rule). HAND-WRITTEN (not --autogenerate). Nullable TEXT stamped by callers
acting on behalf of a logged-in operator (Slopper /reply, /compose, deck produce, meme
produce) with the stable SableWeb SESSION identity (operator_arf / operator_ben /
client_bharat) -- NOT the persona X-handle (personas are shared across humans). NULL =
unattributed: every pre-081 row plus system paths (weekly workflows, ambient deck
generation, sweep timers). 100% additive.

Revision ID: d0e1f2a3b081
Revises: c9d0e1f2a080
Create Date: 2026-07-02 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b081"
down_revision = "c9d0e1f2a080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cost_events", sa.Column("operator_id", sa.Text(), nullable=True))
    op.create_index("idx_cost_operator", "cost_events", ["operator_id"])


def downgrade() -> None:
    op.drop_index("idx_cost_operator", table_name="cost_events")
    op.drop_column("cost_events", "operator_id")
