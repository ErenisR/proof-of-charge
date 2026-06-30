#!/usr/bin/env python3
"""Compare Merkle batch anchoring with per-receipt blockchain anchoring.

Example:

    python scripts/run_anchor_strategy_comparison.py \
      --sessions 10 50 100 250 500 1000 \
      --day 2026-06-20 \
      --seed 42 \
      --session-type-mode all \
      --deploy-contract \
      --rpc-url http://127.0.0.1:8545 \
      --private-key <ANVIL_PRIVATE_KEY> \
      --output-dir results

The per-receipt baseline intentionally reuses the existing batch-only anchor
contract by calling anchorBatch(day, uniquePrefix, receiptHash, 1) once per
receipt. This keeps the blockchain architecture unchanged while measuring the
cost of one on-chain transaction per receipt digest.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.deploy_anchor import ANVIL_PRIVATE_KEY, DeploymentConfig, deploy_anchor
from src.blockchain.cast_client import anchor_batch, get_anchor, get_transaction_receipt
from src.blockchain.config import BlockchainConfig
from src.merkle import merkle_root
from src.receipt_builder import build_receipt, hash_receipt
from src.session_validation import summarize_session_metrics, summarize_validation, validate_session_receipt
from src.synthetic_sessions import generate_session


SUMMARY_FIELDS = [
    "sessions",
    "receipts",
    "batch_tx_count",
    "per_receipt_tx_count",
    "batch_total_gas",
    "per_receipt_total_gas",
    "batch_gas_per_receipt",
    "per_receipt_gas_per_receipt",
    "batch_total_fee_wei",
    "per_receipt_total_fee_wei",
    "batch_fee_per_receipt_wei",
    "per_receipt_fee_per_receipt_wei",
    "gas_reduction_ratio",
    "fee_reduction_ratio",
    "gas_savings_percent",
    "fee_savings_percent",
    "batch_chain_root_match",
    "per_receipt_success_count",
    "per_receipt_failed_count",
]


@dataclass(frozen=True)
class Workload:
    sessions: list[dict[str, Any]]
    receipts: list[dict[str, Any]]
    receipt_hashes: list[str]
    session_metrics: dict[str, Any]
    validation: dict[str, Any]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp() -> str:
    return _now_utc().strftime("%Y%m%d_%H%M%S")


def _day_end(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T23:59:00+00:00")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, capture_output=True, text=True, cwd=REPO_ROOT)


def _combined_output(exc: subprocess.CalledProcessError) -> str:
    return "\n".join(part for part in (exc.stdout, exc.stderr) if part).strip()


def _git_commit() -> str | None:
    try:
        result = _run(["git", "rev-parse", "HEAD"])
    except Exception:
        return None
    return result.stdout.strip() or None


def _redacted_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for value in argv:
        if skip_next:
            redacted.append("<REDACTED>")
            skip_next = False
            continue
        redacted.append(value)
        if value == "--private-key":
            skip_next = True
    return redacted


def _stable_workload_prefix(day: str, seed: int, mode: str, size: int) -> str:
    safe_day = day.replace("-", "")
    safe_mode = mode.replace("_", "-")
    return f"anchorcmp-{safe_day}-seed{seed}-{safe_mode}-n{size}"


def _normalize_mode(mode: str) -> str:
    aliases = {
        "charge-only": "charge_only",
        "charge_only": "charge_only",
        "discharge-only": "discharge_only",
        "discharge_only": "discharge_only",
        "bidirectional": "bidirectional",
        "all": "all",
    }
    if mode not in aliases:
        allowed = ", ".join(sorted(aliases))
        raise argparse.ArgumentTypeError(f"invalid session type mode {mode!r}; choose one of: {allowed}")
    return aliases[mode]


def _require_tool(name: str, purpose: str) -> None:
    if not shutil.which(name):
        raise RuntimeError(f"Foundry tool '{name}' is required for {purpose}, but it was not found on PATH.")


def _read_chain_id(rpc_url: str) -> int:
    _require_tool("cast", "blockchain access")
    try:
        result = _run(["cast", "chain-id", "--rpc-url", rpc_url])
    except subprocess.CalledProcessError as exc:
        detail = _combined_output(exc)
        raise RuntimeError(
            f"Could not reach blockchain RPC at {rpc_url}. Is Anvil running?\n{detail}"
        ) from exc
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"Could not parse chain id from cast output: {result.stdout!r}") from exc


def _deploy_contract(rpc_url: str, private_key: str) -> str:
    _require_tool("forge", "contract deployment")
    try:
        return deploy_anchor(DeploymentConfig(rpc_url=rpc_url, private_key=private_key, chain_id=""))
    except subprocess.CalledProcessError as exc:
        detail = _combined_output(exc)
        raise RuntimeError(f"Contract deployment failed.\n{detail}") from exc


def _build_workload(size: int, day: str, seed: int, mode: str, prefix: str) -> Workload:
    rng = random.Random(seed)
    base_now = _day_end(day)
    sessions: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    receipt_hashes: list[str] = []
    records: list[dict[str, Any]] = []
    validation_failures: dict[str, list[str]] = {}

    for i in range(1, size + 1):
        session = generate_session(
            i,
            rng=rng,
            base_now=base_now,
            session_prefix=prefix,
            deterministic_tx=True,
            session_mode=mode,
        )
        receipt = build_receipt(session)
        receipt_hash = hash_receipt(receipt)
        sessions.append(session)
        receipts.append(receipt)
        receipt_hashes.append(receipt_hash)
        records.append({"session": session, "receipt": receipt, "receipt_hash": receipt_hash})
        validation_failures[session["session_id"]] = validate_session_receipt(session, receipt, receipt_hash)

    return Workload(
        sessions=sessions,
        receipts=receipts,
        receipt_hashes=receipt_hashes,
        session_metrics=summarize_session_metrics(records),
        validation=summarize_validation(validation_failures),
    )


def _build_batch_root(receipt_hashes: list[str]) -> str:
    normalized = []
    for receipt_hash in receipt_hashes:
        value = receipt_hash[2:] if receipt_hash.startswith("0x") else receipt_hash
        normalized.append(value.lower())
    normalized.sort()
    if not normalized:
        raise ValueError("Cannot build batch root from empty receipt hash list")
    return "0x" + merkle_root([bytes.fromhex(value) for value in normalized]).hex()


def _zero_chain_result(status: str = "dry_run") -> dict[str, Any]:
    return {
        "status_label": status,
        "transaction_hash": None,
        "gas_used": 0,
        "effective_gas_price": 0,
        "transaction_fee_wei": 0,
        "block_number": None,
        "block_timestamp": None,
        "status": None,
        "error": None,
    }


def _publish_one(
    config: BlockchainConfig,
    day: str,
    prefix: str,
    root_or_hash: str,
    receipt_count: int,
) -> dict[str, Any]:
    tx_hash = anchor_batch(config, day, prefix, root_or_hash, receipt_count)
    receipt = get_transaction_receipt(config, tx_hash)
    return {
        "status_label": "published" if receipt.status == 1 else "failed",
        "transaction_hash": tx_hash,
        "gas_used": receipt.gas_used,
        "effective_gas_price": receipt.effective_gas_price,
        "transaction_fee_wei": receipt.transaction_fee_wei,
        "block_number": receipt.block_number,
        "block_timestamp": receipt.block_timestamp,
        "status": receipt.status,
        "error": None,
    }


def _safe_publish_one(
    config: BlockchainConfig,
    day: str,
    prefix: str,
    root_or_hash: str,
    receipt_count: int,
) -> dict[str, Any]:
    try:
        return _publish_one(config, day, prefix, root_or_hash, receipt_count)
    except subprocess.CalledProcessError as exc:
        return {**_zero_chain_result("failed"), "error": _combined_output(exc)}
    except Exception as exc:
        return {**_zero_chain_result("failed"), "error": str(exc)}


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return numerator / denominator


def _savings(batch_value: int | float, baseline_value: int | float) -> float | None:
    if not baseline_value:
        return None
    return 100 * (1 - batch_value / baseline_value)


def _round_optional(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _make_row(
    size: int,
    batch_result: dict[str, Any],
    per_receipt_results: list[dict[str, Any]],
    chain_root_match: bool | None,
) -> dict[str, Any]:
    success_results = [result for result in per_receipt_results if result.get("status") == 1]
    failed_results = [result for result in per_receipt_results if result.get("status_label") == "failed"]
    batch_gas = int(batch_result.get("gas_used") or 0)
    per_gas = sum(int(result.get("gas_used") or 0) for result in success_results)
    batch_fee = int(batch_result.get("transaction_fee_wei") or 0)
    per_fee = sum(int(result.get("transaction_fee_wei") or 0) for result in success_results)

    return {
        "sessions": size,
        "receipts": size,
        "batch_tx_count": 1 if batch_result.get("transaction_hash") or batch_result.get("status_label") == "dry_run" else 0,
        "per_receipt_tx_count": len(per_receipt_results),
        "batch_total_gas": batch_gas,
        "per_receipt_total_gas": per_gas,
        "batch_gas_per_receipt": _ratio(batch_gas, size),
        "per_receipt_gas_per_receipt": _ratio(per_gas, len(success_results)),
        "batch_total_fee_wei": batch_fee,
        "per_receipt_total_fee_wei": per_fee,
        "batch_fee_per_receipt_wei": _ratio(batch_fee, size),
        "per_receipt_fee_per_receipt_wei": _ratio(per_fee, len(success_results)),
        "gas_reduction_ratio": _round_optional(_ratio(per_gas, batch_gas)),
        "fee_reduction_ratio": _round_optional(_ratio(per_fee, batch_fee)),
        "gas_savings_percent": _round_optional(_savings(batch_gas, per_gas)),
        "fee_savings_percent": _round_optional(_savings(batch_fee, per_fee)),
        "batch_chain_root_match": chain_root_match,
        "per_receipt_success_count": len(success_results),
        "per_receipt_failed_count": len(failed_results),
    }


def _write_summary_md(path: Path, rows: list[dict[str, Any]], dry_run: bool) -> None:
    headers = [
        "Sessions",
        "Batch gas",
        "Per-receipt gas",
        "Gas reduction",
        "Gas savings",
        "Batch fee/receipt (wei)",
        "Per-receipt fee/receipt (wei)",
        "Failures",
    ]
    lines = ["# Anchor Strategy Comparison", ""]
    if dry_run:
        lines.extend([
            "This was a dry run. Blockchain transaction and fee fields are zero or empty.",
            "",
        ])
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        gas_reduction = _display(row.get("gas_reduction_ratio"))
        gas_savings = _display(row.get("gas_savings_percent"))
        lines.append(
            "| {sessions} | {batch_total_gas} | {per_receipt_total_gas} | {gas_reduction} | "
            "{gas_savings} | {batch_fee_per_receipt_wei} | {per_receipt_fee_per_receipt_wei} | "
            "{per_receipt_failed_count} |".format(
                **{key: _display(row.get(key)) for key in SUMMARY_FIELDS},
                gas_reduction=f"{gas_reduction}x" if gas_reduction else "",
                gas_savings=f"{gas_savings}%" if gas_savings else "",
            )
        )
    lines.extend(
        [
            "",
            "Strategy A anchors one Merkle batch root for the workload.",
            "Strategy B reuses `anchorBatch` with a unique prefix per receipt and `receiptCount=1`, storing the receipt hash as the anchored digest.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _generate_figures(rows: list[dict[str, Any]], figures_dir: Path) -> None:
    figures_dir.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib
    except ModuleNotFoundError:
        _generate_basic_png_figures(rows, figures_dir)
        return

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sessions = [row["sessions"] for row in rows]

    def line_plot(filename: str, series: list[tuple[str, list[float]]], ylabel: str, title: str) -> None:
        plt.figure(figsize=(7.2, 4.2))
        for label, values in series:
            plt.plot(sessions, values, marker="o", label=label)
        plt.xlabel("Receipts")
        plt.ylabel(ylabel)
        plt.title(title)
        plt.grid(alpha=0.25)
        if len(series) > 1:
            plt.legend(frameon=False)
        plt.tight_layout()
        plt.savefig(figures_dir / filename, dpi=160)
        plt.close()

    line_plot(
        "gas_batch_vs_per_receipt.png",
        [
            ("Batch", [row["batch_total_gas"] or 0 for row in rows]),
            ("Per receipt", [row["per_receipt_total_gas"] or 0 for row in rows]),
        ],
        "Total gas used",
        "Gas: Batch vs Per-Receipt Anchoring",
    )
    line_plot(
        "fee_batch_vs_per_receipt.png",
        [
            ("Batch", [row["batch_total_fee_wei"] or 0 for row in rows]),
            ("Per receipt", [row["per_receipt_total_fee_wei"] or 0 for row in rows]),
        ],
        "Total fee (wei)",
        "Transaction Fees: Batch vs Per-Receipt Anchoring",
    )
    line_plot(
        "effective_fee_per_receipt.png",
        [
            ("Batch", [row["batch_fee_per_receipt_wei"] or 0 for row in rows]),
            ("Per receipt", [row["per_receipt_fee_per_receipt_wei"] or 0 for row in rows]),
        ],
        "Fee per receipt (wei)",
        "Effective Fee per Receipt",
    )
    line_plot(
        "gas_savings_percent.png",
        [("Gas savings", [row["gas_savings_percent"] or 0 for row in rows])],
        "Savings (%)",
        "Batch Gas Savings vs Per-Receipt Baseline",
    )
    line_plot(
        "fee_savings_percent.png",
        [("Fee savings", [row["fee_savings_percent"] or 0 for row in rows])],
        "Savings (%)",
        "Batch Fee Savings vs Per-Receipt Baseline",
    )


def _generate_basic_png_figures(rows: list[dict[str, Any]], figures_dir: Path) -> None:
    def values(key: str) -> list[float]:
        return [float(row.get(key) or 0) for row in rows]

    sessions = [float(row["sessions"]) for row in rows]
    charts = [
        (
            "gas_batch_vs_per_receipt.png",
            [values("batch_total_gas"), values("per_receipt_total_gas")],
        ),
        (
            "fee_batch_vs_per_receipt.png",
            [values("batch_total_fee_wei"), values("per_receipt_total_fee_wei")],
        ),
        (
            "effective_fee_per_receipt.png",
            [values("batch_fee_per_receipt_wei"), values("per_receipt_fee_per_receipt_wei")],
        ),
        ("gas_savings_percent.png", [values("gas_savings_percent")]),
        ("fee_savings_percent.png", [values("fee_savings_percent")]),
    ]
    for filename, series in charts:
        _write_basic_line_png(figures_dir / filename, sessions, series)


def _write_basic_line_png(path: Path, x_values: list[float], series: list[list[float]]) -> None:
    import struct
    import zlib

    width, height = 960, 560
    left, right, top, bottom = 72, 32, 32, 64
    pixels = bytearray([255] * (width * height * 3))

    def set_pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            pixels[offset : offset + 3] = bytes(color)

    def line(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    set_pixel(x0 + ox, y0 + oy, color)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    all_y = [value for values in series for value in values]
    min_x, max_x = min(x_values), max(x_values)
    min_y, max_y = 0.0, max(all_y) if all_y else 1.0
    if max_x == min_x:
        max_x += 1.0
    if max_y == min_y:
        max_y += 1.0

    def map_x(value: float) -> int:
        return int(left + (value - min_x) / (max_x - min_x) * (width - left - right))

    def map_y(value: float) -> int:
        return int(height - bottom - (value - min_y) / (max_y - min_y) * (height - top - bottom))

    axis = (40, 40, 40)
    grid = (220, 220, 220)
    line(left, top, left, height - bottom, axis)
    line(left, height - bottom, width - right, height - bottom, axis)
    for i in range(1, 5):
        y = top + i * (height - top - bottom) // 5
        line(left, y, width - right, y, grid)

    colors = [(31, 119, 180), (214, 39, 40), (44, 160, 44)]
    for idx, values in enumerate(series):
        points = [(map_x(x), map_y(y)) for x, y in zip(x_values, values)]
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            line(x0, y0, x1, y1, colors[idx % len(colors)])
        for x, y in points:
            line(x - 4, y, x + 4, y, colors[idx % len(colors)])
            line(x, y - 4, x, y + 4, colors[idx % len(colors)])

    raw = b"".join(b"\x00" + pixels[y * width * 3 : (y + 1) * width * 3] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sessions", nargs="+", type=int, required=True, help="One or more workload sizes")
    parser.add_argument("--day", required=True, help="Experiment day in YYYY-MM-DD format")
    parser.add_argument("--seed", type=int, required=True, help="Deterministic workload seed")
    parser.add_argument("--session-type-mode", type=_normalize_mode, default="all")
    parser.add_argument("--output-dir", default="results", help="Base output directory")
    parser.add_argument("--rpc-url", default="http://127.0.0.1:8545")
    parser.add_argument("--private-key", default=None)
    parser.add_argument("--contract-address", default=None)
    parser.add_argument("--deploy-contract", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Generate workload and artifacts without publishing transactions")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if any(size <= 0 for size in args.sessions):
        print("[ERROR] --sessions values must be positive integers", file=sys.stderr)
        return 2
    if args.deploy_contract and args.contract_address:
        print("[ERROR] Use either --deploy-contract or --contract-address, not both", file=sys.stderr)
        return 2
    if not args.dry_run and not (args.deploy_contract or args.contract_address):
        print("[ERROR] Provide --contract-address, --deploy-contract, or use --dry-run", file=sys.stderr)
        return 2

    run_id = f"anchor_strategy_comparison_{_timestamp()}"
    output_dir = Path(args.output_dir) / run_id

    chain_id: int | None = None
    contract_address = args.contract_address
    private_key = args.private_key or ANVIL_PRIVATE_KEY

    if not args.dry_run:
        try:
            chain_id = _read_chain_id(args.rpc_url)
            if args.deploy_contract:
                contract_address = _deploy_contract(args.rpc_url, private_key)
                print(f"[OK] Deployed ProofOfChargeAnchor at {contract_address}")
            _require_tool("cast", "transaction publishing")
        except RuntimeError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 1

    output_dir.mkdir(parents=True, exist_ok=False)
    print(f"[INFO] Writing results to {output_dir}")

    config = BlockchainConfig(
        rpc_url=args.rpc_url,
        contract_address=contract_address,
        private_key=private_key,
        chain_id=chain_id or 31337,
    )

    rows: list[dict[str, Any]] = []
    workload_details: list[dict[str, Any]] = []

    for size in args.sessions:
        workload_prefix = _stable_workload_prefix(args.day, args.seed, args.session_type_mode, size)
        chain_prefix = f"{run_id}-n{size}"
        print(f"[INFO] Building deterministic workload: sessions={size}")
        workload = _build_workload(size, args.day, args.seed, args.session_type_mode, workload_prefix)
        batch_root = _build_batch_root(workload.receipt_hashes)
        local_root_match = _build_batch_root(workload.receipt_hashes) == batch_root

        batch_prefix = f"{chain_prefix}-batch"
        if args.dry_run:
            batch_result = _zero_chain_result()
            chain_root_match: bool | None = None
        else:
            print(f"[INFO] Publishing batch root for sessions={size}")
            batch_result = _safe_publish_one(config, args.day, batch_prefix, batch_root, size)
            if batch_result.get("status") == 1:
                try:
                    chain_anchor = get_anchor(config, args.day, batch_prefix)
                    chain_root_match = chain_anchor.batch_root.lower() == batch_root.lower()
                except Exception:
                    chain_root_match = False
            else:
                chain_root_match = False

        per_receipt_results: list[dict[str, Any]] = []
        if args.dry_run:
            per_receipt_results = [_zero_chain_result() for _ in workload.receipt_hashes]
        else:
            print(f"[INFO] Publishing per-receipt baseline for sessions={size}")
            for index, receipt_hash in enumerate(workload.receipt_hashes, start=1):
                receipt_prefix = f"{chain_prefix}-receipt-{index:04d}"
                result = _safe_publish_one(config, args.day, receipt_prefix, receipt_hash, 1)
                per_receipt_results.append(result)
                if result.get("status_label") == "failed":
                    print(f"[WARN] Per-receipt tx failed for {receipt_prefix}: {result.get('error')}", file=sys.stderr)

        row = _make_row(size, batch_result, per_receipt_results, chain_root_match)
        rows.append(row)
        workload_details.append(
            {
                "sessions": size,
                "workload_prefix": workload_prefix,
                "chain_prefix": chain_prefix,
                "batch_root": batch_root,
                "local_batch_root_match": local_root_match,
                "session_metrics": workload.session_metrics,
                "validation": workload.validation,
                "batch_publish": batch_result,
                "per_receipt_publish": per_receipt_results,
                "receipt_hashes": workload.receipt_hashes,
            }
        )

    _write_csv(output_dir / "summary.csv", rows, SUMMARY_FIELDS)
    _write_summary_md(output_dir / "summary.md", rows, args.dry_run)
    _write_json(output_dir / "metrics.json", {"rows": rows, "workloads": workload_details})
    manifest = {
        "experiment": "anchor_strategy_comparison",
        "created_at": _now_utc().isoformat(),
        "git_commit": _git_commit(),
        "seed": args.seed,
        "day": args.day,
        "workload_sizes": args.sessions,
        "session_type_mode": args.session_type_mode,
        "output_dir": str(output_dir),
        "dry_run": args.dry_run,
        "rpc_url": args.rpc_url,
        "contract_address": contract_address,
        "chain_id": chain_id,
        "argv": _redacted_argv(sys.argv),
        "summary_fields": SUMMARY_FIELDS,
        "strategy_a": "one anchorBatch transaction storing the Merkle batch root for all receipts",
        "strategy_b": "one anchorBatch transaction per receipt, storing the receipt hash with receiptCount=1",
    }
    _write_json(output_dir / "manifest.json", manifest)
    _generate_figures(rows, output_dir / "figures")

    print(f"[OK] Wrote {output_dir / 'summary.csv'}")
    print(f"[OK] Wrote {output_dir / 'summary.md'}")
    print(f"[OK] Wrote {output_dir / 'metrics.json'}")
    print(f"[OK] Wrote {output_dir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
