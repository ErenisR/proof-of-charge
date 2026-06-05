import argparse
import json
from pathlib import Path
from typing import Any, Dict

from . import db
from .batch_anchoring import ANCHORS_FILE, _build_anchor_memberships
from .repository import persist_batch_anchor, persist_finalized_session
from .storage import INDEX_FILE, load_index


def import_local_receipts(index_path: Path = INDEX_FILE) -> Dict[str, int]:
    index = _load_index(index_path)
    session = db.session_scope()
    imported = 0
    skipped = 0

    try:
        for session_id, entry in sorted(index.items()):
            receipt_path = Path(entry.get("file", ""))
            if not receipt_path.exists():
                skipped += 1
                continue
            payload = _load_json(receipt_path)
            receipt = payload.get("receipt")
            session_payload = payload.get("session")
            receipt_hash = payload.get("hash") or entry.get("hash")
            if not receipt or not session_payload or not receipt_hash:
                skipped += 1
                continue
            persist_finalized_session(
                session_payload,
                receipt,
                receipt_hash,
                db_session=session,
            )
            imported += 1

        session.commit()
        return {"imported": imported, "skipped": skipped}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def import_local_anchors(
    index_path: Path = INDEX_FILE,
    anchors_path: Path = ANCHORS_FILE,
) -> Dict[str, int]:
    if not anchors_path.exists():
        return {"imported": 0, "skipped": 0}

    index = _load_index(index_path)
    anchors = _load_json(anchors_path).get("batches", [])
    session = db.session_scope()
    imported = 0
    skipped = 0

    try:
        for anchor in anchors:
            day = anchor.get("day")
            batch_root = anchor.get("batch_root")
            session_ids = anchor.get("session_ids") or []
            pairs = [
                (session_id, index[session_id]["hash"])
                for session_id in session_ids
                if session_id in index and index[session_id].get("hash")
            ]
            if not day or not batch_root or not pairs:
                skipped += 1
                continue

            imported_session_ids = [session_id for session_id, _ in pairs]
            receipt_hashes = [receipt_hash for _, receipt_hash in pairs]
            memberships = _build_anchor_memberships(imported_session_ids, receipt_hashes)
            persist_batch_anchor(
                day=day,
                session_prefix=anchor.get("session_prefix"),
                batch_root=batch_root,
                receipt_count=len(imported_session_ids),
                receipt_memberships=memberships,
                chain_tx=anchor.get("chain_tx"),
                cid=anchor.get("cid"),
                db_session=session,
            )
            imported += 1

        session.commit()
        return {"imported": imported, "skipped": skipped}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _load_index(index_path: Path) -> Dict[str, Any]:
    if index_path == INDEX_FILE:
        return load_index()
    if not index_path.exists():
        return {}
    return _load_json(index_path)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill local receipt JSON files and anchors into Postgres."
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=INDEX_FILE,
        help="Path to local receipts index.json.",
    )
    parser.add_argument(
        "--skip-anchors",
        action="store_true",
        help="Only import receipts; do not import local anchors.",
    )
    args = parser.parse_args()

    receipt_result = import_local_receipts(index_path=args.index)
    print(
        "[OK] receipts "
        f"imported={receipt_result['imported']} skipped={receipt_result['skipped']}"
    )

    if not args.skip_anchors:
        anchor_result = import_local_anchors(index_path=args.index)
        print(
            "[OK] anchors "
            f"imported={anchor_result['imported']} skipped={anchor_result['skipped']}"
        )
