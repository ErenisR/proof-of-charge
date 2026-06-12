from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.blockchain.cast_client import OnChainAnchor, TransactionReceipt
from src.blockchain.config import BlockchainConfig
from src.blockchain.publisher import publish_batch_anchor
from src.blockchain.verifier import verify_on_chain_anchor
from src.models import Base, BatchAnchor, Verification
from src.repository import persist_batch_anchor


ROOT = "0x" + "a" * 64
TX_HASH = "0x" + "b" * 64


def _config() -> BlockchainConfig:
    return BlockchainConfig(
        rpc_url="http://127.0.0.1:8545",
        contract_address="0x0000000000000000000000000000000000000001",
        private_key="0xabc",
        chain_id=31337,
    )


def test_publish_batch_anchor_updates_chain_tx():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    calls = []
    receipt_calls = []

    def sender(config, day, session_prefix, batch_root, receipt_count):
        calls.append((config, day, session_prefix, batch_root, receipt_count))
        return TX_HASH

    def receipt_reader(config, tx_hash):
        receipt_calls.append((config, tx_hash))
        return TransactionReceipt(
            transaction_hash=tx_hash,
            block_number=2,
            block_timestamp=1780000000,
            gas_used=142711,
            effective_gas_price=880760868,
            transaction_fee_wei=142711 * 880760868,
            status=1,
        )

    with Session(engine) as db_session:
        persist_batch_anchor(
            day="2026-03-07",
            session_prefix="run-test",
            batch_root=ROOT,
            receipt_count=2,
            db_session=db_session,
        )
        db_session.commit()

        result = publish_batch_anchor(
            "2026-03-07",
            session_prefix="run-test",
            db_session=db_session,
            config=_config(),
            sender=sender,
            receipt_reader=receipt_reader,
        )

        anchor = db_session.query(BatchAnchor).one()

    assert result["chain_tx"] == TX_HASH
    assert anchor.chain_tx == TX_HASH
    assert result["chain_gas_used"] == 142711
    assert result["chain_transaction_fee_wei"] == 142711 * 880760868
    assert calls == [(_config(), "2026-03-07", "run-test", ROOT, 2)]
    assert receipt_calls == [(_config(), TX_HASH)]


def test_verify_on_chain_anchor_persists_match_result():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    def reader(config, day, session_prefix):
        return OnChainAnchor(
            batch_root=ROOT,
            receipt_count=2,
            operator="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
            timestamp=1780000000,
        )

    with Session(engine) as db_session:
        persist_batch_anchor(
            day="2026-03-07",
            session_prefix=None,
            batch_root=ROOT,
            receipt_count=2,
            db_session=db_session,
        )
        db_session.commit()

        result = verify_on_chain_anchor(
            "2026-03-07",
            db_session=db_session,
            config=_config(),
            reader=reader,
        )

        verification = db_session.query(Verification).one()

    assert result["match"] is True
    assert result["computed_root"] == ROOT
    assert result["on_chain_receipt_count"] == 2
    assert verification.verification_type == "on_chain_batch"
    assert verification.match is True
    assert verification.details_json["operator"] == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
