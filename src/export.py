# src/export.py
import csv
import json
from pathlib import Path
from typing import Dict, Any, List

from .batch_anchoring import ANCHORS_FILE, _receipt_day
from .receipt_builder import hash_receipt
from .storage import BASE_DIR, load_index

EXPORT_DIR = BASE_DIR / "exports"


def _load_receipt_payload(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _load_anchors() -> List[Dict[str, Any]]:
    if not ANCHORS_FILE.exists():
        return []
    data = json.loads(ANCHORS_FILE.read_text())
    return data.get("batches", [])


def _write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def export_all() -> None:
    index = load_index()
    receipts_rows = []
    sessions_rows = []
    meter_rows = []
    verifications_rows = []

    for session_id, entry in index.items():
        path = Path(entry["file"])
        if not path.exists():
            continue
        payload = _load_receipt_payload(path)
        receipt = payload.get("receipt", {})
        receipt_hash = payload.get("hash")
        session = payload.get("session", {})

        receipts_rows.append(
            {
                "session_id": session_id,
                "receipt_hash": receipt_hash,
                "merkle_root": receipt.get("merkle_root"),
                "pricing_model": (receipt.get("pricing") or {}).get("model"),
                "energy_kwh": receipt.get("energy_kwh"),
                "start_ts": receipt.get("start_ts"),
                "end_ts": receipt.get("end_ts"),
                "batch_root": entry.get("batch_root"),
                "batch_day": entry.get("batch_day"),
            }
        )

        if session:
            sessions_rows.append(
                {
                    "session_id": session_id,
                    "evse_id": session.get("evse_id"),
                    "start_ts": session.get("start_ts"),
                    "end_ts": session.get("end_ts"),
                    "energy_kwh": receipt.get("energy_kwh"),
                    "tariff_model": (session.get("pricing") or {}).get("model"),
                }
            )

            for mv in session.get("meter_values", []) or []:
                meter_rows.append(
                    {
                        "session_id": session_id,
                        "ts": mv.get("ts"),
                        "energy_kwh": mv.get("energy_kwh"),
                    }
                )

        expected = receipt_hash
        computed = hash_receipt(receipt)
        verifications_rows.append(
            {
                "session_id": session_id,
                "expected_hash": expected,
                "computed_hash": computed,
                "match": str(expected == computed),
                "batch_root": entry.get("batch_root"),
                "batch_day": entry.get("batch_day"),
            }
        )

    anchors_rows = []
    for anchor in _load_anchors():
        anchors_rows.append(
            {
                "day": anchor.get("day"),
                "batch_root": anchor.get("batch_root"),
                "receipt_count": anchor.get("receipt_count"),
                "chain_tx": anchor.get("chain_tx"),
                "cid": anchor.get("cid"),
                "anchored_at": anchor.get("anchored_at"),
            }
        )

    _write_csv(
        EXPORT_DIR / "receipts.csv",
        receipts_rows,
        [
            "session_id",
            "receipt_hash",
            "merkle_root",
            "pricing_model",
            "energy_kwh",
            "start_ts",
            "end_ts",
            "batch_root",
            "batch_day",
        ],
    )
    _write_csv(
        EXPORT_DIR / "sessions.csv",
        sessions_rows,
        ["session_id", "evse_id", "start_ts", "end_ts", "energy_kwh", "tariff_model"],
    )
    _write_csv(
        EXPORT_DIR / "meter_values.csv",
        meter_rows,
        ["session_id", "ts", "energy_kwh"],
    )
    _write_csv(
        EXPORT_DIR / "anchors.csv",
        anchors_rows,
        ["day", "batch_root", "receipt_count", "chain_tx", "cid", "anchored_at"],
    )
    _write_csv(
        EXPORT_DIR / "verifications.csv",
        verifications_rows,
        ["session_id", "expected_hash", "computed_hash", "match", "batch_root", "batch_day"],
    )


if __name__ == "__main__":
    export_all()
    print(f"[OK] Exported CSVs to {EXPORT_DIR}")
