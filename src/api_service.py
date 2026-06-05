from sqlalchemy import select
from sqlalchemy.orm import Session

from .api_schemas import (
    AnchorResponse,
    PaginatedResponse,
    ReceiptResponse,
    SessionDetailResponse,
    SessionSummaryResponse,
    VerificationResponse,
)
from .models import BatchAnchor, ChargingSession, Receipt, Verification


def list_sessions(
    db_session: Session,
    limit: int,
    offset: int,
    session_prefix: str | None = None,
) -> PaginatedResponse[SessionSummaryResponse]:
    stmt = select(ChargingSession).order_by(ChargingSession.start_ts.desc(), ChargingSession.session_id)
    if session_prefix:
        stmt = stmt.where(ChargingSession.session_id.like(f"{session_prefix}-%"))
    rows = list(db_session.scalars(stmt.offset(offset).limit(limit)))
    return PaginatedResponse(
        items=[SessionSummaryResponse.from_row(row) for row in rows],
        limit=limit,
        offset=offset,
    )


def get_session(db_session: Session, session_id: str) -> SessionDetailResponse | None:
    row = db_session.get(ChargingSession, session_id)
    if not row:
        return None
    return SessionDetailResponse.from_row(row)


def get_receipt(db_session: Session, session_id: str) -> ReceiptResponse | None:
    row = db_session.get(Receipt, session_id)
    if not row:
        return None
    return ReceiptResponse.from_row(row)


def list_anchors(
    db_session: Session,
    limit: int,
    offset: int,
    day: str | None = None,
    session_prefix: str | None = None,
) -> PaginatedResponse[AnchorResponse]:
    stmt = select(BatchAnchor).order_by(BatchAnchor.anchored_at.desc(), BatchAnchor.id.desc())
    if day:
        stmt = stmt.where(BatchAnchor.day == day)
    if session_prefix is not None:
        stmt = stmt.where(BatchAnchor.session_prefix == session_prefix)
    rows = list(db_session.scalars(stmt.offset(offset).limit(limit)))
    return PaginatedResponse(
        items=[AnchorResponse.from_row(row) for row in rows],
        limit=limit,
        offset=offset,
    )


def list_verifications(
    db_session: Session,
    limit: int,
    offset: int,
    session_id: str | None = None,
    day: str | None = None,
    verification_type: str | None = None,
) -> PaginatedResponse[VerificationResponse]:
    stmt = select(Verification).order_by(Verification.created_at.desc(), Verification.id.desc())
    if session_id:
        stmt = stmt.where(Verification.session_id == session_id)
    if day:
        stmt = stmt.where(Verification.day == day)
    if verification_type:
        stmt = stmt.where(Verification.verification_type == verification_type)
    rows = list(db_session.scalars(stmt.offset(offset).limit(limit)))
    return PaginatedResponse(
        items=[VerificationResponse.from_row(row) for row in rows],
        limit=limit,
        offset=offset,
    )
