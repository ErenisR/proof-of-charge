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


def test_finalize_session_validation_error_returns_400(monkeypatch):
    session_payload = _session()
    session_payload["meter_values"] = session_payload["meter_values"][:1]
    request = main.FinalizeSessionRequest.model_validate(session_payload)

    monkeypatch.setattr(main.db, "database_enabled", lambda: False)

    with pytest.raises(HTTPException) as exc:
        main.finalize_session(request)

    assert exc.value.status_code == 400
    assert "Need at least 2 meter values" in exc.value.detail


def test_finalize_session_database_error_returns_503(monkeypatch):
    request = main.FinalizeSessionRequest.model_validate(_session())

    def raise_database_error(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(main.db, "database_enabled", lambda: True)
    monkeypatch.setattr(main, "persist_finalized_session", raise_database_error)

    with pytest.raises(HTTPException) as exc:
        main.finalize_session(request)

    assert exc.value.status_code == 503
    assert "database unavailable" in exc.value.detail


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


def test_publish_anchor_chain(monkeypatch):
    _prepare_api_db(monkeypatch)

    def publish(anchor_id, force=False):
        return {
            "anchor_id": anchor_id,
            "day": "2026-03-07",
            "session_prefix": "api-session",
            "batch_root": "0xbatchroot",
            "receipt_count": 1,
            "chain_tx": "0x" + "a" * 64,
            "chain_block_number": 12,
            "chain_block_timestamp": 1780000000,
            "chain_gas_used": 142675,
            "chain_effective_gas_price_wei": 771713212,
            "chain_transaction_fee_wei": 110104182522100,
            "chain_status": 1,
        }

    monkeypatch.setattr(main, "publish_batch_anchor_by_id", publish)

    response = main.publish_anchor_chain(1).model_dump()

    assert response["anchor_id"] == 1
    assert response["chain_tx"] == "0x" + "a" * 64
    assert response["chain_gas_used"] == 142675


def test_publish_anchor_chain_conflict(monkeypatch):
    _prepare_api_db(monkeypatch)

    def publish(anchor_id, force=False):
        raise ValueError("Anchor 1 already has chain_tx=0xabc. Use force=true to republish.")

    monkeypatch.setattr(main, "publish_batch_anchor_by_id", publish)

    with pytest.raises(HTTPException) as exc:
        main.publish_anchor_chain(1)

    assert exc.value.status_code == 409


def test_verify_anchor_chain(monkeypatch):
    _prepare_api_db(monkeypatch)

    def verify(anchor_id):
        return {
            "anchor_id": anchor_id,
            "day": "2026-03-07",
            "session_prefix": "api-session",
            "expected_root": "0xbatchroot",
            "computed_root": "0xbatchroot",
            "expected_receipt_count": 1,
            "on_chain_receipt_count": 1,
            "chain_tx": "0x" + "a" * 64,
            "operator": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
            "on_chain_timestamp": 1780000000,
            "match": True,
        }

    monkeypatch.setattr(main, "verify_on_chain_anchor_by_id", verify)

    response = main.verify_anchor_chain(1).model_dump()

    assert response["anchor_id"] == 1
    assert response["match"] is True
    assert response["operator"] == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


def test_verify_anchor_chain_missing_returns_404(monkeypatch):
    _prepare_api_db(monkeypatch)

    def verify(anchor_id):
        raise ValueError(f"No DB batch anchor found for id {anchor_id}")

    monkeypatch.setattr(main, "verify_on_chain_anchor_by_id", verify)

    with pytest.raises(HTTPException) as exc:
        main.verify_anchor_chain(999)

    assert exc.value.status_code == 404


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
