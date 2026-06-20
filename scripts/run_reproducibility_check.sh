#!/usr/bin/env bash
set -euo pipefail

SESSIONS="${1:-100}"
DAY="${DAY:-$(date -u +%F)}"
RUN_ID="${RUN_ID:-reproducibility_check_$(date -u +%Y%m%d_%H%M%S)}"
SEED="${SEED:-42}"
SESSION_TYPE="${SESSION_TYPE:-all}"
RUN_PREFIX="${RUN_ID}_stable"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
TMP_DIR="$(mktemp -d)"
EXPORTS_BACKUP="$TMP_DIR/exports"
EXPERIMENT_OUTPUT_A="$TMP_DIR/attempt_a_output.json"
EXPERIMENT_OUTPUT_B="$TMP_DIR/attempt_b_output.json"
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

FINAL_DIR="$ROOT_DIR/results/$RUN_ID"
TEMP_RUN_DIR="$ROOT_DIR/results/$RUN_PREFIX"

if [[ -e "$FINAL_DIR" || -e "$TEMP_RUN_DIR" ]]; then
  echo "[ERROR] Reproducibility result directory already exists." >&2
  echo "[ERROR] FINAL_DIR=$FINAL_DIR" >&2
  echo "[ERROR] TEMP_RUN_DIR=$TEMP_RUN_DIR" >&2
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

mkdir -p "$FINAL_DIR"

cleanup_prefix() {
  "$PYTHON" - "$RUN_PREFIX" <<'PY'
import sys

from sqlalchemy import delete, select

from src import db
from src.models import (
    BatchAnchor,
    BatchAnchorReceipt,
    ChargingSession,
    MeterValue,
    Receipt,
    Verification,
)

prefix = sys.argv[1]

session = db.session_scope()
try:
    session_ids = list(
        session.scalars(
            select(ChargingSession.session_id).where(ChargingSession.session_id.like(f"{prefix}-%"))
        )
    )
    anchor_ids = list(
        session.scalars(
            select(BatchAnchor.id).where(BatchAnchor.session_prefix == prefix)
        )
    )

    if anchor_ids:
        session.execute(delete(BatchAnchorReceipt).where(BatchAnchorReceipt.anchor_id.in_(anchor_ids)))
        session.execute(delete(BatchAnchor).where(BatchAnchor.id.in_(anchor_ids)))
    if session_ids:
        session.execute(delete(BatchAnchorReceipt).where(BatchAnchorReceipt.session_id.in_(session_ids)))
        session.execute(delete(Verification).where(Verification.session_id.in_(session_ids)))
        session.execute(delete(Receipt).where(Receipt.session_id.in_(session_ids)))
        session.execute(delete(MeterValue).where(MeterValue.session_id.in_(session_ids)))
        session.execute(delete(ChargingSession).where(ChargingSession.session_id.in_(session_ids)))

    session.commit()
finally:
    session.close()
PY
}

run_attempt() {
  local attempt="$1"
  local output_file="$2"

  cleanup_prefix
  if [[ -e "$TEMP_RUN_DIR" ]]; then
    echo "[ERROR] Temporary run directory exists before attempt $attempt: $TEMP_RUN_DIR" >&2
    exit 1
  fi

  echo "[INFO] Running reproducibility attempt $attempt"
  "$PYTHON" -m src.run_experiment "$SESSIONS" \
    --day "$DAY" \
    --seed "$SEED" \
    --run-id "$RUN_PREFIX" \
    --session-type "$SESSION_TYPE" \
    --skip-figures | tee "$output_file"

  if [[ ! -d "$TEMP_RUN_DIR" ]]; then
    echo "[ERROR] Expected temporary run directory was not created: $TEMP_RUN_DIR" >&2
    exit 1
  fi

  mv "$TEMP_RUN_DIR" "$FINAL_DIR/$attempt"
  cp "$output_file" "$FINAL_DIR/$attempt/experiment_output.json"
}

run_attempt "attempt_a" "$EXPERIMENT_OUTPUT_A"
run_attempt "attempt_b" "$EXPERIMENT_OUTPUT_B"

"$PYTHON" - "$FINAL_DIR" "$RUN_ID" "$RUN_PREFIX" "$DAY" "$SEED" "$SESSIONS" "$SESSION_TYPE" <<'PY'
import csv
import json
import sys
from pathlib import Path

final_dir = Path(sys.argv[1])
run_id = sys.argv[2]
run_prefix = sys.argv[3]
day = sys.argv[4]
seed = int(sys.argv[5])
sessions = int(sys.argv[6])
session_type = sys.argv[7]

attempt_a = final_dir / "attempt_a"
attempt_b = final_dir / "attempt_b"

metrics_a = json.loads((attempt_a / "metrics.json").read_text(encoding="utf-8"))
metrics_b = json.loads((attempt_b / "metrics.json").read_text(encoding="utf-8"))

def read_text(relative_path):
    return (
        (attempt_a / relative_path).read_text(encoding="utf-8"),
        (attempt_b / relative_path).read_text(encoding="utf-8"),
    )

def read_csv(relative_path):
    left, right = read_text(relative_path)
    return list(csv.DictReader(left.splitlines())), list(csv.DictReader(right.splitlines()))

receipts_a, receipts_b = read_csv("datasets/receipts.csv")
sessions_a, sessions_b = read_csv("datasets/sessions.csv")
meter_a, meter_b = read_csv("datasets/meter_values.csv")
anchors_a, anchors_b = read_csv("datasets/anchors.csv")

receipt_hashes_a = [row["receipt_hash"] for row in receipts_a]
receipt_hashes_b = [row["receipt_hash"] for row in receipts_b]
merkle_roots_a = [row["merkle_root"] for row in receipts_a]
merkle_roots_b = [row["merkle_root"] for row in receipts_b]

checks = [
    ("batch_root", metrics_a.get("batch_root"), metrics_b.get("batch_root")),
    ("receipt_hash_sequence", receipt_hashes_a, receipt_hashes_b),
    ("receipt_merkle_root_sequence", merkle_roots_a, merkle_roots_b),
    ("sessions_csv", sessions_a, sessions_b),
    ("meter_values_csv", meter_a, meter_b),
    ("receipts_csv", receipts_a, receipts_b),
    ("anchors_csv", anchors_a, anchors_b),
    ("validation_failure_count", metrics_a.get("validation_failure_count"), metrics_b.get("validation_failure_count")),
    ("validation_failed_sessions", metrics_a.get("validation_failed_sessions"), metrics_b.get("validation_failed_sessions")),
]

rows = []
for name, left, right in checks:
    rows.append(
        {
            "check": name,
            "attempt_a": json.dumps(left, sort_keys=True) if isinstance(left, (dict, list)) else left,
            "attempt_b": json.dumps(right, sort_keys=True) if isinstance(right, (dict, list)) else right,
            "match": left == right,
        }
    )

deterministic_match = all(row["match"] for row in rows)

summary = {
    "run_id": run_id,
    "run_prefix": run_prefix,
    "day": day,
    "seed": seed,
    "sessions": sessions,
    "session_type": session_type,
    "deterministic_match": deterministic_match,
    "receipt_count": len(receipts_a),
    "batch_root": metrics_a.get("batch_root"),
    "attempts": ["attempt_a", "attempt_b"],
    "checks": rows,
}

(final_dir / "reproducibility_summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True),
    encoding="utf-8",
)

with (final_dir / "reproducibility_summary.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=["check", "match", "attempt_a", "attempt_b"])
    writer.writeheader()
    writer.writerows(rows)

def yes_no(value):
    return "yes" if value else "no"

report_lines = [
    f"# Reproducibility Check - {day}",
    "",
    f"Run ID: `{run_id}`",
    "",
    "## Configuration",
    "",
    "| Field | Value |",
    "| --- | --- |",
    f"| Stable run prefix | `{run_prefix}` |",
    f"| Sessions | `{sessions}` |",
    f"| Session mode | `{session_type}` |",
    f"| Seed | `{seed}` |",
    f"| Day | `{day}` |",
    "",
    "## Result",
    "",
    "| Claim | Result |",
    "| --- | --- |",
    f"| Deterministic replay match | `{yes_no(deterministic_match)}` |",
    f"| Receipt count | `{len(receipts_a)}` |",
    f"| Batch root | `{metrics_a.get('batch_root')}` |",
    "",
    "## Checks",
    "",
    "| Check | Match |",
    "| --- | --- |",
]
for row in rows:
    report_lines.append(f"| `{row['check']}` | `{yes_no(row['match'])}` |")

report_lines.extend(
    [
        "",
        "## Interpretation",
        "",
        "Both attempts use the same seed, day, session count, session mode, and stable",
        "session prefix. Matching receipt hashes, receipt Merkle roots, meter-value",
        "exports, and batch root indicate deterministic Proof-of-Charge receipt",
        "generation for identical synthetic inputs.",
    ]
)

(final_dir / "reproducibility_summary.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
PY

cleanup_prefix

echo "[OK] Reproducibility check complete"
echo "[OK] Results: $FINAL_DIR"
echo "[OK] Report: $FINAL_DIR/reproducibility_summary.md"
