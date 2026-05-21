"""make batch anchors idempotent

Revision ID: 20260521_0004
Revises: 20260521_0003
Create Date: 2026-05-21 00:20:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260521_0004"
down_revision: Union[str, Sequence[str], None] = "20260521_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE batch_anchors SET session_prefix = '' WHERE session_prefix IS NULL")
    op.alter_column(
        "batch_anchors",
        "session_prefix",
        existing_type=sa.String(length=128),
        nullable=False,
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "batch_anchors",
        "session_prefix",
        existing_type=sa.String(length=128),
        nullable=True,
        existing_nullable=False,
    )
    op.execute("UPDATE batch_anchors SET session_prefix = NULL WHERE session_prefix = ''")
