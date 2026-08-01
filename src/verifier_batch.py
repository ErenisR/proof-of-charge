# src/verifier_batch.py
import argparse
import json
import sys
from datetime import timezone
from pathlib import Path
from typing import Dict, Any, List, Tuple
from contextlib import nullcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db
from .batch_anchoring import ANCHORS_FILE, build_batch_root, _receipt_day
from .models import BatchAnchor, BatchAnchorReceipt
from .batch_merkle import LEGACY_PROFILE, PROFILE_V1, BatchContext, BatchLeafRecord, build_batch_commitment, hex32
from .batch_service import audit_batch_membership
from .repository import persist_batch_verification
from .storage import load_index
from .performance_timing import TimingRecorder


def _load_receipt_payload(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _load_anchors() -> Dict[str, Any]:
    if not ANCHORS_FILE.exists():
        return {"batches": []}
    return json.loads(ANCHORS_FILE.read_text())


def _find_anchor(day: str, session_prefix: str | None = None) -> Dict[str, Any] | None:
    anchors = _load_anchors().get("batches", [])
    expected_prefix = session_prefix
    # Newest match first to avoid stale anchors when same day is anchored multiple times.
    for batch in reversed(anchors):
        if batch.get("day") != day:
            continue
        if batch.get("session_prefix") != expected_prefix:
            continue
        return batch
    return None


def _collect_hashes_for_day(
    day: str,
    session_prefix: str | None = None,
) -> Tuple[List[str], List[str]]:
    index = load_index()
    receipt_hashes: List[str] = []
    session_ids: List[str] = []
    for session_id, entry in index.items():
        if session_prefix and not session_id.startswith(f"{session_prefix}-"):
            continue
        path = Path(entry["file"])
        if not path.exists():
            continue
        payload = _load_receipt_payload(path)
        receipt = payload.get("receipt", {})
        receipt_day = _receipt_day(receipt)
        if receipt_day != day:
            continue
        receipt_hash = payload.get("hash")
        if not receipt_hash:
            continue
        receipt_hashes.append(receipt_hash)
        session_ids.append(session_id)
    session_ids.sort()
    return receipt_hashes, session_ids


def verify_day(day: str, session_prefix: str | None = None,
               timing_recorder: TimingRecorder | None = None) -> Dict[str, Any]:
    if db.database_enabled():
        session = db.session_scope()
        try:
            return verify_day_from_db(day, session_prefix=session_prefix, db_session=session,
                                      timing_recorder=timing_recorder)
        finally:
            session.close()
    return verify_day_from_files(day, session_prefix=session_prefix)


def verify_day_from_files(day: str, session_prefix: str | None = None) -> Dict[str, Any]:
    anchor = _find_anchor(day, session_prefix=session_prefix)
    if not anchor:
        suffix = f" with prefix {session_prefix}" if session_prefix else ""
        raise ValueError(f"No anchor found for day {day}{suffix}")
    receipt_hashes, session_ids = _collect_hashes_for_day(day, session_prefix=session_prefix)
    computed_root = build_batch_root(receipt_hashes)
    expected_root = anchor.get("batch_root")
    result = {
        "day": day,
        "session_prefix": session_prefix,
        "expected_root": expected_root,
        "computed_root": computed_root,
        "match": computed_root == expected_root,
        "receipt_count": len(receipt_hashes),
        "session_ids": session_ids,
    }
    return result


def verify_day_from_db(
    day: str,
    session_prefix: str | None = None,
    db_session: Session | None = None,
    persist_result: bool = True,
    timing_recorder: TimingRecorder | None = None,
) -> Dict[str, Any]:
    owns_session = db_session is None
    session = db_session or db.session_scope()

    measure = lambda stage, **kwargs: timing_recorder.measure(stage, **kwargs) if timing_recorder else nullcontext()
    try:
      with measure("batch_verification_total"):
        anchor = _find_anchor_from_db(session, day=day, session_prefix=session_prefix)
        if not anchor:
            suffix = f" with prefix {session_prefix}" if session_prefix else ""
            raise ValueError(f"No anchor found for day {day}{suffix}")

        with measure("batch_membership_load", anchor_id=anchor.id):
            receipt_hashes, session_ids = _collect_anchor_memberships_from_db(session, anchor_id=anchor.id)
        if not receipt_hashes:
            raise ValueError(f"No receipt memberships found for anchor {anchor.id}")
        if anchor.commitment_profile == PROFILE_V1:
            result = _verify_profiled_anchor(session, anchor, timing_recorder=timing_recorder)
        else:
            with measure("batch_merkle_recomputation", anchor_id=anchor.id):
                computed_root = build_batch_root(receipt_hashes)
            expected_root = anchor.batch_root
            root_match = computed_root == expected_root
            count_match = len(receipt_hashes) == anchor.receipt_count
            result = {
            "anchor_id": anchor.id,
            "day": day,
            "session_prefix": session_prefix,
            "expected_root": expected_root,
            "computed_root": computed_root,
            "root_match": root_match,
            "expected_receipt_count": anchor.receipt_count,
            "actual_membership_count": len(receipt_hashes),
            "receipt_count_match": count_match,
            "match": root_match and count_match,
            "receipt_count": len(receipt_hashes),
            "session_ids": session_ids,
            "receipt_hashes": receipt_hashes,
            "commitment_profile": anchor.commitment_profile or LEGACY_PROFILE,
            "profile_match": True, "context_match": None, "count_match": count_match,
            "ordering_match": None, "leaf_hashes_match": None, "tree_root_match": None,
            "batch_root_match": root_match, "membership_snapshot_match": None,
            "proofs_validated": 0,
            }
        if persist_result:
            with measure("batch_verification_persistence", anchor_id=anchor.id):
                persist_batch_verification(result, db_session=session)
                if owns_session:
                    session.commit()
        return result
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def _find_anchor_from_db(
    session: Session,
    day: str,
    session_prefix: str | None = None,
) -> BatchAnchor | None:
    normalized_prefix = session_prefix or ""
    stmt = (
        select(BatchAnchor)
        .where(BatchAnchor.day == day)
        .where(BatchAnchor.session_prefix == normalized_prefix)
        .order_by(BatchAnchor.id.desc())
    )
    anchors = list(session.scalars(stmt))
    profiled = [anchor for anchor in anchors if anchor.commitment_profile == PROFILE_V1]
    if len(profiled) > 1:
        raise ValueError(f"Ambiguous {PROFILE_V1} anchors; verify by explicit anchor_id")
    return profiled[0] if profiled else (anchors[0] if anchors else None)


def verify_anchor_from_db(anchor_id: int, db_session: Session | None = None, persist_result: bool = True) -> Dict[str, Any]:
    owns = db_session is None; session = db_session or db.session_scope()
    try:
        anchor = session.get(BatchAnchor, anchor_id)
        if not anchor: raise ValueError(f"No anchor found for id {anchor_id}")
        if anchor.commitment_profile == PROFILE_V1: result = _verify_profiled_anchor(session, anchor)
        else:
            hashes, ids = _collect_anchor_memberships_from_db(session, anchor.id); computed = build_batch_root(hashes)
            result = {"anchor_id": anchor.id, "day": anchor.day, "session_prefix": anchor.session_prefix, "commitment_profile": anchor.commitment_profile, "expected_root": anchor.batch_root, "computed_root": computed, "root_match": computed == anchor.batch_root, "receipt_count_match": len(hashes) == anchor.receipt_count, "match": computed == anchor.batch_root and len(hashes) == anchor.receipt_count, "session_ids": ids, "receipt_hashes": hashes}
        if persist_result: persist_batch_verification(result, db_session=session)
        if owns and persist_result: session.commit()
        return result
    finally:
        if owns: session.close()


def _verify_profiled_anchor(session: Session, anchor: BatchAnchor,
                            timing_recorder: TimingRecorder | None = None) -> Dict[str, Any]:
    rows = list(session.scalars(select(BatchAnchorReceipt).where(BatchAnchorReceipt.anchor_id == anchor.id).order_by(BatchAnchorReceipt.leaf_index)))
    profile_match = anchor.commitment_profile == PROFILE_V1
    measure = lambda stage: timing_recorder.measure(stage, anchor_id=anchor.id) if timing_recorder else nullcontext()
    try:
        context = BatchContext.from_dict(anchor.context_json or {})
        with measure("batch_merkle_recomputation"):
            commitment = build_batch_commitment([BatchLeafRecord(row.session_id, row.normalized_start_ts.replace(tzinfo=timezone.utc) if row.normalized_start_ts.tzinfo is None else row.normalized_start_ts, row.receipt_hash) for row in rows], context)
        context_match = hex32(commitment.context_hash) == anchor.context_hash
        ordering_match = [row.leaf_index for row in rows] == list(range(len(rows))) and [row.session_id for row in rows] == [record.session_id for record in commitment.records]
        leaf_hashes_match = all(row.leaf_hash == hex32(commitment.leaf_hashes[index]) for index, row in enumerate(rows))
        tree_root_match = anchor.tree_root == hex32(commitment.tree_root)
        batch_root_match = anchor.batch_root == hex32(commitment.batch_root)
        error = None
    except Exception as exc:
        context_match = ordering_match = leaf_hashes_match = tree_root_match = batch_root_match = False
        commitment = None; error = f"{type(exc).__name__}: {exc}"
    count_match = len(rows) == anchor.receipt_count
    immutable_match = all((profile_match, context_match, count_match, ordering_match, leaf_hashes_match, tree_root_match, batch_root_match))
    with measure("batch_snapshot_audit"):
        audit = audit_batch_membership(anchor.id, session)
    return {
        "anchor_id": anchor.id, "day": anchor.day, "session_prefix": anchor.session_prefix,
        "commitment_profile": anchor.commitment_profile, "expected_root": anchor.batch_root,
        "computed_root": hex32(commitment.batch_root) if commitment else None,
        "expected_receipt_count": anchor.receipt_count, "actual_membership_count": len(rows),
        "receipt_count": len(rows), "receipt_count_match": count_match, "root_match": batch_root_match,
        "profile_match": profile_match, "context_match": context_match, "count_match": count_match,
        "ordering_match": ordering_match, "leaf_hashes_match": leaf_hashes_match,
        "tree_root_match": tree_root_match, "batch_root_match": batch_root_match,
        "membership_snapshot_match": audit["membership_snapshot_match"], "membership_audit": audit,
        "proofs_validated": 0, "match": immutable_match,
        "session_ids": [row.session_id for row in rows], "receipt_hashes": [row.receipt_hash for row in rows],
        "error": error,
    }


def _collect_anchor_memberships_from_db(
    session: Session,
    anchor_id: int,
) -> Tuple[List[str], List[str]]:
    stmt = (
        select(BatchAnchorReceipt)
        .where(BatchAnchorReceipt.anchor_id == anchor_id)
        .order_by(BatchAnchorReceipt.leaf_index)
    )
    memberships = list(session.scalars(stmt))
    return (
        [membership.receipt_hash for membership in memberships],
        [membership.session_id for membership in memberships],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify anchored batch roots.")
    parser.add_argument("day", help="Day in YYYY-MM-DD format")
    parser.add_argument(
        "--prefix",
        help="Only verify sessions with IDs starting with '<prefix>-'",
    )
    args = parser.parse_args()

    result = verify_day(args.day, session_prefix=args.prefix)
    if result["match"]:
        print(
            f"[OK] day={args.day} batch_root matches "
            f"({result['receipt_count']} receipts)"
        )
    else:
        print(
            f"[FAIL] day={args.day} expected={result['expected_root']} "
            f"computed={result['computed_root']}"
        )
        sys.exit(1)
