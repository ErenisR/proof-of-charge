#!/usr/bin/env bash
set -euo pipefail

SESSIONS="${1:-2}"
DAY="${2:-$(date -u +%F)}"
RUN_ID="${3:-chain_smoke_$(date -u +%Y%m%d_%H%M%S)}"
SEED="${SEED:-42}"
WEB3_RPC_URL="${WEB3_RPC_URL:-http://127.0.0.1:8545}"
CHAIN_ID="${CHAIN_ID:-31337}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
TMP_DIR="$(mktemp -d)"
EXPORTS_BACKUP="$TMP_DIR/exports"
EXPERIMENT_OUTPUT="$TMP_DIR/experiment_output.json"
VERIFY_OUTPUT="$TMP_DIR/chain_verification.txt"
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

if [[ -e "$ROOT_DIR/results/$RUN_ID" ]]; then
  echo "[ERROR] Run directory already exists: $ROOT_DIR/results/$RUN_ID" >&2
  echo "[ERROR] Choose a different run id." >&2
  exit 1
fi

if [[ -d "$ROOT_DIR/exports" ]]; then
  EXPORTS_EXISTED=1
  cp -R "$ROOT_DIR/exports" "$EXPORTS_BACKUP"
fi

echo "[INFO] Starting local infrastructure"
docker compose up -d postgres anvil

echo "[INFO] Initializing database schema"
"$PYTHON" -m src.db init

echo "[INFO] Deploying local anchor contract"
WEB3_RPC_URL="$WEB3_RPC_URL" CHAIN_ID="$CHAIN_ID" "$PYTHON" scripts/deploy_anchor.py | tee "$DEPLOY_OUTPUT"
ANCHOR_CONTRACT_ADDRESS="$(grep '^ANCHOR_CONTRACT_ADDRESS=' "$DEPLOY_OUTPUT" | tail -1 | cut -d= -f2)"
if [[ -z "$ANCHOR_CONTRACT_ADDRESS" ]]; then
  echo "[ERROR] Could not parse ANCHOR_CONTRACT_ADDRESS from deployment output" >&2
  exit 1
fi
export WEB3_RPC_URL
export CHAIN_ID
export ANCHOR_CONTRACT_ADDRESS

echo "[INFO] Running blockchain-backed smoke experiment"
"$PYTHON" -m src.run_experiment "$SESSIONS" \
  --day "$DAY" \
  --seed "$SEED" \
  --run-id "$RUN_ID" \
  --skip-figures \
  --publish-chain | tee "$EXPERIMENT_OUTPUT"

RUN_DIR="$ROOT_DIR/results/$RUN_ID"
if [[ ! -d "$RUN_DIR" ]]; then
  echo "[ERROR] Expected run directory was not created: $RUN_DIR" >&2
  exit 1
fi

cp "$DEPLOY_OUTPUT" "$RUN_DIR/deploy_anchor.txt"
cp "$EXPERIMENT_OUTPUT" "$RUN_DIR/experiment_output.json"

echo "[INFO] Verifying on-chain anchor"
"$PYTHON" -m src.blockchain.verifier "$DAY" --prefix "$RUN_ID" | tee "$VERIFY_OUTPUT"
cp "$VERIFY_OUTPUT" "$RUN_DIR/chain_verification.txt"

"$PYTHON" - "$RUN_DIR" "$ANCHOR_CONTRACT_ADDRESS" "$WEB3_RPC_URL" "$CHAIN_ID" "$SESSIONS" "$DAY" "$RUN_ID" <<'PY'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
contract_address = sys.argv[2]
rpc_url = sys.argv[3]
chain_id = sys.argv[4]
sessions = sys.argv[5]
day = sys.argv[6]
run_id = sys.argv[7]

metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
verification = (run_dir / "chain_verification.txt").read_text(encoding="utf-8").strip()

chain_payload = {
    "contract_address": contract_address,
    "rpc_url": rpc_url,
    "chain_id": int(chain_id),
    "transaction_hash": metrics.get("chain_tx"),
    "block_number": metrics.get("chain_block_number"),
    "block_timestamp": metrics.get("chain_block_timestamp"),
    "gas_used": metrics.get("chain_gas_used"),
    "effective_gas_price_wei": metrics.get("chain_effective_gas_price_wei"),
    "transaction_fee_wei": metrics.get("chain_transaction_fee_wei"),
    "transaction_fee_eth": (
        metrics["chain_transaction_fee_wei"] / 10**18
        if metrics.get("chain_transaction_fee_wei") is not None
        else None
    ),
    "status": metrics.get("chain_status"),
}
(run_dir / "chain.json").write_text(json.dumps(chain_payload, indent=2, sort_keys=True), encoding="utf-8")

report = f"""# Chain Smoke Report - {day}

Run ID: `{run_id}`

## Command Scope

```text
sessions={sessions}
day={day}
seed={metrics.get("seed")}
publish_chain=true
```

## Infrastructure

| Field | Value |
| --- | --- |
| RPC URL | `{rpc_url}` |
| Chain ID | `{chain_id}` |
| Contract | `{contract_address}` |

## Results

| Metric | Value |
| --- | ---: |
| Sessions requested | {metrics.get("num_sessions_requested")} |
| Sessions anchored | {metrics.get("num_sessions_anchored")} |
| Receipt count verified | {metrics.get("receipt_count_verified")} |
| Batch root match | {metrics.get("batch_root_match")} |
| On-chain root match | {metrics.get("chain_root_match")} |
| Finalization total seconds | {metrics.get("t_finalize_total_sec")} |
| Finalization average seconds | {metrics.get("t_finalize_avg_sec")} |

## Blockchain Metrics

| Metric | Value |
| --- | ---: |
| Block number | {metrics.get("chain_block_number")} |
| Gas used | {metrics.get("chain_gas_used")} |
| Effective gas price wei | {metrics.get("chain_effective_gas_price_wei")} |
| Transaction fee wei | {metrics.get("chain_transaction_fee_wei")} |
| Transaction fee ETH | {chain_payload["transaction_fee_eth"]} |
| Transaction status | {metrics.get("chain_status")} |

Transaction hash:

```text
{metrics.get("chain_tx")}
```

Batch root:

```text
{metrics.get("batch_root")}
```

Verifier output:

```text
{verification}
```
"""
(run_dir / "report.md").write_text(report, encoding="utf-8")
PY

echo "[OK] Chain smoke workflow complete"
echo "[OK] Results: $RUN_DIR"
echo "[OK] Report: $RUN_DIR/report.md"
