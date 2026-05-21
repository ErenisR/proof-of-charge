from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import db
from .models import BatchAnchor, BatchAnchorReceipt, ChargingSession, MeterValue, Receipt, Verification


def persist_finalized_session(
    session_payload: Dict[str, Any],
    receipt: Dict[str, Any],
    receipt_hash: str,
    db_session: Session | None = None,
) -> None:
    owns_session = db_session is None
    session = db_session or db.session_scope()

    try:
        _upsert_finalized_session(session, session_payload, receipt, receipt_hash)
        if owns_session:
            session.commit()
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def _upsert_finalized_session(
    session: Session,
    session_payload: Dict[str, Any],
    receipt: Dict[str, Any],
    receipt_hash: str,
) -> None:
    session_id = session_payload["session_id"]
    session_start = _parse_ts(session_payload["start_ts"])
    session_end = _parse_ts(session_payload["end_ts"])
    receipt_start = _parse_ts(receipt["start_ts"])
    receipt_end = _parse_ts(receipt["end_ts"])
    existing_session = session.get(ChargingSession, session_id)
    if existing_session:
        charging_session = existing_session
        charging_session.user_id = session_payload["user_id"]
        charging_session.evse_id = session_payload["evse_id"]
        charging_session.ocpp_tx_id = session_payload["ocpp_tx_id"]
        charging_session.schema_version = session_payload.get("schema_version") or receipt["schema_version"]
        charging_session.session_type = session_payload.get("session_type") or receipt["session_type"]
        charging_session.start_ts = session_start
        charging_session.end_ts = session_end
        charging_session.session_json = session_payload
    else:
        charging_session = ChargingSession(
            session_id=session_id,
            user_id=session_payload["user_id"],
            evse_id=session_payload["evse_id"],
            ocpp_tx_id=session_payload["ocpp_tx_id"],
            schema_version=session_payload.get("schema_version") or receipt["schema_version"],
            session_type=session_payload.get("session_type") or receipt["session_type"],
            start_ts=session_start,
            end_ts=session_end,
            session_json=session_payload,
        )
        session.add(charging_session)

    session.execute(delete(MeterValue).where(MeterValue.session_id == session_id))
    for index, meter_value in enumerate(session_payload.get("meter_values", []) or []):
        session.add(
            MeterValue(
                session_id=session_id,
                sample_index=index,
                ts=_parse_ts(meter_value["ts"]),
                energy_kwh=_optional_float(meter_value.get("energy_kwh")),
                import_kwh=_optional_float(meter_value.get("import_kwh")),
                export_kwh=_optional_float(meter_value.get("export_kwh")),
            )
        )

    energy_summary = receipt.get("energy_summary") or {}
    existing_receipt = session.get(Receipt, session_id)
    if existing_receipt:
        db_receipt = existing_receipt
        db_receipt.receipt_hash = receipt_hash
        db_receipt.merkle_root = receipt["merkle_root"]
        db_receipt.schema_version = receipt["schema_version"]
        db_receipt.session_type = receipt["session_type"]
        db_receipt.energy_kwh = float(receipt["energy_kwh"])
        db_receipt.import_kwh = float(energy_summary["import_kwh"])
        db_receipt.export_kwh = float(energy_summary["export_kwh"])
        db_receipt.net_kwh = float(energy_summary["net_kwh"])
        db_receipt.start_ts = receipt_start
        db_receipt.end_ts = receipt_end
        db_receipt.receipt_json = receipt
    else:
        session.add(
            Receipt(
                session_id=session_id,
                receipt_hash=receipt_hash,
                merkle_root=receipt["merkle_root"],
                schema_version=receipt["schema_version"],
                session_type=receipt["session_type"],
                energy_kwh=float(receipt["energy_kwh"]),
                import_kwh=float(energy_summary["import_kwh"]),
                export_kwh=float(energy_summary["export_kwh"]),
                net_kwh=float(energy_summary["net_kwh"]),
                start_ts=receipt_start,
                end_ts=receipt_end,
                receipt_json=receipt,
            )
        )


def get_receipt_by_session_id(session_id: str, db_session: Session) -> Receipt | None:
    return db_session.scalar(select(Receipt).where(Receipt.session_id == session_id))


def persist_batch_anchor(
    day: str,
    batch_root: str,
    receipt_count: int,
    receipt_memberships: list[Dict[str, Any]] | None = None,
    session_prefix: str | None = None,
    chain_tx: str | None = None,
    cid: str | None = None,
    db_session: Session | None = None,
) -> BatchAnchor:
    owns_session = db_session is None
    session = db_session or db.session_scope()
    normalized_prefix = session_prefix or ""

    try:
        existing_anchor = session.scalar(
            select(BatchAnchor).where(
                BatchAnchor.day == day,
                BatchAnchor.session_prefix == normalized_prefix,
                BatchAnchor.batch_root == batch_root,
            )
        )
        if existing_anchor:
            if receipt_memberships:
                _ensure_anchor_memberships(session, existing_anchor, receipt_memberships)
            if owns_session:
                session.commit()
            return existing_anchor

        anchor = BatchAnchor(
            day=day,
            session_prefix=normalized_prefix,
            batch_root=batch_root,
            receipt_count=receipt_count,
            chain_tx=chain_tx,
            cid=cid,
        )
        session.add(anchor)
        session.flush()

        _ensure_anchor_memberships(session, anchor, receipt_memberships or [])
        if owns_session:
            session.commit()
        return anchor
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def persist_batch_verification(result: Dict[str, Any], db_session: Session | None = None) -> None:
    owns_session = db_session is None
    session = db_session or db.session_scope()

    try:
        session.add(
            Verification(
                day=result.get("day"),
                session_id=result.get("session_id"),
                expected_hash=result.get("expected_hash"),
                computed_hash=result.get("computed_hash"),
                expected_root=result.get("expected_root"),
                computed_root=result.get("computed_root"),
                match=bool(result.get("match")),
                verification_type="batch",
                details_json=result,
            )
        )
        if owns_session:
            session.commit()
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _ensure_anchor_memberships(
    session: Session,
    anchor: BatchAnchor,
    receipt_memberships: list[Dict[str, Any]],
) -> None:
    existing = {
        membership.session_id
        for membership in session.scalars(
            select(BatchAnchorReceipt).where(BatchAnchorReceipt.anchor_id == anchor.id)
        )
    }
    for membership in receipt_memberships:
        if membership["session_id"] in existing:
            continue
        session.add(
            BatchAnchorReceipt(
                anchor_id=anchor.id,
                session_id=membership["session_id"],
                receipt_hash=membership["receipt_hash"],
                leaf_index=int(membership["leaf_index"]),
            )
        )


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(raw)
    else:
        raise ValueError(f"Invalid timestamp: {value}")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
