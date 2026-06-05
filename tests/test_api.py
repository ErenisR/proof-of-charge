import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src import main
from src.models import Base
from src.receipt_builder import build_receipt, hash_receipt
from src.receipt_schema import DEFAULT_SCHEMA_VERSION
from src.repository import persist_batch_anchor, persist_batch_verification, persist_finalized_session


def _session() -> dict:
    return {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "session_id": "api-session-0001",
        "user_id": "user-001",
        "evse_id": "EVSE-001",
        "ocpp_tx_id": "tx-api-001",
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


def _prepare_api_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_payload = _session()
    receipt = build_receipt(session_payload)
    receipt_hash = hash_receipt(receipt)

    with Session(engine) as db_session:
        persist_finalized_session(session_payload, receipt, receipt_hash, db_session=db_session)
        persist_batch_anchor(
            day="2026-03-07",
            session_prefix="api-session",
            batch_root="0xbatchroot",
            receipt_count=1,
            receipt_memberships=[
                {
                    "session_id": "api-session-0001",
                    "receipt_hash": receipt_hash,
                    "leaf_index": 0,
                }
            ],
            db_session=db_session,
        )
        persist_batch_verification(
            {
                "day": "2026-03-07",
                "expected_root": "0xbatchroot",
                "computed_root": "0xbatchroot",
                "match": True,
                "receipt_count": 1,
                "session_ids": ["api-session-0001"],
            },
            db_session=db_session,
        )
        db_session.commit()

    monkeypatch.setattr(main.db, "session_scope", lambda: Session(engine))
    monkeypatch.setattr(
        main.db,
        "check_database",
        lambda: {
            "ok": True,
            "user": "proof",
            "database": "proof_of_charge",
            "current_revision": "head",
            "head_revision": "head",
            "missing_tables": [],
        },
    )

def test_health_db(monkeypatch):
    _prepare_api_db(monkeypatch)
    response = main.health_db().model_dump()

    assert response["ok"] is True


def test_list_and_get_sessions(monkeypatch):
    _prepare_api_db(monkeypatch)

    listed = main.list_sessions(limit=50, offset=0).model_dump()
    detail = main.get_session("api-session-0001").model_dump()

    assert listed["items"][0]["session_id"] == "api-session-0001"
    assert listed["items"][0]["receipt_hash"].startswith("0x")
    assert detail["session"]["session_id"] == "api-session-0001"
    assert len(detail["meter_values"]) == 2


def test_get_receipt_anchors_and_verifications(monkeypatch):
    _prepare_api_db(monkeypatch)

    receipt = main.get_receipt("api-session-0001").model_dump()
    anchors = main.list_anchors(limit=50, offset=0, session_prefix="api-session").model_dump()
    verifications = main.list_verifications(limit=50, offset=0, verification_type="batch").model_dump()

    assert receipt["session_id"] == "api-session-0001"
    assert receipt["receipt"]["session_id"] == "api-session-0001"
    assert anchors["items"][0]["batch_root"] == "0xbatchroot"
    assert verifications["items"][0]["match"] is True


def test_audit_session(monkeypatch):
    _prepare_api_db(monkeypatch)

    audit = main.audit_session("api-session-0001").model_dump()
    verifications = main.list_verifications(
        limit=50,
        offset=0,
        session_id="api-session-0001",
        verification_type="audit",
    ).model_dump()

    assert audit["match"] is True
    assert audit["hash_match"] is True
    assert audit["receipt_json_match"] is True
    assert verifications["items"][0]["verification_type"] == "audit"


def test_missing_receipt_returns_404(monkeypatch):
    _prepare_api_db(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        main.get_receipt("missing")

    assert exc.value.status_code == 404
