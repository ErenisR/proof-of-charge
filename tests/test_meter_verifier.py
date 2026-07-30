from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.audit_service import audit_session
from src.meter_verifier import verify_meter_stream_from_db
from src.models import Base, ChargingSession, MeterValue
from src.receipt_builder import build_receipt, hash_receipt
from src.repository import persist_finalized_session


def _payload():
    return {
        "schema_version": "v2g-v1", "session_id": "meter-db-001", "user_id": "u",
        "evse_id": "e", "ocpp_tx_id": "tx", "session_type": "charge_only",
        "start_ts": "2026-07-01T10:00:00Z", "end_ts": "2026-07-01T10:10:00Z",
        "meter_values": [
            {"ts": "2026-07-01T10:00:00Z", "import_kwh": 1.0, "export_kwh": 0.0},
            {"ts": "2026-07-01T10:05:00Z", "import_kwh": 2.0, "export_kwh": 0.0},
            {"ts": "2026-07-01T10:10:00Z", "import_kwh": 3.0, "export_kwh": 0.0},
        ],
        "pricing": {"currency": "EUR", "model": "TOU", "import_components": [], "export_components": []},
    }


def test_normalized_meter_verifier_reads_rows_and_extended_audit_exposes_mismatch():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        payload = _payload()
        receipt = build_receipt(payload)
        persist_finalized_session(payload, receipt, hash_receipt(receipt), session)
        session.commit()
        assert verify_meter_stream_from_db(payload["session_id"], session, False)["match"] is True
        original_audit = audit_session(payload["session_id"], session, False, verify_normalized_meter_rows=False)
        middle = session.query(MeterValue).filter_by(session_id=payload["session_id"], sample_index=1).one()
        middle.import_kwh += 0.25
        session.flush()
        assert session.get(ChargingSession, payload["session_id"]).session_json == payload
        # The legacy rebuild remains valid because it reads session_json.
        legacy_after = audit_session(payload["session_id"], session, False, verify_normalized_meter_rows=False)
        meter_after = verify_meter_stream_from_db(payload["session_id"], session, False)
        combined_after = audit_session(payload["session_id"], session, False)
        assert original_audit["match"] is True
        assert legacy_after["match"] is True
        assert meter_after["match"] is False
        assert meter_after["meter_root_match"] is False
        assert combined_after["match"] is False
        assert combined_after["normalized_meter_rows_match"] is False


def test_meter_deletion_changes_count_and_root():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        payload = _payload()
        receipt = build_receipt(payload)
        persist_finalized_session(payload, receipt, hash_receipt(receipt), session)
        session.commit()
        middle = session.query(MeterValue).filter_by(session_id=payload["session_id"], sample_index=1).one()
        session.delete(middle)
        session.flush()
        result = verify_meter_stream_from_db(payload["session_id"], session, False)
        assert result["meter_sample_count"] == 2
        assert result["expected_sample_count"] == 3
        assert result["meter_sample_count_match"] is False
        assert result["meter_root_match"] is False
