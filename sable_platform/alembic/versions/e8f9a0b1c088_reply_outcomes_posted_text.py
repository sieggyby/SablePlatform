"""reply_outcomes.posted_text — the reply text the operator actually posted (migration 088)

was_edited (mig 056) records THAT the operator changed our draft before posting;
this nullable column records WHAT they posted, captured at link time by the
detect/reconcile jobs that already hold the tweet text in memory (zero extra
SocialData spend). It grounds the edit-diff learning loop: style proposals learn
from operator corrections (draft → posted pairs), not just thumbs-down signal.
100% additive ADD COLUMN; legacy rows stay NULL.

Revision ID: e8f9a0b1c088
Revises: d6e7f8a9b087
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e8f9a0b1c088"
down_revision = "d6e7f8a9b087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reply_outcomes",
        sa.Column("posted_text", sa.Text()),
    )


def downgrade() -> None:
    op.drop_column("reply_outcomes", "posted_text")
