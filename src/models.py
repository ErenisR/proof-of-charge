from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

JsonType = JSON().with_variant(JSONB, "postgresql")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ChargingSession(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    evse_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    ocpp_tx_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    session_type: Mapped[str] = mapped_column(String(32), nullable=False)
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session_json: Mapped[dict] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    receipt: Mapped["Receipt"] = relationship(back_populates="session", uselist=False)
    meter_values: Mapped[list["MeterValue"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="MeterValue.sample_index",
    )


class MeterValue(Base):
    __tablename__ = "meter_values"
    __table_args__ = (UniqueConstraint("session_id", "sample_index", name="uq_meter_values_session_sample"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), nullable=False, index=True)
    sample_index: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    energy_kwh: Mapped[float | None] = mapped_column(Float)
    import_kwh: Mapped[float | None] = mapped_column(Float)
    export_kwh: Mapped[float | None] = mapped_column(Float)

    session: Mapped[ChargingSession] = relationship(back_populates="meter_values")


class Receipt(Base):
    __tablename__ = "receipts"

    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), primary_key=True)
    receipt_hash: Mapped[str] = mapped_column(String(66), nullable=False, unique=True, index=True)
    merkle_root: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    session_type: Mapped[str] = mapped_column(String(32), nullable=False)
    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    import_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    export_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    net_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    receipt_json: Mapped[dict] = mapped_column(JsonType, nullable=False)
    cid: Mapped[str | None] = mapped_column(Text)
    chain_tx: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    session: Mapped[ChargingSession] = relationship(back_populates="receipt")


class BatchAnchor(Base):
    __tablename__ = "batch_anchors"
    __table_args__ = (UniqueConstraint("day", "session_prefix", "batch_root", name="uq_batch_anchor"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    session_prefix: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    batch_root: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    commitment_profile: Mapped[str] = mapped_column(String(64), nullable=False, default="legacy-hash-sort-v0")
    context_json: Mapped[dict | None] = mapped_column(JsonType)
    context_hash: Mapped[str | None] = mapped_column(String(66))
    tree_root: Mapped[str | None] = mapped_column(String(66))
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ordering_rule: Mapped[str | None] = mapped_column(String(128))
    odd_node_rule: Mapped[str | None] = mapped_column(String(32))
    hash_algorithm: Mapped[str | None] = mapped_column(String(32))
    receipt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    chain_tx: Mapped[str | None] = mapped_column(Text)
    cid: Mapped[str | None] = mapped_column(Text)
    anchored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    receipts: Mapped[list["BatchAnchorReceipt"]] = relationship(
        back_populates="anchor",
        cascade="all, delete-orphan",
        order_by="BatchAnchorReceipt.leaf_index",
    )


class BatchAnchorReceipt(Base):
    __tablename__ = "batch_anchor_receipts"
    __table_args__ = (
        UniqueConstraint("anchor_id", "session_id", name="uq_batch_anchor_receipt_session"),
        UniqueConstraint("anchor_id", "leaf_index", name="uq_batch_anchor_receipt_leaf"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anchor_id: Mapped[int] = mapped_column(ForeignKey("batch_anchors.id"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.session_id"), nullable=False, index=True)
    receipt_hash: Mapped[str] = mapped_column(String(66), nullable=False, index=True)
    normalized_start_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leaf_hash: Mapped[str | None] = mapped_column(String(66))
    leaf_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    anchor: Mapped[BatchAnchor] = relationship(back_populates="receipts")


class Verification(Base):
    __tablename__ = "verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.session_id"), index=True)
    day: Mapped[str | None] = mapped_column(String(10), index=True)
    expected_hash: Mapped[str | None] = mapped_column(String(66))
    computed_hash: Mapped[str | None] = mapped_column(String(66))
    expected_root: Mapped[str | None] = mapped_column(String(66))
    computed_root: Mapped[str | None] = mapped_column(String(66))
    match: Mapped[bool] = mapped_column(Boolean, nullable=False)
    verification_type: Mapped[str] = mapped_column(String(32), nullable=False)
    details_json: Mapped[dict | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
