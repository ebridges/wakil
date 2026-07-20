"""Add Source.git_branch and Source.git_pr_url.

Tracks one branch/PR per source across its whole lifecycle (capture, then
possibly a later, separate `wakil enrich` call) instead of per command
invocation, so capture and enrichment land on the same branch and the same
PR rather than two disconnected ones.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("git_branch", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("git_pr_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "git_pr_url")
    op.drop_column("sources", "git_branch")
