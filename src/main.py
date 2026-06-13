# src/main.py
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Query
from sqlalchemy.exc import SQLAlchemyError
from typing import Any, Dict

from . import api_service, audit_service, db
from .api_schemas import (
    AnchorResponse,
    AuditSessionResponse,
    ChainPublishResponse,
    ChainVerifyResponse,
    DbHealthResponse,
    FinalizeSessionRequest,
    FinalizeSessionResponse,
    PaginatedResponse,
    ReceiptResponse,
    SessionDetailResponse,
    SessionSummaryResponse,
    VerificationResponse,
)
from .blockchain.publisher import publish_batch_anchor_by_id
from .blockchain.verifier import verify_on_chain_anchor_by_id
from .receipt_builder import build_receipt, hash_receipt
from .receipt_schema import DEFAULT_SCHEMA_VERSION, DEFAULT_SESSION_TYPE
from .repository import persist_finalized_session
from .storage import save_receipt, write_local_receipts_enabled

app = FastAPI(title="Proof-of-Charge MVP")


@contextmanager
def _with_db_session():
    session = db.session_scope()
    try:
        yield session
    finally:
        session.close()


@app.post("/v1/receipts/finalize", response_model=FinalizeSessionResponse)
def finalize_session(session: FinalizeSessionRequest) -> FinalizeSessionResponse:
    try:
        session_dict: Dict[str, Any] = session.model_dump(by_alias=True)
        session_dict["schema_version"] = session_dict.get("schema_version") or DEFAULT_SCHEMA_VERSION
        session_dict["session_type"] = session_dict.get("session_type") or DEFAULT_SESSION_TYPE
        receipt = build_receipt(session_dict)
        receipt_hash = hash_receipt(receipt)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        db_enabled = db.database_enabled()
        if db_enabled:
            persist_finalized_session(session_dict, receipt, receipt_hash)
        if not db_enabled or write_local_receipts_enabled():
            save_receipt(session.session_id, receipt, receipt_hash, session_dict)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database operation failed") from exc

    # Later: send to IPFS and anchor on-chain.
    return FinalizeSessionResponse(receipt=receipt, hash=receipt_hash)


@app.get("/health/db", response_model=DbHealthResponse)
def health_db() -> DbHealthResponse:
    try:
        return DbHealthResponse(**db.check_database())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.get("/v1/sessions", response_model=PaginatedResponse[SessionSummaryResponse])
def list_sessions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session_prefix: str | None = None,
) -> PaginatedResponse[SessionSummaryResponse]:
    with _with_db_session() as session:
        return api_service.list_sessions(
            db_session=session,
            limit=limit,
            offset=offset,
            session_prefix=session_prefix,
        )


@app.get("/v1/sessions/{session_id}", response_model=SessionDetailResponse)
def get_session(session_id: str) -> SessionDetailResponse:
    with _with_db_session() as session:
        result = api_service.get_session(session, session_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        return result


@app.get("/v1/receipts/{session_id}", response_model=ReceiptResponse)
def get_receipt(session_id: str) -> ReceiptResponse:
    with _with_db_session() as session:
        result = api_service.get_receipt(session, session_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Receipt {session_id} not found")
        return result


@app.get("/v1/anchors", response_model=PaginatedResponse[AnchorResponse])
def list_anchors(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    day: str | None = None,
    session_prefix: str | None = None,
) -> PaginatedResponse[AnchorResponse]:
    with _with_db_session() as session:
        return api_service.list_anchors(
            db_session=session,
            limit=limit,
            offset=offset,
            day=day,
            session_prefix=session_prefix,
        )


@app.post("/v1/anchors/{anchor_id}/publish-chain", response_model=ChainPublishResponse)
def publish_anchor_chain(anchor_id: int, force: bool = False) -> ChainPublishResponse:
    try:
        return ChainPublishResponse(**publish_batch_anchor_by_id(anchor_id, force=force))
    except ValueError as exc:
        detail = str(exc)
        status_code = 409 if "already has chain_tx" in detail else 404
        raise HTTPException(status_code=status_code, detail=detail)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database operation failed") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/v1/anchors/{anchor_id}/verify-chain", response_model=ChainVerifyResponse)
def verify_anchor_chain(anchor_id: int) -> ChainVerifyResponse:
    try:
        return ChainVerifyResponse(**verify_on_chain_anchor_by_id(anchor_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database operation failed") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/v1/verifications", response_model=PaginatedResponse[VerificationResponse])
def list_verifications(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session_id: str | None = None,
    day: str | None = None,
    verification_type: str | None = None,
) -> PaginatedResponse[VerificationResponse]:
    with _with_db_session() as session:
        return api_service.list_verifications(
            db_session=session,
            limit=limit,
            offset=offset,
            session_id=session_id,
            day=day,
            verification_type=verification_type,
        )


@app.post("/v1/audit/sessions/{session_id}", response_model=AuditSessionResponse)
def audit_session(session_id: str) -> AuditSessionResponse:
    try:
        return AuditSessionResponse(**audit_service.audit_session(session_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
