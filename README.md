# Exports

This folder contains research-ready datasets and figures derived from the
proof-of-charge receipt pipeline. All files are generated locally from your
stored receipts and batch anchors.

## Datasets (CSV)

1) `receipts.csv`
- `session_id`: unique session identifier
- `receipt_hash`: deterministic hash of receipt JSON
- `merkle_root`: Merkle root over meter samples
- `pricing_model`: tariff model (e.g., TOU)
- `energy_kwh`: total session energy
- `start_ts`, `end_ts`: session timestamps (ISO 8601)
- `batch_root`: batch anchor root (if anchored)
- `batch_day`: day used for anchoring (YYYY-MM-DD)

2) `sessions.csv`
- `session_id`, `evse_id`: identifiers
- `start_ts`, `end_ts`: session timestamps
- `energy_kwh`: total session energy (from receipt)
- `tariff_model`: pricing model

3) `meter_values.csv`
- `session_id`: identifier
- `ts`: meter sample timestamp
- `energy_kwh`: cumulative energy at `ts`

4) `anchors.csv`
- `day`: batch day (YYYY-MM-DD)
- `batch_root`: Merkle root for that day
- `receipt_count`: number of receipts in batch
- `chain_tx`: placeholder for on-chain tx hash (mock in V1)
- `cid`: placeholder for IPFS CID (mock in V1)
- `anchored_at`: batch anchor timestamp (UTC)

5) `verifications.csv`
- `session_id`: identifier
- `expected_hash`: stored hash
- `computed_hash`: recomputed hash from receipt JSON
- `match`: whether hashes match
- `batch_root`: batch root linked in index
- `batch_day`: day used for anchoring

## Figures

`figures/` contains PNG charts plus `figures/captions.md` with ready-to-use
captions. Generate with:

`python -m src.charts`

## How to generate

1) Finalize some sessions (real or synthetic)
2) Anchor batches (mock):
   `python -m src.batch_anchoring`
3) Export datasets:
   `python -m src.export`
4) Generate figures:
   `python -m src.charts`
