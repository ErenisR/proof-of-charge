# src/batch_anchoring.py
import argparse
import json
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Tuple
from contextlib import nullcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db
from .merkle import merkle_root
from .models import BatchAnchor, Receipt
from .batch_merkle import (
    HASH_ALGORITHM as BATCH_HASH_ALGORITHM,
    LEGACY_PROFILE,
    ODD_NODE_RULE,
    ORDERING_RULE,
    PROFILE_V1,
    BatchContext,
    BatchLeafRecord,
    ClosedBatchMembershipChanged,
    build_batch_commitment,
    hex32,
    parse_timestamp,
)
from .repository import persist_batch_anchor
from .performance_timing import TimingRecorder
from .storage import RECEIPTS_DIR, load_index, save_index

ANCHORS_FILE = RECEIPTS_DIR / "anchors.json"


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _receipt_day(receipt: Dict[str, Any]) -> str:
    for key in ("start_ts", "end_ts"):
        ts = receipt.get(key)
        dt = _parse_ts(ts) if isinstance(ts, str) else None
        if dt:
            return dt.date().isoformat()
    return "unknown"


def _load_receipt_payload(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _load_anchors() -> Dict[str, Any]:
    if not ANCHORS_FILE.exists():
        return {"batches": []}
    return json.loads(ANCHORS_FILE.read_text())


def _save_anchors(payload: Dict[str, Any]) -> None:
    ANCHORS_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True))


def build_batch_root(receipt_hashes: List[str]) -> str:
    """
    Build a batch Merkle root from a list of receipt hashes.
    We:
      - normalize hex (strip '0x'),
      - sort deterministically,
      - convert to bytes,
      - compute merkle_root.
    This must be used BOTH when anchoring and when verifying,
    so that batch roots are identical.
    """
    # normalize
    normalized = []
    for h in receipt_hashes:
        if h.startswith("0x"):
            h = h[2:]
        normalized.append(h.lower())

    # deterministic order
    normalized.sort()

    # convert to bytes
    leaves = [bytes.fromhex(h) for h in normalized]

    # empty list should probably never happen, but just in case
    if not leaves:
        raise ValueError("Cannot build batch root from empty list of hashes")

    root = merkle_root(leaves)
    return "0x" + root.hex()


def anchor_day(day: str, session_prefix: str | None = None, commitment_profile: str | None = None,
               timing_recorder: TimingRecorder | None = None) -> Tuple[str, int]:
    if db.database_enabled():
        session = db.session_scope()
        try:
            return anchor_day_from_db(day, session_prefix=session_prefix, db_session=session,
                                      commitment_profile=commitment_profile or PROFILE_V1,
                                      timing_recorder=timing_recorder)
        finally:
            session.close()
    if commitment_profile and commitment_profile != LEGACY_PROFILE:
        raise ValueError("poc-batch-merkle-v1 anchoring requires PostgreSQL-backed receipt snapshots")
    return anchor_day_from_files(day, session_prefix=session_prefix)


def anchor_day_from_files(day: str, session_prefix: str | None = None) -> Tuple[str, int]:
    index = load_index()
    session_ids = sorted(index.keys())
    receipt_hashes: List[str] = []
    session_ids_in_day: List[str] = []

    for session_id in session_ids:
        if session_prefix and not session_id.startswith(f"{session_prefix}-"):
            continue
        entry = index[session_id]
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
        session_ids_in_day.append(session_id)

    if not receipt_hashes:
        raise ValueError(f"No receipts found for day {day}")

    batch_root = build_batch_root(receipt_hashes)
    anchors = _load_anchors()

    anchors.setdefault("batches", [])
    anchors["batches"].append(
        {
            "day": day,
            "session_prefix": session_prefix,
            "batch_root": batch_root,
            "receipt_count": len(receipt_hashes),
            "session_ids": session_ids_in_day,
            "anchored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "chain_tx": None,
            "cid": None,
        }
    )
    _save_anchors(anchors)

    for session_id in session_ids_in_day:
        index[session_id]["batch_root"] = batch_root
        index[session_id]["batch_day"] = day
    save_index(index)

    return batch_root, len(receipt_hashes)


def anchor_day_from_db(
    day: str,
    session_prefix: str | None = None,
    db_session: Session | None = None,
    commitment_profile: str = PROFILE_V1,
    timing_recorder: TimingRecorder | None = None,
) -> Tuple[str, int]:
    owns_session = db_session is None
    session = db_session or db.session_scope()

    measure = lambda stage, **kwargs: timing_recorder.measure(stage, **kwargs) if timing_recorder else nullcontext()
    try:
      with measure("batch_anchor_total"):
        if commitment_profile == LEGACY_PROFILE:
            with measure("batch_eligibility_query"):
                receipt_hashes, session_ids = _collect_receipt_hashes_from_db(session, day=day, session_prefix=session_prefix)
            if not receipt_hashes: raise ValueError(f"No receipts found for day {day}")
            with measure("batch_merkle_construction"):
                batch_root = build_batch_root(receipt_hashes)
            memberships = _build_anchor_memberships(session_ids, receipt_hashes)
            commitment = None
        elif commitment_profile == PROFILE_V1:
            context = BatchContext.for_day(day)
            with measure("batch_eligibility_query"):
                records = _collect_batch_records_from_db(session, day=day, session_prefix=session_prefix)
            if not records: raise ValueError(f"No receipts found for day {day}")
            with measure("batch_context_and_leaf_construction"):
                # Context normalization and temporal ordering are performed by the profile builder.
                prepared_records = list(records)
            with measure("batch_merkle_construction"):
                commitment = build_batch_commitment(prepared_records, context)
            batch_root = hex32(commitment.batch_root)
            memberships = [
                {"session_id": record.session_id, "receipt_hash": record.receipt_hash,
                 "normalized_start_ts": record.start_ts, "leaf_hash": hex32(commitment.leaf_hashes[index]), "leaf_index": index}
                for index, record in enumerate(commitment.records)
            ]
            receipt_hashes = [record.receipt_hash for record in commitment.records]
            existing = list(session.scalars(select(BatchAnchor).where(BatchAnchor.day == day, BatchAnchor.session_prefix == (session_prefix or ""), BatchAnchor.commitment_profile == PROFILE_V1)))
            if len(existing) > 1: raise ValueError(f"Ambiguous closed {PROFILE_V1} anchors for day={day} prefix={session_prefix}")
            if existing:
                anchor = existing[0]
                if anchor.batch_root == batch_root and anchor.receipt_count == len(records):
                    return anchor.batch_root, anchor.receipt_count
                old_ids = {item.session_id: item.receipt_hash for item in anchor.receipts}
                new_ids = {item.session_id: item.receipt_hash for item in commitment.records}
                raise ClosedBatchMembershipChanged({"added_ids": sorted(set(new_ids)-set(old_ids)), "removed_ids": sorted(set(old_ids)-set(new_ids)), "changed_ids": sorted(k for k in set(old_ids)&set(new_ids) if old_ids[k] != new_ids[k])})
        else:
            raise ValueError(f"Unknown batch commitment profile: {commitment_profile}")
        with measure("batch_anchor_persistence"):
          persist_batch_anchor(
            day=day,
            session_prefix=session_prefix,
            batch_root=batch_root,
            receipt_count=len(receipt_hashes),
            receipt_memberships=memberships,
            db_session=session,
            commitment_profile=commitment_profile,
            context_json=asdict(commitment.context) if commitment else None,
            context_hash=hex32(commitment.context_hash) if commitment else None,
            tree_root=hex32(commitment.tree_root) if commitment else None,
            window_start=parse_timestamp(commitment.context.window_start) if commitment else None,
            window_end=parse_timestamp(commitment.context.window_end) if commitment else None,
            ordering_rule=ORDERING_RULE if commitment else None,
            odd_node_rule=ODD_NODE_RULE if commitment else None,
            hash_algorithm=BATCH_HASH_ALGORITHM if commitment else None,
          )
          session.commit()
        return batch_root, len(receipt_hashes)
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def anchor_all_days(session_prefix: str | None = None) -> Dict[str, Any]:
    if db.database_enabled():
        session = db.session_scope()
        try:
            return anchor_all_days_from_db(session_prefix=session_prefix, db_session=session)
        finally:
            session.close()
    return anchor_all_days_from_files(session_prefix=session_prefix)


def anchor_all_days_from_files(session_prefix: str | None = None) -> Dict[str, Any]:
    index = load_index()
    days: Dict[str, List[str]] = {}
    for session_id, entry in index.items():
        if session_prefix and not session_id.startswith(f"{session_prefix}-"):
            continue
        path = Path(entry["file"])
        if not path.exists():
            continue
        payload = _load_receipt_payload(path)
        receipt = payload.get("receipt", {})
        day = _receipt_day(receipt)
        days.setdefault(day, []).append(session_id)

    results = {}
    for day in sorted(days.keys()):
        batch_root, count = anchor_day(day, session_prefix=session_prefix)
        results[day] = {"batch_root": batch_root, "receipt_count": count}
    return results


def anchor_all_days_from_db(
    session_prefix: str | None = None,
    db_session: Session | None = None,
) -> Dict[str, Any]:
    owns_session = db_session is None
    session = db_session or db.session_scope()

    try:
        days = sorted(
            {
                day
                for day in (
                    _receipt_day_from_row(receipt)
                    for receipt in _receipts_for_prefix(session, session_prefix=session_prefix)
                )
                if day
            }
        )
        results = {}
        for day in days:
            batch_root, count = anchor_day_from_db(
                day,
                session_prefix=session_prefix,
                db_session=session,
            )
            results[day] = {"batch_root": batch_root, "receipt_count": count}
        return results
    finally:
        if owns_session:
            session.close()


def _collect_receipt_hashes_from_db(
    session: Session,
    day: str,
    session_prefix: str | None = None,
) -> Tuple[List[str], List[str]]:
    day_start, day_end = _day_bounds(day)
    stmt = (
        select(Receipt.session_id, Receipt.receipt_hash)
        .where(Receipt.start_ts >= day_start)
        .where(Receipt.start_ts < day_end)
        .order_by(Receipt.session_id)
    )
    if session_prefix:
        stmt = stmt.where(Receipt.session_id.like(f"{session_prefix}-%"))

    pairs = list(session.execute(stmt))
    return [receipt_hash for _, receipt_hash in pairs], [session_id for session_id, _ in pairs]


def _collect_batch_records_from_db(session: Session, day: str, session_prefix: str | None = None) -> List[BatchLeafRecord]:
    day_start, day_end = _day_bounds(day)
    stmt = select(Receipt.session_id, Receipt.start_ts, Receipt.receipt_hash).where(Receipt.start_ts >= day_start, Receipt.start_ts < day_end)
    if session_prefix: stmt = stmt.where(Receipt.session_id.like(f"{session_prefix}-%"))
    return [BatchLeafRecord(session_id, start_ts.replace(tzinfo=timezone.utc) if start_ts.tzinfo is None else start_ts, receipt_hash) for session_id, start_ts, receipt_hash in session.execute(stmt)]


def _build_anchor_memberships(session_ids: List[str], receipt_hashes: List[str]) -> List[Dict[str, Any]]:
    pairs = sorted(
        zip(session_ids, receipt_hashes),
        key=lambda pair: _normalized_hash(pair[1]),
    )
    return [
        {
            "session_id": session_id,
            "receipt_hash": receipt_hash,
            "leaf_index": index,
        }
        for index, (session_id, receipt_hash) in enumerate(pairs)
    ]


def _normalized_hash(receipt_hash: str) -> str:
    if receipt_hash.startswith("0x"):
        receipt_hash = receipt_hash[2:]
    return receipt_hash.lower()


def _receipts_for_prefix(session: Session, session_prefix: str | None = None) -> List[Receipt]:
    stmt = select(Receipt).order_by(Receipt.session_id)
    if session_prefix:
        stmt = stmt.where(Receipt.session_id.like(f"{session_prefix}-%"))
    return list(session.scalars(stmt))


def _day_bounds(day: str) -> tuple[datetime, datetime]:
    parsed_day = date.fromisoformat(day)
    day_start = datetime.combine(parsed_day, time.min, tzinfo=timezone.utc)
    return day_start, day_start + timedelta(days=1)


def _receipt_day_from_row(receipt: Receipt) -> str | None:
    receipt_json = receipt.receipt_json or {}
    day = _receipt_day(receipt_json)
    if day != "unknown":
        return day
    for ts in (receipt.start_ts, receipt.end_ts):
        if ts:
            if hasattr(ts, "date"):
                return ts.date().isoformat()
            return str(ts)[:10]
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build mock batch anchors.")
    parser.add_argument("day", nargs="?", help="Day in YYYY-MM-DD format")
    parser.add_argument(
        "--prefix",
        help="Only include sessions with IDs starting with '<prefix>-'",
    )
    args = parser.parse_args()

    if args.day:
        root, count = anchor_day(args.day, session_prefix=args.prefix)
        print(f"[OK] day={args.day} batch_root={root} receipts={count}")
    else:
        results = anchor_all_days(session_prefix=args.prefix)
        for day, info in results.items():
            print(
                f"[OK] day={day} batch_root={info['batch_root']} "
                f"receipts={info['receipt_count']}"
            )
