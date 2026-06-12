# Proof-of-Charge Backend

Proof-of-Charge is a Python MVP for verifiable EV charging receipts. It turns
charging session data into deterministic receipts, hashes those receipts,
groups them into daily Merkle batch anchors, verifies anchored batches, and
exports datasets and figures for research or thesis material.

The current development workflow is Postgres-first: Postgres is the source of
truth for finalized sessions, receipts, batch anchors, batch memberships,
verifications, and exported query data. Local receipt JSON files are legacy
migration/debug artifacts only. Blockchain transactions and IPFS CIDs are
represented as placeholders for now.

## What It Does

- Finalizes EV charging sessions through a FastAPI endpoint.
- Builds deterministic receipt JSON with session metadata, pricing, settlement,
  meter values, and a Merkle root over the meter stream.
- Supports V2G-ready energy fields: `import_kwh`, `export_kwh`, and `net_kwh`.
- Supports `charge_only`, `discharge_only`, and `bidirectional` synthetic
  sessions.
- Stores finalized sessions and receipts in Postgres.
- Creates mock daily batch anchors from receipt hashes.
- Verifies that stored batch roots match recomputed Merkle roots.
- Exports CSV datasets and PNG figures under `exports/`.
- Runs reproducible synthetic experiments under `results/<run_id>/`.

## Project Structure

```text
src/
  main.py                FastAPI app and receipt finalization endpoint
  api_schemas.py         Pydantic API request/response models
  api_service.py         Read-only API query service
  audit_service.py       DB audit service for rebuilt receipt verification
  receipt_builder.py     Receipt schema, energy math, pricing, receipt hash
  merkle.py              Merkle tree helper
  storage.py             Legacy JSON receipt/index storage helpers
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
  blockchain/            Blockchain anchoring integration helpers

contracts/               Solidity contracts for batch-root anchoring
scripts/                 Deployment and operational scripts
docs/                    Architecture notes, paper outline, and reports
receipts/                Legacy local receipt JSON files and mock anchors
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
  C --> E[Persist receipt in Postgres]
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

The project has three intentionally separate data-model layers:

- API input schema: Pydantic models in `src/main.py` validate request payloads
  before receipt construction.
- Canonical receipt schema: `src/receipt_schema.py` defines the receipt contract
  and accounting invariants that are hashed and audited.
- Persistence schema: SQLAlchemy models in `src/models.py` store queryable
  normalized columns plus full JSON payloads for auditability.

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
GET  /health/db
GET  /v1/sessions
GET  /v1/sessions/{session_id}
GET  /v1/receipts/{session_id}
GET  /v1/anchors
GET  /v1/verifications
POST /v1/audit/sessions/{session_id}
```

The endpoint accepts a charging session payload, builds a receipt, hashes it,
persists it in Postgres, and returns the receipt plus its hash.

The read-only endpoints expose stored Postgres data for demos, debugging, and
future dashboard/frontend integration. List endpoints support bounded pagination
with `limit` and `offset`; session, anchor, and verification lists also support
basic filters such as `session_prefix`, `day`, `session_id`, and
`verification_type`.

## Postgres Storage

The project includes a Postgres persistence layer using SQLAlchemy. The repo
loads `.env` automatically during normal runtime. The local `.env` is configured
with:

```bash
DATABASE_URL=postgresql+psycopg://proof:proof@localhost:5433/proof_of_charge
REQUIRE_DATABASE=1
WRITE_LOCAL_RECEIPTS=0
```

With `REQUIRE_DATABASE=1`, normal commands fail loudly if `DATABASE_URL` is
missing. If Postgres is not running, DB-backed commands fail when they try to
connect, which is intentional for development.

With `WRITE_LOCAL_RECEIPTS=0`, new finalized sessions are stored in Postgres
without writing duplicate receipt JSON files under `receipts/`. Set
`WRITE_LOCAL_RECEIPTS=1` only when you intentionally want compatibility JSON
files for debugging or migration.

Start Postgres locally:

```bash
docker compose up -d postgres
```

Start Postgres and the local Anvil blockchain:

```bash
docker compose up -d postgres anvil
```

If you need to recreate your local env file:

```bash
cp .env.example .env
```

Initialize the schema:

```bash
python3 -m src.db init
```

Check DB readiness:

```bash
python3 -m src.db check
```

Then run the API normally:

```bash
uvicorn src.main:app --reload
```

With the default `.env`, finalized sessions are written to Postgres tables
through SQLAlchemy.

Current database tables:

- `sessions`
- `meter_values`
- `receipts`
- `batch_anchors`
- `batch_anchor_receipts`
- `verifications`

## Local Blockchain

The Docker Compose setup includes an Anvil service for local blockchain
development:

```bash
docker compose up -d anvil
```

The local RPC endpoint is:

```bash
WEB3_RPC_URL=http://127.0.0.1:8545
CHAIN_ID=31337
```

Deploy the anchor contract after Anvil is running:

```bash
python3 scripts/deploy_anchor.py
```

The script prints the `ANCHOR_CONTRACT_ADDRESS` to copy into `.env`. Anvil state
is ephemeral by default, so recreate/redeploy the contract after recreating the
Anvil container.

Publish an existing Postgres batch anchor on-chain:

```bash
python3 -m src.blockchain.publisher 2026-03-07
```

Verify that the Postgres anchor matches the on-chain anchor:

```bash
python3 -m src.blockchain.verifier 2026-03-07
```

If the DB anchor was created with a session prefix, pass the same prefix:

```bash
python3 -m src.blockchain.publisher 2026-03-07 --prefix run-test
python3 -m src.blockchain.verifier 2026-03-07 --prefix run-test
```

Database relationship diagram:

```mermaid
erDiagram
  SESSIONS ||--o{ METER_VALUES : has
  SESSIONS ||--|| RECEIPTS : produces
  SESSIONS ||--o{ VERIFICATIONS : checked_by
  SESSIONS ||--o{ BATCH_ANCHOR_RECEIPTS : included_as
  BATCH_ANCHORS ||--o{ BATCH_ANCHOR_RECEIPTS : contains
  BATCH_ANCHORS ||--o{ VERIFICATIONS : verified_by

  SESSIONS {
    string session_id PK
    string user_id
    string evse_id
    string ocpp_tx_id
    string schema_version
    string session_type
    datetime start_ts
    datetime end_ts
    json session_json
    datetime created_at
    datetime updated_at
  }

  METER_VALUES {
    int id PK
    string session_id FK
    int sample_index
    datetime ts
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
    datetime start_ts
    datetime end_ts
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

  BATCH_ANCHOR_RECEIPTS {
    int id PK
    int anchor_id FK
    string session_id FK
    string receipt_hash
    int leaf_index
    datetime created_at
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

`batch_anchor_receipts` stores the exact receipt-hash snapshot included in each
batch anchor. `leaf_index` records the receipt hash position after deterministic
sorting for Merkle tree construction.

To drop the local schema during development:

```bash
python3 -m src.db drop
```

`python3 -m src.db init` is also available as a wrapper around
`alembic upgrade head`.

`python3 -m src.db check` verifies the connection, applied Alembic revision,
and expected tables.

Create a new migration after changing `src/models.py`:

```bash
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
```

Backfill existing local receipt JSON files into Postgres:

```bash
python3 -m src.import_receipts
```

After verifying the imported data in Postgres, the old local `receipts/*.json`
files and `receipts/index.json` can be treated as migration artifacts rather
than active storage. Do not delete them until you are comfortable that the DB
contains the sessions, receipts, meter values, and anchors you need.

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
does not submit a real blockchain transaction yet. With the default `.env`,
anchoring reads receipt hashes from Postgres and writes to `batch_anchors` plus
`batch_anchor_receipts`.
DB anchoring is idempotent for the same `day`, `session_prefix`, and
`batch_root`: re-running the same command reuses the existing anchor instead of
creating duplicate rows.
If `REQUIRE_DATABASE` is disabled and `DATABASE_URL` is not set, it falls back
to `receipts/index.json`, writes to `receipts/anchors.json`, and updates the
local receipt index with `batch_root` and `batch_day`.

## Verify Receipts

Verify a single receipt:

```bash
python3 -m src.verifier <session_id>
```

With the default `.env`, single-receipt verification reads from Postgres,
recomputes the hash from `receipts.receipt_json`, compares it with
`receipts.receipt_hash`, and writes a `receipt` verification row to
`verifications`. If `REQUIRE_DATABASE` is disabled and `DATABASE_URL` is not
set, it verifies the local receipt JSON file instead.

Verify a daily batch:

```bash
python3 -m src.verifier_batch 2026-03-07
```

Verify a specific run prefix:

```bash
python3 -m src.verifier_batch 2026-03-07 --prefix run_20260307_223049
```

Batch verification reads the latest matching anchor and receipt hashes from
Postgres, recomputes the batch root, and writes the result to `verifications`.
If `REQUIRE_DATABASE` is disabled and `DATABASE_URL` is not set, it uses the
local receipt files and `receipts/anchors.json`.

Audit a DB-backed session by rebuilding the receipt from stored session data:

```bash
python3 -m src.audit_service <session_id>
```

Or through the API:

```text
POST /v1/audit/sessions/{session_id}
```

The audit checks whether the rebuilt receipt, stored `receipt_json`, stored
`receipt_hash`, and normalized receipt columns still agree.

## Export Datasets And Figures

Generate CSV exports:

```bash
python3 -m src.export
```

With the default `.env`, exports and figures are generated from Postgres. If
`REQUIRE_DATABASE` is disabled and `DATABASE_URL` is not set, the command falls
back to the local receipt JSON/index files.

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

Skip figure generation for quick receipt/tamper demos:

```bash
python3 -m src.run_experiment 1 --day 2026-05-22 --seed 42 --run-id tamper_demo --skip-figures
```

Output is written to:

```text
results/<run_id>/
  manifest.json
  metrics.json
  datasets/*.csv
  figures/*          # only when figures are not skipped
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

With the default `.env`, tampering modifies the Postgres `receipt_json` payload
without updating the stored `receipt_hash`. The expected result is that the
single-receipt verifier reports a hash mismatch.

Batch verification may still pass after receipt JSON tampering because
`batch_anchor_receipts` intentionally stores the exact anchored receipt-hash
membership snapshot. This preserves what was anchored historically. Use
`python3 -m src.verifier <session_id>` to detect modified receipt content.

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
- exact batch anchor receipt membership snapshots
- detection of a tampered receipt
- DB audit detection for tampered receipt JSON and normalized receipt columns
- SQLAlchemy persistence for sessions, meter values, and receipts
- DB-backed CSV exports

## Current Limitations

- Legacy file-based JSON storage remains available only for migration/debug
  fallback paths.
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
