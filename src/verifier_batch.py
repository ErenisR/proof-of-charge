# src/verifier_batch.py
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db
from .batch_anchoring import ANCHORS_FILE, build_batch_root, _receipt_day
from .models import BatchAnchor, BatchAnchorReceipt
from .repository import persist_batch_verification
from .storage import load_index


def _load_receipt_payload(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _load_anchors() -> Dict[str, Any]:
    if not ANCHORS_FILE.exists():
        return {"batches": []}
    return json.loads(ANCHORS_FILE.read_text())


def _find_anchor(day: str, session_prefix: str | None = None) -> Dict[str, Any] | None:
    anchors = _load_anchors().get("batches", [])
    # Newest match first to avoid stale anchors when same day is anchored multiple times.
    for batch in reversed(anchors):
        if batch.get("day") != day:
            continue
        if session_prefix and batch.get("session_prefix") != session_prefix:
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


def verify_day(day: str, session_prefix: str | None = None) -> Dict[str, Any]:
    if db.database_enabled():
        session = db.session_scope()
        try:
            return verify_day_from_db(day, session_prefix=session_prefix, db_session=session)
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
) -> Dict[str, Any]:
    owns_session = db_session is None
    session = db_session or db.session_scope()

    try:
        anchor = _find_anchor_from_db(session, day=day, session_prefix=session_prefix)
        if not anchor:
            suffix = f" with prefix {session_prefix}" if session_prefix else ""
            raise ValueError(f"No anchor found for day {day}{suffix}")

        receipt_hashes, session_ids = _collect_anchor_memberships_from_db(
            session,
            anchor_id=anchor.id,
        )
        if not receipt_hashes:
            raise ValueError(f"No receipt memberships found for anchor {anchor.id}")
        computed_root = build_batch_root(receipt_hashes)
        expected_root = anchor.batch_root
        result = {
            "day": day,
            "session_prefix": session_prefix,
            "expected_root": expected_root,
            "computed_root": computed_root,
            "match": computed_root == expected_root,
            "receipt_count": len(receipt_hashes),
            "session_ids": session_ids,
        }
        persist_batch_verification(result, db_session=session)
        session.commit()
        return result
    except Exception:
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
    stmt = (
        select(BatchAnchor)
        .where(BatchAnchor.day == day)
        .order_by(BatchAnchor.id.desc())
    )
    anchors = list(session.scalars(stmt))
    for anchor in anchors:
        if session_prefix and anchor.session_prefix != session_prefix:
            continue
        return anchor
    return None


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
