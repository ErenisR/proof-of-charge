"""Statistics, environment capture, and exports for Reviewer 3 performance evidence."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import random
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence


T_CRITICAL_975 = {1: 12.706205, 2: 4.302653, 3: 3.182446, 4: 2.776445,
                  5: 2.570582, 6: 2.446912, 7: 2.364624, 8: 2.306004,
                  9: 2.262157, 10: 2.228139, 11: 2.200985, 12: 2.178813,
                  13: 2.160369, 14: 2.144787, 15: 2.131450, 16: 2.119905,
                  17: 2.109816, 18: 2.100922, 19: 2.093024, 20: 2.085963,
                  21: 2.079614, 22: 2.073873, 23: 2.068658, 24: 2.063899,
                  25: 2.059539, 26: 2.055529, 27: 2.051831, 28: 2.048407,
                  29: 2.045230, 30: 2.042272}


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation on rank (n-1)q, matching NumPy's default method."""
    if not values: raise ValueError("percentile requires observations")
    if not 0 <= q <= 1: raise ValueError("q must be in [0, 1]")
    ordered = sorted(float(value) for value in values)
    rank = (len(ordered) - 1) * q; low = math.floor(rank); high = math.ceil(rank)
    return ordered[low] if low == high else ordered[low] + (rank - low) * (ordered[high] - ordered[low])


def summarize(values: Sequence[float]) -> dict[str, float | int]:
    if not values: raise ValueError("summary requires observations")
    vals = [float(v) for v in values]; n = len(vals); mean = statistics.fmean(vals)
    sd = statistics.stdev(vals) if n > 1 else 0.0
    if n > 1:
        critical = T_CRITICAL_975.get(n - 1)
        if critical is None: raise ValueError("Student-t table supports at most 31 observations")
        margin = critical * sd / math.sqrt(n)
    else: margin = 0.0
    return {"n": n, "mean": mean, "sample_sd": sd, "minimum": min(vals), "maximum": max(vals),
            "median": statistics.median(vals), "p50": percentile(vals, .50),
            "p95": percentile(vals, .95), "p99": percentile(vals, .99),
            "ci95_low": mean - margin, "ci95_high": mean + margin}


def randomized_execution_order(sizes: Sequence[int], seeds: Sequence[int], orchestration_seed: int) -> list[tuple[int, int, int]]:
    if len(seeds) == 0: raise ValueError("At least one seed is required")
    pairs = [(size, repetition + 1, seed) for size in sizes for repetition, seed in enumerate(seeds)]
    random.Random(orchestration_seed).shuffle(pairs)
    return pairs


def command_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        value = (result.stdout or result.stderr).strip().splitlines()
        return value[0] if value else None
    except (OSError, subprocess.SubprocessError): return None


def collect_environment(*, command: str, source_sha: str | None, dirty: bool,
                        migration_revision: str | None) -> dict[str, Any]:
    physical = None; cpu_model = None; ram_bytes = None
    if platform.system() == "Darwin":
        cpu_model = command_version(["sysctl", "-n", "machdep.cpu.brand_string"])
        raw_physical = command_version(["sysctl", "-n", "hw.physicalcpu"])
        raw_ram = command_version(["sysctl", "-n", "hw.memsize"])
        physical = int(raw_physical) if raw_physical and raw_physical.isdigit() else None
        ram_bytes = int(raw_ram) if raw_ram and raw_ram.isdigit() else None
    return {"cpu_model": cpu_model, "physical_core_count": physical,
            "logical_core_count": os.cpu_count(), "architecture": platform.machine(),
            "total_ram_bytes": ram_bytes, "operating_system": platform.platform(),
            "python_version": platform.python_version(), "postgresql_version": command_version(["psql", "--version"]),
            "docker_version": command_version(["docker", "--version"]),
            "docker_compose_version": command_version(["docker", "compose", "version"]),
            "anvil_version": command_version(["anvil", "--version"]), "cast_version": command_version(["cast", "--version"]),
            "solc_version": command_version(["solc", "--version"]), "database_connection_mode": "SQLAlchemy/psycopg TCP",
            "anvil_chain_id": os.getenv("CHAIN_ID", "31337"), "anvil_mining_mode": "automatic local mining",
            "canonicalization_profile": "poc-c14n-v1", "batch_commitment_profile": "poc-batch-merkle-v1",
            "source_commit_sha": source_sha, "alembic_migration_revision": migration_revision,
            "benchmark_command": command, "timezone": "UTC experiment day; host=" + os.getenv("TZ", "system default"),
            "dirty_worktree": dirty, "local_receipt_writing_disabled": True}


def write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows); path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in rows: writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(observations: list[dict[str, Any]], run_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    valid = [row for row in run_rows if row["valid"] and not row["warmup"]]
    if len(valid) != len([row for row in run_rows if not row["warmup"]]):
        raise RuntimeError("Invalid measured runs must stop aggregation")
    stage_rows: list[dict[str, Any]] = []; workload_rows: list[dict[str, Any]] = []
    obs_groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for obs in observations:
        if not obs.get("warmup"): obs_groups[(int(obs["workload_size"]), obs["stage"])].append(obs)
    for (size, stage), group in sorted(obs_groups.items()):
        by_run: dict[str, float] = defaultdict(float)
        for item in group: by_run[item["run_id"]] += int(item["duration_ns"]) / 1_000_000
        stats = summarize(list(by_run.values()))
        pooled = [int(item["duration_ns"]) / 1_000_000 for item in group]
        stage_rows.append({"stage": stage, "workload_size": size, "n_runs": len(by_run),
                           "n_observations": len(group), **stats,
                           "p50": percentile(pooled, .50), "p95": percentile(pooled, .95), "p99": percentile(pooled, .99),
                           "ci95_low_for_run_mean": stats["ci95_low"], "ci95_high_for_run_mean": stats["ci95_high"]})
    for size in sorted({int(row["workload_size"]) for row in valid}):
        runs = [row for row in valid if int(row["workload_size"]) == size]
        pipeline = summarize([float(row["receipt_pipeline_mean_ms"]) for row in runs])
        throughput = summarize([float(row["receipt_throughput_per_sec"]) for row in runs])
        pooled_pipeline = [int(item["duration_ns"])/1e6 for item in observations if not item.get("warmup") and int(item["workload_size"]) == size and item["stage"] == "receipt_pipeline_total"]
        def stage_mean(stage: str) -> float:
            vals = [int(item["duration_ns"])/1e6 for item in observations if not item.get("warmup") and int(item["workload_size"]) == size and item["stage"] == stage]
            return statistics.fmean(vals) if vals else 0.0
        workload_rows.append({"workload_size": size, "n_runs": len(runs),
            "receipt_pipeline_mean_ms": pipeline["mean"], "receipt_pipeline_sd_ms": pipeline["sample_sd"],
            "receipt_pipeline_ci95_low_ms": pipeline["ci95_low"], "receipt_pipeline_ci95_high_ms": pipeline["ci95_high"],
            "receipt_pipeline_p50_ms": percentile(pooled_pipeline,.5), "receipt_pipeline_p95_ms": percentile(pooled_pipeline,.95),
            "receipt_pipeline_p99_ms": percentile(pooled_pipeline,.99),
            "throughput_mean_receipts_per_sec": throughput["mean"], "throughput_sd": throughput["sample_sd"],
            "throughput_ci95_low": throughput["ci95_low"], "throughput_ci95_high": throughput["ci95_high"],
            "receipt_construction_mean_ms": stage_mean("receipt_construction_total"),
            "database_persistence_mean_ms": stage_mean("database_persistence"), "meter_merkle_mean_ms": stage_mean("meter_merkle_construction"),
            "batch_merkle_mean_ms": stage_mean("batch_merkle_construction"), "batch_verification_mean_ms": stage_mean("batch_verification_total"),
            "chain_send_mean_ms": stage_mean("chain_send_command"), "chain_receipt_query_mean_ms": stage_mean("chain_receipt_query"),
            "chain_read_verification_mean_ms": stage_mean("chain_read_verification"),
            "all_runs_reconciled": all(row["count_reconciliation_ok"] for row in runs),
            "all_batch_roots_matched": all(row["batch_root_match"] for row in runs),
            "all_chain_roots_matched": all(row["chain_root_match"] for row in runs)})
    return workload_rows, stage_rows
