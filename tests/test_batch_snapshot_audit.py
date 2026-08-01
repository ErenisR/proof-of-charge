from copy import deepcopy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.batch_anchoring import anchor_day_from_db
from src.batch_merkle import ClosedBatchMembershipChanged, PROFILE_V1
from src.batch_service import audit_batch_membership, generate_anchor_membership_proof
from src.models import Base, BatchAnchor, BatchAnchorReceipt, Receipt
from src.receipt_builder import build_receipt, hash_receipt
from src.repository import persist_finalized_session
from src.verifier_batch import verify_anchor_from_db, verify_day_from_db


def payload(session_id, hour):
    return {"schema_version":"v2g-v1","session_id":session_id,"user_id":"u","evse_id":"e","ocpp_tx_id":"tx-"+session_id,"session_type":"charge_only","start_ts":f"2026-07-15T{hour:02d}:00:00Z","end_ts":f"2026-07-15T{hour:02d}:10:00Z","meter_values":[{"ts":f"2026-07-15T{hour:02d}:00:00Z","import_kwh":0,"export_kwh":0},{"ts":f"2026-07-15T{hour:02d}:10:00Z","import_kwh":1,"export_kwh":0}],"pricing":{"currency":"EUR","model":"TOU","import_components":[],"export_components":[]}}


def persist(session, value):
    receipt=build_receipt(value); persist_finalized_session(value,receipt,hash_receipt(receipt),session)


def setup_batch(session):
    for i in range(1,4): persist(session,payload(f"audit-{i:04d}",i))
    session.commit(); root,count=anchor_day_from_db("2026-07-15","audit",session)
    anchor=session.query(BatchAnchor).filter_by(batch_root=root).one(); return anchor,count


def test_profiled_snapshot_idempotence_proofs_and_late_insertion_detection():
    engine=create_engine("sqlite:///:memory:",future=True); Base.metadata.create_all(engine)
    with Session(engine) as session:
        anchor,count=setup_batch(session); assert count==3 and anchor.commitment_profile==PROFILE_V1
        same=anchor_day_from_db("2026-07-15","audit",session); assert same==(anchor.batch_root,3)
        assert verify_anchor_from_db(anchor.id,session,False)["match"]
        assert generate_anchor_membership_proof(anchor.id,"audit-0002",session)["leaf_index"]==1
        persist(session,payload("audit-0004",4)); session.flush()
        audit=audit_batch_membership(anchor.id,session)
        assert audit["late_or_unanchored_ids"]==["audit-0004"]
        assert audit["membership_snapshot_match"] is False
        assert verify_anchor_from_db(anchor.id,session,False)["match"] is True
        with pytest.raises(ClosedBatchMembershipChanged): anchor_day_from_db("2026-07-15","audit",session)


def test_removed_changed_hash_and_changed_start_detected():
    engine=create_engine("sqlite:///:memory:",future=True); Base.metadata.create_all(engine)
    with Session(engine) as session:
        anchor,_=setup_batch(session)
        removed=session.get(Receipt,"audit-0003"); session.delete(removed)
        changed=session.get(Receipt,"audit-0002"); changed.receipt_hash="0x"+"ff"*32
        moved=session.get(Receipt,"audit-0001"); moved.start_ts=moved.start_ts.replace(hour=5)
        session.flush(); audit=audit_batch_membership(anchor.id,session)
        assert audit["removed_or_missing_ids"]==["audit-0003"]
        assert audit["changed_receipt_hash_ids"]==["audit-0002"]
        assert audit["changed_start_timestamp_ids"]==["audit-0001"]


def test_ambiguous_new_profile_day_lookup_rejected():
    engine=create_engine("sqlite:///:memory:",future=True); Base.metadata.create_all(engine)
    with Session(engine) as session:
        anchor,_=setup_batch(session)
        duplicate=BatchAnchor(day=anchor.day,session_prefix=anchor.session_prefix,batch_root="0x"+"ee"*32,receipt_count=anchor.receipt_count,commitment_profile=PROFILE_V1)
        session.add(duplicate); session.flush()
        with pytest.raises(ValueError,match="Ambiguous"): verify_day_from_db("2026-07-15","audit",session,persist_result=False)


def test_duplicate_leaf_indices_make_profiled_verification_fail():
    engine=create_engine("sqlite:///:memory:",future=True); Base.metadata.create_all(engine)
    with Session(engine) as session:
        anchor,_=setup_batch(session); rows=session.query(BatchAnchorReceipt).filter_by(anchor_id=anchor.id).order_by(BatchAnchorReceipt.leaf_index).all()
        rows[1].leaf_index=7; session.flush()
        result=verify_anchor_from_db(anchor.id,session,False)
        assert result["ordering_match"] is False and result["match"] is False
