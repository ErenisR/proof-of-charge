import csv
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.batch_anchoring import anchor_day_from_db
from src.count_reconciliation import reconcile_experiment_counts, utc_day_bounds
from src.models import Base, BatchAnchorReceipt, ChargingSession, Receipt
from src.receipt_builder import build_receipt, hash_receipt
from src.repository import persist_finalized_session
from src.synthetic_sessions import generate_session
from src.verifier_batch import verify_day_from_db


def _persist(session, payload):
    receipt = build_receipt(payload)
    persist_finalized_session(payload, receipt, hash_receipt(receipt), session)


def _exports(path, ids):
    path.mkdir(parents=True)
    for name in ("sessions.csv", "receipts.csv", "verifications.csv"):
        with (path / name).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["session_id"])
            writer.writeheader()
            writer.writerows({"session_id": value} for value in ids)


def test_old_2359_minus_24_hours_can_cross_previous_day_and_is_excluded():
    base = datetime(2026, 6, 18, 23, 59, tzinfo=timezone.utc)
    old_start = base - timedelta(hours=23 + 59 / 60 + 30 / 3600)
    day_start, next_day = utc_day_bounds("2026-06-18")
    assert old_start.date().isoformat() == "2026-06-17"
    assert not day_start <= old_start < next_day


@pytest.mark.parametrize("seed,size", [(1, 1), (42, 100), (999, 250)])
def test_target_day_generation_is_deterministic_and_inside_half_open_day(seed, size):
    first = [generate_session(i, rng=random.Random(seed + i), target_day="2026-06-18", deterministic_tx=True) for i in range(1, size + 1)]
    second = [generate_session(i, rng=random.Random(seed + i), target_day="2026-06-18", deterministic_tx=True) for i in range(1, size + 1)]
    start, end = utc_day_bounds("2026-06-18")
    assert [row["start_ts"] for row in first] == [row["start_ts"] for row in second]
    assert [hash_receipt(build_receipt(row)) for row in first] == [hash_receipt(build_receipt(row)) for row in second]
    assert all(start <= datetime.fromisoformat(row["start_ts"].replace("Z", "+00:00")) < end for row in first)


def test_daily_anchor_boundaries_and_cross_midnight_start_policy():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        starts = {
            "boundary-0001": "2026-06-18T00:00:00Z",
            "boundary-0002": "2026-06-18T23:59:59.999999Z",
            "boundary-0003": "2026-06-19T00:00:00Z",
            "boundary-0004": "2026-06-17T23:59:59.999999Z",
        }
        for session_id, start_ts in starts.items():
            start = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
            payload = generate_session(1, rng=random.Random(2), target_day="2026-06-18", session_prefix=session_id.rsplit("-", 1)[0], deterministic_tx=True)
            payload["session_id"] = session_id
            payload["start_ts"] = start_ts
            payload["end_ts"] = (start + timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
            for index, meter in enumerate(payload["meter_values"]):
                meter["ts"] = (start + timedelta(minutes=5 * index)).isoformat().replace("+00:00", "Z")
            payload["pricing"]["import_components"][0].update(from_=start_ts)
            payload["pricing"]["import_components"][0]["from"] = payload["pricing"]["import_components"][0].pop("from_")
            payload["pricing"]["import_components"][0]["to"] = payload["end_ts"]
            payload["pricing"]["export_components"][0]["from"] = start_ts
            payload["pricing"]["export_components"][0]["to"] = payload["end_ts"]
            _persist(session, payload)
        session.commit()
        _, count = anchor_day_from_db("2026-06-18", "boundary", session)
        verification = verify_day_from_db("2026-06-18", "boundary", session, persist_result=False)
        assert count == 2
        assert set(verification["session_ids"]) == {"boundary-0001", "boundary-0002"}
        assert session.get(Receipt, "boundary-0002").end_ts.date().isoformat() == "2026-06-19"


def test_reconciliation_fails_even_when_reduced_batch_root_matches(tmp_path):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    ids = ["reconcile-0001", "reconcile-0002"]
    with Session(engine) as session:
        inside = generate_session(1, rng=random.Random(1), target_day="2026-06-18", session_prefix="reconcile", deterministic_tx=True)
        outside = generate_session(2, rng=random.Random(2), target_day="2026-06-17", session_prefix="reconcile", deterministic_tx=True)
        _persist(session, inside)
        _persist(session, outside)
        session.commit()
        anchor_day_from_db("2026-06-18", "reconcile", session)
        verify = verify_day_from_db("2026-06-18", "reconcile", session, persist_result=False)
        assert verify["match"] is True
        _exports(tmp_path / "datasets", ids)
        result = reconcile_experiment_counts(
            run_id="reconcile", day="2026-06-18", requested_session_ids=ids,
            generated_session_ids=ids, verified_session_ids=verify["session_ids"],
            db_session=session, anchor_id=verify["anchor_id"], exported_dataset_dir=tmp_path / "datasets",
        )
        assert result["count_reconciliation_ok"] is False
        assert result["missing_at_each_stage"]["day_eligible"] == ["reconcile-0002"]
        assert result["missing_at_each_stage"]["anchored"] == ["reconcile-0002"]


def test_reconciliation_detects_missing_receipt_membership_and_export(tmp_path):
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        payloads = [generate_session(i, rng=random.Random(i), target_day="2026-06-18", session_prefix="missing", deterministic_tx=True) for i in (1, 2)]
        for payload in payloads:
            _persist(session, payload)
        session.commit()
        anchor_day_from_db("2026-06-18", "missing", session)
        anchor_verify = verify_day_from_db("2026-06-18", "missing", session, persist_result=False)
        membership = session.query(BatchAnchorReceipt).filter_by(anchor_id=anchor_verify["anchor_id"], session_id="missing-0002").one()
        session.delete(membership)
        session.delete(session.get(Receipt, "missing-0002"))
        session.flush()
        _exports(tmp_path / "datasets", ["missing-0001"])
        result = reconcile_experiment_counts(
            run_id="missing", day="2026-06-18", requested_session_ids=["missing-0001", "missing-0002"],
            generated_session_ids=["missing-0001", "missing-0002"], verified_session_ids=["missing-0001"],
            db_session=session, anchor_id=anchor_verify["anchor_id"], exported_dataset_dir=tmp_path / "datasets",
        )
        assert not result["count_reconciliation_ok"]
        assert result["missing_at_each_stage"]["persisted_receipts"] == ["missing-0002"]
        assert result["missing_at_each_stage"]["anchored"] == ["missing-0002"]
        assert result["missing_at_each_stage"]["exported_receipts"] == ["missing-0002"]
