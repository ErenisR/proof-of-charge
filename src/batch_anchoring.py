# src/batch_anchoring.py
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Tuple

from .merkle import merkle_root
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


def anchor_day(day: str) -> Tuple[str, int]:
    index = load_index()
    session_ids = sorted(index.keys())
    receipt_hashes: List[str] = []
    session_ids_in_day: List[str] = []

    for session_id in session_ids:
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


def anchor_all_days() -> Dict[str, Any]:
    index = load_index()
    days: Dict[str, List[str]] = {}
    for session_id, entry in index.items():
        path = Path(entry["file"])
        if not path.exists():
            continue
        payload = _load_receipt_payload(path)
        receipt = payload.get("receipt", {})
        day = _receipt_day(receipt)
        days.setdefault(day, []).append(session_id)

    results = {}
    for day in sorted(days.keys()):
        batch_root, count = anchor_day(day)
        results[day] = {"batch_root": batch_root, "receipt_count": count}
    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 2:
        day = sys.argv[1]
        root, count = anchor_day(day)
        print(f"[OK] day={day} batch_root={root} receipts={count}")
    elif len(sys.argv) == 1:
        results = anchor_all_days()
        for day, info in results.items():
            print(f"[OK] day={day} batch_root={info['batch_root']} receipts={info['receipt_count']}")
    else:
        print("Usage: python -m src.batch_anchoring [YYYY-MM-DD]")
