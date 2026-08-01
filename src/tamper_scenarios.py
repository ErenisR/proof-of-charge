"""Rollback-isolated, layer-specific tamper matrix for DB-backed receipts."""

from __future__ import annotations

import csv
import json
import platform
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit_service import audit_session
from .batch_anchoring import build_batch_root
from .blockchain.cast_client import OnChainAnchor
from .blockchain.config import BlockchainConfig
from .blockchain.verifier import verify_on_chain_anchor_by_id
from .meter_verifier import verify_meter_stream_from_db
from .models import BatchAnchor, BatchAnchorReceipt, MeterValue, Receipt
from .verifier import verify_session_from_db
from .verifier_batch import verify_day_from_db

LAYER_NAMES = (
    "receipt_hash",
    "normalized_meter_stream",
    "database_audit",
    "batch_membership_and_root",
    "on_chain_comparison",
)
SCENARIOS = {
    "T1": ("Receipt content modification", "receipt_json.energy_kwh", {"receipt_hash", "database_audit"}),
    "T2": ("Meter sample modification", "meter_values.import_kwh", {"normalized_meter_stream", "database_audit"}),
    "T3": ("Meter sample deletion", "meter_values row", {"normalized_meter_stream", "database_audit"}),
    "T4": ("Normalized receipt-column modification", "receipts.merkle_root", {"database_audit"}),
    "T5": ("Batch-membership removal", "batch_anchor_receipts row", {"batch_membership_and_root"}),
    "T6": ("Stored batch-root replacement", "batch_anchors.batch_root", {"batch_membership_and_root", "on_chain_comparison"}),
    "T7": ("Incorrect on-chain root", "blockchain reader result", {"on_chain_comparison"}),
}


@dataclass(frozen=True)
class MatrixContext:
    day: str
    prefix: str
    session_id: str
    anchor_id: int
    chain_root: str
    chain_count: int


def run_tamper_matrix(
    *,
    db_session: Session,
    day: str,
    session_prefix: str,
    session_id: str,
    anchor_id: int | None = None,
    scenario_ids: list[str] | None = None,
    output_dir: Path | None = None,
    run_id: str = "tamper_matrix",
    command_line_arguments: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute scenarios using savepoints and verify rollback after each."""
    anchor = _select_anchor(db_session, day, session_prefix, anchor_id)
    ctx = MatrixContext(day, session_prefix, session_id, anchor.id, anchor.batch_root, anchor.receipt_count)
    _assert_invariants(db_session, ctx)
    selected = scenario_ids or list(SCENARIOS)
    unknown = sorted(set(selected) - set(SCENARIOS))
    if unknown:
        raise ValueError(f"Unknown scenarios: {', '.join(unknown)}")

    records = []
    for scenario_id in selected:
        before = _checks(db_session, ctx, _matching_reader(ctx))
        baseline_valid_before = _baseline_valid(before)
        savepoint = db_session.begin_nested()
        mutation_details: dict[str, Any] = {}
        error = None
        restoration_error = None
        post: dict[str, Any] = {}
        mutation_applied = False
        try:
            mutation_details = _mutate(db_session, ctx, scenario_id)
            mutation_applied = True
            reader = _mismatch_reader(ctx) if scenario_id == "T7" else _matching_reader(ctx)
            post = _checks(db_session, ctx, reader)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                savepoint.rollback()
                db_session.expire_all()
            except Exception as exc:
                restoration_error = f"{type(exc).__name__}: {exc}"
        after = _checks(db_session, ctx, _matching_reader(ctx)) if restoration_error is None else {}
        baseline_valid_after = _baseline_valid(after) if after else False
        record = _record(
            scenario_id=scenario_id,
            ctx=ctx,
            baseline=before,
            post=post,
            mutation_details=mutation_details,
            mutation_applied=mutation_applied,
            state_restored=restoration_error is None,
            baseline_valid_before=baseline_valid_before,
            baseline_valid_after=baseline_valid_after,
            restoration_error=restoration_error,
            error=error,
        )
        records.append(record)

    summary = _summary(records, command_line_arguments or sys.argv, database_backend=db_session.bind.dialect.name)
    if output_dir:
        write_artifacts(output_dir, records, summary, run_id=run_id)
    return records, summary


def _checks(session: Session, ctx: MatrixContext, reader: Callable) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    functions = {
        "receipt_hash": lambda: verify_session_from_db(ctx.session_id, session, False),
        "normalized_meter_stream": lambda: verify_meter_stream_from_db(ctx.session_id, session, False),
        "database_audit": lambda: audit_session(ctx.session_id, session, False),
        "batch_membership_and_root": lambda: verify_day_from_db(
            ctx.day, ctx.prefix, session, persist_result=False
        ),
        "on_chain_comparison": lambda: verify_on_chain_anchor_by_id(
            ctx.anchor_id,
            db_session=session,
            config=BlockchainConfig("injected://reader", "0x" + "1" * 40, None, 31337),
            reader=reader,
            persist_result=False,
        ),
    }
    for name, function in functions.items():
        try:
            details = function()
            raw[name] = _layer("pass" if details["match"] else "mismatch", details["match"], details)
        except (LookupError, ValueError) as exc:
            raw[name] = _layer("lookup_failure", False, {"error": str(exc)})
        except Exception as exc:
            raw[name] = _layer("error", False, {"error": f"{type(exc).__name__}: {exc}"})
    return raw


def _mutate(session: Session, ctx: MatrixContext, scenario_id: str) -> dict[str, Any]:
    receipt = session.get(Receipt, ctx.session_id)
    anchor = session.get(BatchAnchor, ctx.anchor_id)
    rows = list(session.scalars(select(MeterValue).where(MeterValue.session_id == ctx.session_id).order_by(MeterValue.sample_index)))
    if scenario_id == "T1":
        payload = deepcopy(receipt.receipt_json)
        original = float(payload["energy_kwh"])
        changed = round(original + 0.123, 3)
        payload["energy_kwh"] = f"{changed:.3f}" if isinstance(receipt.receipt_json["energy_kwh"], str) else changed
        receipt.receipt_json = payload
        session.flush()
        return {"field": "receipt_json.energy_kwh", "before": original, "after": payload["energy_kwh"]}
    if scenario_id == "T2":
        row = rows[len(rows) // 2]
        original = float(row.import_kwh)
        row.import_kwh = round(original + 0.111, 3)
        session.flush()
        return {"row_id": row.id, "sample_index": row.sample_index, "before": original, "after": row.import_kwh}
    if scenario_id == "T3":
        if len(rows) < 3:
            raise ValueError("T3 requires at least three meter samples")
        row = rows[len(rows) // 2]
        snapshot = {column.name: getattr(row, column.name) for column in MeterValue.__table__.columns}
        session.delete(row)
        session.flush()
        return {"deleted_row": _jsonable(snapshot), "before_count": len(rows), "after_count": len(rows) - 1}
    if scenario_id == "T4":
        original = receipt.merkle_root
        receipt.merkle_root = _incorrect_root(original)
        session.flush()
        return {"field": "receipts.merkle_root", "before": original, "after": receipt.merkle_root}
    if scenario_id == "T5":
        memberships = list(session.scalars(select(BatchAnchorReceipt).where(BatchAnchorReceipt.anchor_id == ctx.anchor_id).order_by(BatchAnchorReceipt.leaf_index)))
        target = next((m for m in memberships if m.session_id == ctx.session_id), memberships[len(memberships) // 2])
        snapshot = {column.name: getattr(target, column.name) for column in BatchAnchorReceipt.__table__.columns}
        session.delete(target)
        session.flush()
        return {"deleted_membership": _jsonable(snapshot), "before_count": len(memberships), "after_count": len(memberships) - 1}
    if scenario_id == "T6":
        original = anchor.batch_root
        anchor.batch_root = _incorrect_root(original)
        session.flush()
        return {"field": "batch_anchors.batch_root", "before": original, "after": anchor.batch_root}
    if scenario_id == "T7":
        return {"condition": "dependency-injected incorrect on-chain root", "database_mutated": False}
    raise ValueError(scenario_id)


def _record(**values: Any) -> dict[str, Any]:
    scenario_id = values["scenario_id"]
    ctx = values["ctx"]
    baseline = values["baseline"]
    post = values["post"]
    name, target, expected = SCENARIOS[scenario_id]
    actual = sorted(name for name, layer in post.items() if layer.get("status") in {"mismatch", "lookup_failure"})
    for layer_name, layer in post.items():
        layer["expected_to_detect"] = layer_name in expected
        layer["detected"] = layer_name in actual
    unaffected_ok = all(
        layer.get("status") == "pass"
        for name_, layer in post.items()
        if name_ not in expected
    )
    expected_detected = expected.issubset(actual)
    no_errors = all(layer.get("status") not in {"error", "skipped"} for layer in post.values())
    expected_met = all(
        [
            values["baseline_valid_before"],
            values["mutation_applied"],
            expected_detected,
            unaffected_ok,
            no_errors,
            values["state_restored"],
            values["baseline_valid_after"],
            values["error"] is None,
        ]
    )
    meter = post.get("normalized_meter_stream", {})
    meter_details = meter.get("details", {})
    batch = post.get("batch_membership_and_root", {})
    batch_details = batch.get("details", {})
    flat = {
        "scenario_id": scenario_id,
        "scenario_name": name,
        "description": f"Controlled mutation of {target}",
        "day": ctx.day,
        "session_prefix": ctx.prefix,
        "session_id": ctx.session_id,
        "anchor_id": ctx.anchor_id,
        "mutation_target": target,
        "mutation_details": values["mutation_details"],
        "execution_mode": "injected_reader_mismatch" if scenario_id == "T7" else "database_savepoint",
        "baseline_checks": baseline,
        "post_tamper_checks": post,
        "baseline_valid_before": values["baseline_valid_before"],
        "mutation_applied": values["mutation_applied"],
        "state_restored": values["state_restored"],
        "restoration_error": values["restoration_error"],
        "receipt_hash_status": post.get("receipt_hash", {}).get("status"),
        "receipt_hash_match": post.get("receipt_hash", {}).get("match"),
        "meter_stream_status": meter.get("status"),
        "meter_root_match": meter_details.get("meter_root_match"),
        "meter_sample_count": meter_details.get("meter_sample_count"),
        "meter_sample_count_match": meter_details.get("meter_sample_count_match"),
        "audit_status": post.get("database_audit", {}).get("status"),
        "audit_match": post.get("database_audit", {}).get("match"),
        "batch_status": batch.get("status"),
        "batch_root_match": batch_details.get("root_match"),
        "batch_receipt_count_match": batch_details.get("receipt_count_match"),
        "on_chain_status": post.get("on_chain_comparison", {}).get("status"),
        "on_chain_match": post.get("on_chain_comparison", {}).get("match"),
        "expected_detecting_layers": sorted(expected),
        "actual_detecting_layers": actual,
        "unaffected_layers_behaved_as_expected": unaffected_ok,
        "expected_behavior_met": expected_met,
        "detected_by_at_least_one_expected_layer": bool(expected.intersection(actual)),
        "restoration_successful": values["state_restored"] and values["baseline_valid_after"],
        "baseline_valid_after": values["baseline_valid_after"],
        "notes": "Checks are experimentally executed; no layer result is inferred from the mutation.",
        "error": values["error"],
    }
    return flat


def _assert_invariants(session: Session, ctx: MatrixContext) -> None:
    receipt = session.get(Receipt, ctx.session_id)
    if not receipt:
        raise ValueError(f"Selected receipt does not exist: {ctx.session_id}")
    rows = list(session.scalars(select(MeterValue).where(MeterValue.session_id == ctx.session_id)))
    if len(rows) < 3:
        raise ValueError("Selected session must have at least three meter samples")
    memberships = list(session.scalars(select(BatchAnchorReceipt).where(BatchAnchorReceipt.anchor_id == ctx.anchor_id)))
    membership = next((m for m in memberships if m.session_id == ctx.session_id), None)
    if not membership:
        raise ValueError("Selected receipt does not belong to selected anchor")
    anchor = session.get(BatchAnchor, ctx.anchor_id)
    if len(memberships) != anchor.receipt_count:
        raise ValueError("Stored membership count does not equal anchor receipt_count")
    if build_batch_root([m.receipt_hash for m in memberships]) != anchor.batch_root:
        raise ValueError("Recomputed baseline batch root differs from stored root")


def _select_anchor(session: Session, day: str, prefix: str, anchor_id: int | None) -> BatchAnchor:
    if anchor_id is not None:
        anchor = session.get(BatchAnchor, anchor_id)
    else:
        anchor = session.scalar(
            select(BatchAnchor)
            .where(BatchAnchor.day == day, BatchAnchor.session_prefix == prefix)
            .order_by(BatchAnchor.id.desc())
        )
    if not anchor:
        raise ValueError("Selected batch anchor does not exist")
    return anchor


def _matching_reader(ctx: MatrixContext) -> Callable:
    return lambda config, day, prefix: OnChainAnchor(ctx.chain_root, ctx.chain_count, "injected-baseline", 0)


def _mismatch_reader(ctx: MatrixContext) -> Callable:
    wrong = _incorrect_root(ctx.chain_root)
    return lambda config, day, prefix: OnChainAnchor(wrong, ctx.chain_count, "injected-mismatch", 0)


def _incorrect_root(real: str) -> str:
    candidate = "0x" + "00" * 32
    return "0x" + "ff" * 32 if candidate.lower() == real.lower() else candidate


def _layer(status: str, match: bool | None, details: dict[str, Any]) -> dict[str, Any]:
    return {"status": status, "match": match, "expected_to_detect": False, "detected": False, "details": details}


def _baseline_valid(checks: dict[str, Any]) -> bool:
    return bool(checks) and all(layer["status"] == "pass" and layer["match"] is True for layer in checks.values())


def _summary(records: list[dict[str, Any]], argv: list[str], database_backend: str) -> dict[str, Any]:
    skipped = sum(any(layer["status"] == "skipped" for layer in r["post_tamper_checks"].values()) for r in records)
    return {
        "total_defined_scenarios": len(SCENARIOS),
        "scenarios_executed": len(records) - skipped,
        "scenarios_skipped": skipped,
        "scenarios_meeting_expected_behavior": sum(r["expected_behavior_met"] for r in records),
        "scenarios_detected_by_expected_layers": sum(r["detected_by_at_least_one_expected_layer"] for r in records),
        "scenarios_with_unexpected_layer_behavior": sum(not r["unaffected_layers_behaved_as_expected"] for r in records),
        "restoration_failures": sum(not r["restoration_successful"] for r in records),
        "baseline_failures": sum(not r["baseline_valid_before"] or not r["baseline_valid_after"] for r in records),
        "real_anvil_used": False,
        "injected_reader_used": True,
        "database_backend": database_backend,
        "source_commit_sha": _git_sha(),
        "execution_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "python_version": platform.python_version(),
        "operating_system": platform.platform(),
        "command_line_arguments": argv,
    }


def write_artifacts(output_dir: Path, records: list[dict[str, Any]], summary: dict[str, Any], *, run_id: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "tamper_matrix.json", records)
    _write_json(output_dir / "tamper_matrix_summary.json", summary)
    _write_json(output_dir / "manifest.json", {"run_id": run_id, "summary": summary, "artifacts": ["tamper_matrix.json", "tamper_matrix.csv", "tamper_matrix.md", "tamper_matrix_summary.json"]})
    scalar_keys = [key for key, value in records[0].items() if not isinstance(value, (dict, list))] if records else []
    with (output_dir / "tamper_matrix.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=scalar_keys)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in scalar_keys} for row in records)
    (output_dir / "tamper_matrix.md").write_text(_markdown(records, summary), encoding="utf-8")


def _markdown(records: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines = [
        "# Controlled tamper matrix",
        "",
        "| Scenario | Modified object | Receipt hash | Meter stream | Database audit | Batch verification | On-chain comparison | Expected behavior met | Restoration successful |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in records:
        lines.append(
            f"| {row['scenario_id']} {row['scenario_name']} | {row['mutation_target']} | "
            f"{row['receipt_hash_status']} | {row['meter_stream_status']} | {row['audit_status']} | "
            f"{row['batch_status']} | {row['on_chain_status']} | {row['expected_behavior_met']} | "
            f"{row['restoration_successful']} |"
        )
    lines += [
        "",
        "These results apply only to the seven controlled deterministic scenarios; they are not a general attack-detection probability. They do not establish source-meter authenticity or prove complete session capture. Unchanged historical batch and chain commitments are expected for some local off-chain mutations because those commitments retain the original receipt-hash membership snapshot.",
        "",
        f"Executed: {summary['scenarios_executed']}; expected behavior met: {summary['scenarios_meeting_expected_behavior']}; restoration failures: {summary['restoration_failures']}.",
    ]
    return "\n".join(lines) + "\n"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _jsonable(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item.isoformat() if hasattr(item, "isoformat") else item for key, item in value.items()}


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    except Exception:
        return None
