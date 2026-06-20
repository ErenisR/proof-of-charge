from datetime import datetime, timezone
import random

from src.receipt_builder import build_receipt, hash_receipt
from src.session_validation import summarize_session_metrics, validate_session_receipt
from src.synthetic_sessions import generate_session


def _session(session_type: str = "charge_only") -> dict:
    return generate_session(
        1,
        rng=random.Random(42),
        base_now=datetime(2026, 3, 7, 23, 59, tzinfo=timezone.utc),
        session_prefix="validation",
        deterministic_tx=True,
        session_mode=session_type,
    )


def test_validate_session_receipt_accepts_valid_charge_only_session():
    session = _session("charge_only")
    receipt = build_receipt(session)
    receipt_hash = hash_receipt(receipt)

    assert validate_session_receipt(session, receipt, receipt_hash) == []


def test_validate_session_receipt_accepts_valid_bidirectional_session():
    session = _session("bidirectional")
    receipt = build_receipt(session)
    receipt_hash = hash_receipt(receipt)

    assert validate_session_receipt(session, receipt, receipt_hash) == []


def test_validate_session_receipt_detects_meter_ordering_error():
    session = _session("charge_only")
    session["meter_values"] = list(reversed(session["meter_values"]))
    receipt = build_receipt(session)
    receipt_hash = hash_receipt(receipt)

    failures = validate_session_receipt(session, receipt, receipt_hash)

    assert "meter_values_not_ordered" in failures


def test_validate_session_receipt_detects_hash_mismatch():
    session = _session("charge_only")
    receipt = build_receipt(session)

    failures = validate_session_receipt(session, receipt, "0x" + "0" * 64)

    assert "receipt_hash_mismatch" in failures


def test_validate_session_receipt_detects_merkle_mismatch():
    session = _session("charge_only")
    receipt = build_receipt(session)
    receipt["merkle_root"] = "0x" + "0" * 64
    receipt_hash = hash_receipt(receipt)

    failures = validate_session_receipt(session, receipt, receipt_hash)

    assert "merkle_root_mismatch" in failures


def test_validate_session_receipt_detects_session_type_counter_mismatch():
    session = _session("charge_only")
    session["session_type"] = "discharge_only"
    receipt = build_receipt(session)
    receipt_hash = hash_receipt(receipt)

    failures = validate_session_receipt(session, receipt, receipt_hash)

    assert "invalid_discharge_only_counters" in failures


def test_summarize_session_metrics_counts_types_and_energy():
    records = []
    for index, session_type in enumerate(("charge_only", "discharge_only", "bidirectional"), start=1):
        session = generate_session(
            index,
            rng=random.Random(index),
            base_now=datetime(2026, 3, 7, 23, 59, tzinfo=timezone.utc),
            session_prefix="validation",
            deterministic_tx=True,
            session_mode=session_type,
        )
        receipt = build_receipt(session)
        records.append({"session": session, "receipt": receipt, "receipt_hash": hash_receipt(receipt)})

    metrics = summarize_session_metrics(records)

    assert metrics["num_sessions_generated"] == 3
    assert metrics["charge_only_sessions"] == 1
    assert metrics["discharge_only_sessions"] == 1
    assert metrics["bidirectional_sessions"] == 1
    assert metrics["session_duration_sec_min"] > 0
    assert metrics["import_kwh_max"] >= metrics["import_kwh_min"]
