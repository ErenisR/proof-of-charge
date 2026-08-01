# Reviewer 3 performance methodology

## Diagnosis of historical timing

The historical `t_finalize_total` timer encloses an inclusive loop containing synthetic-session generation, receipt construction, canonical receipt hashing, validation, database persistence, and—when enabled—local receipt-file writing. `t_finalize_avg` merely divides that inclusive duration by the requested count. These backward-compatible fields therefore are not receipt-construction latency measurements. The historical experiment matrix normally ran one seed once for each workload/mode. Blockchain publication invokes `cast send` and then separately invokes `cast receipt`; on local Anvil these are local automatically mined diagnostic operations, not estimates of public-chain confirmation time.

## Frozen stage scopes

All durations use monotonic `time.perf_counter_ns()` and are stored as unrounded integer nanoseconds. Instrumentation is optional.

- `synthetic_session_generation`: deterministic input generation before the primary pipeline.
- `meter_normalization`: meter sorting, coercion, and monotonicity checks.
- `meter_leaf_encoding`: existing meter-leaf byte encoding.
- `meter_merkle_construction`: the existing meter-stream Merkle function only.
- `receipt_energy_pricing_assembly`: energy deltas, prices, settlement, and receipt-object assembly.
- `receipt_schema_validation`: receipt-model validation inside construction.
- `receipt_construction_total`: inclusive receipt construction; never summed with its child stages.
- `receipt_canonical_hashing`: canonical bytes and SHA-256 receipt hash.
- `receipt_validation`: session/receipt correctness validation.
- `database_persistence`: current `persist_finalized_session`, including session upsert, meter replacement/inserts, receipt upsert, SQLAlchemy session creation, transaction commit, and close.
- `local_file_persistence`: optional local receipt JSON/index writing; disabled for the primary benchmark.
- `receipt_pipeline_total`: inclusive construction, hashing, validation, and database persistence for one already-generated session.
- `batch_eligibility_query`: PostgreSQL UTC-day/prefix receipt query.
- `batch_context_and_leaf_construction`: frozen context and input preparation.
- `batch_merkle_construction`: `poc-batch-merkle-v1` commitment construction.
- `batch_anchor_persistence`: anchor/membership SQL persistence and commit.
- `batch_anchor_total`: inclusive anchor operation.
- `batch_membership_load`: immutable membership-row load.
- `batch_merkle_recomputation`: commitment reconstruction from the snapshot.
- `batch_snapshot_audit`: stored-versus-current eligible membership audit.
- `batch_verification_persistence`: verification row and transaction commit.
- `batch_verification_total`: inclusive database batch verification.
- `chain_send_command`: installed `cast send` call; it may include local mining/wait behavior.
- `chain_receipt_query`: subsequent `cast receipt` query.
- `chain_anchor_database_update`: anchor update transaction commit.
- `chain_read_verification`: `cast call`, comparison, and optional verification persistence path.
- `chain_publication_total`: inclusive send, receipt query, and in-session anchor update, excluding the separately reported final database commit.
- `dataset_export`, `count_reconciliation`, `artifact_generation`: excluded from receipt throughput and recorded only as run-support work.

The benchmark pre-generates sessions, then processes them with production receipt, persistence, anchoring, verification, and blockchain functions. Receipt throughput is reconciled count divided by the sum of sequential per-receipt `receipt_pipeline_total` durations. It excludes generation, export, figures, reconciliation, and database reset.

## Repetition and statistics

The primary design uses workloads 10, 50, 100, 500, and 1000; seeds 42–51; ten independent measured runs per workload; and one excluded warm-up per workload. A fixed orchestration seed randomizes measured run order. PostgreSQL tables are reset in an untimed step and verified empty before each run. Failed correctness gates stop execution and are not silently replaced.

For each workload, mean, sample standard deviation (`n-1`), and two-sided 95% Student-t confidence intervals use independent run-level quantities. No outliers are removed. Pooled per-receipt p50/p95/p99 use linear interpolation at rank `(n-1)q`; these describe the receipt distribution and are not confidence intervals. The environment collector reports unavailable properties as null rather than guessing.

This is one controlled local hardware/software environment. Repetitions quantify within-environment variability and do not establish cross-hardware generalization. Local Anvil automatically mines locally; its timings do not estimate public-network delay. Canonicalization remains `poc-c14n-v1`, batch commitment remains `poc-batch-merkle-v1`, and timing does not alter cryptographic inputs or outputs.
