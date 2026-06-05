# src/verifier.py
import json
import hashlib
from pathlib import Path
from typing import Dict, Any

from sqlalchemy.orm import Session

from . import db
from .models import Receipt
from .receipt_builder import hash_receipt
from .repository import persist_receipt_verification
from .storage import load_index


def compute_hash(payload: Dict[str, Any]) -> str:
    data = json.dumps(payload["receipt"], sort_keys=True).encode("utf-8")
    h = hashlib.sha256(data).hexdigest()
    return "0x" + h


def verify_session_details(session_id: str) -> Dict[str, Any]:
    if db.database_enabled():
        return verify_session_from_db(session_id)
    return verify_session_from_files(session_id)


def verify_session(session_id: str) -> bool:
    return bool(verify_session_details(session_id)["match"])


def verify_session_from_files(session_id: str) -> Dict[str, Any]:
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

    return {
        "source": "files",
        "session_id": session_id,
        "expected_hash": expected_hash,
        "computed_hash": actual_hash,
        "match": actual_hash == expected_hash,
        "path": str(path),
        "day": entry.get("batch_day"),
    }


def verify_session_from_db(
    session_id: str,
    db_session: Session | None = None,
    persist_result: bool = True,
) -> Dict[str, Any]:
    owns_session = db_session is None
    session = db_session or db.session_scope()

    try:
        receipt = session.get(Receipt, session_id)
        if not receipt:
            raise ValueError(f"Session {session_id} not found in receipts table")

        receipt_json = receipt.receipt_json or {}
        computed_hash = hash_receipt(receipt_json)
        result = {
            "source": "db",
            "session_id": session_id,
            "expected_hash": receipt.receipt_hash,
            "computed_hash": computed_hash,
            "expected_root": receipt.merkle_root,
            "computed_root": receipt_json.get("merkle_root"),
            "match": computed_hash == receipt.receipt_hash,
            "day": _receipt_day(receipt_json),
        }
        if persist_result:
            persist_receipt_verification(result, db_session=session)
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


def _receipt_day(receipt: Dict[str, Any]) -> str | None:
    start_ts = receipt.get("start_ts")
    if isinstance(start_ts, str) and len(start_ts) >= 10:
        return start_ts[:10]
    end_ts = receipt.get("end_ts")
    if isinstance(end_ts, str) and len(end_ts) >= 10:
        return end_ts[:10]
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python -m src.verifier <session_id>")
        sys.exit(1)

    sess_id = sys.argv[1]
    try:
        result = verify_session_details(sess_id)
    except Exception as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    if result["match"]:
        print(f"[OK] Receipt for session {sess_id} is valid. source={result['source']}")
    else:
        print(f"[FAIL] Receipt for session {sess_id} has been modified! source={result['source']}")
        print(f"expected={result['expected_hash']}")
        print(f"computed={result['computed_hash']}")
        sys.exit(1)
