#!/usr/bin/env python3
"""CLI for the DB-backed seven-scenario tamper matrix."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db
from src.run_experiment import run_experiment
from src.tamper_scenarios import SCENARIOS, run_tamper_matrix


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute rollback-isolated DB tamper scenarios.")
    parser.add_argument("--run-id", default="reviewer4_tamper_matrix")
    parser.add_argument("--day", required=True)
    parser.add_argument("--prefix")
    parser.add_argument("--session-id")
    parser.add_argument("--anchor-id", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--generate-baseline", action="store_true")
    parser.add_argument("--publish-chain", action="store_true")
    parser.add_argument("--require-chain", action="store_true")
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--keep-baseline-data", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    db.require_database()
    if args.require_chain and not args.publish_chain:
        parser.error("--require-chain requires --publish-chain")
    prefix = args.prefix or args.run_id
    session_id = args.session_id
    if args.generate_baseline:
        metrics = run_experiment(
            num_sessions=3,
            day=args.day,
            seed=args.seed,
            run_id=prefix,
            skip_figures=True,
            publish_chain=args.publish_chain,
        )
        session_id = session_id or f"{prefix}-0001"
        if args.verbose:
            print(json.dumps(metrics, indent=2, sort_keys=True))
    if not session_id:
        parser.error("--session-id is required unless --generate-baseline selects the first session")
    output = args.output_dir or ROOT / "results" / args.run_id / "tamper_matrix"
    session = db.session_scope()
    try:
        records, summary = run_tamper_matrix(
            db_session=session,
            day=args.day,
            session_prefix=prefix,
            session_id=session_id,
            anchor_id=args.anchor_id,
            scenario_ids=args.scenario,
            output_dir=output,
            run_id=args.run_id,
            command_line_arguments=sys.argv,
        )
    finally:
        session.close()
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"Artifacts: {output}")
    return 0 if summary["scenarios_meeting_expected_behavior"] == summary["scenarios_executed"] and not summary["restoration_failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
