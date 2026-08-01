"""add versioned batch commitment snapshot metadata

Revision ID: 20260801_0005
Revises: 20260521_0004
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260801_0005"
down_revision = "20260521_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("batch_anchors", sa.Column("commitment_profile", sa.String(64), nullable=True))
    op.add_column("batch_anchors", sa.Column("context_json", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))
    op.add_column("batch_anchors", sa.Column("context_hash", sa.String(66), nullable=True))
    op.add_column("batch_anchors", sa.Column("tree_root", sa.String(66), nullable=True))
    op.add_column("batch_anchors", sa.Column("window_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("batch_anchors", sa.Column("window_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("batch_anchors", sa.Column("ordering_rule", sa.String(128), nullable=True))
    op.add_column("batch_anchors", sa.Column("odd_node_rule", sa.String(32), nullable=True))
    op.add_column("batch_anchors", sa.Column("hash_algorithm", sa.String(32), nullable=True))
    op.execute("UPDATE batch_anchors SET commitment_profile = 'legacy-hash-sort-v0' WHERE commitment_profile IS NULL")
    op.alter_column("batch_anchors", "commitment_profile", nullable=False)
    op.add_column("batch_anchor_receipts", sa.Column("normalized_start_ts", sa.DateTime(timezone=True), nullable=True))
    op.add_column("batch_anchor_receipts", sa.Column("leaf_hash", sa.String(66), nullable=True))


def downgrade() -> None:
    op.drop_column("batch_anchor_receipts", "leaf_hash")
    op.drop_column("batch_anchor_receipts", "normalized_start_ts")
    for column in ("hash_algorithm", "odd_node_rule", "ordering_rule", "window_end", "window_start", "tree_root", "context_hash", "context_json", "commitment_profile"):
        op.drop_column("batch_anchors", column)
