# src/tamper.py
import json
from pathlib import Path
from typing import Dict, Any

from .storage import load_index
from .receipt_builder import hash_receipt
from .verifier_batch import verify_day


def _load_payload(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _save_payload(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def tamper_receipt(session_id: str, delta_kwh: float = 0.123) -> Dict[str, Any]:
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

    receipt["energy_kwh"] = round(float(receipt["energy_kwh"]) + float(delta_kwh), 3)
    payload["receipt"] = receipt
    _save_payload(path, payload)

    expected = payload.get("hash")
    computed = hash_receipt(receipt)
    return {
        "session_id": session_id,
        "path": str(path),
        "expected_hash": expected,
        "computed_hash": computed,
        "match": expected == computed,
        "batch_day": entry.get("batch_day"),
    }


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
        f"hash_match={result['match']}"
    )

    if result["batch_day"]:
        try:
            batch_result = verify_day(result["batch_day"])
            if batch_result["match"]:
                print(f"[OK] Batch day {result['batch_day']} still matches (unexpected)")
            else:
                print(
                    f"[FAIL] Batch day {result['batch_day']} mismatch after tamper "
                    f"(expected={batch_result['expected_root']} computed={batch_result['computed_root']})"
                )
        except Exception as exc:
            print(f"[WARN] Batch verification failed: {exc}")
