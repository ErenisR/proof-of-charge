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
    meter_values_vs_sessions.png
    session_type_distribution.png
    energy_import_export_net_summary.png
    blockchain_gas_vs_sessions.png
  runs/
    <run_id>/
      metrics.json
      report.md
      manifest.json
      datasets/*.csv
```

`blockchain_gas_vs_sessions.png` is generated only when chain metrics exist.

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

For a blockchain-backed result, include:

- `chain_tx`
- `chain_gas_used`
- `chain_transaction_fee_wei`
- `chain_root_match`

For a tamper-evidence result, include `tamper_summary.md` and the key before
and after fields from `tamper_summary.json`.
