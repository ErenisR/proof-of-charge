# src/main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from .receipt_builder import build_receipt, hash_receipt
from .storage import save_receipt

app = FastAPI(title="Proof-of-Charge MVP")

class MeterValue(BaseModel):
    ts: str
    energy_kwh: float

class PricingComponent(BaseModel):
    from_ts: str = Field(..., alias="from")
    to_ts: str = Field(..., alias="to")
    price_per_kwh: float

class Pricing(BaseModel):
    currency: str = "EUR"
    model: str = "TOU"
    components: List[PricingComponent] = []

class SessionInput(BaseModel):
    session_id: str
    user_id: str
    evse_id: str
    ocpp_tx_id: str
    start_ts: str
    end_ts: str
    meter_values: List[MeterValue]
    pricing: Pricing | None = None

@app.post("/v1/receipts/finalize")
def finalize_session(session: SessionInput):
    try:
        session_dict: Dict[str, Any] = session.model_dump(by_alias=True)
        receipt = build_receipt(session_dict)
        receipt_hash = hash_receipt(receipt)
        
        save_receipt(session.session_id, receipt, receipt_hash, session_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Later: store to DB, send to IPFS, anchor on-chain
    return {
        "receipt": receipt,
        "hash": receipt_hash
    }
