"""Relax `relationships.subject_memory_id` / `object_memory_id` to nullable.

ADR 0006 widened the `relationships` table with nullable `subject_note_id`
/ `object_note_id` columns so Note↔Note structural edges (wikilinks) could
live alongside the existing Memory↔Memory semantic edges. Migration 0002
added those columns, but the memory-side pair remained NOT NULL — meaning
a note-only relationship physically couldn't be written without also
filling the memory FKs, which the one existing Note↔Note test worked
around by inserting a dummy Memory row. Relaxing both memory FKs to
nullable finishes ADR 0006's shape (the table now carries either edge
kind by which pair is populated), and unblocks real wikilink extraction
during indexing.

Every existing row in the wild has both memory FKs populated (that was
the only shape apply_enrichment ever wrote), so widening the constraint
is data-safe — no rewrite of existing rows is needed.

SQLite can't alter a column in place, so batch mode rebuilds the table.
`copy_from` gives the batch operation an explicit shape rather than
reflecting the current DB — reflection can lose column typing on
databases whose tables were rebuilt without declared types (e.g. the
legacy simulation in tests), which then fails at DDL generation.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _relationships_table(*, memory_fks_nullable: bool) -> sa.Table:
    """The `relationships` table's shape as of just before/after this migration.

    The columns declared here match what the schema had at revision 0005 (post
    the widening in 0002 — see schema.py's Relationship model). Only the
    nullability of the two memory FKs changes across upgrade/downgrade.
    """
    return sa.Table(
        "relationships",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column(
            "subject_memory_id",
            sa.Integer,
            sa.ForeignKey("memories.id"),
            nullable=memory_fks_nullable,
        ),
        sa.Column("predicate", sa.String(50), nullable=False),
        sa.Column(
            "object_memory_id",
            sa.Integer,
            sa.ForeignKey("memories.id"),
            nullable=memory_fks_nullable,
        ),
        sa.Column("subject_note_id", sa.Integer, sa.ForeignKey("notes.id"), nullable=True),
        sa.Column("object_note_id", sa.Integer, sa.ForeignKey("notes.id"), nullable=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("note_id", sa.Integer, sa.ForeignKey("notes.id"), nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def upgrade() -> None:
    copy_from = _relationships_table(memory_fks_nullable=False)
    with op.batch_alter_table("relationships", copy_from=copy_from) as batch_op:
        batch_op.alter_column("subject_memory_id", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column("object_memory_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    copy_from = _relationships_table(memory_fks_nullable=True)
    with op.batch_alter_table("relationships", copy_from=copy_from) as batch_op:
        batch_op.alter_column("subject_memory_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("object_memory_id", existing_type=sa.Integer(), nullable=False)
