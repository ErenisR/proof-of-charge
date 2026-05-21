"""initial schema

Revision ID: 20260516_0001
Revises:
Create Date: 2026-05-16 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260516_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("evse_id", sa.String(length=128), nullable=False),
        sa.Column("ocpp_tx_id", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("session_type", sa.String(length=32), nullable=False),
        sa.Column("start_ts", sa.String(length=64), nullable=False),
        sa.Column("end_ts", sa.String(length=64), nullable=False),
        sa.Column("session_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_sessions_evse_id"), "sessions", ["evse_id"], unique=False)
    op.create_index(op.f("ix_sessions_ocpp_tx_id"), "sessions", ["ocpp_tx_id"], unique=False)

    op.create_table(
        "batch_anchors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("session_prefix", sa.String(length=128), nullable=True),
        sa.Column("batch_root", sa.String(length=66), nullable=False),
        sa.Column("receipt_count", sa.Integer(), nullable=False),
        sa.Column("chain_tx", sa.Text(), nullable=True),
        sa.Column("cid", sa.Text(), nullable=True),
        sa.Column("anchored_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("day", "session_prefix", "batch_root", name="uq_batch_anchor"),
    )
    op.create_index(op.f("ix_batch_anchors_day"), "batch_anchors", ["day"], unique=False)
    op.create_index(op.f("ix_batch_anchors_batch_root"), "batch_anchors", ["batch_root"], unique=False)

    op.create_table(
        "meter_values",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("sample_index", sa.Integer(), nullable=False),
        sa.Column("ts", sa.String(length=64), nullable=False),
        sa.Column("energy_kwh", sa.Float(), nullable=True),
        sa.Column("import_kwh", sa.Float(), nullable=True),
        sa.Column("export_kwh", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "sample_index", name="uq_meter_values_session_sample"),
    )
    op.create_index(op.f("ix_meter_values_session_id"), "meter_values", ["session_id"], unique=False)

    op.create_table(
        "receipts",
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("receipt_hash", sa.String(length=66), nullable=False),
        sa.Column("merkle_root", sa.String(length=66), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("session_type", sa.String(length=32), nullable=False),
        sa.Column("energy_kwh", sa.Float(), nullable=False),
        sa.Column("import_kwh", sa.Float(), nullable=False),
        sa.Column("export_kwh", sa.Float(), nullable=False),
        sa.Column("net_kwh", sa.Float(), nullable=False),
        sa.Column("start_ts", sa.String(length=64), nullable=False),
        sa.Column("end_ts", sa.String(length=64), nullable=False),
        sa.Column("receipt_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cid", sa.Text(), nullable=True),
        sa.Column("chain_tx", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index(op.f("ix_receipts_receipt_hash"), "receipts", ["receipt_hash"], unique=True)
    op.create_index(op.f("ix_receipts_merkle_root"), "receipts", ["merkle_root"], unique=False)

    op.create_table(
        "verifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("day", sa.String(length=10), nullable=True),
        sa.Column("expected_hash", sa.String(length=66), nullable=True),
        sa.Column("computed_hash", sa.String(length=66), nullable=True),
        sa.Column("expected_root", sa.String(length=66), nullable=True),
        sa.Column("computed_root", sa.String(length=66), nullable=True),
        sa.Column("match", sa.Boolean(), nullable=False),
        sa.Column("verification_type", sa.String(length=32), nullable=False),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_verifications_session_id"), "verifications", ["session_id"], unique=False)
    op.create_index(op.f("ix_verifications_day"), "verifications", ["day"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_verifications_day"), table_name="verifications")
    op.drop_index(op.f("ix_verifications_session_id"), table_name="verifications")
    op.drop_table("verifications")

    op.drop_index(op.f("ix_receipts_merkle_root"), table_name="receipts")
    op.drop_index(op.f("ix_receipts_receipt_hash"), table_name="receipts")
    op.drop_table("receipts")

    op.drop_index(op.f("ix_meter_values_session_id"), table_name="meter_values")
    op.drop_table("meter_values")

    op.drop_index(op.f("ix_batch_anchors_batch_root"), table_name="batch_anchors")
    op.drop_index(op.f("ix_batch_anchors_day"), table_name="batch_anchors")
    op.drop_table("batch_anchors")

    op.drop_index(op.f("ix_sessions_ocpp_tx_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_evse_id"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_table("sessions")
