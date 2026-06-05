# src/main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Dict, List
from . import db
from .receipt_builder import build_receipt, hash_receipt
from .receipt_schema import DEFAULT_SCHEMA_VERSION, DEFAULT_SESSION_TYPE
from .repository import persist_finalized_session
from .storage import save_receipt, write_local_receipts_enabled

app = FastAPI(title="Proof-of-Charge MVP")

class MeterValue(BaseModel):
    ts: str
    energy_kwh: float | None = None
    import_kwh: float | None = None
    export_kwh: float | None = None

class PricingComponent(BaseModel):
    from_ts: str = Field(..., alias="from")
    to_ts: str = Field(..., alias="to")
    price_per_kwh: float

class Pricing(BaseModel):
    currency: str = "EUR"
    model: str = "TOU"
    components: List[PricingComponent] = Field(default_factory=list)
    import_components: List[PricingComponent] = Field(default_factory=list)
    export_components: List[PricingComponent] = Field(default_factory=list)

class SessionInput(BaseModel):
    session_id: str
    user_id: str
    evse_id: str
    ocpp_tx_id: str
    start_ts: str
    end_ts: str
    schema_version: str | None = DEFAULT_SCHEMA_VERSION
    session_type: str | None = DEFAULT_SESSION_TYPE
    energy_summary: Dict[str, Any] | None = None
    settlement: Dict[str, Any] | None = None
    meter_values: List[MeterValue]
    pricing: Pricing | None = None

@app.post("/v1/receipts/finalize")
def finalize_session(session: SessionInput):
    try:
        session_dict: Dict[str, Any] = session.model_dump(by_alias=True)
        receipt = build_receipt(session_dict)
        receipt_hash = hash_receipt(receipt)

        db_enabled = db.database_enabled()
        if db_enabled:
            persist_finalized_session(session_dict, receipt, receipt_hash)
        if not db_enabled or write_local_receipts_enabled():
            save_receipt(session.session_id, receipt, receipt_hash, session_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Later: send to IPFS and anchor on-chain.
    return {
        "receipt": receipt,
        "hash": receipt_hash
    }
