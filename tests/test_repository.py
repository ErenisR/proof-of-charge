from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.models import Base, BatchAnchor, BatchAnchorReceipt, MeterValue, Verification
from src.receipt_builder import build_receipt, hash_receipt
from src.receipt_schema import DEFAULT_SCHEMA_VERSION
from src.repository import (
    get_receipt_by_session_id,
    persist_batch_anchor,
    persist_batch_verification,
    persist_finalized_session,
)


def _session() -> dict:
    return {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "session_id": "db-session-001",
        "user_id": "user-001",
        "evse_id": "EVSE-001",
        "ocpp_tx_id": "tx-db-001",
        "session_type": "charge_only",
        "start_ts": "2026-03-07T10:00:00Z",
        "end_ts": "2026-03-07T10:05:00Z",
        "meter_values": [
            {
                "ts": "2026-03-07T10:00:00Z",
                "import_kwh": 1.0,
                "export_kwh": 0.0,
                "energy_kwh": 1.0,
            },
            {
                "ts": "2026-03-07T10:05:00Z",
                "import_kwh": 3.5,
                "export_kwh": 0.0,
                "energy_kwh": 3.5,
            },
        ],
        "pricing": {
            "currency": "EUR",
            "model": "TOU",
            "import_components": [
                {
                    "from": "2026-03-07T10:00:00Z",
                    "to": "2026-03-07T10:05:00Z",
                    "price_per_kwh": 0.25,
                }
            ],
            "export_components": [],
        },
    }


def test_persist_finalized_session_writes_session_receipt_and_meter_values():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    session_payload = _session()
    receipt = build_receipt(session_payload)
    receipt_hash = hash_receipt(receipt)

    with Session(engine) as db_session:
        persist_finalized_session(session_payload, receipt, receipt_hash, db_session=db_session)
        db_session.commit()

        stored_receipt = get_receipt_by_session_id("db-session-001", db_session)
        meter_count = db_session.query(MeterValue).filter_by(session_id="db-session-001").count()

        assert stored_receipt is not None
        assert stored_receipt.receipt_hash == receipt_hash
        assert stored_receipt.merkle_root == receipt["merkle_root"]
        assert stored_receipt.import_kwh == 2.5
        assert stored_receipt.net_kwh == 2.5
        assert stored_receipt.receipt_json["session_id"] == "db-session-001"
        assert meter_count == 2


def test_persist_finalized_session_is_idempotent_for_same_session_id():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    session_payload = _session()
    receipt = build_receipt(session_payload)
    receipt_hash = hash_receipt(receipt)

    with Session(engine) as db_session:
        persist_finalized_session(session_payload, receipt, receipt_hash, db_session=db_session)
        persist_finalized_session(session_payload, receipt, receipt_hash, db_session=db_session)
        db_session.commit()

        meter_count = db_session.query(MeterValue).filter_by(session_id="db-session-001").count()

        assert get_receipt_by_session_id("db-session-001", db_session) is not None
        assert meter_count == 2


def test_persist_batch_anchor_and_verification():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    result = {
        "day": "2026-03-07",
        "session_prefix": "run-test",
        "expected_root": "0xabc",
        "computed_root": "0xabc",
        "match": True,
        "receipt_count": 2,
        "session_ids": ["run-test-0001", "run-test-0002"],
    }

    with Session(engine) as db_session:
        session_payload = _session()
        receipt = build_receipt(session_payload)
        receipt_hash = hash_receipt(receipt)
        persist_finalized_session(session_payload, receipt, receipt_hash, db_session=db_session)
        persist_batch_anchor(
            day="2026-03-07",
            session_prefix="run-test",
            batch_root="0xabc",
            receipt_count=2,
            receipt_memberships=[
                {"session_id": "db-session-001", "receipt_hash": receipt_hash, "leaf_index": 0}
            ],
            db_session=db_session,
        )
        persist_batch_verification(result, db_session=db_session)
        db_session.commit()

        anchor = db_session.query(BatchAnchor).one()
        membership = db_session.query(BatchAnchorReceipt).one()
        verification = db_session.query(Verification).one()

        assert anchor.day == "2026-03-07"
        assert anchor.session_prefix == "run-test"
        assert anchor.batch_root == "0xabc"
        assert membership.anchor_id == anchor.id
        assert membership.session_id == "db-session-001"
        assert membership.receipt_hash == receipt_hash
        assert membership.leaf_index == 0
        assert verification.verification_type == "batch"
        assert verification.match is True
        assert verification.details_json["receipt_count"] == 2
