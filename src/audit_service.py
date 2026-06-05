import argparse
import json
from typing import Any

from sqlalchemy.orm import Session

from . import db
from .models import ChargingSession, Receipt
from .receipt_builder import build_receipt, hash_receipt
from .repository import persist_verification_result


def audit_session(
    session_id: str,
    db_session: Session | None = None,
    persist_result: bool = True,
) -> dict[str, Any]:
    owns_session = db_session is None
    session = db_session or db.session_scope()

    try:
        charging_session = session.get(ChargingSession, session_id)
        receipt_row = session.get(Receipt, session_id)
        if not charging_session:
            raise ValueError(f"Session {session_id} not found in sessions table")
        if not receipt_row:
            raise ValueError(f"Receipt {session_id} not found in receipts table")

        rebuilt_receipt = build_receipt(charging_session.session_json)
        rebuilt_hash = hash_receipt(rebuilt_receipt)
        stored_receipt_hash = hash_receipt(receipt_row.receipt_json or {})
        normalized_mismatches = _normalized_mismatches(receipt_row)
        receipt_json_match = rebuilt_receipt == receipt_row.receipt_json
        hash_match = rebuilt_hash == receipt_row.receipt_hash
        stored_json_hash_match = stored_receipt_hash == receipt_row.receipt_hash
        normalized_match = not normalized_mismatches
        audit_ok = all(
            [
                receipt_json_match,
                hash_match,
                stored_json_hash_match,
                normalized_match,
            ]
        )

        result = {
            "session_id": session_id,
            "day": _receipt_day(rebuilt_receipt),
            "match": audit_ok,
            "receipt_json_match": receipt_json_match,
            "hash_match": hash_match,
            "stored_json_hash_match": stored_json_hash_match,
            "normalized_match": normalized_match,
            "expected_hash": receipt_row.receipt_hash,
            "rebuilt_hash": rebuilt_hash,
            "stored_receipt_json_hash": stored_receipt_hash,
            "normalized_mismatches": normalized_mismatches,
        }
        if persist_result:
            persist_verification_result(
                {
                    **result,
                    "computed_hash": rebuilt_hash,
                },
                verification_type="audit",
                db_session=session,
            )
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


def _normalized_mismatches(receipt: Receipt) -> list[dict[str, Any]]:
    receipt_json = receipt.receipt_json or {}
    energy_summary = receipt_json.get("energy_summary") or {}
    expected = {
        "energy_kwh": _as_float(receipt_json.get("energy_kwh")),
        "import_kwh": _as_float(energy_summary.get("import_kwh")),
        "export_kwh": _as_float(energy_summary.get("export_kwh")),
        "net_kwh": _as_float(energy_summary.get("net_kwh")),
        "merkle_root": receipt_json.get("merkle_root"),
        "schema_version": receipt_json.get("schema_version"),
        "session_type": receipt_json.get("session_type"),
    }
    actual = {
        "energy_kwh": _as_float(receipt.energy_kwh),
        "import_kwh": _as_float(receipt.import_kwh),
        "export_kwh": _as_float(receipt.export_kwh),
        "net_kwh": _as_float(receipt.net_kwh),
        "merkle_root": receipt.merkle_root,
        "schema_version": receipt.schema_version,
        "session_type": receipt.session_type,
    }
    return [
        {
            "field": field,
            "stored_column": actual[field],
            "receipt_json": expected[field],
        }
        for field in expected
        if actual[field] != expected[field]
    ]


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _receipt_day(receipt: dict[str, Any]) -> str | None:
    start_ts = receipt.get("start_ts")
    if isinstance(start_ts, str) and len(start_ts) >= 10:
        return start_ts[:10]
    end_ts = receipt.get("end_ts")
    if isinstance(end_ts, str) and len(end_ts) >= 10:
        return end_ts[:10]
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit a DB-backed charging session.")
    parser.add_argument("session_id", help="Session ID to audit")
    args = parser.parse_args()

    audit = audit_session(args.session_id)
    print(json.dumps(audit, indent=2, sort_keys=True))
    if not audit["match"]:
        raise SystemExit(1)
