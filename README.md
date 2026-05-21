# Proof-of-Charge Backend

Proof-of-Charge is a Python MVP for verifiable EV charging receipts. It turns
charging session data into deterministic receipts, hashes those receipts,
groups them into daily Merkle batch anchors, verifies anchored batches, and
exports datasets and figures for research or thesis material.

The current implementation is local-first: receipts, indexes, anchors, exports,
and experiment results are written to files in this repository. Blockchain
transactions and IPFS CIDs are represented as placeholders for now.

## What It Does

- Finalizes EV charging sessions through a FastAPI endpoint.
- Builds deterministic receipt JSON with session metadata, pricing, settlement,
  meter values, and a Merkle root over the meter stream.
- Supports V2G-ready energy fields: `import_kwh`, `export_kwh`, and `net_kwh`.
- Supports `charge_only`, `discharge_only`, and `bidirectional` synthetic
  sessions.
- Stores local receipts under `receipts/`.
- Creates mock daily batch anchors from receipt hashes.
- Verifies that stored batch roots match recomputed Merkle roots.
- Exports CSV datasets and PNG figures under `exports/`.
- Runs reproducible synthetic experiments under `results/<run_id>/`.

## Project Structure

```text
src/
  main.py                FastAPI app and receipt finalization endpoint
  receipt_builder.py     Receipt schema, energy math, pricing, receipt hash
  merkle.py              Merkle tree helper
  storage.py             Local JSON receipt/index storage
  synthetic_sessions.py  Synthetic EV charging/V2G session generator
  batch_anchoring.py     Mock daily batch anchoring
  verifier.py            Single receipt verification
  verifier_batch.py      Daily batch root verification
  export.py              CSV export generation
  charts.py              Figure generation
  run_experiment.py      End-to-end reproducible experiment runner
  tamper.py              Tampering helper for verification demos
  audit_day.py           Day-level audit helper
  release_notes.py       Release notes generator from git history

receipts/                Local receipt JSON files, index, and mock anchors
exports/                 Latest exported CSVs and figures
results/                 Versioned experiment snapshots
samples/                 Optional sample inputs
```

## Current Pipeline

```mermaid
flowchart LR
  A[Session input] --> B[Build receipt]
  B --> C[Hash receipt JSON]
  B --> D[Merkle root over meter values]
  C --> E[Save receipt locally]
  E --> F[Daily batch anchoring]
  F --> G[Batch Merkle root]
  G --> H[Verify batch root]
  E --> I[Export CSV datasets]
  I --> J[Generate figures]
  H --> K[Experiment metrics]
```

## Setup

Use a Python virtual environment and install the project dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`matplotlib` is only needed for chart generation.

## Receipt Data Model

The canonical receipt contract is defined in `src/receipt_schema.py` and applied
by `src/receipt_builder.py` before a receipt is returned or hashed.

Required top-level receipt fields:

- `version`
- `schema_version`
- `session_type`
- `session_id`
- `user_id`
- `evse_id`
- `ocpp_tx_id`
- `start_ts`
- `end_ts`
- `energy_kwh`
- `energy_summary`
- `pricing`
- `settlement`
- `merkle_root`
- `stream_hash_alg`

Required nested fields:

- `energy_summary`: `import_kwh`, `export_kwh`, `net_kwh`
- `pricing`: `currency`, `model`, `components`, `import_components`,
  `export_components`
- `settlement`: `gross_import_cost`, `gross_export_credit`, `net_amount`,
  `currency`

Current fixed values:

- `version`: `1.0`
- `schema_version`: `v2g-v1`
- `stream_hash_alg`: `sha256`

The validator enforces the key accounting invariants:

- `net_kwh = import_kwh - export_kwh`
- `energy_kwh = net_kwh`
- `net_amount = gross_import_cost - gross_export_credit`

## Run The API

```bash
uvicorn src.main:app --reload
```

The API exposes:

```text
POST /v1/receipts/finalize
```

The endpoint accepts a charging session payload, builds a receipt, hashes it,
saves it under `receipts/`, and returns the receipt plus its hash.

## Postgres Storage

The project now includes a Postgres-ready persistence layer using SQLAlchemy.
File-based receipt storage still works and remains enabled. Database writes are
enabled when `DATABASE_URL` is set.

Start Postgres locally:

```bash
docker compose up -d postgres
```

Set the database URL:

```bash
export DATABASE_URL=postgresql+psycopg://proof:proof@localhost:5432/proof_of_charge
```

Initialize the schema:

```bash
alembic upgrade head
```

Then run the API normally:

```bash
uvicorn src.main:app --reload
```

With `DATABASE_URL` set, finalized sessions are written to both:

- local receipt JSON files under `receipts/`
- Postgres tables through SQLAlchemy

Current database tables:

- `sessions`
- `meter_values`
- `receipts`
- `batch_anchors`
- `verifications`

Database relationship diagram:

```mermaid
erDiagram
  SESSIONS ||--o{ METER_VALUES : has
  SESSIONS ||--|| RECEIPTS : produces
  SESSIONS ||--o{ VERIFICATIONS : checked_by
  BATCH_ANCHORS ||--o{ VERIFICATIONS : verified_by

  SESSIONS {
    string session_id PK
    string user_id
    string evse_id
    string ocpp_tx_id
    string schema_version
    string session_type
    string start_ts
    string end_ts
    json session_json
    datetime created_at
    datetime updated_at
  }

  METER_VALUES {
    int id PK
    string session_id FK
    int sample_index
    string ts
    float energy_kwh
    float import_kwh
    float export_kwh
  }

  RECEIPTS {
    string session_id PK
    string receipt_hash
    string merkle_root
    string schema_version
    string session_type
    float energy_kwh
    float import_kwh
    float export_kwh
    float net_kwh
    string start_ts
    string end_ts
    json receipt_json
    string cid
    string chain_tx
    datetime created_at
    datetime updated_at
  }

  BATCH_ANCHORS {
    int id PK
    string day
    string session_prefix
    string batch_root
    int receipt_count
    string chain_tx
    string cid
    datetime anchored_at
  }

  VERIFICATIONS {
    int id PK
    string session_id FK
    string day
    string expected_hash
    string computed_hash
    string expected_root
    string computed_root
    boolean match
    string verification_type
    json details_json
    datetime created_at
  }
```

The receipt table stores normalized fields for querying plus the full
`receipt_json` payload for auditability. The session table does the same with
`session_json`.

To drop the local schema during development:

```bash
python3 -m src.db drop
```

`python3 -m src.db init` is also available as a wrapper around
`alembic upgrade head`.

Create a new migration after changing `src/models.py`:

```bash
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
```

## Generate Synthetic Sessions

Start the API first, then generate sessions:

```bash
python3 -m src.synthetic_sessions 10
```

Useful variants:

```bash
python3 -m src.synthetic_sessions 10 --session-type all
python3 -m src.synthetic_sessions 50 --session-type bidirectional
python3 -m src.synthetic_sessions 100 --bidirectional-ratio 0.30 --discharge-ratio 0.15
```

If an EVSE registry CSV is available:

```bash
python3 -m src.synthetic_sessions 100 --registry exports/evse_registry.csv
```

## Anchor Receipts

Anchor all available days:

```bash
python3 -m src.batch_anchoring
```

Anchor a specific day:

```bash
python3 -m src.batch_anchoring 2026-03-07
```

Anchor only one experiment/run prefix:

```bash
python3 -m src.batch_anchoring 2026-03-07 --prefix run_20260307_223049
```

Anchoring is currently mocked: it computes and stores a Merkle batch root, but
does not submit a real blockchain transaction yet. When `DATABASE_URL` is set,
anchoring reads receipt hashes from Postgres and writes to `batch_anchors`.
Without `DATABASE_URL`, it falls back to `receipts/index.json`, writes to
`receipts/anchors.json`, and updates the local receipt index with `batch_root`
and `batch_day`.

## Verify Receipts

Verify a single receipt:

```bash
python3 -m src.verifier <session_id>
```

Verify a daily batch:

```bash
python3 -m src.verifier_batch 2026-03-07
```

Verify a specific run prefix:

```bash
python3 -m src.verifier_batch 2026-03-07 --prefix run_20260307_223049
```

Batch verification is DB-first when `DATABASE_URL` is set. It reads the latest
matching anchor and receipt hashes from Postgres, recomputes the batch root, and
writes the result to `verifications`. Without `DATABASE_URL`, it uses the local
receipt files and `receipts/anchors.json`.

## Export Datasets And Figures

Generate CSV exports:

```bash
python3 -m src.export
```

When `DATABASE_URL` is set, exports are generated from Postgres. Without
`DATABASE_URL`, the command falls back to the local receipt JSON/index files.

Generate figures:

```bash
python3 -m src.charts
```

Main exported datasets:

- `exports/receipts.csv`
- `exports/sessions.csv`
- `exports/meter_values.csv`
- `exports/anchors.csv`
- `exports/verifications.csv`

Figures are written to `exports/figures/`. See `exports/README.md` for the
export-specific schema notes.

## Run A Reproducible Experiment

Use the experiment runner for the full local pipeline:

```bash
python3 -m src.run_experiment 100 --day 2026-03-07 --seed 42
```

This performs:

```text
generate sessions -> build receipts -> save receipts -> anchor day ->
verify batch -> export datasets -> generate figures -> snapshot results
```

Output is written to:

```text
results/<run_id>/
  manifest.json
  metrics.json
  datasets/*.csv
  figures/*
```

Example with explicit run ID:

```bash
python3 -m src.run_experiment 100 --day 2026-03-07 --seed 42 --run-id demo_100
```

## Tamper Demo

Modify a stored receipt to test verification behavior:

```bash
python3 -m src.tamper <session_id> 1.0
python3 -m src.verifier <session_id>
```

The expected result is that the recomputed hash no longer matches the stored
receipt hash.

## Tests

Run the test suite with:

```bash
.venv/bin/python -m pytest -q
```

Current coverage focuses on:

- deterministic receipt hashing
- canonical receipt schema validation
- import/export/net energy math
- rejection of non-monotone meter counters
- file-backed and DB-backed batch anchoring and batch verification
- detection of a tampered receipt
- SQLAlchemy persistence for sessions, meter values, and receipts
- DB-backed CSV exports

## Current Limitations

- File-based JSON storage is still used alongside the database during the
  migration.
- Batch anchoring is local/mock only.
- `chain_tx` and `cid` are placeholders for future blockchain/IPFS integration.
- Synthetic sessions simulate charging behavior; there is no live OCPP charger
  integration yet.
- Dependencies are listed in `requirements.txt` but not pinned yet.

## Useful Maintenance Commands

Generate release notes from git history:

```bash
python3 -m src.release_notes --max-count 200
```

Generate release notes for a ref range:

```bash
python3 -m src.release_notes --from-ref v0.1.0 --to-ref HEAD
```
