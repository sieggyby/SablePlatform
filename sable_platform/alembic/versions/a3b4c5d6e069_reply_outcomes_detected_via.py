"""reply_outcomes.detected_via — auto-detect vs operator Mark-posted provenance (migration 069)

Adds a nullable provenance column so an AUTO-detected posted reply (the scheduled
persona-timeline scan, value 'auto') is distinguishable from an operator-confirmed
Mark-posted row (value 'operator'). 100% additive ADD COLUMN; legacy rows stay NULL.

Revision ID: a3b4c5d6e069
Revises: a2b3c4d5e068
Create Date: 2026-06-06
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a3b4c5d6e069"
down_revision = "a2b3c4d5e068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reply_outcomes",
        sa.Column("detected_via", sa.Text()),
    )


def downgrade() -> None:
    op.drop_column("reply_outcomes", "detected_via")
