#!/usr/bin/env bash
set -euo pipefail

DAY="${DAY:-$(date -u +%F)}"
MATRIX_ID="${MATRIX_ID:-experiment_matrix_$(date -u +%Y%m%d_%H%M%S)}"
SEED="${SEED:-42}"
PUBLISH_CHAIN="${PUBLISH_CHAIN:-0}"
SKIP_FIGURES="${SKIP_FIGURES:-1}"
WEB3_RPC_URL="${WEB3_RPC_URL:-http://127.0.0.1:8545}"
CHAIN_ID="${CHAIN_ID:-31337}"

if [[ "$#" -gt 0 ]]; then
  SIZES=("$@")
else
  SIZES=(10 50 100 500 1000)
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
TMP_DIR="$(mktemp -d)"
EXPORTS_BACKUP="$TMP_DIR/exports"
DEPLOY_OUTPUT="$TMP_DIR/deploy_anchor.txt"
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

MATRIX_DIR="$ROOT_DIR/results/$MATRIX_ID"
RUNS_DIR="$MATRIX_DIR/runs"
if [[ -e "$MATRIX_DIR" ]]; then
  echo "[ERROR] Matrix directory already exists: $MATRIX_DIR" >&2
  echo "[ERROR] Choose a different MATRIX_ID." >&2
  exit 1
fi

if [[ -d "$ROOT_DIR/exports" ]]; then
  EXPORTS_EXISTED=1
  cp -R "$ROOT_DIR/exports" "$EXPORTS_BACKUP"
fi

mkdir -p "$RUNS_DIR"

echo "[INFO] Starting local infrastructure"
if [[ "$PUBLISH_CHAIN" == "1" ]]; then
  docker compose up -d postgres anvil
else
  docker compose up -d postgres
fi

echo "[INFO] Initializing database schema"
"$PYTHON" -m src.db init

if [[ "$PUBLISH_CHAIN" == "1" ]]; then
  echo "[INFO] Deploying local anchor contract"
  WEB3_RPC_URL="$WEB3_RPC_URL" CHAIN_ID="$CHAIN_ID" "$PYTHON" scripts/deploy_anchor.py | tee "$DEPLOY_OUTPUT"
  ANCHOR_CONTRACT_ADDRESS="$(grep '^ANCHOR_CONTRACT_ADDRESS=' "$DEPLOY_OUTPUT" | tail -1 | cut -d= -f2)"
  if [[ -z "$ANCHOR_CONTRACT_ADDRESS" ]]; then
    echo "[ERROR] Could not parse ANCHOR_CONTRACT_ADDRESS from deployment output" >&2
    exit 1
  fi
  cp "$DEPLOY_OUTPUT" "$MATRIX_DIR/deploy_anchor.txt"
  export WEB3_RPC_URL
  export CHAIN_ID
  export ANCHOR_CONTRACT_ADDRESS
fi

for size in "${SIZES[@]}"; do
  RUN_ID="${MATRIX_ID}_${size}"
  TEMP_RUN_DIR="$ROOT_DIR/results/$RUN_ID"
  FINAL_RUN_DIR="$RUNS_DIR/$RUN_ID"
  EXPERIMENT_OUTPUT="$TMP_DIR/${RUN_ID}_output.json"

  if [[ -e "$TEMP_RUN_DIR" || -e "$FINAL_RUN_DIR" ]]; then
    echo "[ERROR] Run directory already exists for size $size" >&2
    exit 1
  fi

  echo "[INFO] Running matrix size=$size run_id=$RUN_ID"
  RUN_ARGS=(
    "$size"
    "--day" "$DAY"
    "--seed" "$SEED"
    "--run-id" "$RUN_ID"
  )
  if [[ "$SKIP_FIGURES" == "1" ]]; then
    RUN_ARGS+=("--skip-figures")
  fi
  if [[ "$PUBLISH_CHAIN" == "1" ]]; then
    RUN_ARGS+=("--publish-chain")
  fi

  "$PYTHON" -m src.run_experiment "${RUN_ARGS[@]}" | tee "$EXPERIMENT_OUTPUT"
  cp "$EXPERIMENT_OUTPUT" "$TEMP_RUN_DIR/experiment_output.json"
  mv "$TEMP_RUN_DIR" "$FINAL_RUN_DIR"
done

"$PYTHON" - "$MATRIX_DIR" "$MATRIX_ID" "$DAY" "$SEED" "$PUBLISH_CHAIN" "${SIZES[@]}" <<'PY'
import csv
import json
import sys
from pathlib import Path

matrix_dir = Path(sys.argv[1])
matrix_id = sys.argv[2]
day = sys.argv[3]
seed = int(sys.argv[4])
publish_chain = sys.argv[5] == "1"
sizes = sys.argv[6:]
runs_dir = matrix_dir / "runs"

fields = [
    "run_id",
    "sessions",
    "meter_values",
    "receipts",
    "verifications",
    "finalization_total_sec",
    "finalization_avg_sec",
    "batch_root_match",
    "receipt_count_verified",
    "chain_root_match",
    "chain_gas_used",
    "chain_transaction_fee_wei",
    "chain_tx",
]

rows = []
for size in sizes:
    run_id = f"{matrix_id}_{size}"
    metrics = json.loads((runs_dir / run_id / "metrics.json").read_text(encoding="utf-8"))
    datasets = metrics.get("datasets") or {}
    rows.append(
        {
            "run_id": run_id,
            "sessions": metrics.get("num_sessions_requested"),
            "meter_values": datasets.get("meter_values"),
            "receipts": datasets.get("receipts"),
            "verifications": datasets.get("verifications"),
            "finalization_total_sec": metrics.get("t_finalize_total_sec"),
            "finalization_avg_sec": metrics.get("t_finalize_avg_sec"),
            "batch_root_match": metrics.get("batch_root_match"),
            "receipt_count_verified": metrics.get("receipt_count_verified"),
            "chain_root_match": metrics.get("chain_root_match"),
            "chain_gas_used": metrics.get("chain_gas_used"),
            "chain_transaction_fee_wei": metrics.get("chain_transaction_fee_wei"),
            "chain_tx": metrics.get("chain_tx"),
        }
    )

with (matrix_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

manifest = {
    "matrix_id": matrix_id,
    "day": day,
    "seed": seed,
    "publish_chain": publish_chain,
    "sizes": [int(size) for size in sizes],
    "runs": [row["run_id"] for row in rows],
}
(matrix_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

def fmt(value):
    return "" if value is None else str(value)

lines = [
    f"# Experiment Matrix Report - {day}",
    "",
    f"Matrix ID: `{matrix_id}`",
    "",
    "## Configuration",
    "",
    "| Field | Value |",
    "| --- | --- |",
    f"| Day | `{day}` |",
    f"| Seed | `{seed}` |",
    f"| Publish chain | `{str(publish_chain).lower()}` |",
    f"| Sizes | `{', '.join(sizes)}` |",
    "",
    "## Summary",
    "",
    "| Run | Sessions | Meter Values | Finalize Total s | Finalize Avg s | Batch Match | Chain Match | Gas Used |",
    "| --- | ---: | ---: | ---: | ---: | --- | --- | ---: |",
]
for row in rows:
    values = {key: fmt(value) for key, value in row.items()}
    lines.append(
        "| {run_id} | {sessions} | {meter_values} | {finalization_total_sec} | "
        "{finalization_avg_sec} | {batch_root_match} | {chain_root_match} | {chain_gas_used} |".format(**values)
    )

lines.extend(
    [
        "",
        "## Artifacts",
        "",
        "- `summary.csv`",
        "- `manifest.json`",
        "- `runs/<run_id>/metrics.json`",
        "- `runs/<run_id>/manifest.json`",
        "- `runs/<run_id>/datasets/*.csv`",
    ]
)
(matrix_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

echo "[OK] Experiment matrix complete"
echo "[OK] Results: $MATRIX_DIR"
echo "[OK] Summary: $MATRIX_DIR/summary.md"
