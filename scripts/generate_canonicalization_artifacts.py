#!/usr/bin/env python3
"""Execute Python/JavaScript conformance vectors and write review artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.receipt_canonicalization import canonicalize_receipt, hash_canonical_receipt

DEFAULT_VECTORS = ROOT / "tests" / "fixtures" / "canonicalization_vectors.json"
DEFAULT_OUTPUT = ROOT / "results" / "reviewer3_canonicalization"
JS_IMPLEMENTATION = ROOT / "scripts" / "receipt_canonicalization.mjs"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", type=Path, default=DEFAULT_VECTORS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    vector_document = json.loads(args.vectors.read_text(encoding="utf-8"))
    profile = vector_document["profile"]
    rows: list[dict[str, Any]] = []
    for vector in vector_document["vectors"]:
        python_bytes = canonicalize_receipt(vector["receipt"], profile)
        python_hash = hash_canonical_receipt(vector["receipt"], profile)
        javascript = _run_javascript(vector["receipt"])
        expected_bytes = vector["canonical_text"].encode("utf-8")
        rows.append(
            {
                "vector_name": vector["name"],
                "profile": profile,
                "canonical_byte_length": len(python_bytes),
                "expected_hash": vector["sha256"],
                "python_hash": python_hash,
                "javascript_hash": javascript["hash"],
                "python_expected_bytes_match": python_bytes == expected_bytes,
                "javascript_expected_bytes_match": javascript["canonical_text"].encode("utf-8") == expected_bytes,
                "cross_language_bytes_match": javascript["canonical_text"].encode("utf-8") == python_bytes,
                "cross_language_hash_match": javascript["hash"] == python_hash,
                "utf8_without_bom": not python_bytes.startswith(b"\xef\xbb\xbf"),
                "no_trailing_newline": not python_bytes.endswith((b"\n", b"\r")),
                "no_insignificant_whitespace": python_bytes == vector["canonical_text"].encode("utf-8"),
                "status": "pass",
            }
        )
    for row in rows:
        checks = [value for key, value in row.items() if key.endswith("_match") or key in {"utf8_without_bom", "no_trailing_newline", "no_insignificant_whitespace"}]
        row["status"] = "pass" if all(checks) else "fail"

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    overall = bool(rows) and all(row["status"] == "pass" for row in rows)
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    summary = {
        "canonicalization_profile": profile,
        "hash_algorithm": "sha-256",
        "external_hash_format": "0x-prefixed lowercase hexadecimal",
        "vectors_defined": len(rows),
        "vectors_executed": len(rows),
        "vectors_passed": sum(row["status"] == "pass" for row in rows),
        "vectors_failed": sum(row["status"] != "pass" for row in rows),
        "python_implementation_executed": True,
        "javascript_implementation_executed": True,
        "all_canonical_bytes_match": all(row["cross_language_bytes_match"] for row in rows),
        "all_hashes_match": all(row["cross_language_hash_match"] for row in rows),
        "validation_passed": overall,
        "generated_at_utc": generated_at,
        "vector_results": rows,
    }
    _write_json(output_dir / "canonicalization_summary.json", summary)
    _write_csv(output_dir / "canonicalization_vectors.csv", rows)
    (output_dir / "canonicalization_report.md").write_text(
        _report(summary, rows, args.vectors), encoding="utf-8"
    )
    manifest = {
        "artifact_type": "cross-language canonical receipt conformance validation",
        "generated_at_utc": generated_at,
        "source_commit_sha": _command(["git", "rev-parse", "HEAD"]),
        "dirty_worktree_at_generation": bool(_command(["git", "status", "--porcelain"])),
        "python_version": platform.python_version(),
        "node_version": _command(["node", "--version"]),
        "operating_system": platform.platform(),
        "command": " ".join(sys.argv),
        "canonicalization_profile": profile,
        "hash_algorithm": "sha-256",
        "inputs": {
            "vectors": str(args.vectors.relative_to(ROOT)),
            "vectors_sha256": _file_sha256(args.vectors),
            "python_implementation": "src/receipt_canonicalization.py",
            "python_implementation_sha256": _file_sha256(ROOT / "src/receipt_canonicalization.py"),
            "javascript_implementation": "scripts/receipt_canonicalization.mjs",
            "javascript_implementation_sha256": _file_sha256(JS_IMPLEMENTATION),
        },
        "artifacts": {
            name: {"sha256": _file_sha256(output_dir / name)}
            for name in (
                "canonicalization_summary.json",
                "canonicalization_vectors.csv",
                "canonicalization_report.md",
            )
        },
        "validation_passed": overall,
    }
    _write_json(output_dir / "manifest.json", manifest)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if overall else 1


def _run_javascript(receipt: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="poc-c14n-") as temp_dir:
        path = Path(temp_dir) / "receipt.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        completed = subprocess.run(
            ["node", str(JS_IMPLEMENTATION), str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    return json.loads(completed.stdout)


def _report(summary: dict[str, Any], rows: list[dict[str, Any]], vectors_path: Path) -> str:
    lines = [
        "# Canonical receipt cross-language validation",
        "",
        f"Profile: `{summary['canonicalization_profile']}`  ",
        f"Hash algorithm: `{summary['hash_algorithm']}`  ",
        f"Executed: `{summary['generated_at_utc']}`",
        "",
        "## Experimental result",
        "",
        "| Vector | Bytes | Python hash | JavaScript hash | Bytes identical | Hash identical | Status |",
        "|---|---:|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['vector_name']} | {row['canonical_byte_length']} | `{row['python_hash']}` | "
            f"`{row['javascript_hash']}` | {row['cross_language_bytes_match']} | "
            f"{row['cross_language_hash_match']} | {row['status']} |"
        )
    lines += [
        "",
        f"Overall validation: **{'PASS' if summary['validation_passed'] else 'FAIL'}**.",
        "",
        "The Python and JavaScript implementations were both executed against the frozen input in "
        f"`{vectors_path.relative_to(ROOT)}`. The comparison covers the exact UTF-8 byte sequence and "
        "the resulting SHA-256 digest; it is not inferred from source inspection.",
        "",
        "## Scope",
        "",
        "The vector exercises recursive object ordering, semantically significant array order, NFC "
        "Unicode normalization, equivalent timestamp offsets, fixed six-digit UTC timestamps, decimal "
        "ROUND_HALF_UP behavior, fixed energy/price/money scales, and negative-zero normalization. "
        "Automated pytest cases separately exercise rejection of nulls, missing and extra fields, naive "
        "timestamps, non-finite values, duplicate JSON keys, and unknown profiles.",
        "",
        "This validates conformance for the committed profile and vectors. It is not a claim that arbitrary "
        "third-party implementations conform without running the same vectors.",
    ]
    return "\n".join(lines) + "\n"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
