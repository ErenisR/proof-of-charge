#!/usr/bin/env bash
set -euo pipefail

DAY="${DAY:-$(date -u +%F)}"
RUN_ID="${1:-tamper_demo_$(date -u +%Y%m%d_%H%M%S)}"
SEED="${SEED:-42}"
DELTA_KWH="${DELTA_KWH:-1.0}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
TMP_DIR="$(mktemp -d)"
EXPORTS_BACKUP="$TMP_DIR/exports"
EXPERIMENT_OUTPUT="$TMP_DIR/experiment_output.json"
EXPORTS_EXISTED=0

cleanup() {
  rm -rf "$ROOT_DIR/exports"
  if [[ "$EXPORTS_EXISTED" == "1" ]]; then
    cp -R "$EXPORTS_BACKUP" "$ROOT_DIR/exports"
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

cd "$ROOT_DIR"

if [[ ! -x "$PYTHON" ]]; then
  echo "[ERROR] Python executable not found: $PYTHON" >&2
  exit 1
fi

if [[ -e "$ROOT_DIR/results/$RUN_ID" ]]; then
  echo "[ERROR] Run directory already exists: $ROOT_DIR/results/$RUN_ID" >&2
  echo "[ERROR] Choose a different run id." >&2
  exit 1
fi

if [[ -d "$ROOT_DIR/exports" ]]; then
  EXPORTS_EXISTED=1
  cp -R "$ROOT_DIR/exports" "$EXPORTS_BACKUP"
fi

echo "[INFO] Starting local database"
docker compose up -d postgres

echo "[INFO] Initializing database schema"
"$PYTHON" -m src.db init

echo "[INFO] Running clean single-session experiment"
"$PYTHON" -m src.run_experiment 1 \
  --day "$DAY" \
  --seed "$SEED" \
  --run-id "$RUN_ID" \
  --skip-figures | tee "$EXPERIMENT_OUTPUT"

RUN_DIR="$ROOT_DIR/results/$RUN_ID"
if [[ ! -d "$RUN_DIR" ]]; then
  echo "[ERROR] Expected run directory was not created: $RUN_DIR" >&2
  exit 1
fi
cp "$EXPERIMENT_OUTPUT" "$RUN_DIR/experiment_output.json"

SESSION_ID="${RUN_ID}-0001"
echo "[INFO] Tampering session $SESSION_ID by delta_kwh=$DELTA_KWH"

"$PYTHON" - "$RUN_DIR" "$SESSION_ID" "$DAY" "$RUN_ID" "$DELTA_KWH" <<'PY'
import json
import sys
from pathlib import Path

from src import audit_service, db, tamper, verifier, verifier_batch
from src.models import Receipt

run_dir = Path(sys.argv[1])
session_id = sys.argv[2]
day = sys.argv[3]
run_id = sys.argv[4]
delta_kwh = float(sys.argv[5])
tampered_field = "energy_kwh"

def receipt_field_value():
    session = db.session_scope()
    try:
        receipt = session.get(Receipt, session_id)
        if not receipt:
            raise ValueError(f"Receipt not found: {session_id}")
        return (receipt.receipt_json or {}).get(tampered_field)
    finally:
        session.close()

clean_receipt = verifier.verify_session_details(session_id)
clean_audit = audit_service.audit_session(session_id)
clean_batch = verifier_batch.verify_day(day, session_prefix=run_id)
original_value = receipt_field_value()

tamper_result = tamper.tamper_receipt(session_id, delta_kwh=delta_kwh)
tampered_value = receipt_field_value()

tampered_receipt = verifier.verify_session_details(session_id)
tampered_audit = audit_service.audit_session(session_id)
tampered_batch = verifier_batch.verify_day(day, session_prefix=run_id)

summary = {
    "run_id": run_id,
    "day": day,
    "session_id": session_id,
    "tampered_session_id": session_id,
    "tampered_field": tampered_field,
    "original_value": original_value,
    "tampered_value": tampered_value,
    "delta_kwh": delta_kwh,
    "receipt_verification_match_before": clean_receipt["match"],
    "receipt_verification_match_after": tampered_receipt["match"],
    "audit_passed_before": clean_audit["match"],
    "audit_passed_after": tampered_audit["match"],
    "expected_detection": True,
    "actual_detection": (not tampered_receipt["match"]) and (not tampered_audit["match"]),
    "clean_receipt_match": clean_receipt["match"],
    "clean_audit_match": clean_audit["match"],
    "clean_batch_match": clean_batch["match"],
    "tamper_hash_match": tamper_result["match"],
    "tampered_receipt_match": tampered_receipt["match"],
    "tampered_audit_match": tampered_audit["match"],
    "tampered_batch_match": tampered_batch["match"],
    "expected_hash": tampered_receipt["expected_hash"],
    "computed_hash_after_tamper": tampered_receipt["computed_hash"],
}

artifacts = {
    "clean_receipt_verification": clean_receipt,
    "clean_audit": clean_audit,
    "clean_batch_verification": clean_batch,
    "tamper_result": tamper_result,
    "tampered_receipt_verification": tampered_receipt,
    "tampered_audit": tampered_audit,
    "tampered_batch_verification": tampered_batch,
    "summary": summary,
}
(run_dir / "tamper_summary.json").write_text(json.dumps(artifacts, indent=2, sort_keys=True), encoding="utf-8")

mismatches = tampered_audit.get("normalized_mismatches") or []
mismatch_lines = "\n".join(
    f"- `{item['field']}`: stored column `{item['stored_column']}`, receipt JSON `{item['receipt_json']}`"
    for item in mismatches
) or "- none"

report = f"""# Tamper Demo Report - {day}

Run ID: `{run_id}`

Session ID: `{session_id}`

Tamper operation:

```text
receipt_json.{tampered_field}: {original_value} -> {tampered_value}
```

## Before Tampering

| Check | Match |
| --- | --- |
| Receipt hash verification | `{clean_receipt["match"]}` |
| Audit service | `{clean_audit["match"]}` |
| Batch root verification | `{clean_batch["match"]}` |

## After Tampering

| Check | Match |
| --- | --- |
| Tamper helper hash comparison | `{tamper_result["match"]}` |
| Receipt hash verification | `{tampered_receipt["match"]}` |
| Audit service | `{tampered_audit["match"]}` |
| Batch root verification | `{tampered_batch["match"]}` |

Expected hash:

```text
{tampered_receipt["expected_hash"]}
```

Computed hash after tamper:

```text
{tampered_receipt["computed_hash"]}
```

## Audit Mismatches

{mismatch_lines}

## Interpretation

The single-receipt verifier and audit service detect the modified receipt JSON.
The batch verifier can still pass because `batch_anchor_receipts` stores the
exact receipt-hash membership snapshot that was anchored before tampering. This
is intentional: historical batch membership remains stable, while receipt-level
and audit checks reveal content modification.
"""
(run_dir / "tamper_summary.md").write_text(report, encoding="utf-8")
PY

echo "[OK] Tamper demo complete"
echo "[OK] Results: $RUN_DIR"
echo "[OK] Report: $RUN_DIR/tamper_summary.md"
