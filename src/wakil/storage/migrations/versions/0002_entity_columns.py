"""Add Memory.event_date and Relationship.subject_note_id/object_note_id.

The two data-model extensions from docs/entity-model.md: Timeline ordering
needs the event's own date (not created_at), and wikilinks are Note-to-Note
structural edges the Relationship table couldn't represent before.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("event_date", sa.Date(), nullable=True))
    op.add_column("relationships", sa.Column("subject_note_id", sa.Integer(), nullable=True))
    op.add_column("relationships", sa.Column("object_note_id", sa.Integer(), nullable=True))
    # SQLite cannot add a foreign-key constraint to an existing table without
    # a batch rebuild, and it does not enforce FKs by default anyway; the ORM
    # schema declares the FKs for fresh databases, and these columns carry
    # the same values either way.


def downgrade() -> None:
    op.drop_column("relationships", "object_note_id")
    op.drop_column("relationships", "subject_note_id")
    op.drop_column("memories", "event_date")
