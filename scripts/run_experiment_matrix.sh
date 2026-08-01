#!/usr/bin/env bash
set -euo pipefail

DAY="${DAY:-$(date -u +%F)}"
MATRIX_ID="${MATRIX_ID:-experiment_matrix_$(date -u +%Y%m%d_%H%M%S)}"
SEED="${SEED:-42}"
PUBLISH_CHAIN="${PUBLISH_CHAIN:-0}"
SKIP_FIGURES="${SKIP_FIGURES:-1}"
WEB3_RPC_URL="${WEB3_RPC_URL:-http://127.0.0.1:8545}"
CHAIN_ID="${CHAIN_ID:-31337}"
MODES="${MODES:-charge_only discharge_only bidirectional all}"

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

for mode in $MODES; do
  for size in "${SIZES[@]}"; do
    RUN_ID="${MATRIX_ID}_${mode}_${size}"
    TEMP_RUN_DIR="$ROOT_DIR/results/$RUN_ID"
    FINAL_RUN_DIR="$RUNS_DIR/$RUN_ID"
    EXPERIMENT_OUTPUT="$TMP_DIR/${RUN_ID}_output.json"

    if [[ -e "$TEMP_RUN_DIR" || -e "$FINAL_RUN_DIR" ]]; then
      echo "[ERROR] Run directory already exists for mode=$mode size=$size" >&2
      exit 1
    fi

    echo "[INFO] Running matrix mode=$mode size=$size run_id=$RUN_ID"
    RUN_ARGS=(
      "$size"
      "--day" "$DAY"
      "--seed" "$SEED"
      "--run-id" "$RUN_ID"
      "--session-type" "$mode"
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
done

"$PYTHON" - "$MATRIX_DIR" "$MATRIX_ID" "$DAY" "$SEED" "$PUBLISH_CHAIN" "$MODES" "${SIZES[@]}" <<'PY'
import csv
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt

matrix_dir = Path(sys.argv[1])
matrix_id = sys.argv[2]
day = sys.argv[3]
seed = int(sys.argv[4])
publish_chain = sys.argv[5] == "1"
modes = sys.argv[6].split()
sizes = sys.argv[7:]
runs_dir = matrix_dir / "runs"
figures_dir = matrix_dir / "figures"
figures_dir.mkdir(parents=True, exist_ok=True)

fields = [
    "run_id",
    "workload_size",
    "session_type_mode",
    "sessions",
    "sessions_generated",
    "meter_values",
    "receipts",
    "verifications",
    "finalization_total_sec",
    "finalization_avg_sec",
    "batch_root_match",
    "receipt_count_verified",
    "num_sessions_persisted",
    "num_receipts_persisted",
    "num_receipts_day_eligible",
    "num_sessions_anchored",
    "num_memberships_stored",
    "num_sessions_exported",
    "num_receipts_exported",
    "count_reconciliation_ok",
    "validation_failed_sessions",
    "validation_failure_count",
    "charge_only_sessions",
    "discharge_only_sessions",
    "bidirectional_sessions",
    "import_kwh_avg",
    "export_kwh_avg",
    "net_kwh_avg",
    "chain_root_match",
    "chain_gas_used",
    "chain_transaction_fee_wei",
    "chain_tx",
]

rows = []
for mode in modes:
    for size in sizes:
        run_id = f"{matrix_id}_{mode}_{size}"
        metrics = json.loads((runs_dir / run_id / "metrics.json").read_text(encoding="utf-8"))
        if metrics.get("count_reconciliation_ok") is not True:
            raise SystemExit(f"Count reconciliation failed for {run_id}; refusing matrix summary/plots")
        datasets = metrics.get("datasets") or {}
        rows.append(
            {
                "run_id": run_id,
                "workload_size": int(size),
                "session_type_mode": mode,
                "sessions": metrics.get("num_sessions_requested"),
                "sessions_generated": metrics.get("num_sessions_generated"),
                "meter_values": datasets.get("meter_values"),
                "receipts": datasets.get("receipts"),
                "verifications": datasets.get("verifications"),
                "finalization_total_sec": metrics.get("t_finalize_total_sec"),
                "finalization_avg_sec": metrics.get("t_finalize_avg_sec"),
                "batch_root_match": metrics.get("batch_root_match"),
                "receipt_count_verified": metrics.get("receipt_count_verified"),
                "num_sessions_persisted": metrics.get("num_sessions_persisted"),
                "num_receipts_persisted": metrics.get("num_receipts_persisted"),
                "num_receipts_day_eligible": metrics.get("num_receipts_day_eligible"),
                "num_sessions_anchored": metrics.get("num_sessions_anchored"),
                "num_memberships_stored": metrics.get("num_memberships_stored"),
                "num_sessions_exported": metrics.get("num_sessions_exported"),
                "num_receipts_exported": metrics.get("num_receipts_exported"),
                "count_reconciliation_ok": metrics.get("count_reconciliation_ok"),
                "validation_failed_sessions": metrics.get("validation_failed_sessions"),
                "validation_failure_count": metrics.get("validation_failure_count"),
                "charge_only_sessions": metrics.get("charge_only_sessions"),
                "discharge_only_sessions": metrics.get("discharge_only_sessions"),
                "bidirectional_sessions": metrics.get("bidirectional_sessions"),
                "import_kwh_avg": metrics.get("import_kwh_avg"),
                "export_kwh_avg": metrics.get("export_kwh_avg"),
                "net_kwh_avg": metrics.get("net_kwh_avg"),
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
    "modes": modes,
    "sizes": [int(size) for size in sizes],
    "runs": [row["run_id"] for row in rows],
    "batch_timezone": "UTC",
    "batch_eligibility_policy": "Receipt start_ts is in [day 00:00 UTC, next day 00:00 UTC).",
}
(matrix_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

def fmt(value):
    return "" if value is None else str(value)

def display_mode(mode):
    labels = {
        "charge_only": "Charge-only",
        "discharge_only": "Discharge-only",
        "bidirectional": "Bidirectional",
        "all": "Mixed",
        "auto": "Auto mix",
    }
    return labels.get(str(mode), str(mode).replace("_", " ").title())

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
    f"| Modes | `{', '.join(modes)}` |",
    f"| Sizes | `{', '.join(sizes)}` |",
    "",
    "## Summary",
    "",
    "| Run | Mode | Requested | Persisted receipts | Anchored | Verified | Reconciled | Finalize Total s | Batch Match |",
    "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | --- |",
]
for row in rows:
    values = {key: fmt(value) for key, value in row.items()}
    values["session_type_mode"] = display_mode(row["session_type_mode"])
    lines.append(
        "| {run_id} | {session_type_mode} | {sessions} | {num_receipts_persisted} | "
        "{num_sessions_anchored} | {receipt_count_verified} | {count_reconciliation_ok} | "
        "{finalization_total_sec} | {batch_root_match} |".format(**values)
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
        "- `figures/*.png`",
    ]
)
(matrix_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

def by_mode(mode):
    return [row for row in rows if row["session_type_mode"] == mode]

def numeric(value):
    if value in (None, ""):
        return None
    return float(value)

plt.rcParams.update(
    {
        "figure.dpi": 140,
        "savefig.dpi": 300,
        "font.size": 10,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "legend.fontsize": 9,
    }
)

def plot_line(filename, y_key, ylabel, title, transform=None):
    plt.figure(figsize=(7.2, 4.2))
    for mode in modes:
        subset = sorted(by_mode(mode), key=lambda row: row["workload_size"])
        x = []
        y = []
        for row in subset:
            value = numeric(row.get(y_key))
            if value is None:
                continue
            x.append(row["workload_size"])
            y.append(transform(value, row) if transform else value)
        if not x:
            continue
        plt.plot(x, y, marker="o", linewidth=1.8, markersize=4.5, label=display_mode(mode))
    plt.xlabel("Receipts in batch")
    plt.ylabel(ylabel)
    plt.title(title)
    plt.grid(True, alpha=0.3)
    if len(modes) > 1:
        plt.legend(frameon=False)
    plt.tight_layout()
    plt.savefig(figures_dir / filename)
    plt.close()

plot_line(
    "finalization_time_vs_sessions.png",
    "finalization_total_sec",
    "Receipt finalization time (s)",
    "Receipt Finalization Time by Batch Size",
)
plot_line(
    "average_finalization_time_vs_sessions.png",
    "finalization_avg_sec",
    "Average finalization time (ms/receipt)",
    "Average Receipt Finalization Cost",
    transform=lambda value, row: value * 1000,
)
plot_line(
    "finalization_throughput_vs_sessions.png",
    "finalization_total_sec",
    "Receipt finalization throughput (receipts/s)",
    "Receipt Finalization Throughput",
    transform=lambda value, row: (row["workload_size"] / value) if value else 0,
)
plot_line(
    "meter_values_vs_sessions.png",
    "meter_values",
    "Meter-value rows",
    "Meter-Value Rows by Batch Size",
)

plt.figure(figsize=(7.2, 4.2))
last_rows = [row for row in rows if row["workload_size"] == max(int(size) for size in sizes)]
x_labels = [display_mode(row["session_type_mode"]) for row in last_rows]
bottom = [0] * len(last_rows)
for key, label in [
    ("charge_only_sessions", "Charge-only"),
    ("discharge_only_sessions", "Discharge-only"),
    ("bidirectional_sessions", "Bidirectional"),
]:
    values = [row.get(key) or 0 for row in last_rows]
    plt.bar(x_labels, values, bottom=bottom, label=label)
    bottom = [a + b for a, b in zip(bottom, values)]
plt.ylabel("Receipts")
plt.title("Session-Type Composition at Largest Batch Size")
plt.legend(frameon=False)
plt.tight_layout()
plt.savefig(figures_dir / "session_type_distribution.png")
plt.close()

plt.figure(figsize=(7.2, 4.2))
width = 0.25
x = list(range(len(last_rows)))
for offset, key, label in [
    (-width, "import_kwh_avg", "Import"),
    (0, "export_kwh_avg", "Export"),
    (width, "net_kwh_avg", "Net"),
]:
    plt.bar([value + offset for value in x], [row.get(key) or 0 for row in last_rows], width=width, label=label)
plt.xticks(x, x_labels)
plt.ylabel("Average energy per receipt (kWh)")
plt.title("V2G Energy Balance by Session Mode")
plt.axhline(0, color="black", linewidth=0.8, alpha=0.5)
plt.legend(frameon=False)
plt.tight_layout()
plt.savefig(figures_dir / "energy_import_export_net_summary.png")
plt.close()

if publish_chain and any(row.get("chain_gas_used") for row in rows):
    plot_line(
        "blockchain_gas_vs_sessions.png",
        "chain_gas_used",
        "Gas used per batch anchor",
        "Blockchain Gas per Anchored Batch",
    )
    plot_line(
        "blockchain_gas_per_session_vs_sessions.png",
        "chain_gas_used",
        "Gas used per receipt",
        "Amortized Blockchain Gas per Receipt",
        transform=lambda value, row: value / row["workload_size"] if row["workload_size"] else 0,
    )

captions = [
    "- finalization_time_vs_sessions.png: Total receipt finalization time in seconds by batch size and session-generation mode.",
    "- average_finalization_time_vs_sessions.png: Average receipt finalization latency in milliseconds per receipt.",
    "- finalization_throughput_vs_sessions.png: Receipt finalization throughput in receipts per second.",
    "- meter_values_vs_sessions.png: Number of generated meter-value rows by batch size.",
    "- session_type_distribution.png: Session-type composition at the largest tested batch size. The `Mixed` mode corresponds to the raw `all` mode.",
    "- energy_import_export_net_summary.png: Average import, export, and net energy per receipt at the largest tested batch size.",
]
if publish_chain and any(row.get("chain_gas_used") for row in rows):
    captions.append("- blockchain_gas_vs_sessions.png: Gas used to anchor one Merkle batch root per run.")
    captions.append("- blockchain_gas_per_session_vs_sessions.png: Amortized gas per receipt when one batch root covers multiple receipts.")
(figures_dir / "captions.md").write_text("\n".join(captions) + "\n", encoding="utf-8")
PY

echo "[OK] Experiment matrix complete"
echo "[OK] Results: $MATRIX_DIR"
echo "[OK] Summary: $MATRIX_DIR/summary.md"
