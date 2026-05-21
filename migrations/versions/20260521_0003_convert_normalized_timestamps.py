"""convert normalized timestamps to timestamptz

Revision ID: 20260521_0003
Revises: 20260521_0002
Create Date: 2026-05-21 00:10:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260521_0003"
down_revision: Union[str, Sequence[str], None] = "20260521_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "sessions",
        "start_ts",
        existing_type=sa.String(length=64),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="start_ts::timestamptz",
    )
    op.alter_column(
        "sessions",
        "end_ts",
        existing_type=sa.String(length=64),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="end_ts::timestamptz",
    )
    op.alter_column(
        "meter_values",
        "ts",
        existing_type=sa.String(length=64),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="ts::timestamptz",
    )
    op.alter_column(
        "receipts",
        "start_ts",
        existing_type=sa.String(length=64),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="start_ts::timestamptz",
    )
    op.alter_column(
        "receipts",
        "end_ts",
        existing_type=sa.String(length=64),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="end_ts::timestamptz",
    )


def downgrade() -> None:
    op.alter_column(
        "receipts",
        "end_ts",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.String(length=64),
        existing_nullable=False,
        postgresql_using="end_ts::text",
    )
    op.alter_column(
        "receipts",
        "start_ts",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.String(length=64),
        existing_nullable=False,
        postgresql_using="start_ts::text",
    )
    op.alter_column(
        "meter_values",
        "ts",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.String(length=64),
        existing_nullable=False,
        postgresql_using="ts::text",
    )
    op.alter_column(
        "sessions",
        "end_ts",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.String(length=64),
        existing_nullable=False,
        postgresql_using="end_ts::text",
    )
    op.alter_column(
        "sessions",
        "start_ts",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.String(length=64),
        existing_nullable=False,
        postgresql_using="start_ts::text",
    )
