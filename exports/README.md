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

6) `evse_registry.csv`
- `evse_id`: charging point identifier
- `station_id`: station identifier (if available)
- `operator`: operator or site owner
- `station_name`, `address`, `postcode`, `city`
- `longitude`, `latitude`
- `power_kw`: nominal power (if available)
- `connector_type`: connector type (if available)
- `access`: accessibility info (if available)

## Figures

`figures/` contains PNG charts plus `figures/captions.md` with ready-to-use
captions. Generate with:

`python -m src.charts`

Additional figures include:
- `consumption_over_time.png`: Cumulative energy vs time for sample sessions.
- `price_over_time.png`: Price per kWh over time for sample sessions.

## How to generate

1) Finalize some sessions (real or synthetic)
2) Anchor batches (mock):
   `python -m src.batch_anchoring`
3) Export datasets:
   `python -m src.export`
4) Generate figures:
   `python -m src.charts`

## EVSE registry import (optional)

If you have the IRVE dataset file (CSV or GeoJSON) locally, you can import it
to `evse_registry.csv`:

`python -m src.import_irve /path/to/irve.csv`
