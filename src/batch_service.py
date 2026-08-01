"""Database services for immutable batch snapshots, audits, and proofs."""

from __future__ import annotations

from collections import Counter
from typing import Any
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db
from .batch_merkle import (
    PROFILE_V1, BatchContext, BatchLeafRecord, build_batch_commitment,
    generate_membership_proof, normalize_hash, normalize_timestamp,
)
from .models import BatchAnchor, BatchAnchorReceipt, Receipt
from .receipt_builder import hash_receipt


def audit_batch_membership(anchor_id: int, db_session: Session | None = None) -> dict[str, Any]:
    owns = db_session is None; session = db_session or db.session_scope()
    try:
        anchor = session.get(BatchAnchor, anchor_id)
        if not anchor: raise ValueError(f"No batch anchor found for id {anchor_id}")
        if anchor.commitment_profile != PROFILE_V1:
            return {"anchor_id": anchor_id, "commitment_profile": anchor.commitment_profile, "status": "not_applicable", "membership_snapshot_match": None, "limitation": "Historical anchors do not store temporal leaf metadata."}
        stored_rows = list(session.scalars(select(BatchAnchorReceipt).where(BatchAnchorReceipt.anchor_id == anchor_id).order_by(BatchAnchorReceipt.leaf_index)))
        current_rows = list(session.scalars(select(Receipt).where(Receipt.start_ts >= anchor.window_start, Receipt.start_ts < anchor.window_end).where(Receipt.session_id.like(f"{anchor.session_prefix}-%")) if anchor.session_prefix else select(Receipt).where(Receipt.start_ts >= anchor.window_start, Receipt.start_ts < anchor.window_end)))
        stored = {row.session_id: row for row in stored_rows}; current = {row.session_id: row for row in current_rows}
        late = sorted(set(current) - set(stored)); removed = sorted(set(stored) - set(current))
        changed_hash = []; changed_start = []
        for session_id in sorted(set(stored) & set(current)):
            row = current[session_id]; snapshot = stored[session_id]
            try: current_hash = hash_receipt(row.receipt_json or {})
            except ValueError: current_hash = row.receipt_hash
            if normalize_hash(row.receipt_hash) != normalize_hash(snapshot.receipt_hash) or normalize_hash(current_hash) != normalize_hash(snapshot.receipt_hash): changed_hash.append(session_id)
            current_start = row.start_ts.replace(tzinfo=timezone.utc) if row.start_ts.tzinfo is None else row.start_ts
            stored_start = snapshot.normalized_start_ts.replace(tzinfo=timezone.utc) if snapshot.normalized_start_ts.tzinfo is None else snapshot.normalized_start_ts
            if normalize_timestamp(current_start) != normalize_timestamp(stored_start): changed_start.append(session_id)
        duplicate_ids = sorted(key for key, count in Counter(row.session_id for row in stored_rows).items() if count > 1)
        match = not any((late, removed, changed_hash, changed_start, duplicate_ids)) and len(stored_rows) == len(current_rows)
        return {
            "anchor_id": anchor_id, "commitment_profile": anchor.commitment_profile,
            "stored_membership_ids": [row.session_id for row in stored_rows],
            "currently_eligible_ids": sorted(current), "late_or_unanchored_ids": late,
            "removed_or_missing_ids": removed, "changed_receipt_hash_ids": changed_hash,
            "changed_start_timestamp_ids": changed_start, "duplicate_ids": duplicate_ids,
            "stored_count": len(stored_rows), "current_count": len(current_rows),
            "membership_snapshot_match": match,
            "limitation": "Cannot detect a physical session that never entered the platform; production completeness requires an external authoritative register.",
        }
    finally:
        if owns: session.close()


def commitment_from_anchor(anchor_id: int, db_session: Session) -> Any:
    anchor = db_session.get(BatchAnchor, anchor_id)
    if not anchor: raise ValueError(f"No batch anchor found for id {anchor_id}")
    if anchor.commitment_profile != PROFILE_V1: raise ValueError("Membership proofs are unavailable for legacy anchors")
    rows = list(db_session.scalars(select(BatchAnchorReceipt).where(BatchAnchorReceipt.anchor_id == anchor_id).order_by(BatchAnchorReceipt.leaf_index)))
    return build_batch_commitment([BatchLeafRecord(row.session_id, row.normalized_start_ts.replace(tzinfo=timezone.utc) if row.normalized_start_ts.tzinfo is None else row.normalized_start_ts, row.receipt_hash) for row in rows], BatchContext.from_dict(anchor.context_json))


def generate_anchor_membership_proof(anchor_id: int, session_id: str, db_session: Session | None = None) -> dict[str, Any]:
    owns = db_session is None; session = db_session or db.session_scope()
    try: return generate_membership_proof(commitment_from_anchor(anchor_id, session), session_id)
    finally:
        if owns: session.close()
