# Contracts

Smart contracts for anchoring Proof-of-Charge batch roots.

Initial target:

- Local development chain: Anvil or Hardhat
- Testnet target: Sepolia or Base Sepolia
- On-chain scope: daily batch roots only, not individual receipts

## Contract

`src/ProofOfChargeAnchor.sol` stores one Merkle batch root for each
`day + sessionPrefix` pair and emits `BatchAnchored` when a root is anchored.

The backend should publish:

- `day`
- `sessionPrefix`
- `batchRoot`
- `receiptCount`

The backend should store the resulting transaction hash in
`batch_anchors.chain_tx`.

## Local Deploy

Start a local chain:

```bash
anvil
```

Or start Anvil through Docker Compose:

```bash
docker compose up -d anvil
```

In another terminal, deploy the contract:

```bash
python3 scripts/deploy_anchor.py
```

The script uses Anvil's first development account by default. Do not use that
private key outside a local chain.

## Tests

Run the contract tests:

```bash
forge test
```

The tests cover owner-only anchoring, duplicate rejection, invalid inputs,
missing-anchor reads, ownership transfer, and independent anchors per
`day + sessionPrefix`.
