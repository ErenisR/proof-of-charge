"""add batch anchor receipt membership

Revision ID: 20260521_0002
Revises: 20260516_0001
Create Date: 2026-05-21 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260521_0002"
down_revision: Union[str, Sequence[str], None] = "20260516_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "batch_anchor_receipts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("anchor_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("receipt_hash", sa.String(length=66), nullable=False),
        sa.Column("leaf_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["anchor_id"], ["batch_anchors.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("anchor_id", "leaf_index", name="uq_batch_anchor_receipt_leaf"),
        sa.UniqueConstraint("anchor_id", "session_id", name="uq_batch_anchor_receipt_session"),
    )
    op.create_index(
        op.f("ix_batch_anchor_receipts_anchor_id"),
        "batch_anchor_receipts",
        ["anchor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_batch_anchor_receipts_session_id"),
        "batch_anchor_receipts",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_batch_anchor_receipts_receipt_hash"),
        "batch_anchor_receipts",
        ["receipt_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_batch_anchor_receipts_receipt_hash"), table_name="batch_anchor_receipts")
    op.drop_index(op.f("ix_batch_anchor_receipts_session_id"), table_name="batch_anchor_receipts")
    op.drop_index(op.f("ix_batch_anchor_receipts_anchor_id"), table_name="batch_anchor_receipts")
    op.drop_table("batch_anchor_receipts")
