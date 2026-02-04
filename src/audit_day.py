import json

from pathlib import Path
from datetime import datetime, date
from typing import Dict, Any, List, Tuple

from .storage import load_index
from .receipt_builder import hash_receipt
from .merkle import merkle_root
from .batch_anchoring import ANCHORS_FILE

def _load_anchors() -> Dict[str, Any]:
    if not ANCHORS_FILE.exists():
        raise FileNotFoundError(f"Anchors file not found at {ANCHORS_FILE}")
    return json.loads(ANCHORS_FILE.read_text())

def _receipt_day(receipt: Dict[str, Any]) -> str:
    """
    Extract YYYY-MM-DD from receipt['start_ts'].
    Assumes ISO format, e.g. '2025-10-05T16:10:00Z' or '+00:00'.
    """
    ts = receipt.get("start_ts")
    if not ts:
        return ""
    if ts.endswith("Z"):
        ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    return dt.date().isoformat()

def _collect_for_day(day: str) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    index = load_index()
    recomputed_hashes: List[str] = []
    sessions_ids: List[str] = []
    anomalities: List[Dict[str, Any]] = []
    
    for session_id, entry in index.items():
        path = Path(entry["file"])
        if not path.exists():
            anomalities.append({
                "session_id": session_id,
                "type": "missing_file",
                "file": str(path)
            })
            continue
        
        payload = json.loads(path.read_text())
        receipt = payload.get("receipt", {})
        stored_hash_in_file = payload.get("hash", "")
        stored_hash_in_index = entry.get("hash", "")
        
        rec_day = _receipt_day(receipt)
        if rec_day != day:
            # not part of the requested day
             continue
        
        # recompute hash from receipt JSON
        recomputed_hash = hash_receipt(receipt)
        
        # compare against stored hash in file
        if stored_hash_in_file is not None and stored_hash_in_file != recomputed_hash:
            anomalities.append({
                "session_id": session_id,
                    "type": "hash_mismatch_file",
                    "stored": stored_hash_in_file,
                    "recomputed": recomputed_hash,
            })
            
        # compare against strored hash in index
        if stored_hash_in_index is not None and stored_hash_in_index != recomputed_hash:
            anomalities.append({
                "session_id": session_id,
                    "type": "hash_mismatch_index",
                    "stored": stored_hash_in_index,
                    "recomputed": recomputed_hash,
            })
            
        recomputed_hashes.append(recomputed_hash)
        sessions_ids.append(session_id)
    
    return recomputed_hashes, sessions_ids, anomalities
    
def _find_anchor_for_day(day: str) -> Dict[str, Any]:
    anchors_root = _load_anchors()
    batches = anchors_root.get("batches", [])
    for batch in batches:
        if batch.get("day") == day:
            return batch
    raise ValueError(f"No anchor found for day {day} in anchors.json")

def audit_day(day: str) -> Dict[str, Any]:
    """
    Full auditor view for a given day (YYYY-MM-DD).
    Returns a dict with:
      - day
      - expected_root
      - computed_root
      - batch_root_match
      - receipt_count
      - anomalies (list of issues per session)
      - session_ids (in the batch)
    """
    # 1) Load anchor for the day
    anchor = _find_anchor_for_day(day)
    expected_root = anchor.get("batch_root")

    # 2) Collect recomputed hashes for that day
    recomputed_hashes, session_ids, anomalies = _collect_for_day(day)

    if not recomputed_hashes:
        raise ValueError(f"No receipts found for day {day}")

    # 3) Build Merkle root over recomputed hashes
    leaves: List[bytes] = []
    for h in recomputed_hashes:
        h_str = h[2:] if h.startswith("0x") else h
        leaves.append(bytes.fromhex(h_str))

    root_bytes = merkle_root(leaves)
    computed_root = "0x" + root_bytes.hex()
    batch_match = (computed_root == expected_root)

    return {
        "day": day,
        "expected_root": expected_root,
        "computed_root": computed_root,
        "batch_root_match": batch_match,
        "receipt_count": len(recomputed_hashes),
        "anomalies": anomalies,
        "session_ids": session_ids,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m src.audit_day YYYY-MM-DD")
        sys.exit(1)

    day_str = sys.argv[1]

    try:
        result = audit_day(day_str)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    print(f"Audit for day {result['day']}")
    print(f"  expected_root : {result['expected_root']}")
    print(f"  computed_root : {result['computed_root']}")
    print(f"  batch_match   : {result['batch_root_match']}")
    print(f"  receipt_count : {result['receipt_count']}")

    if result["anomalies"]:
        print("\n  Anomalies:")
        for a in result["anomalies"]:
            print(f"   - {a['session_id']}: {a['type']}")
    else:
        print("  Anomalies: none")
