# Research Experiment Workflows

This repository supports reproducible local experiments for the Proof-of-Charge
prototype. The workflows are intended to produce paper-ready evidence for:

- deterministic receipt generation
- Merkle-root receipt and batch verification
- Postgres-backed auditability
- local blockchain batch anchoring with Anvil
- V2G-ready import/export/net-energy settlement
- tamper detection after stored receipt modification

The prototype is local-first. Results are written under `results/` and are not
committed by default.

## Full Experiment Matrix

Run the default matrix:

```bash
scripts/run_experiment_matrix.sh
```

Defaults:

- sizes: `10 50 100 500 1000`
- modes: `charge_only discharge_only bidirectional all`
- seed: `42`
- day: current UTC date
- blockchain publishing: disabled

Use smaller sizes while iterating:

```bash
scripts/run_experiment_matrix.sh 10 50
```

Restrict session modes:

```bash
MODES="charge_only bidirectional" scripts/run_experiment_matrix.sh 10 50 100
```

Publish each batch root to the local Anvil chain:

```bash
PUBLISH_CHAIN=1 scripts/run_experiment_matrix.sh 10 50 100
```

Each matrix writes:

```text
results/<matrix_id>/
  manifest.json
  summary.csv
  summary.md
  deploy_anchor.txt
  figures/
    captions.md
    finalization_time_vs_sessions.png
    average_finalization_time_vs_sessions.png
    finalization_throughput_vs_sessions.png
    meter_values_vs_sessions.png
    session_type_distribution.png
    energy_import_export_net_summary.png
    blockchain_gas_vs_sessions.png
    blockchain_gas_per_session_vs_sessions.png
  runs/
    <run_id>/
      metrics.json
      report.md
      manifest.json
      datasets/*.csv
```

The blockchain gas figures are generated only when chain metrics exist.

## Matrix Metrics

The matrix summary includes:

- requested and generated session counts
- meter value, receipt, and verification row counts
- total and average receipt-finalization time
- batch-root verification result
- receipt-count verification result
- session validation failure counts
- charge-only, discharge-only, and bidirectional session counts
- average import, export, and net energy
- on-chain root match, gas used, transaction fee, and transaction hash when
  `PUBLISH_CHAIN=1`

The per-run `metrics.json` also stores validation details, session duration
statistics, and energy statistics.

## Reproducibility Check

Run:

```bash
scripts/run_reproducibility_check.sh 100
```

Optional environment variables:

- `DAY`: experiment day
- `SEED`: deterministic synthetic-session seed
- `SESSION_TYPE`: `charge_only`, `discharge_only`, `bidirectional`, `all`, or
  `auto`
- `RUN_ID`: result directory name

The workflow runs the same synthetic workload twice using the same stable
internal session prefix. It compares:

- batch root
- receipt hash sequence
- receipt Merkle root sequence
- sessions CSV
- meter values CSV
- receipts CSV
- anchors CSV
- validation failure counts

Outputs:

```text
results/<run_id>/
  reproducibility_summary.csv
  reproducibility_summary.json
  reproducibility_summary.md
  attempt_a/
  attempt_b/
```

Use this as evidence for deterministic Proof-of-Charge generation. The strongest
paper table is the check table from `reproducibility_summary.md`.

## Blockchain Cost Interpretation

After running a matrix with `PUBLISH_CHAIN=1`, generate the cost report:

```bash
python3 scripts/analyze_blockchain_cost.py results/<matrix_id>
```

Outputs:

```text
results/<matrix_id>/
  blockchain_cost_summary.csv
  blockchain_cost_summary.json
  blockchain_cost_interpretation.md
```

This report distinguishes two claims:

- total gas is expected to remain approximately constant per anchored batch
- gas per session should decrease as more receipts are covered by the same
  batch root

This supports the design choice to anchor one Merkle batch root instead of
submitting one blockchain transaction per receipt.

## Tamper Demo

Run:

```bash
scripts/run_tamper_demo.sh
```

Or provide a fixed run id:

```bash
scripts/run_tamper_demo.sh tamper_demo_case
```

The tamper workflow:

1. Generates and finalizes one deterministic session.
2. Verifies the clean receipt and audit state.
3. Modifies `receipt_json.energy_kwh` in Postgres without updating the stored
   `receipt_hash`.
4. Verifies the receipt and audit state again.
5. Writes machine-readable and human-readable evidence.

Outputs:

```text
results/<run_id>/
  tamper_summary.json
  tamper_summary.md
  experiment_output.json
  metrics.json
```

The summary records:

- `tampered_session_id`
- `tampered_field`
- `original_value`
- `tampered_value`
- `receipt_verification_match_before`
- `receipt_verification_match_after`
- `audit_passed_before`
- `audit_passed_after`
- `expected_detection`
- `actual_detection`

Batch verification can still pass after receipt JSON tampering because
`batch_anchor_receipts` stores the historical anchored receipt-hash membership
snapshot. Receipt-level verification and DB audit checks are the expected
tamper-detection mechanisms for modified receipt content.

## Paper Usage

Use `summary.csv` for tables and `figures/*.png` for plots. Cite the run
configuration from `manifest.json`, especially the seed, session sizes, session
modes, and blockchain publishing flag.

For paper text, refer to raw mode `all` as `mixed`. The generated figures
already display it as `Mixed`.

Recommended figure usage:

- `finalization_time_vs_sessions.png`: scalability of total receipt processing
- `average_finalization_time_vs_sessions.png`: per-receipt latency
- `finalization_throughput_vs_sessions.png`: receipt throughput
- `energy_import_export_net_summary.png`: V2G import/export/net-energy evidence
- `blockchain_gas_vs_sessions.png`: per-batch anchoring overhead
- `blockchain_gas_per_session_vs_sessions.png`: amortized blockchain overhead
  per receipt

For a blockchain-backed result, include:

- `chain_tx`
- `chain_gas_used`
- `chain_transaction_fee_wei`
- `chain_root_match`

For a tamper-evidence result, include `tamper_summary.md` and the key before
and after fields from `tamper_summary.json`.
# Controlled database tamper matrix

The Reviewer 4 tamper experiment is a Postgres-backed, rollback-isolated matrix of
seven deterministic mutations. It uses the existing receipt hashing, receipt
rebuild, Merkle, batch membership, audit, and blockchain verifier code. The
normalized meter layer independently reads `meter_values`; it does not reconstruct
samples from `sessions.session_json` or `receipts.receipt_json`.

## Requirements and baseline

Configure `DATABASE_URL` for a migrated Postgres database (`python -m src.db init`).
A controlled three-session baseline can be generated and tested with:

```bash
.venv/bin/python scripts/run_tamper_matrix.py \
  --run-id reviewer4_tamper_matrix \
  --day 2026-07-30 \
  --generate-baseline \
  --output-dir results/reviewer4_tamper_matrix/tamper_matrix
```

The prefix defaults to the run ID and the first generated session is selected. To
use an existing baseline, provide `--prefix`, `--session-id`, and optionally
`--anchor-id`. The selected anchor must contain the receipt, its membership count
and root must be consistent, and the session must contain at least three normalized
meter samples.

Run one mutation with `--scenario T2`. Repeating `--scenario` selects multiple
scenarios. Every scenario starts with all five layers passing, runs inside a
database savepoint, rolls back, and requires the complete baseline to pass again.
Verification persistence is disabled inside scenario transactions so verifier
commits cannot invalidate the savepoint.

## Verification layers

- **Receipt hash** hashes the canonical stored receipt JSON and compares it with
  `receipts.receipt_hash`.
- **Normalized meter stream** orders persisted `meter_values`, uses the receipt
  builder's canonical meter-leaf encoding and Merkle implementation, checks sample
  count/gaps and duplicates, reconstructs directional energy, and compares it with
  receipt JSON and normalized receipt columns.
- **Database audit** rebuilds from the stored session, compares normalized receipt
  columns, and separately embeds the independent normalized-meter result.
- **Batch verification** recomputes the historical membership root and checks both
  membership count and root.
- **On-chain comparison** compares the database anchor root/count with an injected
  deterministic reader in automated tests. This differs from real-chain execution:
  it exercises the same verifier and dependency boundary but is not evidence of an
  Anvil transaction.

For a real local chain, start Anvil, deploy the contract as documented in
`docs/architecture/blockchain_anchoring.md`, configure `WEB3_RPC_URL`,
`ANCHOR_CONTRACT_ADDRESS`, and `ANCHOR_PRIVATE_KEY`, then add `--publish-chain`.
`--require-chain` never silently substitutes a reader and requires
`--publish-chain`.

Artifacts are written under `results/<run_id>/tamper_matrix/`: JSON, CSV,
Markdown, summary JSON, and a manifest. The Markdown table is intended as input to
the manuscript update, but this command does not edit the manuscript.

These controlled scenarios are not a general attack-detection probability. They
do not establish source-meter authenticity or prove complete session capture.
Historical batch or chain commitments are expected to remain unchanged for local
off-chain mutations that do not alter their stored receipt-hash snapshot.
