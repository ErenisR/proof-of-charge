"""Set-based reconciliation for DB-backed experiment stages."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import BatchAnchor, BatchAnchorReceipt, ChargingSession, Receipt

ELIGIBILITY_POLICY = (
    "A receipt belongs to the UTC batch whose calendar date equals its normalized "
    "start_ts; eligibility is [day 00:00:00 UTC, next day 00:00:00 UTC)."
)


class ExperimentCountMismatch(RuntimeError):
    """Raised when an experiment's required ID sets do not reconcile."""


def utc_day_bounds(day: str | date) -> tuple[datetime, datetime]:
    parsed = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime.combine(parsed, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def reconcile_experiment_counts(
    *,
    run_id: str,
    day: str,
    requested_session_ids: Iterable[str],
    generated_session_ids: Iterable[str],
    verified_session_ids: Iterable[str],
    db_session: Session,
    anchor_id: int | None = None,
    exported_dataset_dir: Path | None = None,
) -> dict[str, Any]:
    prefix_pattern = f"{run_id}-%"
    requested = set(requested_session_ids)
    generated = set(generated_session_ids)
    sessions = list(db_session.scalars(select(ChargingSession).where(ChargingSession.session_id.like(prefix_pattern))))
    receipts = list(db_session.scalars(select(Receipt).where(Receipt.session_id.like(prefix_pattern))))
    persisted_sessions = {row.session_id for row in sessions}
    persisted_receipts = {row.session_id for row in receipts}
    start, end = utc_day_bounds(day)
    eligible = {row.session_id for row in receipts if _utc(row.start_ts) >= start and _utc(row.start_ts) < end}
    anchor = _select_anchor(db_session, run_id, day, anchor_id)
    memberships = list(db_session.scalars(select(BatchAnchorReceipt).where(BatchAnchorReceipt.anchor_id == anchor.id).order_by(BatchAnchorReceipt.leaf_index)))
    anchored = {row.session_id for row in memberships}
    verified = set(verified_session_ids)
    exported_sessions = _csv_ids(exported_dataset_dir / "sessions.csv") if exported_dataset_dir else set()
    exported_receipts = _csv_ids(exported_dataset_dir / "receipts.csv") if exported_dataset_dir else set()
    exported_verifications = _csv_ids(exported_dataset_dir / "verifications.csv") if exported_dataset_dir else set()

    stages = {
        "requested": requested,
        "generated": generated,
        "persisted_sessions": persisted_sessions,
        "persisted_receipts": persisted_receipts,
        "day_eligible": eligible,
        "anchored": anchored,
        "verified": verified,
        "exported_sessions": exported_sessions,
        "exported_receipts": exported_receipts,
        "exported_verifications": exported_verifications,
    }
    comparisons = {
        name: {
            "count": len(ids),
            "missing_ids": sorted(requested - ids),
            "unexpected_ids": sorted(ids - requested),
            "status": "pass" if ids == requested else "mismatch",
        }
        for name, ids in stages.items()
    }
    failures = [
        f"{name}: missing={len(info['missing_ids'])}, unexpected={len(info['unexpected_ids'])}"
        for name, info in comparisons.items()
        if info["status"] != "pass"
    ]
    if len(memberships) != anchor.receipt_count:
        failures.append(
            f"anchor receipt_count={anchor.receipt_count} but stored memberships={len(memberships)}"
        )
    excluded = []
    session_by_id = {row.session_id: row for row in sessions}
    receipt_by_id = {row.session_id: row for row in receipts}
    all_relevant = requested | generated | persisted_sessions | persisted_receipts
    for session_id in sorted(all_relevant):
        if all(session_id in ids for ids in stages.values()):
            continue
        session_row = session_by_id.get(session_id)
        receipt_row = receipt_by_id.get(session_id)
        start_ts = receipt_row.start_ts if receipt_row else (session_row.start_ts if session_row else None)
        end_ts = receipt_row.end_ts if receipt_row else (session_row.end_ts if session_row else None)
        reasons = [name for name, ids in stages.items() if session_id not in ids]
        excluded.append({
            "session_id": session_id,
            "start_ts": _iso(start_ts),
            "end_ts": _iso(end_ts),
            "normalized_utc_start_date": _utc(start_ts).date().isoformat() if start_ts else None,
            "requested_anchor_day": day,
            "session_prefix": run_id,
            "persistence_status": session_id in persisted_sessions,
            "receipt_status": session_id in persisted_receipts,
            "eligibility_status": session_id in eligible,
            "membership_status": session_id in anchored,
            "verification_status": session_id in verified,
            "export_status": session_id in exported_sessions and session_id in exported_receipts,
            "exclusion_reason": ", ".join(reasons),
        })
    return {
        "run_id": run_id,
        "day": day,
        "batch_timezone": "UTC",
        "eligibility_policy": ELIGIBILITY_POLICY,
        "eligibility_interval": {"start_inclusive": _iso(start), "end_exclusive": _iso(end)},
        "anchor_id": anchor.id,
        "batch_root": anchor.batch_root,
        "batch_expected_receipt_count": anchor.receipt_count,
        "batch_actual_membership_count": len(memberships),
        "batch_receipt_count_match": len(memberships) == anchor.receipt_count,
        "num_sessions_requested": len(requested),
        "num_sessions_generated": len(generated),
        "num_sessions_persisted": len(persisted_sessions),
        "num_receipts_persisted": len(persisted_receipts),
        "num_receipts_day_eligible": len(eligible),
        "num_sessions_anchored": len(anchored),
        "num_memberships_stored": len(memberships),
        "num_receipts_verified": len(verified),
        "num_sessions_exported": len(exported_sessions),
        "num_receipts_exported": len(exported_receipts),
        "num_verifications_exported": len(exported_verifications),
        "requested_ids": sorted(requested),
        "generated_ids": sorted(generated),
        "persisted_session_ids": sorted(persisted_sessions),
        "persisted_receipt_ids": sorted(persisted_receipts),
        "eligible_receipt_ids": sorted(eligible),
        "anchored_session_ids": sorted(anchored),
        "verified_session_ids": sorted(verified),
        "exported_session_ids": sorted(exported_sessions),
        "exported_receipt_ids": sorted(exported_receipts),
        "exported_verification_ids": sorted(exported_verifications),
        "missing_at_each_stage": {name: info["missing_ids"] for name, info in comparisons.items()},
        "unexpected_at_each_stage": {name: info["unexpected_ids"] for name, info in comparisons.items()},
        "stages": comparisons,
        "excluded_or_missing_sessions": excluded,
        "failure_reasons": failures,
        "count_reconciliation_ok": not failures,
    }


def write_count_reconciliation_artifacts(result: dict[str, Any], output_dir: Path, stem: str = "count_reconciliation") -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{stem}.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    with (output_dir / f"{stem}.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["stage", "count", "missing_ids", "unexpected_ids", "status"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for stage, details in result["stages"].items():
            writer.writerow({"stage": stage, "count": details["count"], "missing_ids": ";".join(details["missing_ids"]), "unexpected_ids": ";".join(details["unexpected_ids"]), "status": details["status"]})
    lines = [
        "# Experiment count reconciliation", "", result["eligibility_policy"], "",
        f"UTC interval: `{result['eligibility_interval']['start_inclusive']}` to `{result['eligibility_interval']['end_exclusive']}` (exclusive).", "",
        "| Stage | Count | Missing IDs | Unexpected IDs | Status |", "|---|---:|---|---|---|",
    ]
    for stage, details in result["stages"].items():
        lines.append(f"| {stage.replace('_', ' ')} | {details['count']} | {', '.join(details['missing_ids']) or '—'} | {', '.join(details['unexpected_ids']) or '—'} | {details['status']} |")
    lines += ["", f"Anchor ID: `{result['anchor_id']}`", "", f"Batch root: `{result['batch_root']}`", "", f"Final reconciliation: **{'PASS' if result['count_reconciliation_ok'] else 'FAIL'}**"]
    if result["excluded_or_missing_sessions"]:
        lines += ["", "## Excluded or missing sessions", "", "```json", json.dumps(result["excluded_or_missing_sessions"], indent=2), "```"]
    (output_dir / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _select_anchor(session: Session, run_id: str, day: str, anchor_id: int | None) -> BatchAnchor:
    if anchor_id is not None:
        anchor = session.get(BatchAnchor, anchor_id)
    else:
        anchors = list(session.scalars(select(BatchAnchor).where(BatchAnchor.day == day, BatchAnchor.session_prefix == run_id).order_by(BatchAnchor.id.desc())))
        anchor = anchors[0] if anchors else None
    if not anchor:
        raise ExperimentCountMismatch(f"No anchor found for run={run_id} day={day}")
    return anchor


def _csv_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["session_id"] for row in csv.DictReader(handle) if row.get("session_id")}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat().replace("+00:00", "Z") if value else None
