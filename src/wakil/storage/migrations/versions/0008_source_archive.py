"""Add Source.archived_at, archive_reason, and superseded_by_id.

A source whose capture went wrong (corrupted content committed against it, a
duplicate ingest of the same recording) had no supported way to be marked
dead: the git branch could be deleted and the PR closed, but the row sat in
`wakil sources list` forever pointing at branches and PRs that no longer
exist, indistinguishable from one that still needs attention (#183).

Soft delete rather than a real one: the row is history, and memories,
relationships, and ingest_runs reference it.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.add_column("sources", sa.Column("archive_reason", sa.Text(), nullable=True))
    # Deliberately no FK constraint: SQLite can't add one to an existing
    # table without a full rebuild, and a superseding source can itself be
    # archived or (in principle) removed, so a dangling id must not break the
    # row that points at it.
    op.add_column("sources", sa.Column("superseded_by_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "superseded_by_id")
    op.drop_column("sources", "archive_reason")
    op.drop_column("sources", "archived_at")
