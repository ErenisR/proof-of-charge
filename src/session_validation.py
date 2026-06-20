"""Validation helpers for research experiment sessions and receipts."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import mean
from typing import Any

from .merkle import merkle_root
from .receipt_builder import hash_receipt
from .receipt_schema import REQUIRED_RECEIPT_FIELDS, validate_receipt_model


def validate_session_receipt(
    session: dict[str, Any],
    receipt: dict[str, Any],
    receipt_hash: str,
) -> list[str]:
    failures: list[str] = []
    session_id = session.get("session_id")

    _require_text(session, "session_id", failures)
    _require_text(session, "user_id", failures)
    _require_text(session, "evse_id", failures)
    _require_text(session, "ocpp_tx_id", failures)

    start_ts = _parse_ts(session.get("start_ts"))
    end_ts = _parse_ts(session.get("end_ts"))
    if not start_ts:
        failures.append("invalid_start_ts")
    if not end_ts:
        failures.append("invalid_end_ts")
    if start_ts and end_ts and end_ts <= start_ts:
        failures.append("non_positive_duration")

    meter_values = session.get("meter_values") or []
    if len(meter_values) < 2:
        failures.append("insufficient_meter_values")
    _validate_meter_values(meter_values, session.get("session_type"), failures)

    missing_receipt_fields = sorted(REQUIRED_RECEIPT_FIELDS - set(receipt))
    if missing_receipt_fields:
        failures.append("missing_receipt_fields")
    try:
        validate_receipt_model(receipt)
    except Exception as exc:
        failures.append(f"invalid_receipt_model:{exc}")

    expected_hash = hash_receipt(receipt)
    if expected_hash != receipt_hash:
        failures.append("receipt_hash_mismatch")

    expected_root = _meter_merkle_root(meter_values)
    if expected_root and receipt.get("merkle_root") != expected_root:
        failures.append("merkle_root_mismatch")

    if receipt.get("session_id") != session_id:
        failures.append("receipt_session_id_mismatch")

    return failures


def summarize_validation(failures_by_session: dict[str, list[str]]) -> dict[str, Any]:
    failure_types = Counter(
        failure
        for failures in failures_by_session.values()
        for failure in failures
    )
    failed_sessions = {
        session_id: failures
        for session_id, failures in failures_by_session.items()
        if failures
    }
    return {
        "validation_failure_count": sum(len(failures) for failures in failures_by_session.values()),
        "validation_failed_sessions": len(failed_sessions),
        "validation_failure_types": dict(sorted(failure_types.items())),
        "validation_failures": failed_sessions,
    }


def summarize_session_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    durations: list[float] = []
    imports: list[float] = []
    exports: list[float] = []
    nets: list[float] = []
    type_counts: Counter[str] = Counter()

    for record in records:
        session = record["session"]
        receipt = record["receipt"]
        start_ts = _parse_ts(session.get("start_ts"))
        end_ts = _parse_ts(session.get("end_ts"))
        if start_ts and end_ts:
            durations.append((end_ts - start_ts).total_seconds())

        summary = receipt.get("energy_summary") or {}
        imports.append(_to_float(summary.get("import_kwh")))
        exports.append(_to_float(summary.get("export_kwh")))
        nets.append(_to_float(summary.get("net_kwh")))
        type_counts[str(receipt.get("session_type") or session.get("session_type"))] += 1

    return {
        "num_sessions_generated": len(records),
        "session_type_counts": dict(sorted(type_counts.items())),
        "charge_only_sessions": type_counts.get("charge_only", 0),
        "discharge_only_sessions": type_counts.get("discharge_only", 0),
        "bidirectional_sessions": type_counts.get("bidirectional", 0),
        **_stat_fields("session_duration_sec", durations),
        **_stat_fields("import_kwh", imports),
        **_stat_fields("export_kwh", exports),
        **_stat_fields("net_kwh", nets),
    }


def _validate_meter_values(meter_values: list[dict[str, Any]], session_type: str | None, failures: list[str]) -> None:
    previous_ts: datetime | None = None
    previous_import: float | None = None
    previous_export: float | None = None

    for mv in meter_values:
        ts = _parse_ts(mv.get("ts"))
        import_kwh = _to_float(mv.get("import_kwh"))
        export_kwh = _to_float(mv.get("export_kwh"))
        energy_kwh = _to_float(mv.get("energy_kwh"))

        if not ts:
            failures.append("invalid_meter_ts")
        if ts and previous_ts and ts < previous_ts:
            failures.append("meter_values_not_ordered")
        if import_kwh < 0 or export_kwh < 0:
            failures.append("negative_meter_counter")
        if previous_import is not None and import_kwh < previous_import:
            failures.append("non_monotone_import_kwh")
        if previous_export is not None and export_kwh < previous_export:
            failures.append("non_monotone_export_kwh")
        if not _nearly_equal(import_kwh - export_kwh, energy_kwh):
            failures.append("meter_energy_kwh_mismatch")

        previous_ts = ts or previous_ts
        previous_import = import_kwh
        previous_export = export_kwh

    if len(meter_values) >= 2:
        first = meter_values[0]
        last = meter_values[-1]
        import_delta = round(_to_float(last.get("import_kwh")) - _to_float(first.get("import_kwh")), 3)
        export_delta = round(_to_float(last.get("export_kwh")) - _to_float(first.get("export_kwh")), 3)
        if session_type == "charge_only" and (import_delta <= 0 or export_delta != 0):
            failures.append("invalid_charge_only_counters")
        if session_type == "discharge_only" and (export_delta <= 0 or import_delta != 0):
            failures.append("invalid_discharge_only_counters")
        if session_type == "bidirectional" and (import_delta <= 0 or export_delta <= 0):
            failures.append("invalid_bidirectional_counters")


def _meter_merkle_root(meter_values: list[dict[str, Any]]) -> str | None:
    if not meter_values:
        return None
    leaves = []
    for mv in sorted(meter_values, key=lambda item: item["ts"]):
        leaf_str = f'{mv["ts"]}|{round(_to_float(mv.get("import_kwh")), 3)}|{round(_to_float(mv.get("export_kwh")), 3)}'
        leaves.append(leaf_str.encode("utf-8"))
    return "0x" + merkle_root(leaves).hex()


def _require_text(payload: dict[str, Any], field: str, failures: list[str]) -> None:
    if not str(payload.get(field) or "").strip():
        failures.append(f"missing_{field}")


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _nearly_equal(left: float, right: float, tolerance: float = 0.0015) -> bool:
    return abs(round(left, 3) - round(right, 3)) <= tolerance


def _stat_fields(prefix: str, values: list[float]) -> dict[str, float | None]:
    if not values:
        return {
            f"{prefix}_min": None,
            f"{prefix}_max": None,
            f"{prefix}_avg": None,
        }
    return {
        f"{prefix}_min": round(min(values), 6),
        f"{prefix}_max": round(max(values), 6),
        f"{prefix}_avg": round(mean(values), 6),
    }
