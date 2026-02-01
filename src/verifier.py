# src/verifier.py
import json
import hashlib
from pathlib import Path
from typing import Dict, Any
from .storage import BASE_DIR, RECEIPTS_DIR, load_index

def compute_hash(payload: Dict[str, Any]) -> str:
    data = json.dumps(payload["receipt"], sort_keys=True).encode("utf-8")
    h = hashlib.sha256(data).hexdigest()
    return "0x" + h

def verify_session(session_id: str) -> bool:
    index = load_index()
    if session_id not in index:
        raise ValueError(f"Session {session_id} not found in index")

    entry = index[session_id]
    expected_hash = entry["hash"]
    path = Path(entry["file"])

    if not path.exists():
        raise FileNotFoundError(f"Receipt file {path} not found")

    stored = json.loads(path.read_text())
    actual_hash = compute_hash(stored)

    return actual_hash == expected_hash

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python -m src.verifier <session_id>")
        sys.exit(1)

    sess_id = sys.argv[1]
    try:
        ok = verify_session(sess_id)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    if ok:
        print(f"[OK] Receipt for session {sess_id} is valid.")
    else:
        print(f"[FAIL] Receipt for session {sess_id} has been modified!")
        sys.exit(1)
