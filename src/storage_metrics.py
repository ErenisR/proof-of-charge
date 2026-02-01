# src/storage_metrics.py
from pathlib import Path
import os

from .storage import BASE_DIR, RECEIPTS_DIR

ANCHORS_FILE = BASE_DIR / "anchors.json"
INDEX_FILE = RECEIPTS_DIR / "index.json"


def sizeof_fmt(num: int, suffix="B") -> str:
    for unit in ["", "K", "M", "G", "T", "P"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Y{suffix}"


def dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            fp = Path(root) / name
            total += fp.stat().st_size
    return total


if __name__ == "__main__":
    total_receipts_size = dir_size(RECEIPTS_DIR)
    anchors_size = ANCHORS_FILE.stat().st_size if ANCHORS_FILE.exists() else 0
    index_size = INDEX_FILE.stat().st_size if INDEX_FILE.exists() else 0

    print(f"Receipts dir: {sizeof_fmt(total_receipts_size)}")
    print(f"  - index.json: {sizeof_fmt(index_size)}")
    print(f"anchors.json: {sizeof_fmt(anchors_size)}")
    print(f"Total (receipts + anchors): {sizeof_fmt(total_receipts_size + anchors_size)}")
