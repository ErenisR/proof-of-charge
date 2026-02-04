import csv
import os
import sys
import subprocess
from pathlib import Path
from typing import Dict, Any

from .synthetic_sessions import run_synthetic_sessions
from .verifier_batch import verify_day
from .storage import BASE_DIR, RECEIPTS_DIR
from .batch_anchoring import ANCHORS_FILE

RESULTS_DIR = BASE_DIR / "results"
METRICS_CSV = RESULTS_DIR / "metrics.csv"

def _ensure_results_dir_and_header() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    if not METRICS_CSV.exists():
        with METRICS_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "run_id",
                "num_sessions",
                "day",
                "t_finalize_total",
                "t_finalize_avg",
                "batch_root_match",
                "receipt_count",
                "receipts_bytes",
                "anchors_bytes",
                "total_bytes",
            ])
            
def _dir_size_bytes(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            fp = Path(root) / name
            total += fp.stat().st_size
    return total

def _run_batch_anchoring_cli(day: str) -> None:
    cmd = [sys.executable, "-m", "src.batch_anchoring", day]
    print(f"[run_experiment] Running batch anchoring: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[Error] batch anchoring failed")
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(1)
    else:
        print(result.stdout.strip())
        
def run_experiment(num_sessions: int, day: str, run_id: str | None = None) -> Dict[str, Any]:
    if run_id is None:
        run_id = f"run_{num_sessions}_{day}"
        
    _ensure_results_dir_and_header()
    
    print(f"[run_experiment] Starting experiment: run_id={run_id}, num_sessions={num_sessions}, day={day}")
    
    synth_stats = run_synthetic_sessions(num_sessions)
    print(
        f"[run_experiment] Finalized {synth_stats['num_sessions']} sessions "
        f"in {synth_stats['t_finalize_total']:.3f}s "
        f"(avg {synth_stats['t_finalize_avg']:.6f}s)"
    )
    
    _run_batch_anchoring_cli(day)
    
    verify_result = verify_day(day)
    match = verify_result["match"]
    receipt_count = verify_result["receipt_count"]
    print(
        f"[run_experiment] Batch verify day={day}: match={match}, "
        f"receipt_count={receipt_count}"
    )
    
    receipt_size = _dir_size_bytes(RECEIPTS_DIR)
    anchors_size = ANCHORS_FILE.stat().st_size if ANCHORS_FILE.exists() else 0
    total_bytes = receipt_size + anchors_size
    
    print(f"[run_experiment] Storage: receipts={receipt_size} B, anchors={anchors_size} B")
    
    with METRICS_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            run_id,
            num_sessions,
            day,
            synth_stats["t_finalize_total"],
            synth_stats["t_finalize_avg"],
            match,
            receipt_count,
            receipt_size,
            anchors_size,
            total_bytes,
        ])
        
    return {
        "run_id": run_id,
        "num_sessions": num_sessions,
        "day": day,
        "t_finalize_total": synth_stats["t_finalize_total"],
        "t_finalize_avg": synth_stats["t_finalize_avg"],
        "batch_root_match": match,
        "receipt_count": receipt_count,
        "receipts_bytes": receipt_size,
        "anchors_bytes": anchors_size,
        "total_bytes": total_bytes
    }
    
    
if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m src.run_experiment <num_sessions> <YYYY-MM-DD>")
        sys.exit(1)

    n = int(sys.argv[1])
    day_str = sys.argv[2]

    stats = run_experiment(n, day_str)
    print(f"\nExperiment completed. Results:\n{stats}")
    for key, value in stats.items():
        print(f"  {key}: {value}")
        
