"""cost_events vendor units — credits, credit_rate_usd, note (migration 089)

cost_events was built for LLM calls: its only raw quantities are input/output
tokens. Credits-billed vendors (Higgsfield first) had nowhere to keep their raw
units, so a logged USD figure could never be recomputed when the plan's credit
rate changes. These three nullable columns keep the raw credits + the rate used
at log time (cost_usd = credits x credit_rate_usd for vendor rows) plus a
free-text operator note (vendor job id / what the job was for — manual ledger
entries need context that call_type/model cannot carry). 100% additive ADD
COLUMN; token-based and pre-089 rows stay NULL.

Revision ID: a9b0c1d2e089
Revises: e8f9a0b1c088
Create Date: 2026-08-03
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a9b0c1d2e089"
down_revision = "e8f9a0b1c088"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cost_events", sa.Column("credits", sa.Float()))
    op.add_column("cost_events", sa.Column("credit_rate_usd", sa.Float()))
    op.add_column("cost_events", sa.Column("note", sa.Text()))


def downgrade() -> None:
    op.drop_column("cost_events", "note")
    op.drop_column("cost_events", "credit_rate_usd")
    op.drop_column("cost_events", "credits")
