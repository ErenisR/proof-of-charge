"""Independent verification of the normalized ``meter_values`` table."""

from __future__ import annotations

from collections import Counter
from datetime import timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db
from .merkle import merkle_root
from .models import MeterValue, Receipt
from .receipt_builder import meter_leaf_bytes
from .repository import persist_verification_result


def verify_meter_stream_from_db(
    session_id: str,
    db_session: Session | None = None,
    persist_result: bool = True,
) -> dict[str, Any]:
    """Reconstruct the committed meter stream exclusively from normalized rows."""
    owns_session = db_session is None
    session = db_session or db.session_scope()
    try:
        receipt = session.get(Receipt, session_id)
        if not receipt:
            raise ValueError(f"Receipt {session_id} not found in receipts table")
        rows = list(
            session.scalars(
                select(MeterValue)
                .where(MeterValue.session_id == session_id)
                .order_by(MeterValue.ts, MeterValue.sample_index, MeterValue.id)
            )
        )
        indices = [row.sample_index for row in rows]
        timestamps = [_canonical_ts(row.ts) for row in rows]
        duplicate_indices = sorted(k for k, count in Counter(indices).items() if count > 1)
        duplicate_timestamps = sorted(k for k, count in Counter(timestamps).items() if count > 1)
        samples = [
            {
                "ts": timestamp,
                "import_kwh": row.import_kwh if row.import_kwh is not None else row.energy_kwh,
                "export_kwh": row.export_kwh if row.export_kwh is not None else 0.0,
            }
            for row, timestamp in zip(rows, timestamps)
        ]
        computed_root = "0x" + merkle_root([meter_leaf_bytes(mv) for mv in samples]).hex() if samples else None
        expected_root = (receipt.receipt_json or {}).get("merkle_root") or receipt.merkle_root
        expected_count = _expected_sample_count(receipt)
        if expected_count is None and indices:
            # Repository persistence assigns contiguous zero-based sample indices.
            # A gap therefore exposes deletion without consulting session_json.
            expected_count = max(indices) + 1
        actual_count = len(rows)
        count_match = expected_count is None or actual_count == expected_count

        reconstructed = _energy_summary(samples)
        receipt_summary = (receipt.receipt_json or {}).get("energy_summary") or {}
        receipt_expected = {
            "import_kwh": _rounded(receipt_summary.get("import_kwh")),
            "export_kwh": _rounded(receipt_summary.get("export_kwh")),
            "net_kwh": _rounded(receipt_summary.get("net_kwh")),
        }
        normalized_expected = {
            "import_kwh": _rounded(receipt.import_kwh),
            "export_kwh": _rounded(receipt.export_kwh),
            "net_kwh": _rounded(receipt.net_kwh),
        }
        receipt_mismatches = _field_mismatches(reconstructed, receipt_expected)
        normalized_mismatches = _field_mismatches(reconstructed, normalized_expected)
        root_match = computed_root == expected_root
        ordering_ambiguous = bool(duplicate_indices or duplicate_timestamps)
        match = all(
            [
                root_match,
                count_match,
                not ordering_ambiguous,
                not receipt_mismatches,
                not normalized_mismatches,
            ]
        )
        result = {
            "source": "meter_values",
            "session_id": session_id,
            "expected_root": expected_root,
            "computed_root": computed_root,
            "meter_root_match": root_match,
            "expected_sample_count": expected_count,
            "meter_sample_count": actual_count,
            "actual_sample_count": actual_count,
            "meter_sample_count_match": count_match,
            "ordered_sample_indices": indices,
            "ordered_timestamps": timestamps,
            "duplicate_timestamp_detected": bool(duplicate_timestamps),
            "duplicate_sample_index_detected": bool(duplicate_indices),
            "duplicate_timestamps": duplicate_timestamps,
            "duplicate_sample_indices": duplicate_indices,
            "ordering_ambiguous": ordering_ambiguous,
            "reconstructed_import_kwh": reconstructed["import_kwh"],
            "reconstructed_export_kwh": reconstructed["export_kwh"],
            "reconstructed_net_kwh": reconstructed["net_kwh"],
            "receipt_energy_summary_match": not receipt_mismatches,
            "normalized_energy_columns_match": not normalized_mismatches,
            "receipt_energy_mismatches": receipt_mismatches,
            "normalized_energy_mismatches": normalized_mismatches,
            "match": match,
        }
        if persist_result:
            persist_verification_result(
                result, verification_type="normalized_meter_stream", db_session=session
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


def _expected_sample_count(receipt: Receipt) -> int | None:
    value = (receipt.receipt_json or {}).get("meter_sample_count")
    if value is not None:
        return int(value)
    # Existing receipt schema does not store a count. Persisted baseline row count is
    # supplied by the matrix runner for before/after comparisons.
    return None


def _energy_summary(samples: list[dict[str, Any]]) -> dict[str, float | None]:
    if len(samples) < 2:
        return {"import_kwh": None, "export_kwh": None, "net_kwh": None}
    imported = round(float(samples[-1]["import_kwh"]) - float(samples[0]["import_kwh"]), 3)
    exported = round(float(samples[-1]["export_kwh"]) - float(samples[0]["export_kwh"]), 3)
    return {"import_kwh": imported, "export_kwh": exported, "net_kwh": round(imported - exported, 3)}


def _field_mismatches(actual: dict[str, Any], expected: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"field": key, "reconstructed": actual[key], "expected": expected[key]}
        for key in actual
        if actual[key] != expected[key]
    ]


def _rounded(value: Any) -> float | None:
    return None if value is None else round(float(value), 3)


def _canonical_ts(value: Any) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")
