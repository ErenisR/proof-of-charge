from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.batch_anchoring import anchor_day_from_db
from src.models import Base, MeterValue
from src.receipt_builder import build_receipt, hash_receipt
from src.repository import persist_finalized_session
from src.tamper_scenarios import LAYER_NAMES, SCENARIOS, run_tamper_matrix


def _payload(session_id, offset):
    values = [offset, offset + 1, offset + 2]
    return {
        "schema_version": "v2g-v1", "session_id": session_id, "user_id": "u", "evse_id": "e",
        "ocpp_tx_id": f"tx-{session_id}", "session_type": "charge_only",
        "start_ts": "2026-07-02T10:00:00Z", "end_ts": "2026-07-02T10:10:00Z",
        "meter_values": [
            {"ts": f"2026-07-02T10:{minute:02d}:00Z", "import_kwh": value, "export_kwh": 0.0}
            for minute, value in zip((0, 5, 10), values)
        ],
        "pricing": {"currency": "EUR", "model": "TOU", "import_components": [], "export_components": []},
    }


def test_all_scenarios_execute_restore_and_write_artifacts(tmp_path: Path):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for index in range(3):
            payload = _payload(f"matrix-00000{index + 1}", float(index))
            receipt = build_receipt(payload)
            persist_finalized_session(payload, receipt, hash_receipt(receipt), session)
        session.commit()
        anchor_day_from_db("2026-07-02", "matrix", session)
        original_meter = session.query(MeterValue).filter_by(session_id="matrix-000001", sample_index=1).one().import_kwh
        output = tmp_path / "matrix"
        records, summary = run_tamper_matrix(
            db_session=session, day="2026-07-02", session_prefix="matrix",
            session_id="matrix-000001", output_dir=output, run_id="test",
        )
        assert len(records) == len(SCENARIOS) == 7
        assert summary["scenarios_executed"] == 7
        assert summary["scenarios_meeting_expected_behavior"] == 7
        assert all(record["mutation_applied"] for record in records)
        assert all(record["restoration_successful"] for record in records)
        assert all(record["baseline_valid_after"] for record in records)
        assert session.query(MeterValue).filter_by(session_id="matrix-000001", sample_index=1).one().import_kwh == original_meter
        assert set(records[0]["post_tamper_checks"]) == set(LAYER_NAMES)
        for filename in ("tamper_matrix.json", "tamper_matrix.csv", "tamper_matrix.md", "tamper_matrix_summary.json", "manifest.json"):
            assert (output / filename).exists()
        t2 = next(record for record in records if record["scenario_id"] == "T2")
        t3 = next(record for record in records if record["scenario_id"] == "T3")
        assert t2["meter_root_match"] is False
        assert t2["post_tamper_checks"]["normalized_meter_stream"]["details"]["source"] == "meter_values"
        assert t3["meter_sample_count_match"] is False
