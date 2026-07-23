"""Add Memory.stance: the commitment/register axis (docs/adr/0014).

Orthogonal to memory_type -- "casual" marks a low-commitment claim (e.g. a
1:1 hot take), distinct from confidence (certainty rather than register).
Named `stance` rather than `register` because the latter collides with
ABCMeta.register on Pydantic's ModelMetaclass, which CandidateMemoryModel
inherits from (see docs/TROUBLESHOOTING.md).

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("stance", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("memories", "stance")
