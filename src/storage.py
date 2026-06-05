# src/storage.py
import json
import os
from pathlib import Path
from typing import Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent  # project root
RECEIPTS_DIR = BASE_DIR / "receipts"
INDEX_FILE = RECEIPTS_DIR / "index.json"

RECEIPTS_DIR.mkdir(exist_ok=True)


def write_local_receipts_enabled() -> bool:
    value = os.getenv("WRITE_LOCAL_RECEIPTS")
    return value is not None and value.lower() in {"1", "true", "yes", "on"}


def load_index() -> Dict[str, Any]:
    if not INDEX_FILE.exists():
        return {}
    return json.loads(INDEX_FILE.read_text())

def save_index(index: Dict[str, Any]) -> None:
    INDEX_FILE.write_text(json.dumps(index, indent=2, sort_keys=True))

def save_receipt(
    session_id: str,
    receipt: Dict[str, Any],
    receipt_hash: str,
    session: Dict[str, Any] | None = None,
) -> None:
    """
    Save the receipt as receipts/{session_id}.json and update index.json
    """
    path = RECEIPTS_DIR / f"{session_id}.json"
    payload = {
        "receipt": receipt,
        "hash": receipt_hash,
    }
    if session is not None:
        payload["session"] = session
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    # update index
    index = load_index()
    index[session_id] = {
        "file": str(path),
        "hash": receipt_hash,
        "cid": None,        # IPFS CID (later)
        "chain_tx": None,   # blockchain tx hash (later)
        "batch_root": None  # Merkle root for the batch/day
    }
    save_index(index)
