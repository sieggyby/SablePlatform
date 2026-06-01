"""media_assets — shared cross-tool media registry (migration 055)

Holds the canonical R2 reference (bucket/key) for media created in
SableSlopper (clips/cards/brainrot/memes) and surfaced in SableTracking
(contribution media), plus optional entity/content linkage and a searchable
caption. See docs/SHARED_MEDIA_LAYER_PLAN_V1.md.

UNIQUE (org_id, r2_ref) is the registration idempotency key.

Revision ID: a3b4c5d6e055
Revises: f2a3b4c5d054
Create Date: 2026-05-30 18:30:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = 'a3b4c5d6e055'
down_revision: str | None = 'f2a3b4c5d054'
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        'media_assets',
        sa.Column('asset_id', sa.Text(), nullable=False),
        sa.Column('org_id', sa.Text(), nullable=False),
        sa.Column('source_project', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('r2_ref', sa.Text(), nullable=False),
        sa.Column('mime', sa.Text(), nullable=True),
        sa.Column('bytes', sa.Integer(), nullable=True),
        sa.Column('sha256', sa.Text(), nullable=True),
        sa.Column('entity_id', sa.Text(), nullable=True),
        sa.Column('content_item_id', sa.Text(), nullable=True),
        sa.Column('source_ref', sa.Text(), nullable=True),
        sa.Column('caption', sa.Text(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column(
            'created_at',
            sa.Text(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint('asset_id'),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.org_id']),
        sa.ForeignKeyConstraint(['entity_id'], ['entities.entity_id']),
        sa.ForeignKeyConstraint(['content_item_id'], ['content_items.item_id']),
    )
    op.create_index(
        'ux_media_assets_org_ref',
        'media_assets',
        ['org_id', 'r2_ref'],
        unique=True,
    )
    op.create_index(
        'ix_media_assets_org_kind',
        'media_assets',
        ['org_id', 'kind'],
    )
    op.create_index(
        'ix_media_assets_sha',
        'media_assets',
        ['org_id', 'sha256'],
    )


def downgrade() -> None:
    op.drop_index('ix_media_assets_sha', table_name='media_assets')
    op.drop_index('ix_media_assets_org_kind', table_name='media_assets')
    op.drop_index('ux_media_assets_org_ref', table_name='media_assets')
    op.drop_table('media_assets')
