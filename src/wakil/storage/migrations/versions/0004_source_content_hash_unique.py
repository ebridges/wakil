"""Add a unique constraint on Source(workspace_id, content_hash).

Closes the check-then-insert race in prepare_capture/apply_capture:
prepare_capture's duplicate check and apply_capture's insert are two
separate calls, so two processes capturing identical content at the same
moment could both pass the check and both insert, instead of the second
being caught as a duplicate. `apply_capture` now catches the resulting
IntegrityError and reports it the same way an early duplicate-of hit is
reported; this migration is what actually makes that error reachable
instead of the race silently producing two rows.

Existing databases may already have such duplicate pairs (this exact race
was reachable, just less likely, before concurrent ingest across git
worktrees was supported) -- dedupe them first, repointing any
memories/relationships/ingest_runs that reference a duplicate onto the
earliest (lowest id) row for that (workspace_id, content_hash), before
the unique index is created. A plain `CREATE UNIQUE INDEX` (rather than
`create_unique_constraint`, which needs SQLite's batch/table-rebuild mode)
keeps this a simple, direct migration.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_REFERENCING_TABLES = ("memories", "relationships", "ingest_runs")


def upgrade() -> None:
    conn = op.get_bind()
    sources = sa.table(
        "sources",
        sa.column("id", sa.Integer),
        sa.column("workspace_id", sa.Integer),
        sa.column("content_hash", sa.String),
    )

    rows = conn.execute(
        sa.select(sources.c.id, sources.c.workspace_id, sources.c.content_hash)
        .where(sources.c.content_hash.isnot(None))
        .order_by(sources.c.id)
    ).all()

    groups: dict[tuple[int, str], list[int]] = {}
    for row in rows:
        groups.setdefault((row.workspace_id, row.content_hash), []).append(row.id)

    for ids in groups.values():
        if len(ids) < 2:
            continue
        survivor, *duplicates = ids  # lowest id first: rows were id-ordered
        for table_name in _REFERENCING_TABLES:
            table = sa.table(table_name, sa.column("source_id", sa.Integer))
            conn.execute(
                table.update().where(table.c.source_id.in_(duplicates)).values(source_id=survivor)
            )
        conn.execute(sources.delete().where(sources.c.id.in_(duplicates)))

    op.create_index(
        "uq_sources_workspace_content_hash",
        "sources",
        ["workspace_id", "content_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_sources_workspace_content_hash", table_name="sources")
