#!/usr/bin/env python3
"""Build a blockchain cost interpretation report from an experiment matrix."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _read_rows(matrix_dir: Path) -> list[dict[str, Any]]:
    summary_path = matrix_dir / "summary.csv"
    if not summary_path.exists():
        raise FileNotFoundError(f"Missing summary.csv: {summary_path}")
    with summary_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _chain_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        gas_used = _to_int(row.get("chain_gas_used"))
        if gas_used is None:
            continue
        sessions = _to_int(row.get("sessions") or row.get("workload_size"))
        fee_wei = _to_int(row.get("chain_transaction_fee_wei"))
        normalized.append(
            {
                "run_id": row.get("run_id"),
                "session_type_mode": row.get("session_type_mode"),
                "sessions": sessions,
                "gas_used": gas_used,
                "gas_per_session": round(gas_used / sessions, 6) if sessions else None,
                "transaction_fee_wei": fee_wei,
                "transaction_fee_eth": round(fee_wei / 10**18, 18) if fee_wei is not None else None,
                "chain_root_match": row.get("chain_root_match"),
                "chain_tx": row.get("chain_tx"),
            }
        )
    return normalized


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    gas_values = [row["gas_used"] for row in rows if row.get("gas_used") is not None]
    per_session_values = [
        row["gas_per_session"]
        for row in rows
        if row.get("gas_per_session") is not None
    ]
    fees = [
        row["transaction_fee_wei"]
        for row in rows
        if row.get("transaction_fee_wei") is not None
    ]
    return {
        "anchored_runs": len(rows),
        "gas_used_min": min(gas_values) if gas_values else None,
        "gas_used_max": max(gas_values) if gas_values else None,
        "gas_used_avg": round(mean(gas_values), 6) if gas_values else None,
        "gas_used_range": (max(gas_values) - min(gas_values)) if gas_values else None,
        "gas_per_session_min": round(min(per_session_values), 6) if per_session_values else None,
        "gas_per_session_max": round(max(per_session_values), 6) if per_session_values else None,
        "gas_per_session_avg": round(mean(per_session_values), 6) if per_session_values else None,
        "transaction_fee_wei_min": min(fees) if fees else None,
        "transaction_fee_wei_max": max(fees) if fees else None,
        "transaction_fee_wei_avg": round(mean(fees), 6) if fees else None,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "run_id",
        "session_type_mode",
        "sessions",
        "gas_used",
        "gas_per_session",
        "transaction_fee_wei",
        "transaction_fee_eth",
        "chain_root_match",
        "chain_tx",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, matrix_dir: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# Blockchain Cost Interpretation",
        "",
        f"Matrix: `{matrix_dir.name}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Anchored runs | {summary['anchored_runs']} |",
        f"| Gas used min | {summary['gas_used_min']} |",
        f"| Gas used max | {summary['gas_used_max']} |",
        f"| Gas used avg | {summary['gas_used_avg']} |",
        f"| Gas used range | {summary['gas_used_range']} |",
        f"| Gas per session avg | {summary['gas_per_session_avg']} |",
        f"| Transaction fee wei avg | {summary['transaction_fee_wei_avg']} |",
        "",
        "## Per-Run Cost",
        "",
        "| Run | Mode | Sessions | Gas Used | Gas/Session | Fee Wei | Chain Match |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['run_id']}` | `{row['session_type_mode']}` | {row['sessions']} | "
            f"{row['gas_used']} | {row['gas_per_session']} | {row['transaction_fee_wei']} | "
            f"`{row['chain_root_match']}` |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The contract anchors one Merkle batch root per run. Because the on-chain",
            "transaction stores a fixed-size root, date, prefix, and receipt count, gas",
            "used should remain approximately constant as the number of receipts in the",
            "batch grows. The useful scalability signal is therefore not that total gas",
            "increases with sessions, but that gas per session decreases as more receipts",
            "are covered by the same anchored batch root.",
            "",
            "This supports the design choice to avoid per-receipt blockchain writes.",
            "Receipts remain individually verifiable through Merkle membership and the",
            "single on-chain batch root.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_report(matrix_dir: Path) -> dict[str, Any]:
    rows = _chain_rows(_read_rows(matrix_dir))
    if not rows:
        raise ValueError(
            f"No chain metrics found in {matrix_dir / 'summary.csv'}. "
            "Run the matrix with PUBLISH_CHAIN=1 first."
        )

    summary = _summary(rows)
    payload = {"summary": summary, "rows": rows}

    _write_csv(matrix_dir / "blockchain_cost_summary.csv", rows)
    (matrix_dir / "blockchain_cost_summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    _write_report(matrix_dir / "blockchain_cost_interpretation.md", matrix_dir, rows, summary)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix_dir", type=Path, help="Path to results/<matrix_id>")
    args = parser.parse_args()

    payload = build_report(args.matrix_dir)
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
