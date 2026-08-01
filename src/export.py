# src/export.py
import csv
import json
from pathlib import Path
from typing import Dict, Any, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db
from .batch_anchoring import ANCHORS_FILE, _receipt_day
from .models import BatchAnchor, BatchAnchorReceipt, ChargingSession, MeterValue, Receipt, Verification
from .receipt_builder import hash_receipt
from .storage import BASE_DIR, load_index

EXPORT_DIR = BASE_DIR / "exports"

RECEIPTS_FIELDS = [
    "session_id",
    "user_id",
    "pricing_model",
    "energy_kwh",
    "import_kwh",
    "export_kwh",
    "net_kwh",
    "schema_version",
    "canonicalization_profile",
    "hash_algorithm",
    "start_ts",
    "end_ts",
    "batch_day",
    "receipt_hash",
    "merkle_root",
    "batch_root",
]

SESSIONS_FIELDS = [
    "session_id",
    "user_id",
    "evse_id",
    "start_ts",
    "end_ts",
    "session_type",
    "energy_kwh",
    "import_kwh",
    "export_kwh",
    "net_kwh",
    "tariff_model",
]

METER_VALUES_FIELDS = ["session_id", "ts", "energy_kwh", "import_kwh", "export_kwh"]
ANCHORS_FIELDS = ["day", "receipt_count", "anchored_at", "chain_tx", "cid", "batch_root"]
VERIFICATIONS_FIELDS = ["session_id", "match", "batch_day", "expected_hash", "computed_hash", "batch_root"]


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


def export_all(export_dir: Path = EXPORT_DIR) -> None:
    if db.database_enabled():
        session = db.session_scope()
        try:
            export_all_from_db(session, export_dir=export_dir)
        finally:
            session.close()
        return
    export_all_from_files(export_dir=export_dir)


def export_all_from_files(export_dir: Path = EXPORT_DIR) -> None:
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
                "user_id": receipt.get("user_id"),
                "receipt_hash": receipt_hash,
                "merkle_root": receipt.get("merkle_root"),
                "pricing_model": (receipt.get("pricing") or {}).get("model"),
                "energy_kwh": receipt.get("energy_kwh"),
                "import_kwh": (receipt.get("energy_summary") or {}).get("import_kwh"),
                "export_kwh": (receipt.get("energy_summary") or {}).get("export_kwh"),
                "net_kwh": (receipt.get("energy_summary") or {}).get("net_kwh"),
                "schema_version": receipt.get("schema_version"),
                "canonicalization_profile": receipt.get("canonicalization_profile"),
                "hash_algorithm": receipt.get("hash_algorithm"),
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
                    "user_id": session.get("user_id"),
                    "evse_id": session.get("evse_id"),
                    "start_ts": session.get("start_ts"),
                    "end_ts": session.get("end_ts"),
                    "energy_kwh": receipt.get("energy_kwh"),
                    "session_type": session.get("session_type"),
                    "import_kwh": (receipt.get("energy_summary") or {}).get("import_kwh"),
                    "export_kwh": (receipt.get("energy_summary") or {}).get("export_kwh"),
                    "net_kwh": (receipt.get("energy_summary") or {}).get("net_kwh"),
                    "tariff_model": (session.get("pricing") or {}).get("model"),
                }
            )

            for mv in session.get("meter_values", []) or []:
                meter_rows.append(
                    {
                        "session_id": session_id,
                        "ts": mv.get("ts"),
                        "energy_kwh": mv.get("energy_kwh"),
                        "import_kwh": mv.get("import_kwh"),
                        "export_kwh": mv.get("export_kwh"),
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
        export_dir / "receipts.csv",
        receipts_rows,
        RECEIPTS_FIELDS,
    )
    _write_csv(
        export_dir / "sessions.csv",
        sessions_rows,
        SESSIONS_FIELDS,
    )
    _write_csv(
        export_dir / "meter_values.csv",
        meter_rows,
        METER_VALUES_FIELDS,
    )
    _write_csv(
        export_dir / "anchors.csv",
        anchors_rows,
        ANCHORS_FIELDS,
    )
    _write_csv(
        export_dir / "verifications.csv",
        verifications_rows,
        VERIFICATIONS_FIELDS,
    )


def export_all_from_db(db_session: Session, export_dir: Path = EXPORT_DIR) -> None:
    sessions = {
        row.session_id: row
        for row in db_session.scalars(select(ChargingSession).order_by(ChargingSession.session_id))
    }
    receipts = list(db_session.scalars(select(Receipt).order_by(Receipt.session_id)))
    meter_values = list(
        db_session.scalars(
            select(MeterValue).order_by(MeterValue.session_id, MeterValue.sample_index)
        )
    )
    anchors = list(
        db_session.scalars(
            select(BatchAnchor).order_by(BatchAnchor.day, BatchAnchor.id)
        )
    )
    memberships = list(
        db_session.scalars(
            select(BatchAnchorReceipt).order_by(BatchAnchorReceipt.anchor_id, BatchAnchorReceipt.leaf_index)
        )
    )
    verifications = list(
        db_session.scalars(
            select(Verification).order_by(Verification.created_at, Verification.id)
        )
    )

    anchor_by_receipt = _anchor_lookup(memberships, anchors)

    receipts_rows = []
    sessions_rows = []
    for receipt in receipts:
        charging_session = sessions.get(receipt.session_id)
        batch = anchor_by_receipt.get(receipt.session_id, {})
        receipt_json = receipt.receipt_json or {}
        pricing = receipt_json.get("pricing") or {}
        session_json = charging_session.session_json if charging_session else {}

        receipts_rows.append(
            {
                "session_id": receipt.session_id,
                "user_id": receipt_json.get("user_id") or (charging_session.user_id if charging_session else None),
                "receipt_hash": receipt.receipt_hash,
                "merkle_root": receipt.merkle_root,
                "pricing_model": pricing.get("model"),
                "energy_kwh": receipt.energy_kwh,
                "import_kwh": receipt.import_kwh,
                "export_kwh": receipt.export_kwh,
                "net_kwh": receipt.net_kwh,
                "schema_version": receipt.schema_version,
                "canonicalization_profile": receipt_json.get("canonicalization_profile"),
                "hash_algorithm": receipt_json.get("hash_algorithm"),
                "start_ts": _isoformat(receipt.start_ts),
                "end_ts": _isoformat(receipt.end_ts),
                "batch_root": batch.get("batch_root"),
                "batch_day": batch.get("batch_day"),
            }
        )

        if charging_session:
            session_pricing = (session_json.get("pricing") or {})
            sessions_rows.append(
                {
                    "session_id": charging_session.session_id,
                    "user_id": charging_session.user_id,
                    "evse_id": charging_session.evse_id,
                    "start_ts": _isoformat(charging_session.start_ts),
                    "end_ts": _isoformat(charging_session.end_ts),
                    "session_type": charging_session.session_type,
                    "energy_kwh": receipt.energy_kwh,
                    "import_kwh": receipt.import_kwh,
                    "export_kwh": receipt.export_kwh,
                    "net_kwh": receipt.net_kwh,
                    "tariff_model": session_pricing.get("model"),
                }
            )

    meter_rows = [
        {
            "session_id": mv.session_id,
            "ts": _isoformat(mv.ts),
            "energy_kwh": mv.energy_kwh,
            "import_kwh": mv.import_kwh,
            "export_kwh": mv.export_kwh,
        }
        for mv in meter_values
    ]

    anchors_rows = [
        {
            "day": anchor.day,
            "batch_root": anchor.batch_root,
            "receipt_count": anchor.receipt_count,
            "chain_tx": anchor.chain_tx,
            "cid": anchor.cid,
            "anchored_at": _isoformat(anchor.anchored_at),
        }
        for anchor in anchors
    ]

    verifications_rows = []
    for verification in verifications:
        details = verification.details_json or {}
        verifications_rows.append(
            {
                "session_id": verification.session_id,
                "expected_hash": verification.expected_hash,
                "computed_hash": verification.computed_hash,
                "match": str(verification.match),
                "batch_root": verification.expected_root or details.get("expected_root"),
                "batch_day": verification.day or details.get("day"),
            }
        )

    _write_csv(export_dir / "receipts.csv", receipts_rows, RECEIPTS_FIELDS)
    _write_csv(export_dir / "sessions.csv", sessions_rows, SESSIONS_FIELDS)
    _write_csv(export_dir / "meter_values.csv", meter_rows, METER_VALUES_FIELDS)
    _write_csv(export_dir / "anchors.csv", anchors_rows, ANCHORS_FIELDS)
    _write_csv(export_dir / "verifications.csv", verifications_rows, VERIFICATIONS_FIELDS)


def _anchor_lookup(
    memberships: List[BatchAnchorReceipt],
    anchors: List[BatchAnchor],
) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    anchors_by_id = {anchor.id: anchor for anchor in anchors}
    for membership in memberships:
        anchor = anchors_by_id.get(membership.anchor_id)
        if not anchor:
            continue
        lookup[membership.session_id] = {
            "batch_day": anchor.day,
            "batch_root": anchor.batch_root,
        }
    return lookup


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


if __name__ == "__main__":
    export_all()
    print(f"[OK] Exported CSVs to {EXPORT_DIR}")
