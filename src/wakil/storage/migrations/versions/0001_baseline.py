"""Baseline: the pre-Alembic schema as created by Base.metadata.create_all.

This revision is an anchor, not an executor. Databases created before
Alembic existed already contain every table this revision represents
(users, workspaces, sources, notes, memories, relationships, ingest_runs,
query_runs, git_changes, plus the FTS shadow tables) — init_db stamps them
here and upgrades forward. Fresh databases are still created by
create_all() and stamped at head, so this upgrade() never needs to build
tables and stays a no-op rather than hand-duplicating ~150 lines of DDL
that would drift from schema.py.

Revision ID: 0001
Revises:
Create Date: 2026-07-10
"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
