"""Deploy the ProofOfChargeAnchor contract with Foundry."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass


DEFAULT_RPC_URL = "http://127.0.0.1:8545"
DEFAULT_CHAIN_ID = "31337"

# Public Anvil development key. Never use this outside a local chain.
ANVIL_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


@dataclass(frozen=True)
class DeploymentConfig:
    rpc_url: str
    private_key: str
    chain_id: str


def load_deployment_config() -> DeploymentConfig:
    return DeploymentConfig(
        rpc_url=os.getenv("WEB3_RPC_URL", DEFAULT_RPC_URL),
        private_key=os.getenv("ANCHOR_PRIVATE_KEY", ANVIL_PRIVATE_KEY),
        chain_id=os.getenv("CHAIN_ID", DEFAULT_CHAIN_ID),
    )


def parse_deployed_address(output: str) -> str:
    match = re.search(r"Deployed to:\s*(0x[a-fA-F0-9]{40})", output)
    if not match:
        raise ValueError("Could not find deployed contract address in forge output")
    return match.group(1)


def deploy_anchor(config: DeploymentConfig) -> str:
    command = [
        "forge",
        "create",
        "contracts/src/ProofOfChargeAnchor.sol:ProofOfChargeAnchor",
        "--rpc-url",
        config.rpc_url,
        "--private-key",
        config.private_key,
        "--broadcast",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return parse_deployed_address(output)


def main() -> int:
    config = load_deployment_config()
    try:
        address = deploy_anchor(config)
    except subprocess.CalledProcessError as exc:
        print(exc.stdout, end="")
        print(exc.stderr, end="", file=sys.stderr)
        return exc.returncode
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print("[OK] ProofOfChargeAnchor deployed")
    print(f"ANCHOR_CONTRACT_ADDRESS={address}")
    print(f"WEB3_RPC_URL={config.rpc_url}")
    print(f"CHAIN_ID={config.chain_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
