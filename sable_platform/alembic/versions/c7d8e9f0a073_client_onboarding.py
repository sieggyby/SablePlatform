"""client & operator onboarding -- intake SSOT + entitlements (migration 073)

Mirrors SQLite migration 073_client_onboarding.sql for Postgres parity
(SablePlatform dual-migration rule). Four additive OPS-ONLY tables:
client_intake (per-org client header), client_accounts (unified handle registry),
client_docs (explainer/bio/voice pointers), org_entitlements (the SKU/entitlement
ledger -- state only, never money). All FK -> orgs. See docs/CLIENT_ONBOARDING_PLAN.md.

Revision ID: c7d8e9f0a073
Revises: c6d7e8f9a072
Create Date: 2026-06-07 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7d8e9f0a073"
down_revision = "c6d7e8f9a072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_intake",
        sa.Column("org_id", sa.Text(), sa.ForeignKey("orgs.org_id"), primary_key=True),
        sa.Column("manifest_status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("primary_contact_name", sa.Text()),
        sa.Column("primary_contact_email", sa.Text()),
        sa.Column("primary_contact_telegram", sa.Text()),
        sa.Column("website_url", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "manifest_status IN ('draft','ready','applied')",
            name="ck_client_intake_status",
        ),
    )

    op.create_table(
        "client_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("orgs.org_id"), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("handle", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("controlled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("display_name", sa.Text()),
        sa.Column("bio", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "platform", "handle", name="uq_client_accounts_handle"),
    )
    op.create_index("client_accounts_by_org", "client_accounts", ["org_id"])

    op.create_table(
        "client_docs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("orgs.org_id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("client_docs_by_org", "client_docs", ["org_id"])

    op.create_table(
        "org_entitlements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Text(), sa.ForeignKey("orgs.org_id"), nullable=False),
        sa.Column("service_key", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("started_at", sa.Text()),
        sa.Column("ended_at", sa.Text()),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('trial','active','paused','ended')",
            name="ck_org_entitlements_status",
        ),
        sa.UniqueConstraint("org_id", "service_key", name="uq_org_entitlements_service"),
    )
    op.create_index("org_entitlements_by_org", "org_entitlements", ["org_id"])


def downgrade() -> None:
    op.drop_index("org_entitlements_by_org", table_name="org_entitlements")
    op.drop_table("org_entitlements")
    op.drop_index("client_docs_by_org", table_name="client_docs")
    op.drop_table("client_docs")
    op.drop_index("client_accounts_by_org", table_name="client_accounts")
    op.drop_table("client_accounts")
    op.drop_table("client_intake")
