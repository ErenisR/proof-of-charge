# Reviewer 3 Comment 7 — count reconciliation

## Concern and exact diagnosis

Reviewer 3 asked why a 1000-session workload verified only 999 receipts while
the batch root still matched. The preserved PostgreSQL run
`experiment_matrix_20260618_193131_all_1000` (seed 42, mixed/all mode) was
reconciled using its manifest IDs, persisted sessions and receipts, anchor 29,
the 999 stored `BatchAnchorReceipt` rows, and its exported CSV datasets.

The exact excluded receipt was:

| Field | Value |
|---|---|
| Session ID | `experiment_matrix_20260618_193131_all_1000-0952` |
| Start | `2026-06-17T23:59:48.074911Z` |
| End | `2026-06-18T00:19:48.074911Z` |
| Requested batch day | `2026-06-18` |
| Persisted session/receipt | yes / yes |
| Day eligible | no |
| Anchored/verified | no / no |
| Exported | yes |

The old generator used `2026-06-18T23:59:00Z - uniform(0, 24 hours)`.
For session 0952 the sampled offset crossed into June 17. The daily SQL
predicate required `start_ts >= 2026-06-18T00:00:00Z` and
`start_ts < 2026-06-19T00:00:00Z`, so it correctly excluded that receipt from
the June 18 batch. Anchoring and verification both operated on the same reduced
999-receipt membership snapshot; consequently, both the Merkle root and stored
membership count matched despite the workload-count discrepancy. No receipt was
lost by the blockchain and the verifier did not randomly skip a receipt.

Pre-fix evidence is in
`results/reviewer3_count_diagnostic_20260618/count_reconciliation_before.*`.

## Implemented correction

Experiments now generate each start timestamp directly inside the requested UTC
day's half-open interval. A receipt belongs to the UTC batch identified by the
calendar date of normalized `start_ts`:

```text
[day 00:00:00 UTC, next day 00:00:00 UTC)
```

A session that starts in the interval stays eligible even if it ends after
midnight. Callers that do not provide a target day retain the previous generator
behavior. The experiment finalizer checks every generated timestamp and raises
with exact offending IDs and timestamps if this invariant is violated.

After export, the runner queries and compares exact ID sets for requested,
generated, persisted sessions, persisted receipts, day-eligible receipts,
anchor memberships, verified memberships, and all exported datasets. It writes
JSON, CSV, and Markdown reconciliation artifacts. A count or ID difference raises
`ExperimentCountMismatch`; a matching batch root alone is insufficient.

Batch verification now reports anchor ID, expected and actual membership counts,
count match, expected and computed roots, root match, ordered IDs, and ordered
receipt hashes. Exported anchor count comes from actual stored membership rows.

## Corrected 1000-session result

Command:

```bash
.venv/bin/python -m src.run_experiment 1000 \
  --day 2026-07-15 \
  --seed 42 \
  --run-id reviewer3_corrected_1000_20260801 \
  --session-type all \
  --skip-figures
```

| Stage | Count |
|---|---:|
| Requested | 1000 |
| Generated | 1000 |
| Persisted sessions | 1000 |
| Persisted receipts | 1000 |
| Day eligible | 1000 |
| Anchored memberships | 1000 |
| Verified memberships | 1000 |
| Exported sessions | 1000 |
| Exported receipts | 1000 |
| Exported verifications | 1000 |

The stored and actual membership counts match, the batch root matches, and exact
set reconciliation passes. The corrected root is
`0xb347d8cf16d465346c764f2d67691ddf1a14650c9101de97e445fbbbe95e3885`.
Artifacts are under `results/reviewer3_corrected_1000_20260801/`.

## Tests and limitations

Tests cover the old boundary behavior, deterministic target-day generation,
half-open boundaries, cross-midnight inclusion, reduced-root reconciliation
failure, missing receipts/memberships/exports, and prefix isolation through the
existing anchoring suite. The result establishes count completeness for this
controlled pipeline run; it does not establish source-meter authenticity or
prove that an external data source captured every physical charging event.
