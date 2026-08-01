from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from .models import BatchAnchor, ChargingSession, Receipt, Verification
from .receipt_canonicalization import CANONICALIZATION_PROFILE_V1


T = TypeVar("T")


def isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


class MeterValueInput(BaseModel):
    ts: str
    energy_kwh: float | None = None
    import_kwh: float | None = None
    export_kwh: float | None = None


class PricingComponentInput(BaseModel):
    from_ts: str = Field(..., alias="from")
    to_ts: str = Field(..., alias="to")
    price_per_kwh: float


class PricingInput(BaseModel):
    currency: str = "EUR"
    model: str = "TOU"
    components: list[PricingComponentInput] = Field(default_factory=list)
    import_components: list[PricingComponentInput] = Field(default_factory=list)
    export_components: list[PricingComponentInput] = Field(default_factory=list)


class FinalizeSessionRequest(BaseModel):
    session_id: str
    user_id: str
    evse_id: str
    ocpp_tx_id: str
    start_ts: str
    end_ts: str
    schema_version: str | None = None
    canonicalization_profile: str = CANONICALIZATION_PROFILE_V1
    session_type: str | None = None
    energy_summary: dict[str, Any] | None = None
    settlement: dict[str, Any] | None = None
    meter_values: list[MeterValueInput]
    pricing: PricingInput | None = None


class FinalizeSessionResponse(BaseModel):
    receipt: dict[str, Any]
    hash: str
    canonicalization_profile: str
    hash_algorithm: str


class DbHealthResponse(BaseModel):
    ok: bool
    user: str
    database: str
    current_revision: str | None
    head_revision: str | None
    missing_tables: list[str]


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    limit: int
    offset: int


class MeterValueResponse(BaseModel):
    sample_index: int
    ts: str | None
    energy_kwh: float | None
    import_kwh: float | None
    export_kwh: float | None


class SessionSummaryResponse(BaseModel):
    session_id: str
    user_id: str
    evse_id: str
    ocpp_tx_id: str
    schema_version: str
    session_type: str
    start_ts: str | None
    end_ts: str | None
    energy_kwh: float | None
    import_kwh: float | None
    export_kwh: float | None
    net_kwh: float | None
    receipt_hash: str | None
    merkle_root: str | None
    created_at: str | None
    updated_at: str | None

    @classmethod
    def from_row(cls, row: ChargingSession) -> "SessionSummaryResponse":
        receipt = row.receipt
        return cls(
            session_id=row.session_id,
            user_id=row.user_id,
            evse_id=row.evse_id,
            ocpp_tx_id=row.ocpp_tx_id,
            schema_version=row.schema_version,
            session_type=row.session_type,
            start_ts=isoformat(row.start_ts),
            end_ts=isoformat(row.end_ts),
            energy_kwh=receipt.energy_kwh if receipt else None,
            import_kwh=receipt.import_kwh if receipt else None,
            export_kwh=receipt.export_kwh if receipt else None,
            net_kwh=receipt.net_kwh if receipt else None,
            receipt_hash=receipt.receipt_hash if receipt else None,
            merkle_root=receipt.merkle_root if receipt else None,
            created_at=isoformat(row.created_at),
            updated_at=isoformat(row.updated_at),
        )


class SessionDetailResponse(SessionSummaryResponse):
    session: dict[str, Any]
    meter_values: list[MeterValueResponse]

    @classmethod
    def from_row(cls, row: ChargingSession) -> "SessionDetailResponse":
        summary = SessionSummaryResponse.from_row(row).model_dump()
        return cls(
            **summary,
            session=row.session_json,
            meter_values=[
                MeterValueResponse(
                    sample_index=mv.sample_index,
                    ts=isoformat(mv.ts),
                    energy_kwh=mv.energy_kwh,
                    import_kwh=mv.import_kwh,
                    export_kwh=mv.export_kwh,
                )
                for mv in row.meter_values
            ],
        )


class ReceiptResponse(BaseModel):
    session_id: str
    receipt_hash: str
    merkle_root: str
    schema_version: str
    canonicalization_profile: str | None
    hash_algorithm: str | None
    session_type: str
    energy_kwh: float
    import_kwh: float
    export_kwh: float
    net_kwh: float
    start_ts: str | None
    end_ts: str | None
    cid: str | None
    chain_tx: str | None
    created_at: str | None
    updated_at: str | None
    receipt: dict[str, Any]

    @classmethod
    def from_row(cls, row: Receipt) -> "ReceiptResponse":
        return cls(
            session_id=row.session_id,
            receipt_hash=row.receipt_hash,
            merkle_root=row.merkle_root,
            schema_version=row.schema_version,
            canonicalization_profile=(row.receipt_json or {}).get("canonicalization_profile"),
            hash_algorithm=(row.receipt_json or {}).get("hash_algorithm"),
            session_type=row.session_type,
            energy_kwh=row.energy_kwh,
            import_kwh=row.import_kwh,
            export_kwh=row.export_kwh,
            net_kwh=row.net_kwh,
            start_ts=isoformat(row.start_ts),
            end_ts=isoformat(row.end_ts),
            cid=row.cid,
            chain_tx=row.chain_tx,
            created_at=isoformat(row.created_at),
            updated_at=isoformat(row.updated_at),
            receipt=row.receipt_json,
        )


class AnchorResponse(BaseModel):
    id: int
    day: str
    session_prefix: str
    batch_root: str
    commitment_profile: str
    context: dict[str, Any] | None
    context_hash: str | None
    tree_root: str | None
    receipt_count: int
    chain_tx: str | None
    cid: str | None
    anchored_at: str | None

    @classmethod
    def from_row(cls, row: BatchAnchor) -> "AnchorResponse":
        return cls(
            id=row.id,
            day=row.day,
            session_prefix=row.session_prefix,
            batch_root=row.batch_root,
            commitment_profile=row.commitment_profile,
            context=row.context_json,
            context_hash=row.context_hash,
            tree_root=row.tree_root,
            receipt_count=row.receipt_count,
            chain_tx=row.chain_tx,
            cid=row.cid,
            anchored_at=isoformat(row.anchored_at),
        )


class ChainPublishResponse(BaseModel):
    anchor_id: int
    day: str
    session_prefix: str
    batch_root: str
    receipt_count: int
    chain_tx: str
    chain_block_number: int
    chain_block_timestamp: int | None
    chain_gas_used: int
    chain_effective_gas_price_wei: int
    chain_transaction_fee_wei: int
    chain_status: int
    commitment_profile: str | None = None


class ChainVerifyResponse(BaseModel):
    anchor_id: int
    day: str
    session_prefix: str
    expected_root: str
    computed_root: str
    expected_receipt_count: int
    on_chain_receipt_count: int
    chain_tx: str | None
    operator: str
    on_chain_timestamp: int
    match: bool
    commitment_profile: str | None = None


class VerificationResponse(BaseModel):
    id: int
    session_id: str | None
    day: str | None
    expected_hash: str | None
    computed_hash: str | None
    expected_root: str | None
    computed_root: str | None
    match: bool
    verification_type: str
    details: dict[str, Any] | None
    created_at: str | None

    @classmethod
    def from_row(cls, row: Verification) -> "VerificationResponse":
        return cls(
            id=row.id,
            session_id=row.session_id,
            day=row.day,
            expected_hash=row.expected_hash,
            computed_hash=row.computed_hash,
            expected_root=row.expected_root,
            computed_root=row.computed_root,
            match=row.match,
            verification_type=row.verification_type,
            details=row.details_json,
            created_at=isoformat(row.created_at),
        )


class AuditMismatchResponse(BaseModel):
    field: str
    stored_column: Any
    receipt_json: Any


class AuditSessionResponse(BaseModel):
    session_id: str
    day: str | None
    match: bool
    receipt_json_match: bool
    hash_match: bool
    stored_json_hash_match: bool
    normalized_match: bool
    expected_hash: str
    rebuilt_hash: str
    stored_receipt_json_hash: str
    normalized_mismatches: list[AuditMismatchResponse]
