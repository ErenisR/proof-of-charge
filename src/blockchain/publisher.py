"""Publish persisted batch anchors to the configured blockchain."""

from __future__ import annotations

import argparse
from typing import Any, Callable
from contextlib import nullcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from src import db
from src.models import BatchAnchor

from .cast_client import TransactionReceipt, anchor_batch, get_transaction_receipt
from .config import BlockchainConfig, load_blockchain_config
from src.performance_timing import TimingRecorder


AnchorSender = Callable[[BlockchainConfig, str, str | None, str, int], str]
ReceiptReader = Callable[[BlockchainConfig, str], TransactionReceipt]


def publish_batch_anchor(
    day: str,
    session_prefix: str | None = None,
    *,
    force: bool = False,
    db_session: Session | None = None,
    config: BlockchainConfig | None = None,
    sender: AnchorSender = anchor_batch,
    receipt_reader: ReceiptReader = get_transaction_receipt,
    timing_recorder: TimingRecorder | None = None,
) -> dict[str, Any]:
    owns_session = db_session is None
    session = db_session or db.session_scope()
    chain_config = config or load_blockchain_config()

    try:
        anchor = _find_latest_anchor(session, day=day, session_prefix=session_prefix)
        if not anchor:
            suffix = f" with prefix {session_prefix}" if session_prefix else ""
            raise ValueError(f"No DB batch anchor found for day {day}{suffix}")
        if anchor.chain_tx and not force:
            raise ValueError(
                f"Anchor {anchor.id} already has chain_tx={anchor.chain_tx}. "
                "Use --force to republish."
            )

        result = _publish_anchor_row(
            session=session,
            anchor=anchor,
            chain_config=chain_config,
            sender=sender,
            receipt_reader=receipt_reader,
            timing_recorder=timing_recorder,
        )
        measure = timing_recorder.measure("chain_anchor_database_update", anchor_id=result["anchor_id"]) if timing_recorder else nullcontext()
        with measure:
            session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def publish_batch_anchor_by_id(
    anchor_id: int,
    *,
    force: bool = False,
    db_session: Session | None = None,
    config: BlockchainConfig | None = None,
    sender: AnchorSender = anchor_batch,
    receipt_reader: ReceiptReader = get_transaction_receipt,
) -> dict[str, Any]:
    owns_session = db_session is None
    session = db_session or db.session_scope()
    chain_config = config or load_blockchain_config()

    try:
        anchor = session.get(BatchAnchor, anchor_id)
        if not anchor:
            raise ValueError(f"No DB batch anchor found for id {anchor_id}")
        if anchor.chain_tx and not force:
            raise ValueError(
                f"Anchor {anchor.id} already has chain_tx={anchor.chain_tx}. "
                "Use force=true to republish."
            )

        result = _publish_anchor_row(
            session=session,
            anchor=anchor,
            chain_config=chain_config,
            sender=sender,
            receipt_reader=receipt_reader,
        )
        session.commit()
        return result
    except Exception:
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


def _publish_anchor_row(
    session: Session,
    anchor: BatchAnchor,
    chain_config: BlockchainConfig,
    sender: AnchorSender,
    receipt_reader: ReceiptReader,
    timing_recorder: TimingRecorder | None = None,
) -> dict[str, Any]:
    measure = lambda stage: timing_recorder.measure(stage, anchor_id=anchor.id) if timing_recorder else nullcontext()
    with measure("chain_publication_total"):
        with measure("chain_send_command"):
            tx_hash = sender(chain_config, anchor.day, anchor.session_prefix, anchor.batch_root, anchor.receipt_count)
        with measure("chain_receipt_query"):
            receipt = receipt_reader(chain_config, tx_hash)
        anchor.chain_tx = tx_hash
        session.flush()
    return {
        "anchor_id": anchor.id,
        "day": anchor.day,
        "session_prefix": anchor.session_prefix,
        "batch_root": anchor.batch_root,
        "commitment_profile": anchor.commitment_profile,
        "receipt_count": anchor.receipt_count,
        "chain_tx": tx_hash,
        "chain_block_number": receipt.block_number,
        "chain_block_timestamp": receipt.block_timestamp,
        "chain_gas_used": receipt.gas_used,
        "chain_effective_gas_price_wei": receipt.effective_gas_price,
        "chain_transaction_fee_wei": receipt.transaction_fee_wei,
        "chain_status": receipt.status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a DB batch anchor on-chain.")
    parser.add_argument("day", help="Day in YYYY-MM-DD format")
    parser.add_argument("--prefix", help="Session prefix used when creating the DB anchor")
    parser.add_argument("--force", action="store_true", help="Republish even if chain_tx is already set")
    args = parser.parse_args()

    result = publish_batch_anchor(args.day, session_prefix=args.prefix, force=args.force)
    print(
        f"[OK] anchor_id={result['anchor_id']} day={result['day']} "
        f"tx={result['chain_tx']} gas={result['chain_gas_used']} "
        f"fee_wei={result['chain_transaction_fee_wei']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
