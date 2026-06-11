"""Configuration helpers for blockchain anchoring."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class BlockchainConfig:
    rpc_url: str
    contract_address: str | None
    private_key: str | None
    chain_id: int


def load_blockchain_config() -> BlockchainConfig:
    """Load blockchain configuration from environment variables."""
    chain_id = int(os.getenv("CHAIN_ID", "31337"))
    return BlockchainConfig(
        rpc_url=os.getenv("WEB3_RPC_URL", "http://127.0.0.1:8545"),
        contract_address=os.getenv("ANCHOR_CONTRACT_ADDRESS"),
        private_key=os.getenv("ANCHOR_PRIVATE_KEY"),
        chain_id=chain_id,
    )

