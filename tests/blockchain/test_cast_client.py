import subprocess

import pytest

from src.blockchain.cast_client import (
    OnChainAnchor,
    anchor_batch,
    get_anchor,
    normalize_bytes32,
    parse_get_anchor_output,
    parse_transaction_hash,
)
from src.blockchain.config import BlockchainConfig


ROOT = "0x" + "a" * 64
TX_HASH = "0x" + "b" * 64


def test_normalize_bytes32_accepts_prefixed_and_unprefixed_values():
    assert normalize_bytes32(ROOT.upper()) == ROOT
    assert normalize_bytes32("a" * 64) == ROOT


def test_normalize_bytes32_rejects_invalid_values():
    with pytest.raises(ValueError):
        normalize_bytes32("0xabc")


def test_parse_transaction_hash_from_json():
    assert parse_transaction_hash(f'{{"transactionHash":"{TX_HASH}"}}') == TX_HASH


def test_parse_transaction_hash_from_text():
    assert parse_transaction_hash(f"transactionHash {TX_HASH}") == TX_HASH


def test_parse_get_anchor_output():
    output = f"""
    {ROOT}
    2
    0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
    1780000000
    """

    anchor = parse_get_anchor_output(output)

    assert anchor == OnChainAnchor(
        batch_root=ROOT,
        receipt_count=2,
        operator="0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        timestamp=1780000000,
    )


def test_anchor_batch_builds_cast_send_command():
    commands = []
    config = BlockchainConfig(
        rpc_url="http://127.0.0.1:8545",
        contract_address="0x0000000000000000000000000000000000000001",
        private_key="0xabc",
        chain_id=31337,
    )

    def runner(command):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=f'{{"transactionHash":"{TX_HASH}"}}', stderr="")

    assert anchor_batch(config, "2026-03-07", "run-test", ROOT, 2, runner=runner) == TX_HASH

    assert commands == [
        [
            "cast",
            "send",
            "0x0000000000000000000000000000000000000001",
            "anchorBatch(string,string,bytes32,uint256)",
            "2026-03-07",
            "run-test",
            ROOT,
            "2",
            "--rpc-url",
            "http://127.0.0.1:8545",
            "--private-key",
            "0xabc",
            "--json",
        ]
    ]


def test_get_anchor_builds_cast_call_command():
    commands = []
    config = BlockchainConfig(
        rpc_url="http://127.0.0.1:8545",
        contract_address="0x0000000000000000000000000000000000000001",
        private_key=None,
        chain_id=31337,
    )

    def runner(command):
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{ROOT}\n2\n0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266\n1780000000\n",
            stderr="",
        )

    anchor = get_anchor(config, "2026-03-07", None, runner=runner)

    assert anchor.batch_root == ROOT
    assert commands == [
        [
            "cast",
            "call",
            "0x0000000000000000000000000000000000000001",
            "getAnchor(string,string)(bytes32,uint256,address,uint256)",
            "2026-03-07",
            "",
            "--rpc-url",
            "http://127.0.0.1:8545",
        ]
    ]

