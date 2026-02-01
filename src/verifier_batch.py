# src/verifier_batch.py
import json
from pathlib import Path
from typing import Dict, Any, List, Tuple

from .batch_anchoring import ANCHORS_FILE, build_batch_root, _receipt_day
from .storage import load_index


def _load_receipt_payload(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _load_anchors() -> Dict[str, Any]:
    if not ANCHORS_FILE.exists():
        return {"batches": []}
    return json.loads(ANCHORS_FILE.read_text())


def _find_anchor(day: str) -> Dict[str, Any] | None:
    anchors = _load_anchors().get("batches", [])
    for batch in anchors:
        if batch.get("day") == day:
            return batch
    return None


def _collect_hashes_for_day(day: str) -> Tuple[List[str], List[str]]:
    index = load_index()
    receipt_hashes: List[str] = []
    session_ids: List[str] = []
    for session_id, entry in index.items():
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
    return receipt_hashes, session_ids


def verify_day(day: str) -> Dict[str, Any]:
    anchor = _find_anchor(day)
    if not anchor:
        raise ValueError(f"No anchor found for day {day}")
    receipt_hashes, session_ids = _collect_hashes_for_day(day)
    computed_root = build_batch_root(receipt_hashes)
    expected_root = anchor.get("batch_root")
    return {
        "day": day,
        "expected_root": expected_root,
        "computed_root": computed_root,
        "match": computed_root == expected_root,
        "receipt_count": len(receipt_hashes),
        "session_ids": session_ids,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m src.verifier_batch YYYY-MM-DD")
        sys.exit(1)

    day = sys.argv[1]
    result = verify_day(day)
    if result["match"]:
        print(f"[OK] day={day} batch_root matches ({result['receipt_count']} receipts)")
    else:
        print(f"[FAIL] day={day} expected={result['expected_root']} computed={result['computed_root']}")
        sys.exit(1)
