"""Verify persisted batch anchors against the configured blockchain."""

from __future__ import annotations

import argparse
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from src import db
from src.models import BatchAnchor
from src.repository import persist_verification_result

from .cast_client import OnChainAnchor, get_anchor
from .config import BlockchainConfig, load_blockchain_config


AnchorReader = Callable[[BlockchainConfig, str, str | None], OnChainAnchor]


def verify_on_chain_anchor(
    day: str,
    session_prefix: str | None = None,
    *,
    db_session: Session | None = None,
    config: BlockchainConfig | None = None,
    reader: AnchorReader = get_anchor,
    persist_result: bool = True,
) -> dict[str, Any]:
    owns_session = db_session is None
    session = db_session or db.session_scope()
    chain_config = config or load_blockchain_config()

    try:
        anchor = _find_latest_anchor(session, day=day, session_prefix=session_prefix)
        if not anchor:
            suffix = f" with prefix {session_prefix}" if session_prefix else ""
            raise ValueError(f"No DB batch anchor found for day {day}{suffix}")

        result = _verify_anchor_row(anchor=anchor, chain_config=chain_config, reader=reader)
        if persist_result:
            persist_verification_result(result, verification_type="on_chain_batch", db_session=session)
            if owns_session:
                session.commit()
        return result
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def verify_on_chain_anchor_by_id(
    anchor_id: int,
    *,
    db_session: Session | None = None,
    config: BlockchainConfig | None = None,
    reader: AnchorReader = get_anchor,
    persist_result: bool = True,
) -> dict[str, Any]:
    owns_session = db_session is None
    session = db_session or db.session_scope()
    chain_config = config or load_blockchain_config()

    try:
        anchor = session.get(BatchAnchor, anchor_id)
        if not anchor:
            raise ValueError(f"No DB batch anchor found for id {anchor_id}")

        result = _verify_anchor_row(anchor=anchor, chain_config=chain_config, reader=reader)
        if persist_result:
            persist_verification_result(result, verification_type="on_chain_batch", db_session=session)
            if owns_session:
                session.commit()
        return result
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def _find_latest_anchor(
    session: Session,
    day: str,
    session_prefix: str | None = None,
) -> BatchAnchor | None:
    normalized_prefix = session_prefix or ""
    stmt = (
        select(BatchAnchor)
        .where(BatchAnchor.day == day)
        .where(BatchAnchor.session_prefix == normalized_prefix)
        .order_by(BatchAnchor.id.desc())
    )
    return session.scalar(stmt)


def _verify_anchor_row(
    anchor: BatchAnchor,
    chain_config: BlockchainConfig,
    reader: AnchorReader,
) -> dict[str, Any]:
    on_chain = reader(chain_config, anchor.day, anchor.session_prefix)
    return {
        "anchor_id": anchor.id,
        "day": anchor.day,
        "session_prefix": anchor.session_prefix,
        "expected_root": anchor.batch_root,
        "commitment_profile": anchor.commitment_profile,
        "computed_root": on_chain.batch_root,
        "expected_receipt_count": anchor.receipt_count,
        "on_chain_receipt_count": on_chain.receipt_count,
        "chain_tx": anchor.chain_tx,
        "operator": on_chain.operator,
        "on_chain_timestamp": on_chain.timestamp,
        "match": (
            anchor.batch_root.lower() == on_chain.batch_root.lower()
            and anchor.receipt_count == on_chain.receipt_count
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a DB batch anchor against the blockchain.")
    parser.add_argument("day", help="Day in YYYY-MM-DD format")
    parser.add_argument("--prefix", help="Session prefix used when creating the DB anchor")
    args = parser.parse_args()

    result = verify_on_chain_anchor(args.day, session_prefix=args.prefix)
    if result["match"]:
        print(
            f"[OK] day={result['day']} on-chain root matches "
            f"({result['expected_receipt_count']} receipts)"
        )
        return 0

    print(
        f"[FAIL] day={result['day']} db_root={result['expected_root']} "
        f"on_chain_root={result['computed_root']}"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
