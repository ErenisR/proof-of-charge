"""Small Foundry cast wrapper for the anchor contract."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
from typing import Callable

from .config import BlockchainConfig


ANCHOR_BATCH_SIG = "anchorBatch(string,string,bytes32,uint256)"
GET_ANCHOR_SIG = "getAnchor(string,string)(bytes32,uint256,address,uint256)"


CastRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class OnChainAnchor:
    batch_root: str
    receipt_count: int
    operator: str
    timestamp: int


@dataclass(frozen=True)
class TransactionReceipt:
    transaction_hash: str
    block_number: int
    block_timestamp: int | None
    gas_used: int
    effective_gas_price: int
    transaction_fee_wei: int
    status: int


def default_cast_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def anchor_batch(
    config: BlockchainConfig,
    day: str,
    session_prefix: str | None,
    batch_root: str,
    receipt_count: int,
    runner: CastRunner = default_cast_runner,
) -> str:
    if not config.contract_address:
        raise ValueError("ANCHOR_CONTRACT_ADDRESS is not set")
    if not config.private_key:
        raise ValueError("ANCHOR_PRIVATE_KEY is not set")

    command = [
        "cast",
        "send",
        config.contract_address,
        ANCHOR_BATCH_SIG,
        day,
        session_prefix or "",
        normalize_bytes32(batch_root),
        str(receipt_count),
        "--rpc-url",
        config.rpc_url,
        "--private-key",
        config.private_key,
        "--json",
    ]
    result = runner(command)
    return parse_transaction_hash(_combined_output(result))


def get_anchor(
    config: BlockchainConfig,
    day: str,
    session_prefix: str | None,
    runner: CastRunner = default_cast_runner,
) -> OnChainAnchor:
    if not config.contract_address:
        raise ValueError("ANCHOR_CONTRACT_ADDRESS is not set")

    command = [
        "cast",
        "call",
        config.contract_address,
        GET_ANCHOR_SIG,
        day,
        session_prefix or "",
        "--rpc-url",
        config.rpc_url,
    ]
    result = runner(command)
    return parse_get_anchor_output(_combined_output(result))


def get_transaction_receipt(
    config: BlockchainConfig,
    tx_hash: str,
    runner: CastRunner = default_cast_runner,
) -> TransactionReceipt:
    command = [
        "cast",
        "receipt",
        tx_hash,
        "--rpc-url",
        config.rpc_url,
        "--json",
    ]
    result = runner(command)
    return parse_transaction_receipt(_combined_output(result))


def normalize_bytes32(value: str) -> str:
    normalized = value.lower()
    if normalized.startswith("0x"):
        normalized = normalized[2:]
    if len(normalized) != 64 or not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError(f"Expected 32-byte hex value, got {value!r}")
    return "0x" + normalized


def parse_transaction_hash(output: str) -> str:
    stripped = output.strip()
    if not stripped:
        raise ValueError("Empty cast output")

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        for key in ("transactionHash", "transaction_hash", "hash"):
            value = payload.get(key)
            if isinstance(value, str) and _is_tx_hash(value):
                return value

    match = re.search(r"0x[a-fA-F0-9]{64}", stripped)
    if match:
        return match.group(0)

    raise ValueError("Could not find transaction hash in cast output")


def parse_get_anchor_output(output: str) -> OnChainAnchor:
    values = re.findall(r"0x[a-fA-F0-9]{64}|0x[a-fA-F0-9]{40}|\b\d+\b", output)
    if len(values) < 4:
        raise ValueError("Could not parse getAnchor output")

    batch_root = normalize_bytes32(values[0])
    receipt_count = int(values[1])
    operator = values[2]
    timestamp = int(values[3])
    return OnChainAnchor(
        batch_root=batch_root,
        receipt_count=receipt_count,
        operator=operator,
        timestamp=timestamp,
    )


def parse_transaction_receipt(output: str) -> TransactionReceipt:
    try:
        payload = json.loads(output.strip())
    except json.JSONDecodeError as exc:
        raise ValueError("Could not parse transaction receipt JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Transaction receipt JSON must be an object")

    transaction_hash = payload.get("transactionHash")
    if not isinstance(transaction_hash, str) or not _is_tx_hash(transaction_hash):
        raise ValueError("Transaction receipt is missing transactionHash")

    gas_used = _parse_int_field(payload, "gasUsed")
    effective_gas_price = _parse_int_field(payload, "effectiveGasPrice")
    block_number = _parse_int_field(payload, "blockNumber")
    status = _parse_int_field(payload, "status")
    block_timestamp = payload.get("blockTimestamp")
    if block_timestamp is not None:
        block_timestamp = _parse_int(block_timestamp)

    return TransactionReceipt(
        transaction_hash=transaction_hash,
        block_number=block_number,
        block_timestamp=block_timestamp,
        gas_used=gas_used,
        effective_gas_price=effective_gas_price,
        transaction_fee_wei=gas_used * effective_gas_price,
        status=status,
    )


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _is_tx_hash(value: str) -> bool:
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{64}", value))


def _parse_int_field(payload: dict, key: str) -> int:
    if key not in payload:
        raise ValueError(f"Transaction receipt is missing {key}")
    return _parse_int(payload[key])


def _parse_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    raise ValueError(f"Expected integer-compatible value, got {value!r}")
