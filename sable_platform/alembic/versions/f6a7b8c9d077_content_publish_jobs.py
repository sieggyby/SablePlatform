"""Content Deck Phase 4 release substrate -- content_publish_jobs (migration 077)

Mirrors SQLite migration 077_content_publish_jobs.sql for Postgres parity (the dual-migration
rule). HAND-WRITTEN (not --autogenerate) so the CheckConstraint + both ForeignKeys + the
ON DELETE CASCADE are rendered on Postgres (autogenerate silently drops CHECKs -> SQLite/PG
drift). content_candidates (the FK parent) already exists from migration 076. See
~/sable-workspace/CONTENT_DECK_MASTERPLAN.md Phase 4.

Revision ID: f6a7b8c9d077
Revises: e5f6a7b8c076
Create Date: 2026-06-23 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d077"
down_revision = "e5f6a7b8c076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_publish_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # A job dies with its candidate (candidates SOFT-expire in normal operation, so the FK
        # holds; a GC hard-delete cascades the job away).
        sa.Column(
            "candidate_id",
            sa.Integer(),
            sa.ForeignKey("content_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("orgs.org_id"), nullable=False),
        sa.Column("target_handle", sa.Text(), nullable=False),
        sa.Column("release_state", sa.Text(), nullable=False, server_default="scheduled"),
        sa.Column("publish_at", sa.Text(), nullable=False),
        sa.Column("next_attempt_at", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.Text()),
        sa.Column("handed_off_at", sa.Text()),
        sa.Column("posted_ref", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "release_state IN ('scheduled','due','claimed','handed_off','posted','canceled')",
            name="ck_content_publish_jobs_release_state",
        ),
        # publish_at STRICT-UTC FORMAT backstop (Codex Tier-2). The claim-due worker compares
        # publish_at LEXICALLY, so an offset/naive/space/compact/fractional value would release
        # early or never release. The Slopper route + schedule_candidate() validate this, but a
        # direct writer/backfill must not be able to store a malformed value. Postgres POSIX-regex
        # (~) enforces SHAPE (the SQLite migration uses an equivalent GLOB); calendar validity stays
        # in the accessor's strptime per the finding (a regex cannot range-check month/day).
        sa.CheckConstraint(
            r"publish_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'",
            name="ck_content_publish_jobs_publish_at_utc",
        ),
    )
    op.create_index(
        "content_publish_jobs_by_org_state",
        "content_publish_jobs",
        ["org_id", "release_state", "publish_at"],
    )
    op.create_index(
        "content_publish_jobs_due", "content_publish_jobs", ["release_state", "publish_at"]
    )
    op.create_index(
        "content_publish_jobs_by_candidate", "content_publish_jobs", ["candidate_id"]
    )


def downgrade() -> None:
    op.drop_table("content_publish_jobs")
