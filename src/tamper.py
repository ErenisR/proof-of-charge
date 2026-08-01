# src/tamper.py
from copy import deepcopy
import json
from pathlib import Path
from typing import Dict, Any

from sqlalchemy.orm import Session

from . import db
from .models import Receipt
from .storage import load_index
from .receipt_builder import hash_receipt
from .verifier_batch import verify_day


def _load_payload(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _save_payload(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def tamper_receipt(session_id: str, delta_kwh: float = 0.123) -> Dict[str, Any]:
    if db.database_enabled():
        return tamper_receipt_from_db(session_id, delta_kwh=delta_kwh)
    return tamper_receipt_from_files(session_id, delta_kwh=delta_kwh)


def tamper_receipt_from_files(session_id: str, delta_kwh: float = 0.123) -> Dict[str, Any]:
    index = load_index()
    if session_id not in index:
        raise ValueError(f"Session {session_id} not found in index")

    entry = index[session_id]
    path = Path(entry["file"])
    if not path.exists():
        raise FileNotFoundError(f"Receipt file {path} not found")

    payload = _load_payload(path)
    receipt = payload.get("receipt", {})
    if "energy_kwh" not in receipt:
        raise ValueError("Receipt has no energy_kwh field to tamper")

    changed = round(float(receipt["energy_kwh"]) + float(delta_kwh), 3)
    receipt["energy_kwh"] = f"{changed:.3f}" if isinstance(receipt["energy_kwh"], str) else changed
    payload["receipt"] = receipt
    _save_payload(path, payload)

    expected = payload.get("hash")
    computed = hash_receipt(receipt)
    return {
        "source": "files",
        "session_id": session_id,
        "path": str(path),
        "expected_hash": expected,
        "computed_hash": computed,
        "match": expected == computed,
        "batch_day": entry.get("batch_day"),
    }


def tamper_receipt_from_db(
    session_id: str,
    delta_kwh: float = 0.123,
    db_session: Session | None = None,
) -> Dict[str, Any]:
    owns_session = db_session is None
    session = db_session or db.session_scope()

    try:
        stored = session.get(Receipt, session_id)
        if not stored:
            raise ValueError(f"Session {session_id} not found in receipts table")

        receipt = deepcopy(stored.receipt_json or {})
        if "energy_kwh" not in receipt:
            raise ValueError("Receipt has no energy_kwh field to tamper")

        changed = round(float(receipt["energy_kwh"]) + float(delta_kwh), 3)
        receipt["energy_kwh"] = f"{changed:.3f}" if isinstance(receipt["energy_kwh"], str) else changed
        stored.receipt_json = receipt
        session.flush()

        expected = stored.receipt_hash
        computed = hash_receipt(receipt)
        result = {
            "source": "db",
            "session_id": session_id,
            "expected_hash": expected,
            "computed_hash": computed,
            "match": expected == computed,
            "batch_day": _receipt_day(receipt),
        }
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

    if len(sys.argv) < 2:
        print("Usage: python -m src.tamper <session_id> [delta_kwh]")
        sys.exit(1)

    session_id = sys.argv[1]
    delta = float(sys.argv[2]) if len(sys.argv) > 2 else 0.123
    result = tamper_receipt(session_id, delta_kwh=delta)
    print(
        f"[OK] Tampered {session_id} energy_kwh by {delta}. "
        f"source={result['source']} "
        f"hash_match={result['match']}"
    )

    if result["batch_day"]:
        try:
            batch_result = verify_day(result["batch_day"])
            if batch_result["match"]:
                print(
                    f"[OK] Batch day {result['batch_day']} still matches. "
                    "The anchored membership snapshot is unchanged."
                )
            else:
                print(
                    f"[FAIL] Batch day {result['batch_day']} mismatch after tamper "
                    f"(expected={batch_result['expected_root']} computed={batch_result['computed_root']})"
                )
        except Exception as exc:
            print(f"[WARN] Batch verification failed: {exc}")
