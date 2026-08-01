"""Low-overhead, opt-in performance observations for controlled benchmarks."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from time import perf_counter_ns
from typing import Any, Iterator


STAGE_NAMES = frozenset({
    "synthetic_session_generation",
    "meter_normalization", "meter_leaf_encoding", "meter_merkle_construction",
    "receipt_energy_pricing_assembly", "receipt_schema_validation",
    "receipt_construction_total", "receipt_canonical_hashing", "receipt_validation",
    "database_persistence", "local_file_persistence", "receipt_pipeline_total",
    "batch_eligibility_query", "batch_context_and_leaf_construction",
    "batch_merkle_construction", "batch_anchor_persistence", "batch_anchor_total",
    "batch_membership_load", "batch_merkle_recomputation", "batch_snapshot_audit",
    "batch_verification_persistence", "batch_verification_total",
    "chain_send_command", "chain_receipt_query", "chain_anchor_database_update",
    "chain_read_verification", "chain_publication_total",
    "dataset_export", "count_reconciliation", "artifact_generation",
})


@dataclass(frozen=True)
class TimingObservation:
    benchmark_id: str
    run_id: str
    repetition_index: int | None
    seed: int | None
    workload_size: int | None
    session_mode: str | None
    stage: str
    duration_ns: int
    session_id: str | None
    anchor_id: int | None
    observed_at_utc: str
    metadata: dict[str, Any]


class TimingRecorder:
    def __init__(self, *, benchmark_id: str, run_id: str, repetition_index: int | None = None,
                 seed: int | None = None, workload_size: int | None = None,
                 session_mode: str | None = None) -> None:
        self.context = {"benchmark_id": benchmark_id, "run_id": run_id,
                        "repetition_index": repetition_index, "seed": seed,
                        "workload_size": workload_size, "session_mode": session_mode}
        self._observations: list[TimingObservation] = []

    @contextmanager
    def measure(self, stage: str, *, session_id: str | None = None,
                anchor_id: int | None = None, metadata: dict[str, Any] | None = None) -> Iterator[None]:
        started = perf_counter_ns()
        try:
            yield
        finally:
            self.add_duration_ns(stage, perf_counter_ns() - started, session_id=session_id,
                                 anchor_id=anchor_id, metadata=metadata)

    def add_duration_ns(self, stage: str, duration_ns: int, *, session_id: str | None = None,
                        anchor_id: int | None = None, metadata: dict[str, Any] | None = None) -> None:
        if stage not in STAGE_NAMES:
            raise ValueError(f"Unknown performance timing stage: {stage}")
        if isinstance(duration_ns, bool) or not isinstance(duration_ns, int) or duration_ns < 0:
            raise ValueError("duration_ns must be a non-negative integer")
        self._observations.append(TimingObservation(
            **self.context, stage=stage, duration_ns=duration_ns, session_id=session_id,
            anchor_id=anchor_id, observed_at_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            metadata=dict(metadata or {}),
        ))

    def observations(self) -> list[TimingObservation]:
        return list(self._observations)

    def as_dicts(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self._observations]

