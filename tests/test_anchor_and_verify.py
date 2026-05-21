import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src import batch_anchoring, verifier, verifier_batch
from src.models import Base, BatchAnchor, BatchAnchorReceipt, Receipt, Verification
from src.receipt_builder import build_receipt, hash_receipt
from src.receipt_schema import DEFAULT_SCHEMA_VERSION
from src.repository import persist_finalized_session


def _session(session_id: str, start_import: float, end_import: float) -> dict:
    return {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "session_id": session_id,
        "user_id": "user-001",
        "evse_id": "EVSE-001",
        "ocpp_tx_id": f"tx-{session_id}",
        "session_type": "charge_only",
        "start_ts": "2026-03-07T10:00:00Z",
        "end_ts": "2026-03-07T10:05:00Z",
        "meter_values": [
            {
                "ts": "2026-03-07T10:00:00Z",
                "import_kwh": start_import,
                "export_kwh": 0.0,
            },
            {
                "ts": "2026-03-07T10:05:00Z",
                "import_kwh": end_import,
                "export_kwh": 0.0,
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


def _write_payload(tmp_path: Path, session: dict) -> tuple[dict, dict]:
    receipt = build_receipt(session)
    receipt_hash = hash_receipt(receipt)
    payload = {
        "receipt": receipt,
        "hash": receipt_hash,
        "session": session,
    }
    path = tmp_path / f"{session['session_id']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload, {
        "file": str(path),
        "hash": receipt_hash,
        "cid": None,
        "chain_tx": None,
        "batch_root": None,
        "batch_day": None,
    }


def test_anchor_day_and_verify_day_round_trip(tmp_path, monkeypatch):
    _, first_entry = _write_payload(tmp_path, _session("run-test-0001", 0.0, 2.0))
    _, second_entry = _write_payload(tmp_path, _session("run-test-0002", 1.0, 4.0))
    index = {
        "run-test-0001": first_entry,
        "run-test-0002": second_entry,
    }

    def save_index(updated_index):
        saved = dict(updated_index)
        index.clear()
        index.update(saved)

    anchors_file = tmp_path / "anchors.json"
    monkeypatch.setattr(batch_anchoring, "ANCHORS_FILE", anchors_file)
    monkeypatch.setattr(batch_anchoring, "load_index", lambda: index)
    monkeypatch.setattr(batch_anchoring, "save_index", save_index)
    monkeypatch.setattr(verifier_batch, "ANCHORS_FILE", anchors_file)
    monkeypatch.setattr(verifier_batch, "load_index", lambda: index)

    batch_root, count = batch_anchoring.anchor_day("2026-03-07", session_prefix="run-test")
    result = verifier_batch.verify_day("2026-03-07", session_prefix="run-test")

    assert count == 2
    assert batch_root.startswith("0x")
    assert result["match"] is True
    assert result["receipt_count"] == 2
    assert index["run-test-0001"]["batch_root"] == batch_root
    assert index["run-test-0001"]["batch_day"] == "2026-03-07"


def test_tampered_receipt_fails_single_receipt_verification(tmp_path, monkeypatch):
    payload, entry = _write_payload(tmp_path, _session("session-to-tamper", 0.0, 2.0))
    path = Path(entry["file"])
    payload["receipt"]["energy_kwh"] = 99.0
    path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(verifier, "load_index", lambda: {"session-to-tamper": entry})

    assert verifier.verify_session("session-to-tamper") is False


def test_anchor_day_and_verify_day_from_db_round_trip():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as db_session:
        _persist_session(db_session, _session("run-db-0001", 0.0, 2.0))
        _persist_session(db_session, _session("run-db-0002", 1.0, 4.0))
        db_session.commit()

        batch_root, count = batch_anchoring.anchor_day_from_db(
            "2026-03-07",
            session_prefix="run-db",
            db_session=db_session,
        )
        result = verifier_batch.verify_day_from_db(
            "2026-03-07",
            session_prefix="run-db",
            db_session=db_session,
        )

        anchors = db_session.query(BatchAnchor).all()
        memberships = db_session.query(BatchAnchorReceipt).order_by(BatchAnchorReceipt.leaf_index).all()
        verifications = db_session.query(Verification).all()

        assert count == 2
        assert batch_root.startswith("0x")
        assert result["match"] is True
        assert result["receipt_count"] == 2
        assert set(result["session_ids"]) == {"run-db-0001", "run-db-0002"}
        assert len(anchors) == 1
        assert anchors[0].batch_root == batch_root
        assert len(memberships) == 2
        assert [membership.leaf_index for membership in memberships] == [0, 1]
        assert {membership.session_id for membership in memberships} == {"run-db-0001", "run-db-0002"}
        assert len(verifications) == 1
        assert verifications[0].match is True


def test_anchor_day_from_db_filters_by_prefix():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as db_session:
        _persist_session(db_session, _session("prefix-a-0001", 0.0, 2.0))
        _persist_session(db_session, _session("prefix-a-0002", 0.0, 3.0))
        _persist_session(db_session, _session("prefix-b-0001", 0.0, 4.0))
        db_session.commit()

        _batch_root, count = batch_anchoring.anchor_day_from_db(
            "2026-03-07",
            session_prefix="prefix-a",
            db_session=db_session,
        )

        assert count == 2
        assert db_session.query(BatchAnchor).one().receipt_count == 2
        assert db_session.query(BatchAnchorReceipt).count() == 2


def test_verify_day_from_db_uses_stored_anchor_membership_snapshot():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as db_session:
        _persist_session(db_session, _session("run-snapshot-0001", 0.0, 2.0))
        _persist_session(db_session, _session("run-snapshot-0002", 1.0, 4.0))
        db_session.commit()

        batch_root, _count = batch_anchoring.anchor_day_from_db(
            "2026-03-07",
            session_prefix="run-snapshot",
            db_session=db_session,
        )

        receipt = db_session.get(Receipt, "run-snapshot-0001")
        receipt.receipt_hash = "0x" + "f" * 64
        db_session.commit()

        result = verifier_batch.verify_day_from_db(
            "2026-03-07",
            session_prefix="run-snapshot",
            db_session=db_session,
        )

        assert result["match"] is True
        assert result["computed_root"] == batch_root


def _persist_session(db_session: Session, session_payload: dict) -> None:
    receipt = build_receipt(session_payload)
    receipt_hash = hash_receipt(receipt)
    persist_finalized_session(session_payload, receipt, receipt_hash, db_session=db_session)
