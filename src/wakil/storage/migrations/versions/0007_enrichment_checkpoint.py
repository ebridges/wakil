"""Add EnrichmentCheckpoint: per-phase resume state for `wakil enrich`.

docs/adr/0020: `prepare_enrichment`'s 4-call DAG (extraction, resolution,
revision, synthesis) previously lost all completed model-call output on a
crash or a failed `validate_proposal` gate, forcing a full redo on retry.
One row per (source, phase) lets a re-invocation skip any phase whose
checkpoint is still valid (`content_hash` matches) instead of recalling the
model.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-03
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "enrichment_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False
        ),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("phase", sa.String(20), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "source_id", "phase", name="uq_enrichment_checkpoints_source_phase"
        ),
    )


def downgrade() -> None:
    op.drop_table("enrichment_checkpoints")
