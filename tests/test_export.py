import csv

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.export import export_all_from_db
from src.models import Base
from src.receipt_builder import build_receipt, hash_receipt
from src.receipt_schema import DEFAULT_SCHEMA_VERSION
from src.repository import persist_batch_anchor, persist_batch_verification, persist_finalized_session


def _session() -> dict:
    return {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "session_id": "run-export-0001",
        "user_id": "user-001",
        "evse_id": "EVSE-001",
        "ocpp_tx_id": "tx-export-001",
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
                "import_kwh": 4.0,
                "export_kwh": 0.0,
                "energy_kwh": 4.0,
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


def test_export_all_from_db_writes_research_csvs(tmp_path):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    session_payload = _session()
    receipt = build_receipt(session_payload)
    receipt_hash = hash_receipt(receipt)
    verification = {
        "day": "2026-03-07",
        "session_prefix": "run-export",
        "expected_root": "0xbatchroot",
        "computed_root": "0xbatchroot",
        "match": True,
        "receipt_count": 1,
        "session_ids": ["run-export-0001"],
    }

    with Session(engine) as db_session:
        persist_finalized_session(session_payload, receipt, receipt_hash, db_session=db_session)
        persist_batch_anchor(
            day="2026-03-07",
            session_prefix="run-export",
            batch_root="0xbatchroot",
            receipt_count=1,
            db_session=db_session,
        )
        persist_batch_verification(verification, db_session=db_session)
        db_session.commit()

        export_all_from_db(db_session, export_dir=tmp_path)

    receipts = _read_csv(tmp_path / "receipts.csv")
    sessions = _read_csv(tmp_path / "sessions.csv")
    meter_values = _read_csv(tmp_path / "meter_values.csv")
    anchors = _read_csv(tmp_path / "anchors.csv")
    verifications = _read_csv(tmp_path / "verifications.csv")

    assert receipts[0]["session_id"] == "run-export-0001"
    assert receipts[0]["receipt_hash"] == receipt_hash
    assert receipts[0]["batch_root"] == "0xbatchroot"
    assert receipts[0]["batch_day"] == "2026-03-07"
    assert sessions[0]["evse_id"] == "EVSE-001"
    assert sessions[0]["tariff_model"] == "TOU"
    assert len(meter_values) == 2
    assert anchors[0]["receipt_count"] == "1"
    assert verifications[0]["batch_root"] == "0xbatchroot"
    assert verifications[0]["match"] == "True"


def _read_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
